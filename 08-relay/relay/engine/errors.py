"""Exceptions raised by step code and by fenced engine writes."""

from typing import Any

from relay.engine.states import ErrorClass


class StepError(Exception):
    """Raised by a step handler to report a classified failure."""

    error_class: ErrorClass = ErrorClass.TRANSIENT

    def __init__(
        self,
        summary: str,
        *,
        details: dict[str, Any] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(summary)
        self.summary = summary[:500]
        self.details = details or {}
        self.retry_after_seconds = retry_after_seconds


class PermanentStepError(StepError):
    """Retrying cannot help (e.g. validation failure, HTTP 400/409/422)."""

    error_class = ErrorClass.PERMANENT


class TransientStepError(StepError):
    """The effect definitely did not happen; a later attempt may succeed."""

    error_class = ErrorClass.TRANSIENT


class AmbiguousStepError(StepError):
    """The external effect may or may not have happened (e.g. read timeout after sending)."""

    error_class = ErrorClass.AMBIGUOUS


class ApprovalRequired(Exception):
    """Raised by a step handler: a person must decide before the workflow continues.

    `output` is what the step will hand to its successor if the decision is "approve".
    It is stored with the request, so nothing is recomputed after the decision.
    """

    def __init__(self, reason: str, output: dict[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason[:500]
        self.output = output


class LeaseLost(Exception):
    """A fenced write matched no row: this worker no longer owns the step's lease."""


class InvariantViolation(Exception):
    """The database is in a state the engine's invariants say is impossible."""


class UnknownWorkflow(LookupError):
    pass
