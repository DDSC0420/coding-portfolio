"""Workflow submission (transaction T1 in docs/architecture.md §3).

One transaction inserts the run, materialises every step row from the pinned
definition, and appends `workflow.created`. Submission is idempotent per key:
same key + same request returns the original run; same key + a different
request is a conflict.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from relay.db.session import to_json
from relay.engine.events import append_event
from relay.engine.states import StepStatus, WorkflowStatus
from relay.workflows.registry import Registry


def canonical_hash(value: Any) -> str:
    """SHA-256 of canonical JSON: sorted keys, no insignificant whitespace."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SubmissionConflict(Exception):
    """The submission key was already used with a different request body."""

    def __init__(self, workflow_run_id: UUID) -> None:
        super().__init__(
            f"submission key already used for run {workflow_run_id} with a different body"
        )
        self.workflow_run_id = workflow_run_id


@dataclass(frozen=True)
class SubmitResult:
    workflow_run_id: UUID
    created: bool  # False when an earlier submission with the same key was returned


def external_idempotency_key(workflow_run_id: UUID, step_name: str) -> str:
    return f"relay:{workflow_run_id}:{step_name}"


def submit_workflow(
    engine: Engine,
    registry: Registry,
    *,
    workflow_type: str,
    input: dict[str, Any],
    workflow_version: int | None = None,
    submission_key: str | None = None,
    batch_id: str | None = None,
) -> SubmitResult:
    definition = registry.get(workflow_type, workflow_version)
    request_hash = canonical_hash(
        {
            "workflow_type": workflow_type,
            "workflow_version": workflow_version,  # as requested; None means "latest"
            "input": input,
            "batch_id": batch_id,
        }
    )

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO workflow_runs
                    (workflow_type, workflow_version, status, input,
                     submission_key, request_hash, batch_id)
                VALUES (:type, :version, :status, CAST(:input AS jsonb), :key, :hash, :batch)
                ON CONFLICT (submission_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "type": definition.type,
                "version": definition.version,
                "status": WorkflowStatus.RUNNING,
                "input": to_json(input),
                "key": submission_key,
                "hash": request_hash if submission_key else None,
                "batch": batch_id,
            },
        ).first()

        if row is None:
            # Key already used. A concurrent insert of the same key blocks on
            # the unique index until it commits, so the row is visible here.
            existing = conn.execute(
                text("SELECT id, request_hash FROM workflow_runs WHERE submission_key = :key"),
                {"key": submission_key},
            ).one()
            if existing.request_hash != request_hash:
                raise SubmissionConflict(existing.id)
            return SubmitResult(existing.id, created=False)

        run_id: UUID = row.id
        conn.execute(
            text(
                """
                INSERT INTO step_runs
                    (workflow_run_id, name, position, status, input, max_attempts,
                     next_eligible_at, idempotency_key)
                VALUES (:wf, :name, :position, :status, CAST(:input AS jsonb), :max_attempts,
                        CASE WHEN :status = 'ready' THEN now() END, :idem)
                """
            ),
            [
                {
                    "wf": run_id,
                    "name": step.name,
                    "position": position,
                    "status": StepStatus.READY if position == 0 else StepStatus.PENDING,
                    "input": to_json(input) if position == 0 else None,
                    "max_attempts": step.max_attempts,
                    "idem": external_idempotency_key(run_id, step.name)
                    if step.external_effect
                    else None,
                }
                for position, step in enumerate(definition.steps)
            ],
        )
        append_event(
            conn,
            run_id,
            "workflow.created",
            workflow=definition.key,
            steps=[s.name for s in definition.steps],
            batch_id=batch_id,
            submission_key=submission_key,
        )
    return SubmitResult(run_id, created=True)
