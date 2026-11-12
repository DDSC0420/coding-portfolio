"""The database triggers enforce exactly the transition tables in relay/engine/states.py.

Every (from, to) status pair is attempted directly in SQL. Allowed pairs must
succeed; every other pair must be rejected by PostgreSQL. This keeps the
Python table, the docs and the database from drifting apart.
"""

from itertools import product

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from relay.engine.states import (
    STEP_TRANSITIONS,
    TERMINAL_WORKFLOW,
    WORKFLOW_TRANSITIONS,
    StepStatus,
    WorkflowStatus,
)


def _new_run(conn, status: str = "running") -> str:
    completed = "now()" if status in TERMINAL_WORKFLOW else "NULL"
    return conn.execute(
        text(
            "INSERT INTO workflow_runs "
            "(workflow_type, workflow_version, status, input, completed_at) "
            f"VALUES ('enquiry', 1, :s, '{{}}'::jsonb, {completed}) RETURNING id"
        ),
        {"s": status},
    ).scalar_one()


def _step_columns(status: str) -> str:
    """Column values that satisfy the CHECK constraints for a given status."""
    leased = status == "running"
    owner = "'w/1'" if leased else "NULL"
    expiry = "now() + interval '1 minute'" if leased else "NULL"
    eligible = "now()" if status == "ready" else "NULL"
    return (
        f"status = '{status}', lease_owner = {owner}, lease_expires_at = {expiry}, "
        f"next_eligible_at = {eligible}"
    )


@pytest.mark.parametrize(
    ("old", "new"), [(a, b) for a, b in product(StepStatus, StepStatus) if a != b]
)
def test_step_trigger_matches_transition_table(
    engine: Engine, old: StepStatus, new: StepStatus
) -> None:
    allowed = new in STEP_TRANSITIONS[old]
    with engine.connect() as conn:
        run_id = _new_run(conn)
        step_id = conn.execute(
            text(
                "INSERT INTO step_runs (workflow_run_id, name, position, status, max_attempts) "
                "VALUES (:wf, 's', 0, 'pending', 1) RETURNING id"
            ),
            {"wf": run_id},
        ).scalar_one()
        # Put the row into `old` with ordinary triggers skipped
        # (session_replication_role = replica). Test setup only.
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            text(f"UPDATE step_runs SET {_step_columns(old)} WHERE id = :id"), {"id": step_id}
        )
        conn.execute(text("SET LOCAL session_replication_role = origin"))
        conn.execute(text("SAVEPOINT attempt"))
        try:
            conn.execute(
                text(f"UPDATE step_runs SET {_step_columns(new)} WHERE id = :id"), {"id": step_id}
            )
            accepted = True
        except DBAPIError as exc:
            assert "illegal step transition" in str(exc.orig)
            accepted = False
        conn.rollback()
    assert accepted == allowed


@pytest.mark.parametrize(
    ("old", "new"), [(a, b) for a, b in product(WorkflowStatus, WorkflowStatus) if a != b]
)
def test_workflow_trigger_matches_transition_table(
    engine: Engine, old: WorkflowStatus, new: WorkflowStatus
) -> None:
    allowed = new in WORKFLOW_TRANSITIONS[old]
    completed = "now()" if new in TERMINAL_WORKFLOW else "NULL"
    with engine.connect() as conn:
        run_id = _new_run(conn, old)
        try:
            conn.execute(
                text(
                    f"UPDATE workflow_runs SET status = :s, completed_at = {completed} "
                    "WHERE id = :id"
                ),
                {"s": new, "id": run_id},
            )
            accepted = True
        except DBAPIError as exc:
            assert "illegal workflow transition" in str(exc.orig)
            accepted = False
        conn.rollback()
    assert accepted == allowed


def test_pinned_version_and_input_are_immutable(engine: Engine) -> None:
    with engine.connect() as conn:
        run_id = _new_run(conn)
        with pytest.raises(DBAPIError, match="immutable"):
            conn.execute(
                text("UPDATE workflow_runs SET workflow_version = 2 WHERE id = :id"), {"id": run_id}
            )
        conn.rollback()


def test_lease_generation_cannot_decrease(engine: Engine) -> None:
    with engine.connect() as conn:
        run_id = _new_run(conn)
        step_id = conn.execute(
            text(
                "INSERT INTO step_runs (workflow_run_id, name, position, status, max_attempts, "
                "lease_generation) VALUES (:wf, 's', 0, 'pending', 1, 5) RETURNING id"
            ),
            {"wf": run_id},
        ).scalar_one()
        with pytest.raises(DBAPIError, match="must not decrease"):
            conn.execute(
                text("UPDATE step_runs SET lease_generation = 4 WHERE id = :id"), {"id": step_id}
            )
        conn.rollback()


def test_at_most_one_active_step_per_workflow(engine: Engine) -> None:
    """Invariant I1, enforced by a partial unique index."""
    with engine.connect() as conn:
        run_id = _new_run(conn)
        conn.execute(
            text(
                "INSERT INTO step_runs (workflow_run_id, name, position, status, max_attempts, "
                "next_eligible_at) VALUES (:wf, 'a', 0, 'ready', 1, now())"
            ),
            {"wf": run_id},
        )
        with pytest.raises(DBAPIError, match="uq_step_runs_one_active"):
            conn.execute(
                text(
                    "INSERT INTO step_runs (workflow_run_id, name, position, status, max_attempts, "
                    "next_eligible_at) VALUES (:wf, 'b', 1, 'ready', 1, now())"
                ),
                {"wf": run_id},
            )
        conn.rollback()
