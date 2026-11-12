"""Recovering steps whose worker vanished (transaction T6, docs/architecture.md §3).

A worker that is killed, frozen or partitioned stops renewing its leases. Once
a lease's expiry passes (database time), any worker's recovery sweep may take
the step back:

- the open attempt is closed as `lease_expired`;
- with attempts left, the step becomes `ready` again immediately;
- otherwise it ends as `failed`, or `outcome_unknown` for a step with an
  external effect (the vanished attempt may have applied it).

Every worker runs the sweep. They cannot process the same step twice: the
sweep locks expired rows with FOR UPDATE SKIP LOCKED, so concurrent sweeps
take disjoint sets, and each update re-checks status and generation. A worker
whose fenced write is waiting on that row lock sees the new state once the
sweep commits, and its write matches nothing.

A step is treated as having an external effect exactly when it carries an
idempotency key; both are fixed at submission from the pinned definition.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, text

from relay.engine.errors import InvariantViolation
from relay.engine.events import append_event
from relay.engine.states import ErrorClass, StepStatus
from relay.engine.transitions import (
    close_attempt,
    finish_failed_workflow,
    had_ambiguous_attempt,
    terminal_statuses,
)

_FIND_EXPIRED = text(
    """
    SELECT id, workflow_run_id, name, attempt_count, max_attempts, lease_owner,
           lease_generation, lease_expires_at, idempotency_key
    FROM step_runs
    WHERE status = 'running' AND lease_expires_at < now()
    ORDER BY lease_expires_at
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)


@dataclass(frozen=True)
class RecoveredStep:
    step_id: UUID
    workflow_run_id: UUID
    name: str
    previous_owner: str
    lease_generation: int
    new_status: StepStatus


def recover_expired_leases(
    engine: Engine, *, swept_by: str, limit: int = 50
) -> list[RecoveredStep]:
    """One bounded sweep. Returns what it recovered (empty when nothing had expired)."""
    recovered: list[RecoveredStep] = []
    with engine.begin() as conn:
        for row in conn.execute(_FIND_EXPIRED, {"limit": limit}).all():
            summary = f"lease held by {row.lease_owner} (gen {row.lease_generation}) expired"
            close_attempt(
                conn,
                row.id,
                row.lease_generation,
                "lease_expired",
                ErrorClass.LEASE_EXPIRED,
                summary,
            )
            external_effect = row.idempotency_key is not None
            if row.attempt_count < row.max_attempts:
                new_status = StepStatus.READY
                updated = conn.execute(
                    text(
                        """
                        UPDATE step_runs
                        SET status = 'ready', next_eligible_at = now(),
                            lease_owner = NULL, lease_expires_at = NULL,
                            error_class = 'lease_expired', error_summary = :summary
                        WHERE id = :id AND status = 'running' AND lease_generation = :gen
                        """
                    ),
                    {"id": row.id, "gen": row.lease_generation, "summary": summary},
                ).rowcount
            else:
                new_status, workflow_status = terminal_statuses(
                    external_effect=external_effect,
                    error_class=ErrorClass.LEASE_EXPIRED,
                    ambiguous_before=had_ambiguous_attempt(conn, row.id),
                )
                updated = conn.execute(
                    text(
                        """
                        UPDATE step_runs
                        SET status = :status, finished_at = now(),
                            lease_owner = NULL, lease_expires_at = NULL,
                            error_class = 'lease_expired', error_summary = :summary
                        WHERE id = :id AND status = 'running' AND lease_generation = :gen
                        """
                    ),
                    {
                        "id": row.id,
                        "gen": row.lease_generation,
                        "summary": summary,
                        "status": new_status,
                    },
                ).rowcount
            if updated != 1:
                raise InvariantViolation(f"locked expired step {row.id} changed under the sweep")

            append_event(
                conn,
                row.workflow_run_id,
                "step.lease_expired",
                step_run_id=row.id,
                step=row.name,
                previous_owner=row.lease_owner,
                lease_generation=row.lease_generation,
                attempt=row.attempt_count,
                max_attempts=row.max_attempts,
                expired_at=row.lease_expires_at.isoformat(),
                recovered_by=swept_by,
                new_status=new_status,
            )
            if new_status != StepStatus.READY:
                finish_failed_workflow(
                    conn,
                    row.workflow_run_id,
                    workflow_status,
                    {
                        "step": row.name,
                        "error_class": ErrorClass.LEASE_EXPIRED,
                        "summary": summary,
                        "details": {},
                        "attempts": row.attempt_count,
                    },
                )
            recovered.append(
                RecoveredStep(
                    row.id,
                    row.workflow_run_id,
                    row.name,
                    row.lease_owner,
                    row.lease_generation,
                    new_status,
                )
            )
    return recovered
