import random

import pytest

from relay.engine import failpoints
from relay.engine.retries import RetryPolicy, is_retryable
from relay.engine.states import ErrorClass


def test_backoff_doubles_with_equal_jitter_and_respects_the_cap() -> None:
    policy = RetryPolicy(base_seconds=1, max_delay_seconds=8)
    rng = random.Random(7)
    for attempt, full in [(1, 1), (2, 2), (3, 4), (4, 8), (5, 8), (9, 8)]:
        for _ in range(200):
            assert full / 2 <= policy.delay(attempt, rng=rng) <= full


def test_jitter_spreads_retries_out() -> None:
    policy = RetryPolicy(base_seconds=1, max_delay_seconds=30)
    delays = {round(policy.delay(4, rng=random.Random(seed)), 3) for seed in range(50)}
    assert len(delays) > 40


def test_retry_after_raises_the_delay_but_is_capped() -> None:
    policy = RetryPolicy(base_seconds=1, max_delay_seconds=8, max_retry_after_seconds=60)
    assert policy.delay(1, retry_after_seconds=20) == 20
    assert policy.delay(1, retry_after_seconds=600) == 60
    assert policy.delay(4, retry_after_seconds=0) >= 4  # never lower than our own backoff


@pytest.mark.parametrize(
    ("error_class", "safe", "expected"),
    [
        (ErrorClass.PERMANENT, True, False),
        (ErrorClass.TRANSIENT, False, True),
        (ErrorClass.AMBIGUOUS, True, True),
        (ErrorClass.AMBIGUOUS, False, False),
        (ErrorClass.LEASE_EXPIRED, True, True),
        (ErrorClass.LEASE_EXPIRED, False, False),
    ],
)
def test_which_failures_may_be_retried(error_class, safe, expected) -> None:
    assert is_retryable(error_class, safe_to_repeat=safe) is expected


def test_failpoints_are_off_unless_configured(monkeypatch) -> None:
    monkeypatch.delenv("RELAY_FAILPOINTS", raising=False)
    assert failpoints.active() == {}
    failpoints.hit("before_complete", "crm_upsert")  # no-op


def test_failpoint_spec_parsing(monkeypatch) -> None:
    monkeypatch.setenv("RELAY_FAILPOINTS", "after_claim@extract=exit, before_complete=sleep:0.01")
    assert failpoints.active() == {
        ("after_claim", "extract"): "exit",
        ("before_complete", None): "sleep:0.01",
    }
    failpoints.hit("before_complete", "enrich")  # sleeps 10 ms, returns
    monkeypatch.setenv("RELAY_FAILPOINTS", "during_lunch=exit")
    with pytest.raises(ValueError):
        failpoints.active()
