"""Stage 3 end-to-end: the approval gate and the operations API over HTTP, with real processes."""

import httpx
import pytest
from sqlalchemy import Engine

from tests.e2e.processes import wait_ready, wait_until
from tests.e2e.test_vertical_slice import Stack, stack  # noqa: F401  (pytest fixture)
from tests.helpers import fetch_all, fetch_one

pytestmark = pytest.mark.e2e

BIG = {
    "enquiry_ref": "APPROVE-1",
    "name": "Priya Shah",
    "email": "priya.shah@northwind-logistics.example",
    "company": "Northwind Logistics Ltd",
    "message": "We want to replace our warehouse management system. Budget £180,000.",
}


def _submit(api: str, enquiry: dict) -> str:
    r = httpx.post(f"{api}/workflows", json={"input": enquiry}, timeout=5)
    assert r.status_code == 201, r.text
    return r.json()["workflow_run_id"]


def _run(api: str, run_id: str) -> dict:
    return httpx.get(f"{api}/workflows/{run_id}", timeout=5).json()


def _wait_status(api: str, run_id: str, status: str, timeout: float = 20) -> dict:
    return wait_until(
        lambda: (r := _run(api, run_id))["status"] == status and r,
        timeout,
        f"run {run_id} to be {status}",
    )


def test_an_approval_survives_a_full_restart_then_continues(stack: Stack, engine: Engine) -> None:  # noqa: F811
    run_id = _submit(stack.api, BIG)
    paused = _wait_status(stack.api, run_id, "awaiting_approval")
    assert paused["workflow_version"] == 2 and paused["current_step"] == "approval"
    assert paused["approval"]["status"] == "pending"
    assert paused["approval"]["project_value_gbp"] == 180_000

    pending = httpx.get(f"{stack.api}/approvals", timeout=5).json()
    assert [(a["workflow_run_id"], a["label"]) for a in pending] == [(run_id, "APPROVE-1")]

    # Stop every process. Only the databases keep running.
    for s in stack.services.values():
        s.stop()
    assert fetch_one(engine, "SELECT status FROM approvals")["status"] == "pending"
    for s in stack.services.values():
        s.start()
    wait_ready(f"{stack.api}/health/ready")
    wait_ready(f"{stack.crm}/health/ready")

    still = _run(stack.api, run_id)
    assert (still["status"], still["approval"]["status"]) == ("awaiting_approval", "pending")
    assert httpx.get(f"{stack.crm}/mutations/summary", timeout=5).json()["mutations"] == 0

    body = {"reviewer": "kyran", "comment": "go ahead"}
    first = httpx.post(f"{stack.api}/workflows/{run_id}/approve", json=body, timeout=5)
    assert first.status_code == 200 and first.json()["replayed"] is False

    done = _wait_status(stack.api, run_id, "succeeded")
    assert [s["status"] for s in done["steps"]] == ["succeeded"] * 5
    assert done["approval"]["decided_by"] == "kyran"
    assert httpx.get(f"{stack.crm}/mutations/summary", timeout=5).json() == {
        "mutations": 1,
        "keys_with_duplicates": [],
    }

    # A lost response is safe to retry; changing your mind is not allowed.
    again = httpx.post(f"{stack.api}/workflows/{run_id}/approve", json=body, timeout=5)
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["decided_at"] == first.json()["decided_at"]
    conflict = httpx.post(
        f"{stack.api}/workflows/{run_id}/reject", json={"reviewer": "abby"}, timeout=5
    )
    assert conflict.status_code == 409


def test_a_rejected_enquiry_never_reaches_the_crm(stack: Stack, engine: Engine) -> None:  # noqa: F811
    run_id = _submit(stack.api, {**BIG, "enquiry_ref": "REJECT-1"})
    _wait_status(stack.api, run_id, "awaiting_approval")
    r = httpx.post(
        f"{stack.api}/workflows/{run_id}/reject",
        json={"reviewer": "abby", "comment": "not our kind of work"},
        timeout=5,
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    run = _run(stack.api, run_id)
    assert run["status"] == "rejected"
    assert {s["name"]: s["status"] for s in run["steps"]}["crm_upsert"] == "cancelled"
    assert run["failure"]["comment"] == "not our kind of work"
    assert httpx.get(f"{stack.crm}/mutations/summary", timeout=5).json()["mutations"] == 0


def test_decisions_on_runs_that_cannot_take_one(stack: Stack) -> None:  # noqa: F811
    small = _submit(
        stack.api, {**BIG, "enquiry_ref": "SMALL-1", "message": "A small job, about £4,000 please."}
    )
    _wait_status(stack.api, small, "succeeded")
    body = {"reviewer": "kyran"}
    assert (
        httpx.post(f"{stack.api}/workflows/{small}/approve", json=body, timeout=5).status_code
        == 404
    )
    missing = "00000000-0000-0000-0000-000000000000"
    assert (
        httpx.post(f"{stack.api}/workflows/{missing}/reject", json=body, timeout=5).status_code
        == 404
    )
    assert (
        httpx.post(f"{stack.api}/workflows/{small}/approve", json={}, timeout=5).status_code == 422
    )


def test_a_demo_batch_and_the_operations_views_agree(stack: Stack, engine: Engine) -> None:  # noqa: F811
    r = httpx.post(
        f"{stack.api}/batches",
        json={"count": 12, "invalid": 3, "needs_approval": 4, "batch_id": "ops"},
        timeout=10,
    )
    assert r.status_code == 201, r.text
    assert (r.json()["batch_id"], r.json()["submitted"]) == ("ops", 15)

    def settled() -> dict | None:
        m = httpx.get(f"{stack.api}/metrics", timeout=5).json()
        return m if m["workflows"]["running"] == 0 else None

    metrics = wait_until(settled, 30, "the batch to settle")
    assert metrics["workflows"] == {
        "running": 0,
        "awaiting_approval": 4,
        "succeeded": 8,
        "failed": 3,
        "outcome_unknown": 0,
        "rejected": 0,
    }
    assert metrics["total"] == 15 and metrics["steps_running"] == 0
    assert metrics["retries_total"] == 0 and metrics["stale_writes_rejected_total"] == 0
    assert metrics["p50_seconds"] is not None

    overview = httpx.get(f"{stack.api}/overview", timeout=5).json()
    assert overview["metrics"]["workflows"] == metrics["workflows"]
    assert overview["leases"] == []
    reasons = sorted(a["reason"][:14] for a in overview["pending_approvals"])
    assert reasons == ["project value ", "project value ", "review request", "review request"]

    listed = httpx.get(
        f"{stack.api}/workflows", params={"status": "awaiting_approval"}, timeout=5
    ).json()
    assert listed["total"] == 4

    # Event feed: the newest page, then nothing new after its last id.
    feed = httpx.get(f"{stack.api}/events", params={"limit": 500}, timeout=5).json()["items"]
    ids = [e["id"] for e in feed]
    assert ids == sorted(ids) and all(e["label"].startswith("ops-") for e in feed)
    total_events = fetch_one(engine, "SELECT count(*) AS n FROM workflow_events")["n"]
    assert len(feed) == min(500, total_events)
    after = httpx.get(f"{stack.api}/events", params={"after": ids[-1]}, timeout=5).json()
    assert after == {"items": [], "next_cursor": None}
    requested = httpx.get(
        f"{stack.api}/events", params={"type_prefix": "approval."}, timeout=5
    ).json()
    assert [e["type"] for e in requested["items"]] == ["approval.requested"] * 4

    # Same batch id again: every submission is a replay, nothing new is created.
    again = httpx.post(
        f"{stack.api}/batches",
        json={"count": 12, "invalid": 3, "needs_approval": 4, "batch_id": "ops"},
        timeout=10,
    )
    assert again.json()["workflow_run_ids"] == r.json()["workflow_run_ids"]
    assert fetch_one(engine, "SELECT count(*) AS n FROM workflow_runs")["n"] == 15

    defs = httpx.get(f"{stack.api}/workflow-definitions", timeout=5).json()
    assert [(d["version"], len(d["steps"])) for d in defs] == [(1, 4), (2, 5)]

    crm = httpx.get(f"{stack.api}/demo/crm", timeout=5).json()
    assert crm["mutations"] == 8 and crm["faults"] == {"demo_mode": False}
    assert (
        httpx.put(f"{stack.api}/demo/crm/faults", json={"delay_ms": 5}, timeout=5).status_code
        == 404
    )
    assert fetch_all(engine, "SELECT 1 FROM step_runs WHERE status = 'running'") == []
