"""State names and the allowed transition tables (docs/architecture.md §2).

The same tables are enforced inside PostgreSQL by triggers created in the
initial migration, so an illegal transition fails at the database even if
Python code has a bug. tests/integration/test_state_triggers.py checks that the
trigger and these tables agree.
"""

from enum import StrEnum


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"


class StepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AttemptOutcome(StrEnum):
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LEASE_EXPIRED = "lease_expired"
    PAUSED_FOR_APPROVAL = "paused_for_approval"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ErrorClass(StrEnum):
    PERMANENT = "permanent"  # retrying cannot help
    TRANSIENT = "transient"  # definitely not applied; may succeed later
    AMBIGUOUS = "ambiguous"  # may or may not have been applied
    LEASE_EXPIRED = "lease_expired"  # worker vanished mid-attempt


W, S = WorkflowStatus, StepStatus

WORKFLOW_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    W.RUNNING: frozenset({W.AWAITING_APPROVAL, W.SUCCEEDED, W.FAILED, W.OUTCOME_UNKNOWN}),
    W.AWAITING_APPROVAL: frozenset({W.RUNNING, W.REJECTED}),
    W.SUCCEEDED: frozenset(),
    W.FAILED: frozenset(),
    W.OUTCOME_UNKNOWN: frozenset(),
    W.REJECTED: frozenset(),
}

STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    S.PENDING: frozenset({S.READY, S.CANCELLED}),
    S.READY: frozenset({S.RUNNING}),
    S.RUNNING: frozenset({S.SUCCEEDED, S.READY, S.FAILED, S.OUTCOME_UNKNOWN, S.AWAITING_APPROVAL}),
    S.AWAITING_APPROVAL: frozenset({S.SUCCEEDED, S.REJECTED}),
    S.SUCCEEDED: frozenset(),
    S.FAILED: frozenset(),
    S.OUTCOME_UNKNOWN: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL_WORKFLOW = frozenset(s for s, nxt in WORKFLOW_TRANSITIONS.items() if not nxt)
TERMINAL_STEP = frozenset(s for s, nxt in STEP_TRANSITIONS.items() if not nxt)
ACTIVE_STEP = frozenset({S.READY, S.RUNNING, S.AWAITING_APPROVAL})
