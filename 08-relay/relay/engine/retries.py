"""Retry policy: which failures are retried, and when.

Execution is at least once. A retry re-runs the whole step from its persisted
input, so steps must be safe to repeat (see docs/guarantees-and-limitations.md).

Backoff is exponential with "equal jitter": half of the exponential delay is
fixed and half is random. The fixed half guarantees a minimum pause; the
random half stops many failed steps from retrying in lockstep against a
recovering service. A Retry-After from the server raises the delay (up to a cap).
"""

import random
from dataclasses import dataclass

from relay.engine.states import ErrorClass


@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    max_retry_after_seconds: float = 120.0

    def delay(
        self,
        attempt: int,
        retry_after_seconds: float | None = None,
        rng: random.Random | None = None,
    ) -> float:
        """Seconds to wait before attempt `attempt + 1`, given that attempt `attempt` failed."""
        rng = rng or random  # type: ignore[assignment]
        exponential = min(self.max_delay_seconds, self.base_seconds * 2 ** (attempt - 1))
        delay = exponential / 2 + rng.uniform(0, exponential / 2)
        if retry_after_seconds is not None:
            delay = max(delay, min(retry_after_seconds, self.max_retry_after_seconds))
        return delay


def is_retryable(error_class: ErrorClass, *, safe_to_repeat: bool) -> bool:
    """Whether this kind of failure may be retried at all (attempt budget aside).

    `safe_to_repeat` is true for steps with no external effect, and for
    external steps whose receiver deduplicates by idempotency key.

    An ambiguous failure, or a worker vanishing mid-attempt, means the step's
    effect may already have happened. Those are retried only when repeating
    the step is safe; otherwise a retry could apply the change twice.
    """
    if error_class == ErrorClass.PERMANENT:
        return False
    if error_class in (ErrorClass.AMBIGUOUS, ErrorClass.LEASE_EXPIRED):
        return safe_to_repeat
    return True  # transient: definitely not applied
