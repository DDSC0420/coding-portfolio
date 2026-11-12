"""Stage 3 on real PostgreSQL: the approval gate (T4a, T7) and version pinning (I9).

Steps are run by the real executor against the real registry; only the CRM
step is left unexecuted here (the end-to-end tests cover it over HTTP).
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from relay.config import EngineSettings
from relay.engine.approvals import ApprovalConflict, ApprovalNotFound, decide
from relay.engine.claims import ClaimedStep, claim_next_step
from relay.engine.executor import execute_claimed
from relay.engine.recovery import recover_expired_leases
from relay.engine.states import TERMINAL_STEP
from relay.engine.submission import submit_workflow
from tests.conftest import VALID_ENQUIRY
from tests.helpers import event_types, expire_lease, fetch_all, fetch_one, steps_of

SETTINGS = EngineSettings(approval_threshold_gbp=50_000)
HIGH_VALUE = "We need a full warehouse system rebuild. Budget is £120,000."
V1, V2, BOTH = ["enquiry:1"], ["enquiry:2"], ["enquiry:1", "enquiry:2"]


def _submit(engine, registry, version=None, **overrides):
    return submit_workflow(
        engine,
        registry,
        workflow_type="enquiry",
        workflow_version=version,
        input={**VALID_ENQUIRY, **overrides},
    ).workflow_run_id


def _claim(engine, supported=BOTH, worker="worker-a/0001") -> ClaimedStep | None:
    return claim_next_step(engine, worker_id=worker, lease_seconds=30, supported=supported)


def _run_until_crm_or_idle(engine, registry, supported=BOTH) -> list[tuple[str, str]]:
    """Execute claimable steps with the real handlers, stopping before any CRM call."""
    ran = []
    while (step := _claim(engine, supported)) is not None:
        if step.name == "crm_upsert":
            ran.append((step.name, "claimed"))
            break
        ran.append((step.name, execute_claimed(engine, registry, SETTINGS, step, http=None)))
    return ran


def _paused_run(engine, registry, **overrides):
    run_id = _submit(engine, registry, message=HIGH_VALUE, **overrides)
    assert _run_until_crm_or_idle(engine, registry)[-1] == ("approval", "awaiting_approval")
    return run_id


def _status(engine, run_id) -> str:
    return fetch_one(engine, "SELECT status FROM workflow_runs WHERE id = :id", id=run_id)["status"]


# --- the gate -----------------------------------------------------------------


def test_latest_version_has_the_approval_step_and_low_value_passes_straight_through(
    engine, registry
) -> None:
    run_id = _submit(engine, registry)  # VALID_ENQUIRY mentions £25,000
    ran = _run_until_crm_or_idle(engine, registry)
    assert ran == [
        ("extract", "succeeded"),
        ("validate", "succeeded"),
        ("enrich", "succeeded"),
        ("approval", "succeeded"),
        ("crm_upsert", "claimed"),
    ]
    approval = steps_of(engine, run_id)[3]
    assert approval["output"]["approval"] == {"required": False}
    assert fetch_all(engine, "SELECT * FROM approvals") == []


def test_high_value_pauses_holding_no_lease(engine, registry) -> None:
    run_id = _paused_run(engine, registry)
    assert _status(engine, run_id) == "awaiting_approval"
    by_name = {s["name"]: s for s in steps_of(engine, run_id)}
    gate = by_name["approval"]
    assert gate["status"] == "awaiting_approval"
    assert (gate["lease_owner"], gate["lease_expires_at"]) == (None, None)
    assert gate["output"] is None  # not decided, so nothing has been handed on
    assert by_name["crm_upsert"]["status"] == "pending"

    request = fetch_one(engine, "SELECT * FROM approvals WHERE workflow_run_id = :id", id=run_id)
    assert request["status"] == "pending"
    assert request["reason"] == "project value £120,000 is at or above the £50,000 threshold"
    assert request["proposed_output"]["enquiry"]["project_value_gbp"] == 120_000
    attempt = fetch_one(
        engine, "SELECT * FROM step_attempts WHERE step_run_id = :id", id=gate["id"]
    )
    assert (attempt["outcome"], attempt["finished_at"] is not None) == ("paused_for_approval", True)
    assert event_types(engine, run_id)[-2:] == ["step.claimed", "approval.requested"]


def test_a_review_request_pauses_even_a_small_enquiry(engine, registry) -> None:
    run_id = _submit(engine, registry, approval_required=True)
    assert _run_until_crm_or_idle(engine, registry)[-1] == ("approval", "awaiting_approval")
    request = fetch_one(
        engine, "SELECT reason FROM approvals WHERE workflow_run_id = :id", id=run_id
    )
    assert request["reason"] == "review requested by the submitter"


def test_a_paused_workflow_is_invisible_to_workers_and_to_recovery(engine, registry) -> None:
    run_id = _paused_run(engine, registry)
    assert _claim(engine) is None
    # However long it waits, there is no lease to expire: recovery has nothing to do.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE step_runs SET updated_at = now() - interval '30 days' WHERE status <> 'x'")
        )
    assert recover_expired_leases(engine, swept_by="worker-b/0002") == []
    assert _status(engine, run_id) == "awaiting_approval"


def test_a_worker_that_lost_its_lease_cannot_pause_the_workflow(engine, registry) -> None:
    run_id = _submit(engine, registry, message=HIGH_VALUE)
    for _ in range(3):
        execute_claimed(engine, registry, SETTINGS, _claim(engine), http=None)
    stale = _claim(engine, worker="worker-a/0001")
    assert stale.name == "approval"
    expire_lease(engine, stale.step_id)
    assert len(recover_expired_leases(engine, swept_by="worker-b/0002")) == 1
    with engine.begin() as conn:  # skip the retry delay
        conn.execute(text("UPDATE step_runs SET next_eligible_at = now() WHERE status = 'ready'"))
    fresh = _claim(engine, worker="worker-b/0002")
    assert (fresh.step_id, fresh.lease_generation) == (stale.step_id, 2)

    assert execute_claimed(engine, registry, SETTINGS, stale, http=None) == "lease_lost"
    assert fetch_all(engine, "SELECT * FROM approvals") == []
    assert _status(engine, run_id) == "running"
    assert "step.stale_write_rejected" in event_types(engine, run_id)

    assert execute_claimed(engine, registry, SETTINGS, fresh, http=None) == "awaiting_approval"
    assert fetch_one(engine, "SELECT count(*) AS n FROM approvals")["n"] == 1


# --- the decision -------------------------------------------------------------


def test_approve_completes_the_step_and_readies_the_successor_in_one_transaction(
    engine, registry
) -> None:
    run_id = _paused_run(engine, registry)
    decision = decide(engine, run_id, approve=True, decided_by="kyran", comment="fine by me")
    assert (decision.status, decision.decided_by, decision.replayed) == ("approved", "kyran", False)

    assert _status(engine, run_id) == "running"
    by_name = {s["name"]: s for s in steps_of(engine, run_id)}
    gate, crm = by_name["approval"], by_name["crm_upsert"]
    assert gate["status"] == "succeeded" and gate["finished_at"] is not None
    assert gate["output"]["approval"]["decision"] == "approved"
    assert gate["output"]["approval"]["decided_by"] == "kyran"
    assert crm["status"] == "ready"
    assert crm["input"] == gate["output"]  # the stored output is handed on unchanged
    assert event_types(engine, run_id)[-2:] == ["approval.approved", "step.ready"]

    claimed = _claim(engine)
    assert (claimed.name, claimed.workflow_run_id) == ("crm_upsert", run_id)


def test_reject_ends_the_workflow_and_the_crm_step_never_runs(engine, registry) -> None:
    run_id = _paused_run(engine, registry)
    decision = decide(engine, run_id, approve=False, decided_by="abby", comment="out of scope")
    assert decision.status == "rejected"

    run = fetch_one(engine, "SELECT * FROM workflow_runs WHERE id = :id", id=run_id)
    assert run["status"] == "rejected" and run["completed_at"] is not None
    assert run["failure"]["decided_by"] == "abby" and run["failure"]["comment"] == "out of scope"
    statuses = {s["name"]: s["status"] for s in steps_of(engine, run_id)}
    assert statuses["approval"] == "rejected" and statuses["crm_upsert"] == "cancelled"
    assert all(s in TERMINAL_STEP for s in statuses.values())  # I11
    assert event_types(engine, run_id)[-2:] == ["approval.rejected", "workflow.rejected"]
    assert _claim(engine) is None


def test_repeating_a_decision_returns_the_original_and_changes_nothing(engine, registry) -> None:
    run_id = _paused_run(engine, registry)
    first = decide(engine, run_id, approve=True, decided_by="kyran")
    events_after_first = event_types(engine, run_id)
    again = decide(engine, run_id, approve=True, decided_by="someone-else", comment="me too")
    assert again.replayed is True
    assert (again.decided_by, again.decided_at, again.comment) == ("kyran", first.decided_at, None)
    assert event_types(engine, run_id) == events_after_first


def test_the_opposite_decision_is_a_conflict(engine, registry) -> None:
    rejected = _paused_run(engine, registry)
    decide(engine, rejected, approve=False, decided_by="abby")
    with pytest.raises(ApprovalConflict, match="already rejected by abby"):
        decide(engine, rejected, approve=True, decided_by="kyran")
    assert _status(engine, rejected) == "rejected"

    approved = _paused_run(engine, registry, enquiry_ref="TEST-002")
    decide(engine, approved, approve=True, decided_by="kyran")
    with pytest.raises(ApprovalConflict, match="already approved by kyran"):
        decide(engine, approved, approve=False, decided_by="abby")
    assert _status(engine, approved) == "running"


def test_a_workflow_that_never_paused_has_nothing_to_decide(engine, registry) -> None:
    run_id = _submit(engine, registry)
    with pytest.raises(ApprovalNotFound):
        decide(engine, run_id, approve=True, decided_by="kyran")


def test_sixteen_decisions_at_once_produce_exactly_one(engine, registry) -> None:
    run_id = _paused_run(engine, registry)
    n = 16
    barrier = Barrier(n)

    def attempt(i: int) -> str:
        barrier.wait()
        try:
            d = decide(engine, run_id, approve=i % 2 == 0, decided_by=f"reviewer-{i}")
            return "replayed" if d.replayed else f"won:{d.status}"
        except ApprovalConflict:
            return "conflict"

    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(attempt, range(n)))
    winners = [r for r in results if r.startswith("won:")]
    assert len(winners) == 1
    won_approve = winners[0] == "won:approved"
    # Everyone who asked for the winning decision got it back; everyone else conflicted.
    assert results.count("replayed") == n // 2 - 1
    assert results.count("conflict") == n // 2
    decided = [
        t for t in event_types(engine, run_id) if t in ("approval.approved", "approval.rejected")
    ]
    assert decided == ["approval.approved" if won_approve else "approval.rejected"]
    assert _status(engine, run_id) == ("running" if won_approve else "rejected")


def test_the_database_refuses_to_change_a_recorded_decision(engine, registry) -> None:
    run_id = _paused_run(engine, registry)
    decide(engine, run_id, approve=False, decided_by="abby")
    with (
        pytest.raises(DBAPIError, match="already rejected and cannot change"),
        engine.begin() as conn,
    ):
        conn.execute(
            text("UPDATE approvals SET status = 'approved' WHERE workflow_run_id = :id"),
            {"id": run_id},
        )


# --- version pinning (I9) -----------------------------------------------------


def test_a_v1_run_stays_v1_after_v2_is_published(engine, registry) -> None:
    run_id = _submit(engine, registry, version=1, message=HIGH_VALUE)
    assert [s["name"] for s in steps_of(engine, run_id)] == [
        "extract",
        "validate",
        "enrich",
        "crm_upsert",
    ]
    ran = _run_until_crm_or_idle(engine, registry)
    # No gate in v1: a £120,000 enquiry goes straight to the CRM step.
    assert [name for name, _ in ran] == ["extract", "validate", "enrich", "crm_upsert"]
    assert fetch_all(engine, "SELECT * FROM approvals") == []
    run = fetch_one(engine, "SELECT workflow_version FROM workflow_runs WHERE id = :id", id=run_id)
    assert run["workflow_version"] == 1


def test_a_worker_only_claims_versions_it_can_run(engine, registry) -> None:
    v1 = _submit(engine, registry, version=1)
    v2 = _submit(engine, registry, version=2, enquiry_ref="TEST-002")
    old_worker = _claim(engine, V1)
    assert old_worker.workflow_run_id == v1
    assert _claim(engine, V1) is None  # the v2 run is ready, but not for this worker
    new_worker = _claim(engine, V2)
    assert new_worker.workflow_run_id == v2


def test_the_pinned_version_cannot_be_changed_afterwards(engine, registry) -> None:
    run_id = _submit(engine, registry, version=1)
    with pytest.raises(DBAPIError, match="immutable"), engine.begin() as conn:
        conn.execute(
            text("UPDATE workflow_runs SET workflow_version = 2 WHERE id = :id"), {"id": run_id}
        )
