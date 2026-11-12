"""Run one claimed step and record its outcome.

Execution happens between two transactions: the claim (already committed) and
the fenced completion or failure. No database transaction is open while the
handler runs (invariant I10).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import Engine

from relay.config import EngineSettings
from relay.engine import failpoints
from relay.engine.approvals import pause_for_approval
from relay.engine.claims import ClaimedStep
from relay.engine.errors import ApprovalRequired, LeaseLost, StepError, UnknownWorkflow
from relay.engine.retries import RetryPolicy
from relay.engine.states import ErrorClass
from relay.engine.transitions import complete_step, fail_step
from relay.workflows.registry import Registry, StepContext

log = logging.getLogger("relay.executor")


def retry_policy(settings: EngineSettings) -> RetryPolicy:
    return RetryPolicy(
        base_seconds=settings.retry_base_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
    )


@dataclass
class _Failure:
    error_class: ErrorClass
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    retry_after_seconds: float | None = None


def execute_claimed(
    engine: Engine,
    registry: Registry,
    settings: EngineSettings,
    step: ClaimedStep,
    http: httpx.Client | None,
) -> str:
    """Run the step and record the outcome.

    Returns succeeded, ready (retry scheduled), awaiting_approval, failed,
    outcome_unknown or lease_lost.
    """
    try:
        definition = registry.get(step.workflow_type, step.workflow_version).step(step.name)
    except UnknownWorkflow as exc:
        # Cannot happen when claims filter on supported versions; fail loudly if it does.
        return _record(engine, settings, step, _Failure(ErrorClass.PERMANENT, str(exc)), False)

    ctx = StepContext(
        workflow_run_id=str(step.workflow_run_id),
        step_name=step.name,
        attempt=step.attempt,
        input=step.input,
        idempotency_key=step.idempotency_key,
        settings=settings,
        http=http,
    )
    try:
        output = definition.handler(ctx)
    except ApprovalRequired as exc:
        try:
            pause_for_approval(engine, step, reason=exc.reason, output=exc.output)
        except LeaseLost as lost:
            log.warning("%s", lost)
            return "lease_lost"
        return "awaiting_approval"
    except StepError as exc:
        failure = _Failure(exc.error_class, exc.summary, exc.details, exc.retry_after_seconds)
        return _record(engine, settings, step, failure, definition.external_effect)
    except Exception as exc:  # a bug or an unclassified library error
        log.exception("unexpected error in step %s of %s", step.name, step.workflow_run_id)
        # For a step with external effects we cannot rule out that the effect happened.
        error_class = ErrorClass.AMBIGUOUS if definition.external_effect else ErrorClass.TRANSIENT
        failure = _Failure(error_class, f"unexpected {type(exc).__name__}: {exc}"[:500])
        return _record(engine, settings, step, failure, definition.external_effect)

    failpoints.hit("before_complete", step.name)
    try:
        complete_step(engine, step, output)
    except LeaseLost as exc:
        log.warning("%s", exc)
        return "lease_lost"
    return "succeeded"


def _record(
    engine: Engine,
    settings: EngineSettings,
    step: ClaimedStep,
    failure: _Failure,
    external_effect: bool,
) -> str:
    try:
        status = fail_step(
            engine,
            step,
            error_class=failure.error_class,
            summary=failure.summary,
            details=failure.details,
            external_effect=external_effect,
            retry_after_seconds=failure.retry_after_seconds,
            policy=retry_policy(settings),
        )
    except LeaseLost as exc:
        log.warning("%s", exc)
        return "lease_lost"
    return str(status)
