"""The verifier must pass on a healthy database and name the problem on a broken one.

Breaking the data needs the guard triggers switched off for the session, which is the point:
the verifier is the second line of defence if a bug or a manual fix ever gets past them.
"""

from sqlalchemy import text

from relay.config import EngineSettings
from relay.engine.approvals import decide
from relay.engine.claims import claim_next_step
from relay.engine.executor import execute_claimed
from relay.engine.submission import submit_workflow
from relay.verify import render, verify
from tests.conftest import VALID_ENQUIRY

SETTINGS = EngineSettings()


def _run_to_crm(engine, registry, **overrides):
    run_id = submit_workflow(
        engine, registry, workflow_type="enquiry", input={**VALID_ENQUIRY, **overrides}
    ).workflow_run_id
    while (
        step := claim_next_step(
            engine, worker_id="worker-a/1", lease_seconds=30, supported=registry.keys()
        )
    ) is not None:
        if step.name == "crm_upsert":
            return run_id, step
        execute_claimed(engine, registry, SETTINGS, step, http=None)
    return run_id, None


def _failed(report) -> list[str]:
    return [c.name for c in report.checks if not c.ok]


def _tamper(engine, sql: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))  # triggers off
        conn.execute(text(sql))


def test_a_mixed_healthy_state_passes_every_check(engine, crm_engine, registry) -> None:
    _run_to_crm(engine, registry)  # CRM step claimed, in flight
    _run_to_crm(engine, registry, enquiry_ref="B", approval_required=True)  # left paused
    rejected, _ = _run_to_crm(engine, registry, enquiry_ref="C", approval_required=True)
    decide(engine, rejected, approve=False, decided_by="abby")
    submit_workflow(engine, registry, workflow_type="enquiry", input={**VALID_ENQUIRY, "name": ""})
    _run_to_crm(engine, registry, enquiry_ref="E")  # also runs the invalid one to failure
    report = verify(engine, crm_engine)
    assert _failed(report) == [], render(report)
    assert report.facts["workflows"] == {
        "running": 2,  # both stopped with the CRM step claimed
        "awaiting_approval": 1,
        "rejected": 1,
        "failed": 1,
    }
    assert "All invariants hold." in render(report)


def test_a_workflow_marked_done_with_a_step_still_open_is_caught(engine, registry) -> None:
    run_id, _ = _run_to_crm(engine, registry)
    _tamper(
        engine,
        "UPDATE workflow_runs SET status = 'succeeded', completed_at = now() "
        f"WHERE id = '{run_id}'",
    )
    failed = _failed(verify(engine))
    assert "A finished workflow has no unfinished steps (I11)" in failed
    assert "A succeeded workflow has every step succeeded" in failed


def test_a_succeeded_crm_step_with_no_write_in_the_crm_is_caught(
    engine, crm_engine, registry
) -> None:
    _, step = _run_to_crm(engine, registry)
    _tamper(
        engine,
        f"""
        UPDATE step_attempts SET outcome = 'succeeded', finished_at = now()
          WHERE step_run_id = '{step.step_id}';
        UPDATE step_runs SET status = 'succeeded', lease_owner = NULL, lease_expires_at = NULL
          WHERE id = '{step.step_id}';
        UPDATE workflow_runs SET status = 'succeeded', completed_at = now()
          WHERE id = '{step.workflow_run_id}';
        """,
    )
    report = verify(engine, crm_engine)
    assert _failed(report) == ["Every succeeded CRM step has exactly one write in the CRM"]
    assert "FAIL" in render(report) and "INVARIANT VIOLATIONS FOUND." in render(report)


def test_a_reset_lease_generation_is_caught(engine, registry) -> None:
    _, step = _run_to_crm(engine, registry)
    _tamper(engine, f"UPDATE step_runs SET lease_generation = 0 WHERE id = '{step.step_id}'")
    assert _failed(verify(engine)) == [
        "Every claim is one attempt: attempts = attempt count = lease generation (I3)"
    ]


def test_two_active_steps_in_one_workflow_are_caught(engine, registry) -> None:
    run_id, _ = _run_to_crm(engine, registry)
    _tamper(
        engine,
        f"""
        DROP INDEX uq_step_runs_one_active;
        UPDATE step_runs SET status = 'ready', next_eligible_at = now()
          WHERE workflow_run_id = '{run_id}' AND name = 'enrich';
        """,
    )
    try:
        failed = _failed(verify(engine))
    finally:
        _tamper(
            engine,
            f"""
            UPDATE step_runs SET status = 'succeeded', next_eligible_at = NULL
              WHERE workflow_run_id = '{run_id}' AND name = 'enrich';
            CREATE UNIQUE INDEX uq_step_runs_one_active ON step_runs (workflow_run_id)
              WHERE status IN ('ready','running','awaiting_approval');
            """,
        )
    assert "An unfinished workflow has exactly one active step (I1)" in failed
