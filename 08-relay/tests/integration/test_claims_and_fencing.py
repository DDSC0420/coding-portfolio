"""Claims (T2) and fenced writes (T4/T5) at the engine level, on real PostgreSQL.

Process-level crash tests (real worker death, lease renewal, the recovery
sweep) arrive in Stage 2. Here the stale-worker case is constructed directly
in the database to prove the fence itself.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Engine, text

from relay.engine import transitions
from relay.engine.claims import claim_next_step
from relay.engine.errors import LeaseLost
from relay.engine.recovery import recover_expired_leases
from relay.engine.states import ErrorClass
from relay.engine.submission import submit_workflow
from relay.engine.transitions import complete_step, fail_step
from relay.workflows.registry import Registry
from tests.conftest import VALID_ENQUIRY
from tests.helpers import event_types, expire_lease, fetch_all, fetch_one, steps_of

SUPPORTED = ["enquiry:1"]


def _claim(engine: Engine, worker: str = "worker-a/0001"):
    return claim_next_step(engine, worker_id=worker, lease_seconds=30, supported=SUPPORTED)


def _submit(engine: Engine, registry: Registry, **overrides):
    return submit_workflow(
        engine,
        registry,
        workflow_type="enquiry",
        workflow_version=1,
        input={**VALID_ENQUIRY, **overrides},
    ).workflow_run_id


def test_claim_records_owner_generation_lease_attempt_and_event(engine, registry) -> None:
    run_id = _submit(engine, registry)
    claimed = _claim(engine)
    assert claimed is not None
    assert (claimed.workflow_run_id, claimed.name, claimed.attempt, claimed.lease_generation) == (
        run_id,
        "extract",
        1,
        1,
    )

    step = fetch_one(
        engine,
        "SELECT *, lease_expires_at > now() AS live FROM step_runs WHERE id = :id",
        id=claimed.step_id,
    )
    assert (step["status"], step["lease_owner"], step["live"]) == ("running", "worker-a/0001", True)
    attempt = fetch_one(
        engine, "SELECT * FROM step_attempts WHERE step_run_id = :id", id=claimed.step_id
    )
    assert (attempt["attempt_number"], attempt["lease_generation"], attempt["outcome"]) == (
        1,
        1,
        "in_progress",
    )
    assert event_types(engine, run_id) == ["workflow.created", "step.claimed"]
    assert _claim(engine) is None  # nothing else is eligible


def test_steps_not_yet_eligible_or_of_unsupported_versions_are_not_claimed(
    engine, registry
) -> None:
    run_id = _submit(engine, registry)
    assert (
        claim_next_step(engine, worker_id="w/1", lease_seconds=30, supported=["enquiry:99"]) is None
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE step_runs SET next_eligible_at = now() + interval '1 hour' "
                "WHERE workflow_run_id = :id AND status = 'ready'"
            ),
            {"id": run_id},
        )
    assert _claim(engine) is None


def test_competing_workers_never_claim_the_same_step(engine, registry) -> None:
    """Spec test 2: 12 claimers race over 40 eligible steps; each step is claimed exactly once."""
    for i in range(40):
        _submit(engine, registry, enquiry_ref=f"RACE-{i}")
    workers = 12
    barrier = Barrier(workers)

    def drain(worker_no: int) -> list:
        barrier.wait()
        claimed = []
        while (c := _claim(engine, f"worker-{worker_no}/x")) is not None:
            claimed.append(c.step_id)
        return claimed

    with ThreadPoolExecutor(workers) as pool:
        per_worker = list(pool.map(drain, range(workers)))
    all_claims = [step for claims in per_worker for step in claims]
    assert len(all_claims) == 40 and len(set(all_claims)) == 40
    assert fetch_one(engine, "SELECT count(*) AS n FROM step_attempts")["n"] == 40
    assert sum(1 for claims in per_worker if claims) > 1  # the work really was shared


def test_completion_saves_output_and_makes_successor_ready_with_that_input(
    engine, registry
) -> None:
    run_id = _submit(engine, registry)
    claimed = _claim(engine)
    complete_step(engine, claimed, {"enquiry": {"x": 1}})

    extract, validate, *_ = steps_of(engine, run_id)
    assert (extract["status"], extract["output"], extract["lease_owner"]) == (
        "succeeded",
        {"enquiry": {"x": 1}},
        None,
    )
    assert (validate["status"], validate["input"]) == ("ready", {"enquiry": {"x": 1}})
    attempt = fetch_one(
        engine, "SELECT outcome FROM step_attempts WHERE step_run_id = :id", id=claimed.step_id
    )
    assert attempt["outcome"] == "succeeded"


def test_completion_and_successor_scheduling_are_atomic(engine, registry, monkeypatch) -> None:
    """Spec test 18: if anything fails after the step update, nothing is committed."""
    run_id = _submit(engine, registry)
    claimed = _claim(engine)

    def crash(*_args, **_kwargs):
        raise RuntimeError("simulated crash between 'step done' and 'next step scheduled'")

    monkeypatch.setattr(transitions, "schedule_successor", crash)
    with pytest.raises(RuntimeError):
        complete_step(engine, claimed, {"enquiry": {}})

    extract, validate, *_ = steps_of(engine, run_id)
    assert (extract["status"], extract["lease_owner"], extract["output"]) == (
        "running",
        claimed.worker_id,
        None,
    )  # completion rolled back with it
    assert validate["status"] == "pending"
    assert "step.succeeded" not in event_types(engine, run_id)

    monkeypatch.undo()
    complete_step(engine, claimed, {"enquiry": {}})  # the same lease can still finish the job
    assert [s["status"] for s in steps_of(engine, run_id)][:2] == ["succeeded", "ready"]


def test_final_step_completion_finishes_the_workflow(engine, registry) -> None:
    run_id = _submit(engine, registry)
    for _ in range(4):
        complete_step(engine, _claim(engine), {"step": "done"})
    run = fetch_one(
        engine, "SELECT status, result, completed_at FROM workflow_runs WHERE id = :id", id=run_id
    )
    assert run["status"] == "succeeded" and run["result"] == {"step": "done"}
    assert run["completed_at"] is not None
    assert event_types(engine, run_id)[-1] == "workflow.succeeded"


def test_stale_worker_cannot_complete_after_lease_was_replaced(engine, registry) -> None:
    """Spec test 6: worker A's lease expires, worker B re-claims; A's late completion is refused."""
    run_id = _submit(engine, registry)
    stale = _claim(engine, "worker-a/aaaa")
    expire_lease(engine, stale.step_id)
    recover_expired_leases(engine, swept_by="test-sweep")
    fresh = _claim(engine, "worker-b/bbbb")
    assert fresh.step_id == stale.step_id
    assert (fresh.lease_generation, fresh.attempt) == (2, 2)

    with pytest.raises(LeaseLost):
        complete_step(engine, stale, {"from": "stale worker"})

    step = fetch_one(engine, "SELECT * FROM step_runs WHERE id = :id", id=stale.step_id)
    assert (step["status"], step["lease_owner"], step["lease_generation"], step["output"]) == (
        "running",
        "worker-b/bbbb",
        2,
        None,
    )
    rejected = fetch_all(
        engine, "SELECT details FROM workflow_events WHERE type = 'step.stale_write_rejected'"
    )
    assert rejected[0]["details"]["current_generation"] == 2

    complete_step(engine, fresh, {"from": "fresh worker"})
    assert steps_of(engine, run_id)[0]["output"] == {"from": "fresh worker"}


def test_generation_fences_the_same_worker_instance_against_itself(engine, registry) -> None:
    """Why the owner check alone is not enough.

    Slot 1 of worker-a hangs; its lease expires and slot 2 of the *same*
    process re-claims the step. The owner string is identical, so only the
    generation tells the two attempts apart.
    """
    _submit(engine, registry)
    hung = _claim(engine, "worker-a/same")
    expire_lease(engine, hung.step_id)
    recover_expired_leases(engine, swept_by="test-sweep")
    retry = _claim(engine, "worker-a/same")
    assert retry.worker_id == hung.worker_id
    assert retry.lease_generation == hung.lease_generation + 1

    with pytest.raises(LeaseLost):
        complete_step(engine, hung, {"from": "hung slot"})
    complete_step(engine, retry, {"from": "retry slot"})
    step = fetch_one(engine, "SELECT output FROM step_runs WHERE id = :id", id=hung.step_id)
    assert step["output"] == {"from": "retry slot"}


def test_expired_lease_rejects_completion_even_before_anyone_reclaims(engine, registry) -> None:
    _submit(engine, registry)
    claimed = _claim(engine)
    expire_lease(engine, claimed.step_id)
    with pytest.raises(LeaseLost):
        complete_step(engine, claimed, {})
    with pytest.raises(LeaseLost):
        fail_step(engine, claimed, error_class=ErrorClass.PERMANENT, summary="late")
    step = fetch_one(
        engine, "SELECT status, output FROM step_runs WHERE id = :id", id=claimed.step_id
    )
    assert (step["status"], step["output"]) == ("running", None)


def test_permanent_failure_fails_workflow_and_cancels_remaining_steps(engine, registry) -> None:
    run_id = _submit(engine, registry)
    claimed = _claim(engine)
    status = fail_step(
        engine,
        claimed,
        error_class=ErrorClass.PERMANENT,
        summary="bad input",
        details={"errors": [{"field": "email"}]},
    )
    assert status == "failed"
    assert [s["status"] for s in steps_of(engine, run_id)] == [
        "failed",
        "cancelled",
        "cancelled",
        "cancelled",
    ]
    run = fetch_one(engine, "SELECT status, failure FROM workflow_runs WHERE id = :id", id=run_id)
    assert run["status"] == "failed"
    assert run["failure"]["step"] == "extract" and run["failure"]["error_class"] == "permanent"
