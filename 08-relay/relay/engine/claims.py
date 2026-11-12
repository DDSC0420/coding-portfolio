"""Claiming work (transaction T2 in docs/architecture.md §3).

The claim is one short transaction that commits *before* the step executes.
No row lock or transaction is held while the step runs.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from relay.engine.events import append_event

# The candidate subquery locks one eligible row. SKIP LOCKED makes concurrent
# claimers pass over rows another transaction has already locked rather than
# wait for them, so each claimer gets a different step or nothing.
#
# Only steps of workflow versions this worker can execute are eligible, so a
# mixed-version fleet never claims work it cannot run.
#
# now() is the database clock: lease expiry never depends on a worker's clock.
_CLAIM = text(
    """
    WITH candidate AS (
        SELECT s.id
        FROM step_runs s
        JOIN workflow_runs w ON w.id = s.workflow_run_id
        WHERE s.status = 'ready'
          AND s.next_eligible_at <= now()
          AND (w.workflow_type || ':' || w.workflow_version) = ANY(:supported)
        ORDER BY s.next_eligible_at, s.id
        LIMIT 1
        FOR UPDATE OF s SKIP LOCKED
    )
    UPDATE step_runs s
    SET status = 'running',
        lease_owner = :worker_id,
        lease_generation = s.lease_generation + 1,
        lease_expires_at = now() + make_interval(secs => :lease_seconds),
        attempt_count = s.attempt_count + 1,
        started_at = COALESCE(s.started_at, now()),
        next_eligible_at = NULL
    FROM candidate, workflow_runs w
    WHERE s.id = candidate.id AND w.id = s.workflow_run_id
    RETURNING s.id, s.workflow_run_id, s.name, s.position, s.input, s.attempt_count,
              s.max_attempts, s.lease_generation, s.lease_expires_at, s.idempotency_key,
              w.workflow_type, w.workflow_version
    """
)

_RECORD_ATTEMPT = text(
    """
    INSERT INTO step_attempts (step_run_id, attempt_number, worker_id, lease_generation)
    VALUES (:step_id, :attempt, :worker_id, :generation)
    """
)


@dataclass(frozen=True)
class ClaimedStep:
    step_id: UUID
    workflow_run_id: UUID
    workflow_type: str
    workflow_version: int
    name: str
    position: int
    input: dict[str, Any]
    attempt: int
    max_attempts: int
    worker_id: str
    lease_generation: int
    lease_expires_at: datetime
    idempotency_key: str | None


def claim_next_step(
    engine: Engine, *, worker_id: str, lease_seconds: float, supported: list[str]
) -> ClaimedStep | None:
    with engine.begin() as conn:
        row = conn.execute(
            _CLAIM,
            {"worker_id": worker_id, "lease_seconds": lease_seconds, "supported": supported},
        ).first()
        if row is None:
            return None
        conn.execute(
            _RECORD_ATTEMPT,
            {
                "step_id": row.id,
                "attempt": row.attempt_count,
                "worker_id": worker_id,
                "generation": row.lease_generation,
            },
        )
        append_event(
            conn,
            row.workflow_run_id,
            "step.claimed",
            step_run_id=row.id,
            step=row.name,
            worker_id=worker_id,
            attempt=row.attempt_count,
            lease_generation=row.lease_generation,
            lease_expires_at=row.lease_expires_at.isoformat(),
        )
    # Committed: the lease is now durable and visible to every other process.
    return ClaimedStep(
        step_id=row.id,
        workflow_run_id=row.workflow_run_id,
        workflow_type=row.workflow_type,
        workflow_version=row.workflow_version,
        name=row.name,
        position=row.position,
        input=row.input,
        attempt=row.attempt_count,
        max_attempts=row.max_attempts,
        worker_id=worker_id,
        lease_generation=row.lease_generation,
        lease_expires_at=row.lease_expires_at,
        idempotency_key=row.idempotency_key,
    )
