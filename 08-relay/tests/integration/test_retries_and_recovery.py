"""Stage 2 engine behaviour on real PostgreSQL: retries, lease renewal, recovery.

Process-level crash tests (real worker death) are in tests/fault_injection/.
"""

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from relay.engine.claims import ClaimedStep, claim_next_step
from relay.engine.errors import LeaseLost
from relay.engine.leases import renew_lease
from relay.engine.recovery import recover_expired_leases
from relay.engine.retries import RetryPolicy
from relay.engine.states import ErrorClass
from relay.engine.submission import submit_workflow
from relay.engine.transitions import complete_step, fail_step
from tests.conftest import VALID_ENQUIRY
from tests.helpers import event_types, expire_lease, fetch_all, fetch_one, steps_of

FAST = RetryPolicy(base_seconds=0.001, max_delay_seconds=0.001)


def _submit(engine, registry, **overrides):
    return submit_workflow(
        engine,
        registry,
        workflow_type="enquiry",
        workflow_version=1,
        input={**VALID_ENQUIRY, **overrides},
    ).workflow_run_id


def _claim(engine, worker: str = "worker-a/0001", lease: float = 30) -> ClaimedStep:
    claimed = claim_next_step(
        engine, worker_id=worker, lease_seconds=lease, supported=["enquiry:1"]
    )
    assert claimed is not None
    return claimed


def _make_eligible_now(engine, step_id) -> None:
    """Skip a scheduled backoff deterministically instead of sleeping through it."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE step_runs SET next_eligible_at = now() WHERE id = :id"), {"id": step_id}
        )


def _advance_to_crm_step(engine, registry) -> ClaimedStep:
    run_id = _submit(engine, registry)
    for _ in range(3):
        complete_step(engine, _claim(engine), {"enquiry": {"email": "a@b.example"}})
    crm = _claim(engine)
    assert crm.name == "crm_upsert" and crm.workflow_run_id == run_id
    return crm


# --- retries ------------------------------------------------------------------


def test_transient_failure_schedules_a_backoff_retry_from_the_same_input(engine, registry) -> None:
    run_id = _submit(engine, registry)
    first = _claim(engine)
    status = fail_step(
        engine,
        first,
        error_class=ErrorClass.TRANSIENT,
        summary="blip",
        policy=RetryPolicy(base_seconds=10, max_delay_seconds=60),
    )
    assert status == "ready"

    step = fetch_one(
        engine,
        "SELECT status, lease_owner, error_class, "
        "EXTRACT(EPOCH FROM next_eligible_at - now()) AS wait "
        "FROM step_runs WHERE id = :id",
        id=first.step_id,
    )
    assert (step["status"], step["lease_owner"], step["error_class"]) == (
        "ready",
        None,
        "transient",
    )
    assert 4.5 <= step["wait"] <= 10.5  # equal jitter: between half and all of 10s
    assert (
        claim_next_step(engine, worker_id="w/2", lease_seconds=30, supported=["enquiry:1"]) is None
    )  # still backing off

    _make_eligible_now(engine, first.step_id)
    second = _claim(engine, "worker-b/0002")
    assert (second.step_id, second.attempt, second.lease_generation) == (first.step_id, 2, 2)
    assert second.input == first.input
    attempts = fetch_all(
        engine,
        "SELECT attempt_number, outcome, error_class FROM step_attempts "
        "WHERE step_run_id = :id ORDER BY attempt_number",
        id=first.step_id,
    )
    assert attempts == [
        {"attempt_number": 1, "outcome": "failed", "error_class": "transient"},
        {"attempt_number": 2, "outcome": "in_progress", "error_class": None},
    ]
    assert "step.retry_scheduled" in event_types(engine, run_id)


def test_retry_after_raises_the_delay(engine, registry) -> None:
    _submit(engine, registry)
    claimed = _claim(engine)
    fail_step(
        engine,
        claimed,
        error_class=ErrorClass.TRANSIENT,
        summary="429",
        retry_after_seconds=30,
        policy=FAST,
    )
    wait = fetch_one(
        engine,
        "SELECT EXTRACT(EPOCH FROM next_eligible_at - now()) AS w FROM step_runs WHERE id = :id",
        id=claimed.step_id,
    )["w"]
    assert 29 <= wait <= 30.5


def test_permanent_failure_is_not_retried_even_with_attempts_left(engine, registry) -> None:
    crm = _advance_to_crm_step(engine, registry)
    assert crm.max_attempts > 1
    status = fail_step(
        engine,
        crm,
        error_class=ErrorClass.PERMANENT,
        summary="422",
        external_effect=True,
        policy=FAST,
    )
    assert status == "failed"


def _exhaust(engine, first: ClaimedStep, first_class: ErrorClass) -> str:
    """Fail attempt 1 with first_class, then every later attempt as transient."""
    step, error_class = first, first_class
    while True:
        status = fail_step(
            engine,
            step,
            error_class=error_class,
            summary=f"attempt {step.attempt}",
            external_effect=True,
            policy=FAST,
        )
        if status != "ready":
            return status
        _make_eligible_now(engine, step.step_id)
        step, error_class = _claim(engine), ErrorClass.TRANSIENT


def test_exhausted_retries_of_definite_failures_end_as_failed(engine, registry) -> None:
    """Spec test 10: every attempt was refused before commit, so nothing was applied."""
    crm = _advance_to_crm_step(engine, registry)
    assert _exhaust(engine, crm, ErrorClass.TRANSIENT) == "failed"
    run = fetch_one(
        engine, "SELECT status, failure FROM workflow_runs WHERE id = :id", id=crm.workflow_run_id
    )
    assert run["status"] == "failed" and run["failure"]["attempts"] == crm.max_attempts
    n = fetch_one(
        engine, "SELECT count(*) AS n FROM step_attempts WHERE step_run_id = :id", id=crm.step_id
    )["n"]
    assert n == crm.max_attempts == 6


def test_exhausted_retries_after_an_ambiguous_attempt_end_as_outcome_unknown(
    engine, registry
) -> None:
    """The first attempt may have reached the CRM; giving up does not prove it didn't."""
    crm = _advance_to_crm_step(engine, registry)
    assert _exhaust(engine, crm, ErrorClass.AMBIGUOUS) == "outcome_unknown"
    run = fetch_one(
        engine, "SELECT status FROM workflow_runs WHERE id = :id", id=crm.workflow_run_id
    )
    assert run["status"] == "outcome_unknown"


def test_ambiguous_failure_is_not_retried_when_the_receiver_cannot_deduplicate(
    engine, registry
) -> None:
    crm = _advance_to_crm_step(engine, registry)
    no_key = dataclasses.replace(crm, idempotency_key=None)
    status = fail_step(
        engine,
        no_key,
        error_class=ErrorClass.AMBIGUOUS,
        summary="timeout",
        external_effect=True,
        policy=FAST,
    )
    assert status == "outcome_unknown"


# --- lease renewal ------------------------------------------------------------


def test_renewal_extends_a_live_lease_held_by_its_owner(engine, registry) -> None:
    _submit(engine, registry)
    claimed = _claim(engine, lease=5)
    new_expiry = renew_lease(engine, claimed, lease_seconds=120)
    remaining = fetch_one(
        engine,
        "SELECT EXTRACT(EPOCH FROM lease_expires_at - now()) AS r FROM step_runs WHERE id = :id",
        id=claimed.step_id,
    )["r"]
    assert remaining > 100 and new_expiry is not None


def test_stale_worker_cannot_renew_an_expired_lease(engine, registry) -> None:
    """Spec test 7: renewal must not revive a lease that has already expired."""
    _submit(engine, registry)
    claimed = _claim(engine)
    expire_lease(engine, claimed.step_id)
    with pytest.raises(LeaseLost):
        renew_lease(engine, claimed, lease_seconds=120)
    step = fetch_one(
        engine,
        "SELECT status, lease_expires_at < now() AS expired FROM step_runs WHERE id = :id",
        id=claimed.step_id,
    )
    assert step == {"status": "running", "expired": True}  # not revived
    rejected = fetch_all(
        engine, "SELECT details FROM workflow_events WHERE type = 'step.stale_write_rejected'"
    )
    assert rejected[0]["details"]["action"] == "renew"


def test_another_worker_cannot_renew_someone_elses_lease(engine, registry) -> None:
    _submit(engine, registry)
    claimed = _claim(engine, "worker-a/aaaa")
    impostor = dataclasses.replace(claimed, worker_id="worker-b/bbbb")
    with pytest.raises(LeaseLost):
        renew_lease(engine, impostor, lease_seconds=120)


# --- recovery -----------------------------------------------------------------


def test_recovery_returns_an_expired_step_to_ready_and_records_why(engine, registry) -> None:
    run_id = _submit(engine, registry)
    dead = _claim(engine, "worker-a/dead")
    assert recover_expired_leases(engine, swept_by="worker-b/live") == []  # lease still live

    expire_lease(engine, dead.step_id)
    recovered = recover_expired_leases(engine, swept_by="worker-b/live")
    assert [(r.step_id, r.previous_owner, r.new_status) for r in recovered] == [
        (dead.step_id, "worker-a/dead", "ready")
    ]

    step = steps_of(engine, run_id)[0]
    assert (step["status"], step["lease_owner"], step["error_class"]) == (
        "ready",
        None,
        "lease_expired",
    )
    attempt = fetch_one(
        engine,
        "SELECT outcome, error_class FROM step_attempts WHERE step_run_id = :id",
        id=dead.step_id,
    )
    assert attempt == {"outcome": "lease_expired", "error_class": "lease_expired"}
    event = fetch_one(
        engine, "SELECT details FROM workflow_events WHERE type = 'step.lease_expired'"
    )["details"]
    assert (event["previous_owner"], event["recovered_by"]) == ("worker-a/dead", "worker-b/live")

    again = _claim(engine, "worker-b/live")
    assert (again.step_id, again.attempt, again.lease_generation) == (dead.step_id, 2, 2)


def test_recovery_of_an_exhausted_local_step_fails_the_workflow(engine, registry) -> None:
    run_id = _submit(engine, registry)
    complete_step(engine, _claim(engine), {"enquiry": {}})
    validate = _claim(engine)
    assert (validate.name, validate.max_attempts) == ("validate", 1)
    expire_lease(engine, validate.step_id)
    [r] = recover_expired_leases(engine, swept_by="sweeper")
    assert r.new_status == "failed"
    assert [s["status"] for s in steps_of(engine, run_id)] == [
        "succeeded",
        "failed",
        "cancelled",
        "cancelled",
    ]


def test_recovery_of_an_exhausted_external_step_reports_outcome_unknown(engine, registry) -> None:
    crm = _advance_to_crm_step(engine, registry)
    with engine.begin() as conn:  # make this the last allowed attempt
        conn.execute(
            text("UPDATE step_runs SET max_attempts = attempt_count WHERE id = :id"),
            {"id": crm.step_id},
        )
    expire_lease(engine, crm.step_id)
    [r] = recover_expired_leases(engine, swept_by="sweeper")
    assert r.new_status == "outcome_unknown"
    run = fetch_one(
        engine, "SELECT status FROM workflow_runs WHERE id = :id", id=crm.workflow_run_id
    )
    assert run["status"] == "outcome_unknown"


def test_concurrent_recovery_sweeps_recover_each_step_exactly_once(engine, registry) -> None:
    """Many workers sweep at the same moment; no step is recovered twice."""
    for i in range(30):
        _submit(engine, registry, enquiry_ref=f"SWEEP-{i}")
    claimed = [_claim(engine, "worker-a/dead") for _ in range(30)]
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE step_runs SET lease_expires_at = now() - interval '1 second' "
                "WHERE status = 'running'"
            )
        )
    sweepers = 8
    barrier = Barrier(sweepers)

    def sweep(n: int) -> list:
        barrier.wait()
        found = []
        while batch := recover_expired_leases(engine, swept_by=f"sweeper-{n}", limit=3):
            found += [r.step_id for r in batch]
        return found

    with ThreadPoolExecutor(sweepers) as pool:
        results = list(pool.map(sweep, range(sweepers)))
    recovered = [step_id for r in results for step_id in r]
    assert sorted(map(str, recovered)) == sorted(str(c.step_id) for c in claimed)
    assert (
        fetch_one(
            engine, "SELECT count(*) AS n FROM workflow_events WHERE type = 'step.lease_expired'"
        )["n"]
        == 30
    )
    assert (
        fetch_one(engine, "SELECT count(*) AS n FROM step_runs WHERE status = 'ready'")["n"] == 30
    )


def test_renewing_a_step_this_worker_just_finished_is_not_a_stale_write(engine, registry) -> None:
    """The renewal thread can lose a race with its own worker's completion. Nothing is wrong,
    so nothing is logged as refused (the dashboard counts those events)."""
    run_id = _submit(engine, registry)
    claimed = _claim(engine)
    complete_step(engine, claimed, {"enquiry": {}})
    with pytest.raises(LeaseLost, match="already finished"):
        renew_lease(engine, claimed, lease_seconds=120)
    assert "step.stale_write_rejected" not in event_types(engine, run_id)
