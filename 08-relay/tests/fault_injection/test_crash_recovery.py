"""Crash and fault tests with real worker processes (spec tests 3, 4, 5, 6, 8, 10, 17).

Each test creates one precise failure, then checks the database records on
both sides of the side-effect boundary: the engine's steps, attempts and
events, and the CRM's mutation log.
"""

import sys

import pytest

from tests.e2e.processes import wait_until
from tests.fault_injection.conftest import Cluster

pytestmark = pytest.mark.fault_injection

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="needs SIGSTOP/SIGCONT; run from WSL2 or Linux"
)


def test_worker_killed_after_claim_is_recovered_by_another_worker(cluster: Cluster) -> None:
    """Spec test 3: worker death after the claim committed but before the step ran."""
    cluster.start_crm()
    [run_id] = cluster.submit()
    worker_a = cluster.start_worker("worker-a", failpoints="after_claim@extract=exit")
    assert worker_a.wait_exit() == 137  # died holding the lease

    extract = cluster.step(run_id, "extract")
    assert extract["status"] == "running" and extract["lease_owner"].startswith("worker-a/")

    cluster.start_worker("worker-b")
    assert cluster.wait_terminal([run_id]) == {"succeeded": 1}
    assert cluster.attempts(extract["id"]) == [
        {
            "attempt_number": 1,
            "outcome": "lease_expired",
            "error_class": "lease_expired",
            "worker": "worker-a",
        },
        {"attempt_number": 2, "outcome": "succeeded", "error_class": None, "worker": "worker-b"},
    ]
    [expired] = cluster.events(run_id, "step.lease_expired")
    assert expired["previous_owner"].startswith("worker-a/")
    assert expired["recovered_by"].startswith("worker-b/")
    assert cluster.crm_mutations() == {"total": 1, "duplicate_keys": 0}


def test_worker_killed_after_crm_commit_is_retried_and_crm_replays(cluster: Cluster) -> None:
    """Spec test 4: the CRM committed and answered, then the worker died before recording it."""
    cluster.start_crm()
    [run_id] = cluster.submit()
    worker_a = cluster.start_worker("worker-a", failpoints="before_complete@crm_upsert=exit")
    assert worker_a.wait_exit() == 137

    crm_step = cluster.step(run_id, "crm_upsert")
    assert crm_step["status"] == "running"  # the engine never heard about the success...
    assert cluster.crm_mutations() == {"total": 1, "duplicate_keys": 0}  # ...but the CRM applied it

    cluster.start_worker("worker-b")
    assert cluster.wait_terminal([run_id]) == {"succeeded": 1}
    crm_step = cluster.step(run_id, "crm_upsert")
    assert crm_step["output"]["crm"]["replayed"] is True  # stored result, not a second change
    assert crm_step["output"]["crm"]["idempotency_key"] == crm_step["idempotency_key"]
    assert [a["outcome"] for a in cluster.attempts(crm_step["id"])] == [
        "lease_expired",
        "succeeded",
    ]
    assert cluster.crm_mutations() == {"total": 1, "duplicate_keys": 0}


@pytest.mark.parametrize(
    ("fault", "settings"),
    [
        ("dropped connection", {"drop_after_commit_next": 1}),
        ("500 after commit", {"error_after_commit_next": 1}),
        (
            "response slower than the client timeout",
            {"hang_after_commit_next": 1, "hang_after_commit_ms": 2500},
        ),
    ],
)
def test_lost_crm_response_after_commit_is_retried_and_replayed(
    cluster: Cluster, fault: str, settings: dict
) -> None:
    """Spec test 5: the CRM committed but the worker never got a usable answer."""
    cluster.start_crm()
    cluster.faults(**settings)
    [run_id] = cluster.submit()
    cluster.start_worker("worker-a")
    assert cluster.wait_terminal([run_id]) == {"succeeded": 1}, fault

    crm_step = cluster.step(run_id, "crm_upsert")
    assert [(a["outcome"], a["error_class"]) for a in cluster.attempts(crm_step["id"])] == [
        ("failed", "ambiguous"),
        ("succeeded", None),
    ]
    [retry] = cluster.events(run_id, "step.retry_scheduled")
    assert retry["error_class"] == "ambiguous"
    assert crm_step["output"]["crm"]["replayed"] is True
    assert cluster.crm_mutations() == {"total": 1, "duplicate_keys": 0}


@posix_only
def test_frozen_worker_cannot_overwrite_the_worker_that_took_over(cluster: Cluster) -> None:
    """Spec test 6, with a real process: worker-a freezes mid-step (SIGSTOP, like a long
    GC pause), its lease expires, worker-b finishes the workflow, then worker-a wakes up
    and tries to renew and complete. Both writes must be refused."""
    cluster.start_crm()
    [run_id] = cluster.submit()
    # The failpoint just widens the window between "step done" and "recorded".
    worker_a = cluster.start_worker("worker-a", failpoints="before_complete@enrich=sleep:3")
    wait_until(
        lambda: (
            (s := cluster.step(run_id, "enrich"))["status"] == "running"
            and s["lease_owner"].startswith("worker-a/")
        ),
        15,
        "worker-a to hold enrich",
    )
    worker_a.pause()

    cluster.start_worker("worker-b")
    assert cluster.wait_terminal([run_id]) == {"succeeded": 1}
    worker_a.resume()

    def refused_completion():
        return [
            e
            for e in cluster.events(run_id, "step.stale_write_rejected")
            if e["action"] == "complete" and e["worker_id"].startswith("worker-a/")
        ]

    [refused] = wait_until(refused_completion, 15, "worker-a's late completion to be refused")
    assert refused["current_status"] == "succeeded"
    enrich = cluster.step(run_id, "enrich")
    assert [(a["worker"], a["outcome"]) for a in cluster.attempts(enrich["id"])] == [
        ("worker-a", "lease_expired"),
        ("worker-b", "succeeded"),
    ]
    assert worker_a.proc.poll() is None  # alive; it just lost the step
    assert cluster.crm_mutations() == {"total": 1, "duplicate_keys": 0}


def test_temporary_crm_outage_recovers_with_backoff(cluster: Cluster) -> None:
    """Spec test 8: three refusals before commit, then success, with one mutation."""
    cluster.start_crm()
    cluster.faults(fail_before_commit_next=3)
    [run_id] = cluster.submit()
    cluster.start_worker("worker-a")
    assert cluster.wait_terminal([run_id]) == {"succeeded": 1}

    crm_step = cluster.step(run_id, "crm_upsert")
    assert [(a["outcome"], a["error_class"]) for a in cluster.attempts(crm_step["id"])] == [
        ("failed", "transient")
    ] * 3 + [("succeeded", None)]
    retries = cluster.events(run_id, "step.retry_scheduled")
    assert [r["attempt"] for r in retries] == [1, 2, 3]
    assert all(r["delay_seconds"] > 0 for r in retries)
    assert crm_step["output"]["crm"]["replayed"] is False
    assert cluster.crm_mutations() == {"total": 1, "duplicate_keys": 0}


def test_crm_down_for_good_exhausts_retries_and_fails(cluster: Cluster) -> None:
    """Spec test 10: every attempt refused before commit -> failed (not unknown), no mutation."""
    cluster.start_crm()
    cluster.faults(fail_before_commit_rate=1.0)
    [run_id] = cluster.submit()
    cluster.start_worker("worker-a")
    assert cluster.wait_terminal([run_id]) == {"failed": 1}
    crm_step = cluster.step(run_id, "crm_upsert")
    assert crm_step["attempt_count"] == crm_step["max_attempts"] == 6
    assert cluster.crm_mutations() == {"total": 0, "duplicate_keys": 0}


def test_full_restart_with_work_in_flight_loses_nothing(cluster: Cluster) -> None:
    """Spec test 17: kill every worker and the CRM mid-batch, keep the databases, restart."""
    crm = cluster.start_crm()
    cluster.faults(delay_ms=150)  # keep CRM calls in flight when we pull the plug
    run_ids = cluster.submit(30, prefix="RESTART")
    first = [cluster.start_worker("worker-a"), cluster.start_worker("worker-b")]
    wait_until(
        lambda: sum(cluster.status(r) == "succeeded" for r in run_ids) >= 5,
        30,
        "some workflows to finish before the crash",
    )
    for w in first:
        w.kill()
    crm.kill()
    in_flight = sum(cluster.status(r) != "succeeded" for r in run_ids)
    assert in_flight > 0, "crash happened too late to test recovery"

    cluster.start_crm()  # fresh process: faults reset, CRM healthy
    cluster.start_worker("worker-a")
    cluster.start_worker("worker-b")
    assert cluster.wait_terminal(run_ids, timeout=60) == {"succeeded": 30}
    assert cluster.crm_mutations() == {"total": 30, "duplicate_keys": 0}
    stranded = [r for r in run_ids if cluster.step(r, "crm_upsert")["status"] != "succeeded"]
    assert stranded == []
    recovered = sum(len(cluster.events(r, "step.lease_expired")) for r in run_ids)
    assert recovered >= 1  # steps the killed workers held were taken back by recovery


def test_failpoints_are_not_active_in_a_normal_worker(cluster: Cluster) -> None:
    cluster.start_crm()
    [run_id] = cluster.submit()
    w = cluster.start_worker("worker-a")
    assert cluster.wait_terminal([run_id]) == {"succeeded": 1}
    assert "FAILPOINT" not in w.log()


def test_a_step_slower_than_the_lease_keeps_its_lease_by_renewal(cluster: Cluster) -> None:
    """A healthy worker on a slow step (4 s, lease 2 s) must not lose it to recovery."""
    cluster.start_crm()
    [run_id] = cluster.submit()
    cluster.start_worker("worker-a", failpoints="before_complete@enrich=sleep:4")
    cluster.start_worker("worker-b")  # its recovery sweep is watching for expired leases
    assert cluster.wait_terminal([run_id]) == {"succeeded": 1}
    enrich = cluster.step(run_id, "enrich")
    assert [a["outcome"] for a in cluster.attempts(enrich["id"])] == ["succeeded"]
    assert cluster.events(run_id, "step.lease_expired") == []
