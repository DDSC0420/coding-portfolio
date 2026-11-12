"""Fenced step completion and failure (transactions T4 and T5, docs/architecture.md §3).

Every write here passes the lease fence (relay/engine/leases.py). If the fence
matches no row, this worker has lost ownership; nothing is written except an
audit event, and LeaseLost is raised.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from relay.db.session import to_json
from relay.engine.claims import ClaimedStep
from relay.engine.errors import InvariantViolation
from relay.engine.events import append_event
from relay.engine.leases import FENCE, fence_params, reject_stale_write
from relay.engine.retries import RetryPolicy, is_retryable
from relay.engine.states import ErrorClass, StepStatus, WorkflowStatus

DEFAULT_POLICY = RetryPolicy()


def close_attempt(
    conn: Connection,
    step_id: UUID,
    generation: int,
    outcome: str,
    error_class: str | None = None,
    error_summary: str | None = None,
) -> None:
    closed = conn.execute(
        text(
            """
            UPDATE step_attempts
            SET outcome = :outcome, finished_at = now(),
                error_class = :error_class, error_summary = :error_summary
            WHERE step_run_id = :step_id AND lease_generation = :generation
              AND outcome = 'in_progress'
            """
        ),
        {
            "step_id": step_id,
            "generation": generation,
            "outcome": outcome,
            "error_class": error_class,
            "error_summary": error_summary,
        },
    ).rowcount
    if closed != 1:
        raise InvariantViolation(f"attempt for step {step_id} gen {generation} not open")


def schedule_successor(
    conn: Connection, workflow_run_id: UUID, step_id: UUID, position: int, output: dict[str, Any]
) -> None:
    """Make the next step eligible, or finish the workflow. Same transaction as completion (I6).

    Used by a worker completing a step (T4) and by an approval decision (T7).
    """
    successor = conn.execute(
        text(
            """
            UPDATE step_runs
            SET status = 'ready', next_eligible_at = now(), input = CAST(:input AS jsonb)
            WHERE workflow_run_id = :wf AND position = :position AND status = 'pending'
            RETURNING id, name
            """
        ),
        {"wf": workflow_run_id, "position": position + 1, "input": to_json(output)},
    ).first()
    if successor is not None:
        append_event(
            conn, workflow_run_id, "step.ready", step_run_id=successor.id, step=successor.name
        )
        return

    later = conn.execute(
        text("SELECT count(*) FROM step_runs WHERE workflow_run_id = :wf AND position > :p"),
        {"wf": workflow_run_id, "p": position},
    ).scalar_one()
    if later:
        raise InvariantViolation(f"successor of step {step_id} exists but is not pending")

    finished = conn.execute(
        text(
            """
            UPDATE workflow_runs
            SET status = 'succeeded', completed_at = now(), result = CAST(:result AS jsonb)
            WHERE id = :wf AND status = 'running'
            """
        ),
        {"wf": workflow_run_id, "result": to_json(output)},
    ).rowcount
    if finished != 1:
        raise InvariantViolation(f"workflow {workflow_run_id} was not running at completion")
    append_event(conn, workflow_run_id, "workflow.succeeded")


def complete_step(engine: Engine, step: ClaimedStep, output: dict[str, Any]) -> None:
    """T4: record success, close the attempt, and schedule the successor — atomically."""
    with engine.begin() as conn:
        updated = conn.execute(
            text(
                f"""
                UPDATE step_runs
                SET status = 'succeeded', output = CAST(:output AS jsonb),
                    lease_owner = NULL, lease_expires_at = NULL, finished_at = now(),
                    error_class = NULL, error_summary = NULL
                WHERE {FENCE}
                """
            ),
            {**fence_params(step), "output": to_json(output)},
        ).rowcount
        lost = updated == 0  # matched nothing, so nothing was written; commit is a no-op
        if not lost:
            close_attempt(conn, step.step_id, step.lease_generation, "succeeded")
            append_event(
                conn,
                step.workflow_run_id,
                "step.succeeded",
                step_run_id=step.step_id,
                step=step.name,
                attempt=step.attempt,
                worker_id=step.worker_id,
                lease_generation=step.lease_generation,
            )
            schedule_successor(conn, step.workflow_run_id, step.step_id, step.position, output)
    if lost:
        raise reject_stale_write(engine, step, "complete")


def had_ambiguous_attempt(conn: Connection, step_id: UUID) -> bool:
    return conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM step_attempts WHERE step_run_id = :id "
            "AND error_class IN ('ambiguous', 'lease_expired'))"
        ),
        {"id": step_id},
    ).scalar_one()


def terminal_statuses(
    *, external_effect: bool, error_class: ErrorClass, ambiguous_before: bool
) -> tuple[StepStatus, WorkflowStatus]:
    """failed, or outcome_unknown if an external effect may have happened.

    "We gave up" is not "it didn't happen": if any attempt of an external step
    ended ambiguously, the receiver may have applied the change.
    """
    ambiguous_now = error_class in (ErrorClass.AMBIGUOUS, ErrorClass.LEASE_EXPIRED)
    if external_effect and (ambiguous_now or ambiguous_before):
        return StepStatus.OUTCOME_UNKNOWN, WorkflowStatus.OUTCOME_UNKNOWN
    return StepStatus.FAILED, WorkflowStatus.FAILED


def fail_step(
    engine: Engine,
    step: ClaimedStep,
    *,
    error_class: ErrorClass,
    summary: str,
    details: dict[str, Any] | None = None,
    external_effect: bool = False,
    retry_after_seconds: float | None = None,
    policy: RetryPolicy = DEFAULT_POLICY,
) -> StepStatus:
    """T5: record a failed attempt, then either schedule a retry or end the workflow.

    Returns the step's new status: `ready` (retry scheduled), `failed` or `outcome_unknown`.
    """
    details = details or {}
    safe_to_repeat = not external_effect or step.idempotency_key is not None
    retry = (
        is_retryable(error_class, safe_to_repeat=safe_to_repeat)
        and step.attempt < step.max_attempts
    )
    with engine.begin() as conn:
        if retry:
            delay = policy.delay(step.attempt, retry_after_seconds)
            row = conn.execute(
                text(
                    f"""
                    UPDATE step_runs
                    SET status = 'ready',
                        next_eligible_at = now() + make_interval(secs => :delay),
                        error_class = :error_class, error_summary = :summary,
                        lease_owner = NULL, lease_expires_at = NULL
                    WHERE {FENCE}
                    RETURNING next_eligible_at
                    """
                ),
                {
                    **fence_params(step),
                    "delay": delay,
                    "error_class": error_class,
                    "summary": summary,
                },
            ).first()
            lost = row is None
            if not lost:
                close_attempt(
                    conn, step.step_id, step.lease_generation, "failed", error_class, summary
                )
                append_event(
                    conn,
                    step.workflow_run_id,
                    "step.retry_scheduled",
                    step_run_id=step.step_id,
                    step=step.name,
                    attempt=step.attempt,
                    max_attempts=step.max_attempts,
                    worker_id=step.worker_id,
                    lease_generation=step.lease_generation,
                    error_class=error_class,
                    summary=summary,
                    details=details,
                    delay_seconds=round(delay, 3),
                    retry_after_seconds=retry_after_seconds,
                    next_eligible_at=row.next_eligible_at.isoformat(),
                )
            new_status = StepStatus.READY
        else:
            step_status, workflow_status = terminal_statuses(
                external_effect=external_effect,
                error_class=error_class,
                ambiguous_before=external_effect and had_ambiguous_attempt(conn, step.step_id),
            )
            updated = conn.execute(
                text(
                    f"""
                    UPDATE step_runs
                    SET status = :status, error_class = :error_class, error_summary = :summary,
                        lease_owner = NULL, lease_expires_at = NULL, finished_at = now()
                    WHERE {FENCE}
                    """
                ),
                {
                    **fence_params(step),
                    "status": step_status,
                    "error_class": error_class,
                    "summary": summary,
                },
            ).rowcount
            lost = updated == 0
            if not lost:
                close_attempt(
                    conn, step.step_id, step.lease_generation, "failed", error_class, summary
                )
                append_event(
                    conn,
                    step.workflow_run_id,
                    "step.failed",
                    step_run_id=step.step_id,
                    step=step.name,
                    attempt=step.attempt,
                    max_attempts=step.max_attempts,
                    worker_id=step.worker_id,
                    lease_generation=step.lease_generation,
                    error_class=error_class,
                    summary=summary,
                    details=details,
                    terminal_status=step_status,
                )
                finish_failed_workflow(
                    conn,
                    step.workflow_run_id,
                    workflow_status,
                    {
                        "step": step.name,
                        "error_class": error_class,
                        "summary": summary,
                        "details": details,
                        "attempts": step.attempt,
                    },
                )
            new_status = step_status
    if lost:
        raise reject_stale_write(engine, step, "fail")
    return new_status


def finish_failed_workflow(
    conn: Connection, workflow_run_id: UUID, status: WorkflowStatus, failure: dict[str, Any]
) -> None:
    """End a workflow after a terminal step failure; cancel the steps that will never run (I11)."""
    cancelled = conn.execute(
        text(
            "UPDATE step_runs SET status = 'cancelled' "
            "WHERE workflow_run_id = :wf AND status = 'pending'"
        ),
        {"wf": workflow_run_id},
    ).rowcount
    updated = conn.execute(
        text(
            """
            UPDATE workflow_runs
            SET status = :status, completed_at = now(), failure = CAST(:failure AS jsonb)
            WHERE id = :wf AND status = 'running'
            """
        ),
        {"wf": workflow_run_id, "status": status, "failure": to_json(failure)},
    ).rowcount
    if updated != 1:
        raise InvariantViolation(f"workflow {workflow_run_id} was not running at failure")
    append_event(
        conn,
        workflow_run_id,
        f"workflow.{status}",
        failed_step=failure.get("step"),
        cancelled_steps=cancelled,
    )
