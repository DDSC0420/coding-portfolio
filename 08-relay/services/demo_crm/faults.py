"""Demo-mode fault injection for the CRM. Off unless CRM_DEMO_MODE=true.

Faults let the demo and tests "break the CRM" in the three ways that matter:

- before commit:  respond 503 without touching the database. The change
                  definitely did not happen (the engine classifies it transient).
- after commit:   apply and commit the change, then answer 500, or drop the
                  connection mid-response, or stall past the worker's timeout.
                  The change DID happen but the caller cannot know (ambiguous).
- delay:          slow every request down.

Each fault fires either for the next N requests (`*_next`, deterministic, for
tests) or with a probability (`*_rate`, for the live demo). Counters are
consumed before rates. State is in memory and resets when the CRM restarts.
"""

import random
import threading
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class FaultSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_ms: int = Field(0, ge=0, le=30_000)

    fail_before_commit_next: int = Field(0, ge=0, le=10_000)
    fail_before_commit_rate: float = Field(0.0, ge=0, le=1)
    retry_after_seconds: int | None = Field(None, ge=0, le=60)

    error_after_commit_next: int = Field(0, ge=0, le=10_000)
    error_after_commit_rate: float = Field(0.0, ge=0, le=1)

    drop_after_commit_next: int = Field(0, ge=0, le=10_000)
    drop_after_commit_rate: float = Field(0.0, ge=0, le=1)

    hang_after_commit_next: int = Field(0, ge=0, le=10_000)
    hang_after_commit_ms: int = Field(15_000, ge=0, le=60_000)


@dataclass
class Decision:
    delay_ms: int = 0
    fail_before_commit: bool = False
    retry_after_seconds: int | None = None
    after_commit: str | None = None  # "error" | "drop" | "hang"
    hang_ms: int = 0


class FaultInjector:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._lock = threading.Lock()
        self._settings = FaultSettings()
        self._rng = rng or random.Random()
        self.injected: dict[str, int] = {}

    def get(self) -> FaultSettings:
        with self._lock:
            return self._settings.model_copy()

    def set(self, settings: FaultSettings) -> None:
        with self._lock:
            self._settings = settings.model_copy()

    def reset(self) -> None:
        with self._lock:
            self._settings = FaultSettings()
            self.injected = {}

    def _take(self, name: str) -> bool:
        """Consume one `<name>_next`, or roll `<name>_rate`. Caller holds the lock."""
        s = self._settings
        remaining = getattr(s, f"{name}_next")
        if remaining > 0:
            setattr(s, f"{name}_next", remaining - 1)
            fired = True
        else:
            fired = self._rng.random() < getattr(s, f"{name}_rate", 0.0)
        if fired:
            self.injected[name] = self.injected.get(name, 0) + 1
        return fired

    def decide(self) -> Decision:
        with self._lock:
            s = self._settings
            decision = Decision(delay_ms=s.delay_ms, retry_after_seconds=s.retry_after_seconds)
            if self._take("fail_before_commit"):
                decision.fail_before_commit = True
                return decision
            for name, kind in (
                ("error_after_commit", "error"),
                ("drop_after_commit", "drop"),
                ("hang_after_commit", "hang"),
            ):
                if self._take(name):
                    decision.after_commit = kind
                    decision.hang_ms = s.hang_after_commit_ms
                    break
            return decision
