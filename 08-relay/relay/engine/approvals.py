"""The approval gate (transactions T4a and T7, docs/architecture.md §3).

T4a, by a worker: a step decides a person must sign off. In one fenced
transaction the step goes `running -> awaiting_approval` and gives up its
lease, the attempt closes as `paused_for_approval`, an approval request is
stored with the output the step would hand on, and the workflow goes
`awaiting_approval`. From then on no worker is involved: a paused step holds
no lease, so it cannot expire, and the recovery sweep never touches it. A
workflow can wait for days, across restarts of everything.

T7, by the API: the decision. Approve makes the step `succeeded` with the
stored output and makes the successor eligible, exactly as a worker's
completion would. Reject ends the workflow. Both happen in one transaction
with the decision itself, so there is no state where a decision is recorded
but the workflow has not moved.

A decision is final. Repeating the same decision returns the original (safe
to retry a lost HTTP response); the opposite decision is a conflict.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from relay.db.session import to_json
from relay.engine.claims import ClaimedStep
from relay.engine.errors import InvariantViolation
from relay.engine.events import append_event
from relay.engine.leases import FENCE, fence_params, reject_stale_write
from relay.engine.states import ApprovalStatus
from relay.engine.transitions import close_attempt, schedule_successor


class ApprovalNotFound(LookupError):
    """The workflow has no approval request (it never paused)."""


class ApprovalConflict(Exception):
    """The request was already decided the other way."""

    def __init__(self, existing: "Decision") -> None:
        super().__init__(
            f"approval for run {existing.workflow_run_id} was already {existing.status} "
            f"by {existing.decided_by}"
        )
        self.existing = existing


@dataclass(frozen=True)
class Decision:
    approval_id: int
    workflow_run_id: UUID
    step_run_id: UUID
    status: ApprovalStatus
    decided_by: str
    decided_at: datetime
    comment: str | None
    replayed: bool  # True when this call repeated an earlier, identical decision


def pause_for_approval(
    engine: Engine, step: ClaimedStep, *, reason: str, output: dict[str, Any]
) -> None:
    """T4a: park the step for a human decision and release the lease. Fenced."""
    with engine.begin() as conn:
        updated = conn.execute(
            text(
                f"""
                UPDATE step_runs
                SET status = 'awaiting_approval', lease_owner = NULL, lease_expires_at = NULL,
                    error_class = NULL, error_summary = NULL
                WHERE {FENCE}
                """
            ),
            fence_params(step),
        ).rowcount
        lost = updated == 0
        if not lost:
            close_attempt(conn, step.step_id, step.lease_generation, "paused_for_approval")
            conn.execute(
                text(
                    "INSERT INTO approvals (workflow_run_id, step_run_id, reason, proposed_output) "
                    "VALUES (:wf, :step, :reason, CAST(:output AS jsonb))"
                ),
                {
                    "wf": step.workflow_run_id,
                    "step": step.step_id,
                    "reason": reason,
                    "output": to_json(output),
                },
            )
            moved = conn.execute(
                text(
                    "UPDATE workflow_runs SET status = 'awaiting_approval' "
                    "WHERE id = :wf AND status = 'running'"
                ),
                {"wf": step.workflow_run_id},
            ).rowcount
            if moved != 1:
                raise InvariantViolation(f"workflow {step.workflow_run_id} was not running")
            append_event(
                conn,
                step.workflow_run_id,
                "approval.requested",
                step_run_id=step.step_id,
                step=step.name,
                reason=reason,
                worker_id=step.worker_id,
                attempt=step.attempt,
                lease_generation=step.lease_generation,
            )
    if lost:
        raise reject_stale_write(engine, step, "pause_for_approval")


def decide(
    engine: Engine,
    workflow_run_id: UUID,
    *,
    approve: bool,
    decided_by: str,
    comment: str | None = None,
) -> Decision:
    """T7: record the decision and move the workflow, atomically."""
    wanted = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    with engine.begin() as conn:
        # The row lock serialises concurrent decisions: the second waits here,
        # then sees the first one's result.
        row = conn.execute(
            text(
                """
                SELECT a.id, a.step_run_id, a.status, a.decided_by, a.decided_at, a.comment,
                       a.proposed_output, a.reason, s.name AS step_name, s.position
                FROM approvals a JOIN step_runs s ON s.id = a.step_run_id
                WHERE a.workflow_run_id = :wf
                ORDER BY a.id DESC LIMIT 1
                FOR UPDATE OF a
                """
            ),
            {"wf": workflow_run_id},
        ).first()
        if row is None:
            raise ApprovalNotFound(f"workflow run {workflow_run_id} has no approval request")

        if row.status != ApprovalStatus.PENDING:
            existing = Decision(
                row.id,
                workflow_run_id,
                row.step_run_id,
                ApprovalStatus(row.status),
                row.decided_by,
                row.decided_at,
                row.comment,
                replayed=True,
            )
            if existing.status != wanted:
                raise ApprovalConflict(existing)
            return existing

        decided = conn.execute(
            text(
                """
                UPDATE approvals
                SET status = :status, decided_at = now(), decided_by = :by, comment = :comment
                WHERE id = :id
                RETURNING decided_at
                """
            ),
            {"status": wanted, "by": decided_by, "comment": comment, "id": row.id},
        ).one()

        if approve:
            output = {
                **row.proposed_output,
                "approval": {
                    **row.proposed_output.get("approval", {}),
                    "decision": "approved",
                    "decided_by": decided_by,
                    "decided_at": decided.decided_at.isoformat(),
                },
            }
            stepped = conn.execute(
                text(
                    """
                    UPDATE step_runs
                    SET status = 'succeeded', output = CAST(:output AS jsonb), finished_at = now()
                    WHERE id = :id AND status = 'awaiting_approval'
                    """
                ),
                {"id": row.step_run_id, "output": to_json(output)},
            ).rowcount
            resumed = conn.execute(
                text(
                    "UPDATE workflow_runs SET status = 'running' "
                    "WHERE id = :wf AND status = 'awaiting_approval'"
                ),
                {"wf": workflow_run_id},
            ).rowcount
            if stepped != 1 or resumed != 1:
                raise InvariantViolation(f"run {workflow_run_id} was not awaiting approval")
            append_event(
                conn,
                workflow_run_id,
                "approval.approved",
                step_run_id=row.step_run_id,
                step=row.step_name,
                decided_by=decided_by,
                comment=comment,
            )
            schedule_successor(conn, workflow_run_id, row.step_run_id, row.position, output)
        else:
            stepped = conn.execute(
                text(
                    """
                    UPDATE step_runs
                    SET status = 'rejected', finished_at = now(),
                        error_summary = :summary
                    WHERE id = :id AND status = 'awaiting_approval'
                    """
                ),
                {"id": row.step_run_id, "summary": f"rejected by {decided_by}"[:500]},
            ).rowcount
            cancelled = conn.execute(
                text(
                    "UPDATE step_runs SET status = 'cancelled' "
                    "WHERE workflow_run_id = :wf AND status = 'pending'"
                ),
                {"wf": workflow_run_id},
            ).rowcount
            ended = conn.execute(
                text(
                    """
                    UPDATE workflow_runs
                    SET status = 'rejected', completed_at = now(),
                        failure = CAST(:failure AS jsonb)
                    WHERE id = :wf AND status = 'awaiting_approval'
                    """
                ),
                {
                    "wf": workflow_run_id,
                    "failure": to_json(
                        {
                            "step": row.step_name,
                            "summary": "rejected at the approval gate",
                            "decided_by": decided_by,
                            "comment": comment,
                            "reason": row.reason,
                        }
                    ),
                },
            ).rowcount
            if stepped != 1 or ended != 1:
                raise InvariantViolation(f"run {workflow_run_id} was not awaiting approval")
            append_event(
                conn,
                workflow_run_id,
                "approval.rejected",
                step_run_id=row.step_run_id,
                step=row.step_name,
                decided_by=decided_by,
                comment=comment,
            )
            append_event(conn, workflow_run_id, "workflow.rejected", cancelled_steps=cancelled)

    return Decision(
        row.id,
        workflow_run_id,
        row.step_run_id,
        wanted,
        decided_by,
        decided.decided_at,
        comment,
        replayed=False,
    )
