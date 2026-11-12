"""Lease ownership: the fence every worker write must pass, and lease renewal (T3).

A claim gives a worker a lease on one step: an owner (the worker's per-process
instance id), a generation (incremented on every claim) and an expiry in
database time. A worker's write to that step is accepted only while all of
these still hold:

    status = 'running' AND lease_owner = me AND lease_generation = mine
    AND lease_expires_at > now()

If the row no longer matches, someone else may own the step (or recovery has
reclaimed it), so the worker writes nothing except an audit event.

Renewal uses the same fence, so a worker can extend only its own lease and
only while it is still live. An expired lease is never revived: once it has
passed, recovery owns the decision about what happens next.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Engine, text

from relay.engine.claims import ClaimedStep
from relay.engine.errors import LeaseLost
from relay.engine.events import append_event

FENCE = """
    id = :step_id
    AND status = 'running'
    AND lease_owner = :worker_id
    AND lease_generation = :generation
    AND lease_expires_at > now()
"""


def fence_params(step: ClaimedStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "worker_id": step.worker_id,
        "generation": step.lease_generation,
    }


def reject_stale_write(engine: Engine, step: ClaimedStep, action: str) -> LeaseLost:
    """Record (append-only) that a stale write was refused, and build the exception."""
    with engine.begin() as conn:
        current = conn.execute(
            text(
                "SELECT status, lease_owner, lease_generation, "
                "lease_expires_at <= now() AS expired FROM step_runs WHERE id = :id"
            ),
            {"id": step.step_id},
        ).one()
        append_event(
            conn,
            step.workflow_run_id,
            "step.stale_write_rejected",
            step_run_id=step.step_id,
            step=step.name,
            action=action,
            worker_id=step.worker_id,
            lease_generation=step.lease_generation,
            current_status=current.status,
            current_owner=current.lease_owner,
            current_generation=current.lease_generation,
            lease_expired=bool(current.expired),
        )
    return LeaseLost(
        f"{action} of step {step.step_id} by {step.worker_id} gen {step.lease_generation} "
        f"rejected; now status={current.status} owner={current.lease_owner} "
        f"gen={current.lease_generation}"
    )


def renew_lease(engine: Engine, step: ClaimedStep, lease_seconds: float) -> datetime:
    """T3: extend this worker's live lease.

    Raises LeaseLost if the lease is no longer ours or has already expired.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"""
                UPDATE step_runs
                SET lease_expires_at = now() + make_interval(secs => :lease_seconds)
                WHERE {FENCE}
                RETURNING lease_expires_at
                """
            ),
            {**fence_params(step), "lease_seconds": lease_seconds},
        ).first()
    if row is None:
        raise reject_stale_write(engine, step, "renew")
    return row.lease_expires_at
