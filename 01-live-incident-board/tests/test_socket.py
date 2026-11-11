"""The WebSocket protocol, exercised through real connections to the app."""

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import TokenBucket, create_app
from app.rooms import Registry

ROOM = "/ws/testroom1"


@pytest.fixture
def client():
    app = create_app(Registry(seed_rooms=False), drill_delay=0)
    with TestClient(app) as test_client:
        yield test_client


def until(ws, kind):
    """Read messages until one of the wanted type arrives."""
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg


def handshake(ws):
    snapshot = until(ws, "snapshot")
    until(ws, "presence")
    return snapshot


@pytest.fixture
def connect(client):
    """Open a connection to a board; every connection is closed cleanly after the test."""
    with ExitStack() as stack:

        def _connect(room=ROOM):
            ws = stack.enter_context(client.websocket_connect(room))
            return ws, handshake(ws)

        yield _connect


def test_pages_and_health(client):
    redirect = client.get("/", follow_redirects=False)
    assert redirect.status_code == 302 and redirect.headers["location"].startswith("/b/")
    page = client.get(redirect.headers["location"])
    assert page.status_code == 200 and "Incident board" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/b/NOT-VALID").status_code == 404
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/static/app.js").status_code == 200


def test_new_visitor_gets_a_snapshot_of_the_seeded_board():
    with TestClient(create_app(drill_delay=0)) as client, client.websocket_connect(ROOM) as ws:
        snapshot = handshake(ws)
        assert len(snapshot["incidents"]) == 3
        assert snapshot["you"]["name"] and snapshot["people"] == [snapshot["you"]]


def test_a_change_by_one_person_reaches_everyone(connect):
    ana, _ = connect()
    ben, _ = connect()
    until(ana, "presence")  # Ana sees Ben arrive

    ana.send_json({"type": "create", "title": "API down", "severity": "sev1"})
    seen_by_ben = until(ben, "incident")
    seen_by_ana = until(ana, "incident")
    assert seen_by_ben == seen_by_ana
    assert seen_by_ben["incident"]["title"] == "API down"

    incident = seen_by_ben["incident"]
    ben.send_json({"type": "move", "id": incident["id"], "status": "identified", "version": incident["version"]})
    assert until(ana, "incident")["incident"]["status"] == "identified"


def test_two_people_moving_the_same_card_only_the_first_wins(connect):
    ana, _ = connect()
    ben, _ = connect()
    ana.send_json({"type": "create", "title": "API down", "severity": "sev1"})
    incident = until(ben, "incident")["incident"]

    # Both act on version 1. Ana's message arrives first.
    ana.send_json({"type": "move", "id": incident["id"], "status": "identified", "version": 1})
    ben.send_json({"type": "move", "id": incident["id"], "status": "resolved", "version": 1, "ref": "m1"})

    error = until(ben, "error")
    assert error["code"] == "conflict" and error["ref"] == "m1"
    assert error["incident"]["status"] == "identified"  # Ben is handed the truth


def test_late_joiner_sees_everything_that_already_happened(connect):
    ana, _ = connect()
    ana.send_json({"type": "create", "title": "API down", "severity": "sev2"})
    incident = until(ana, "incident")["incident"]
    ana.send_json({"type": "note", "id": incident["id"], "text": "Rolling back"})
    until(ana, "incident")

    late, snapshot = connect()
    assert [i["title"] for i in snapshot["incidents"]] == ["API down"]
    assert snapshot["incidents"][0]["updates"][-1]["text"] == "Rolling back"
    assert len(snapshot["activity"]) == 2


def test_boards_are_isolated_from_each_other(connect):
    ana, _ = connect("/ws/roomaaaa")
    ben, _ = connect("/ws/roombbbb")
    ana.send_json({"type": "create", "title": "only on A", "severity": "sev3"})
    until(ana, "incident")
    carol, snapshot = connect("/ws/roombbbb")
    assert snapshot["incidents"] == []


def test_presence_updates_on_join_rename_and_leave(connect):
    ana, _ = connect()
    ben, _ = connect()
    assert len(until(ana, "presence")["people"]) == 2

    ben.send_json({"type": "rename", "name": "Ben"})
    assert "Ben" in [p["name"] for p in until(ana, "presence")["people"]]

    ben.close()
    assert len(until(ana, "presence")["people"]) == 1


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("not json", "invalid"),
        ("[1, 2]", "invalid"),
        ('{"type": "nope"}', "invalid"),
        ('{"type": "create", "title": "", "severity": "sev1"}', "invalid"),
        ('{"type": "move", "id": "missing", "status": "resolved", "version": 1}', "not_found"),
        ('{"type": "note", "id": {"a": 1}, "text": "x"}', "not_found"),
        ('{"type": "create", "title": "' + "x" * 3000 + '", "severity": "sev1"}', "too_large"),
    ],
)
def test_bad_messages_get_an_error_and_the_connection_survives(connect, raw, expected):
    ws, _ = connect()
    ws.send_text(raw)
    assert until(ws, "error")["code"] == expected
    ws.send_json({"type": "ping"})
    assert until(ws, "pong")


def test_invalid_board_id_is_refused(client):
    with client.websocket_connect("/ws/BAD") as ws:
        error = ws.receive_json()
        assert error["code"] == "invalid" and error["fatal"] is True


def test_flooding_is_rate_limited(connect, monkeypatch):
    monkeypatch.setattr(main, "TokenBucket", lambda: TokenBucket(burst=3, rate=0))
    ws, _ = connect()
    for _ in range(3):
        ws.send_json({"type": "ping"})
        assert until(ws, "pong")
    ws.send_json({"type": "ping"})
    assert until(ws, "error")["code"] == "rate_limited"


def test_token_bucket_refills_over_time():
    now = [0.0]
    bucket = TokenBucket(burst=2, rate=1, clock=lambda: now[0])
    assert bucket.take() and bucket.take() and not bucket.take()
    now[0] += 1.5
    assert bucket.take() and not bucket.take()
    now[0] += 100
    assert bucket.take() and bucket.take() and not bucket.take()  # never exceeds the burst


def test_drill_plays_an_incident_through_to_resolved(connect):
    ana, _ = connect()
    ben, _ = connect()
    ana.send_json({"type": "drill"})
    assert until(ben, "drill")["running"] is True

    statuses = []
    while True:
        msg = ben.receive_json()
        if msg["type"] == "incident":
            statuses.append(msg["incident"]["status"])
        if msg["type"] == "drill" and msg["running"] is False:
            break
    assert statuses[0] == "investigating" and statuses[-1] == "resolved"
    assert [s for i, s in enumerate(statuses) if i == 0 or statuses[i - 1] != s] == [
        "investigating",
        "identified",
        "monitoring",
        "resolved",
    ]


def test_only_one_drill_runs_at_a_time():
    app = create_app(Registry(seed_rooms=False), drill_delay=0.2)
    with TestClient(app) as client, client.websocket_connect(ROOM) as ws:
        handshake(ws)
        ws.send_json({"type": "drill"})
        ws.send_json({"type": "drill"})
        assert until(ws, "error")["code"] == "busy"
