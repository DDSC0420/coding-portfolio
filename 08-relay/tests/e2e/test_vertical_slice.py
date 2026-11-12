"""Stage 1 end-to-end: real API, demo CRM and two worker processes over HTTP and PostgreSQL."""

import json
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier

import httpx
import pytest
from sqlalchemy import Engine

from relay.steps.crm_upsert import build_payload
from tests.e2e.processes import ROOT, Service, free_port, uvicorn, wait_ready, worker
from tests.helpers import fetch_all, fetch_one

pytestmark = pytest.mark.e2e

GOLDEN = json.loads((ROOT / "fixtures/enquiries/golden.json").read_text(encoding="utf-8"))
INVALID = json.loads((ROOT / "fixtures/enquiries/invalid.json").read_text(encoding="utf-8"))
TERMINAL = {"succeeded", "failed", "outcome_unknown", "rejected"}


@dataclass
class Stack:
    api: str
    crm: str
    services: dict[str, Service]


@pytest.fixture
def stack(database_urls, engine: Engine, crm_engine: Engine, tmp_path: Path) -> Iterator[Stack]:
    api_port, crm_port = free_port(), free_port()
    crm_url = f"http://127.0.0.1:{crm_port}"
    engine_env = {
        "RELAY_DATABASE_URL": database_urls["engine"],
        "RELAY_CRM_BASE_URL": crm_url,
        "RELAY_POLL_INTERVAL_SECONDS": "0.1",
        "RELAY_WORKER_CONCURRENCY": "2",
        "RELAY_WORKER_HEARTBEAT_SECONDS": "0.5",
    }
    services = {
        "crm": uvicorn(
            "crm",
            "services.demo_crm.app:app",
            crm_port,
            {"CRM_DATABASE_URL": database_urls["crm"]},
            tmp_path,
        ),
        "api": uvicorn("api", "services.api.app:app", api_port, engine_env, tmp_path),
        "worker-a": worker("worker-a", engine_env, tmp_path),
        "worker-b": worker("worker-b", engine_env, tmp_path),
    }
    try:
        for s in services.values():
            s.start()
        wait_ready(f"{crm_url}/health/ready")
        wait_ready(f"http://127.0.0.1:{api_port}/health/ready")
        _wait_for(lambda: fetch_one(engine, "SELECT count(*) AS n FROM workers")["n"] == 2, 15)
        yield Stack(f"http://127.0.0.1:{api_port}", crm_url, services)
    finally:
        for s in services.values():
            s.stop()
        for s in services.values():
            print(f"----- {s.name} log -----\n{s.log()[-3000:]}")


def _wait_for(predicate, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise TimeoutError("condition not met")


def _submit(api: str, enquiry: dict, batch: str) -> httpx.Response:
    return httpx.post(
        f"{api}/workflows",
        json={
            "workflow_type": "enquiry",
            "workflow_version": 1,
            "input": enquiry,
            "batch_id": batch,
        },
        headers={"Idempotency-Key": f"e2e-{enquiry['enquiry_ref']}"},
        timeout=5,
    )


def test_batch_runs_to_completion_across_two_worker_processes(stack: Stack, engine: Engine) -> None:
    valid, invalid = GOLDEN[:20], INVALID
    created = {e["enquiry_ref"]: _submit(stack.api, e, "e2e") for e in valid + invalid}
    assert {r.status_code for r in created.values()} == {201}

    # Resubmitting with the same key returns the same run instead of a new one.
    replay = _submit(stack.api, valid[0], "e2e")
    assert replay.status_code == 200
    assert (
        replay.json()["workflow_run_id"]
        == created[valid[0]["enquiry_ref"]].json()["workflow_run_id"]
    )
    conflict = _submit(stack.api, {**valid[0], "company": "Changed Ltd"}, "e2e")
    assert conflict.status_code == 409

    def all_terminal() -> bool:
        page = httpx.get(f"{stack.api}/workflows", params={"batch_id": "e2e", "limit": 200}).json()
        return page["total"] == 25 and all(w["status"] in TERMINAL for w in page["items"])

    _wait_for(all_terminal, 60)
    items = httpx.get(f"{stack.api}/workflows", params={"batch_id": "e2e", "limit": 200}).json()[
        "items"
    ]
    by_ref = {w["label"]: w for w in items}
    assert {by_ref[e["enquiry_ref"]]["status"] for e in valid} == {"succeeded"}
    assert {by_ref[e["enquiry_ref"]]["status"] for e in invalid} == {"failed"}

    # A validation failure is permanent, structured and attempted once.
    bad = httpx.get(f"{stack.api}/workflows/{by_ref['BAD-002']['id']}").json()
    assert bad["failure"]["step"] == "validate"
    assert {"field": "email", "code": "invalid_format"}.items() <= bad["failure"]["details"][
        "errors"
    ][0].items()
    assert [s["status"] for s in bad["steps"]] == ["succeeded", "failed", "cancelled", "cancelled"]

    # With no faults injected, every executed step ran exactly once.
    attempts = fetch_all(engine, "SELECT step_run_id, count(*) AS n FROM step_attempts GROUP BY 1")
    assert attempts and {a["n"] for a in attempts} == {1}
    owners = {
        r["name"]
        for r in fetch_all(
            engine,
            "SELECT DISTINCT w.name FROM step_attempts a JOIN workers w ON w.id = a.worker_id",
        )
    }
    assert owners == {"worker-a", "worker-b"}

    # Exactly one CRM mutation per successful workflow, none duplicated.
    summary = httpx.get(f"{stack.crm}/mutations/summary").json()
    assert summary == {"mutations": 20, "keys_with_duplicates": []}

    # Durable event timeline, read with cursor pagination.
    run_id = by_ref[valid[0]["enquiry_ref"]]["id"]
    events, cursor = [], 0
    while True:
        page = httpx.get(
            f"{stack.api}/workflows/{run_id}/events", params={"after": cursor, "limit": 3}
        ).json()
        events += page["items"]
        if page["next_cursor"] is None:
            break
        cursor = page["next_cursor"]
    types = [e["type"] for e in events]
    assert types[0] == "workflow.created" and types[-1] == "workflow.succeeded"
    assert types.count("step.claimed") == 4 and types.count("step.succeeded") == 4


def test_crm_replays_the_stored_result_for_a_repeated_request(stack: Stack) -> None:
    """The retry the engine would send after losing a response: same key, same payload."""
    enquiry = GOLDEN[20]
    run_id = _submit(stack.api, enquiry, "e2e-replay").json()["workflow_run_id"]
    _wait_for(lambda: httpx.get(f"{stack.api}/workflows/{run_id}").json()["status"] in TERMINAL, 30)
    detail = httpx.get(f"{stack.api}/workflows/{run_id}").json()
    crm_step = detail["steps"][-1]
    assert detail["status"] == "succeeded" and crm_step["name"] == "crm_upsert"

    payload = build_payload(crm_step["input"], run_id)  # rebuilt from persisted input
    resent = httpx.post(
        f"{stack.crm}/contacts/upsert",
        json=payload,
        headers={"Idempotency-Key": crm_step["idempotency_key"]},
    )
    assert resent.status_code == 200 and resent.headers["Idempotent-Replayed"] == "true"
    assert resent.json()["mutation_id"] == crm_step["output"]["crm"]["mutation_id"]
    assert httpx.get(f"{stack.crm}/mutations/summary").json()["mutations"] == 1

    changed = httpx.post(
        f"{stack.crm}/contacts/upsert",
        json={**payload, "name": "Other"},
        headers={"Idempotency-Key": crm_step["idempotency_key"]},
    )
    assert changed.status_code == 409


def test_concurrent_http_requests_with_one_key_apply_one_mutation(stack: Stack, crm_engine) -> None:
    n = 10
    barrier = Barrier(n)
    payload = {
        "email": "race@kestrel-robotics.example",
        "name": "Race",
        "company": "Kestrel Robotics",
    }

    def send(_: int) -> httpx.Response:
        barrier.wait()
        return httpx.post(
            f"{stack.crm}/contacts/upsert",
            json=payload,
            headers={"Idempotency-Key": "concurrent-http-key"},
            timeout=10,
        )

    with ThreadPoolExecutor(n) as pool:
        responses = list(pool.map(send, range(n)))
    assert {r.status_code for r in responses} == {200}
    assert len({r.json()["mutation_id"] for r in responses}) == 1
    assert [r.headers["Idempotent-Replayed"] for r in responses].count("false") == 1
    assert fetch_one(crm_engine, "SELECT count(*) AS n FROM mutations")["n"] == 1


@pytest.mark.skipif(
    sys.platform == "win32", reason="SIGTERM is a hard kill on native Windows; run from WSL2"
)
def test_worker_health_reflects_heartbeats_and_graceful_stop(stack: Stack) -> None:
    workers = {w["name"]: w for w in httpx.get(f"{stack.api}/workers").json()}
    assert {w["status"] for w in workers.values()} == {"online"}
    assert stack.services["worker-a"].stop() == 0
    workers = {w["name"]: w for w in httpx.get(f"{stack.api}/workers").json()}
    assert workers["worker-a"]["status"] == "stopped"
    assert workers["worker-b"]["status"] == "online"
