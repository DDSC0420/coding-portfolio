"""Rules of the board, tested without any web server."""

import pytest

from app import rooms
from app.rooms import BoardError, Registry, Room


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        self.now += 0.001  # every reading is distinct, like a real clock
        return self.now


@pytest.fixture
def room():
    return Room("testroom", Clock())


def code(excinfo):
    return excinfo.value.code


def test_create_starts_in_first_column_with_a_timeline_entry(room):
    incident, entry, removed = room.create("  API   down\n", "sev1", "Ana")
    assert incident["title"] == "API down"  # whitespace and newlines collapsed
    assert incident["status"] == "investigating"
    assert incident["version"] == 1
    assert incident["updates"][0]["kind"] == "status"
    assert entry["by"] == "Ana" and removed is None


@pytest.mark.parametrize(
    "title, severity, expected",
    [
        ("", "sev1", "invalid"),
        ("   ", "sev1", "invalid"),
        (None, "sev1", "invalid"),
        (123, "sev1", "invalid"),
        ("x" * 121, "sev1", "too_long"),
        ("ok", "sev9", "invalid"),
        ("ok", None, "invalid"),
    ],
)
def test_create_rejects_bad_input(room, title, severity, expected):
    with pytest.raises(BoardError) as err:
        room.create(title, severity, "Ana")
    assert code(err) == expected
    assert room.incidents == {}


def test_control_characters_are_stripped(room):
    incident, _, _ = room.create("a\x00b\x07c", "sev2", "Ana")
    assert incident["title"] == "a b c"


def test_move_bumps_version_and_records_who_did_it(room):
    incident, _, _ = room.create("API down", "sev1", "Ana")
    moved, entry = room.move(incident["id"], "identified", 1, "Ben")
    assert moved["status"] == "identified" and moved["version"] == 2
    assert moved["updates"][-1]["by"] == "Ben"
    assert "identified" in entry["text"]


def test_stale_move_is_refused_and_returns_current_state(room):
    """Two people drag the same card at once: the second one loses, visibly."""
    incident, _, _ = room.create("API down", "sev1", "Ana")
    room.move(incident["id"], "identified", 1, "Ana")
    with pytest.raises(BoardError) as err:
        room.move(incident["id"], "resolved", 1, "Ben")  # Ben still thinks it's version 1
    assert code(err) == "conflict"
    assert err.value.incident["status"] == "identified"
    assert room.incidents[incident["id"]]["status"] == "identified"


@pytest.mark.parametrize("version", [None, "1", 1.0, True])
def test_move_requires_an_integer_version(room, version):
    incident, _, _ = room.create("API down", "sev1", "Ana")
    with pytest.raises(BoardError) as err:
        room.move(incident["id"], "identified", version, "Ana")
    assert code(err) == "invalid"


def test_move_rejects_unknown_status_missing_incident_and_no_op(room):
    incident, _, _ = room.create("API down", "sev1", "Ana")
    for args, expected in [
        ((incident["id"], "deleted", 1), "invalid"),
        (("nope", "identified", 1), "not_found"),
        ((["list"], "identified", 1), "not_found"),
        ((incident["id"], "investigating", 1), "no_change"),
    ]:
        with pytest.raises(BoardError) as err:
            room.move(*args, "Ana")
        assert code(err) == expected


def test_note_is_added_and_makes_older_versions_stale(room):
    incident, _, _ = room.create("API down", "sev1", "Ana")
    noted, _ = room.note(incident["id"], "Looking at logs", "Ben")
    assert noted["updates"][-1] == {**noted["updates"][-1], "kind": "note", "text": "Looking at logs", "by": "Ben"}
    with pytest.raises(BoardError) as err:
        room.move(incident["id"], "identified", 1, "Ana")
    assert code(err) == "conflict"


def test_note_length_and_timeline_are_capped(room):
    incident, _, _ = room.create("API down", "sev1", "Ana")
    with pytest.raises(BoardError) as err:
        room.note(incident["id"], "x" * 281, "Ana")
    assert code(err) == "too_long"
    for n in range(rooms.MAX_UPDATES + 10):
        room.note(incident["id"], f"update {n}", "Ana")
    assert len(incident["updates"]) == rooms.MAX_UPDATES
    assert incident["updates"][-1]["text"] == f"update {rooms.MAX_UPDATES + 9}"


def test_activity_log_is_capped(room):
    incident, _, _ = room.create("API down", "sev1", "Ana")
    for n in range(rooms.MAX_ACTIVITY + 5):
        room.note(incident["id"], f"n{n}", "Ana")
    assert len(room.activity) == rooms.MAX_ACTIVITY


def test_full_board_refuses_new_incidents_until_one_is_resolved(room):
    made = [room.create(f"incident {n}", "sev3", "Ana")[0] for n in range(rooms.MAX_INCIDENTS)]
    with pytest.raises(BoardError) as err:
        room.create("one too many", "sev3", "Ana")
    assert code(err) == "board_full"

    room.move(made[3]["id"], "resolved", 1, "Ana")
    _, _, removed = room.create("fits now", "sev3", "Ana")
    assert removed == made[3]["id"]
    assert len(room.incidents) == rooms.MAX_INCIDENTS


def test_people_join_leave_rename(room):
    ana = room.join("Ana")
    anon = room.join(None)
    assert ana["name"] == "Ana" and anon["name"]  # anonymous visitors get a generated name
    assert ana["id"] != anon["id"]
    assert room.rename(ana["id"], " Ana B ")["name"] == "Ana B"
    with pytest.raises(BoardError):
        room.rename(ana["id"], "")
    room.leave(ana["id"])
    assert list(room.people) == [anon["id"]]


def test_room_has_a_people_limit(room):
    for _ in range(rooms.MAX_PEOPLE):
        room.join()
    with pytest.raises(BoardError) as err:
        room.join()
    assert code(err) == "room_full"


def test_registry_validates_ids_and_seeds_new_boards():
    registry = Registry(Clock())
    for bad in ["", "abc", "UPPERCASE1", "has space", "../etc/passwd", "x" * 17]:
        with pytest.raises(BoardError):
            registry.get_or_create(bad)
    room = registry.get_or_create("abc12345")
    assert len(room.incidents) == 3
    assert registry.get_or_create("abc12345") is room


def test_idle_empty_boards_expire_but_occupied_ones_do_not():
    clock = Clock()
    registry = Registry(clock)
    registry.get_or_create("emptyroom")
    busy = registry.get_or_create("busyroom1")
    busy.join("Ana")
    clock.now += rooms.ROOM_TTL_SECONDS + 1
    assert registry.sweep() == 1
    assert list(registry.rooms) == ["busyroom1"]


def test_at_capacity_the_oldest_empty_board_is_evicted(monkeypatch):
    monkeypatch.setattr(rooms, "MAX_ROOMS", 3)
    registry = Registry(Clock(), seed_rooms=False)
    for name in ("room0001", "room0002", "room0003"):
        registry.get_or_create(name)
    registry.get_or_create("room0004")
    assert "room0001" not in registry.rooms and len(registry.rooms) == 3


def test_at_capacity_with_every_board_occupied_new_boards_are_refused(monkeypatch):
    monkeypatch.setattr(rooms, "MAX_ROOMS", 2)
    registry = Registry(Clock(), seed_rooms=False)
    for name in ("room0001", "room0002"):
        registry.get_or_create(name).join()
    with pytest.raises(BoardError) as err:
        registry.get_or_create("room0003")
    assert code(err) == "busy"
