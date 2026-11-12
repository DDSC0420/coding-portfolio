"""Submission (T1): materialised steps, pinned version, and idempotent submission keys."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Engine

from relay.engine.submission import SubmissionConflict, submit_workflow
from relay.workflows.registry import Registry
from tests.conftest import VALID_ENQUIRY
from tests.helpers import event_types, fetch_one, steps_of


def test_submission_materialises_every_step_from_the_pinned_definition(
    engine: Engine, registry: Registry
) -> None:
    result = submit_workflow(
        engine, registry, workflow_type="enquiry", workflow_version=1, input=VALID_ENQUIRY
    )
    assert result.created
    run = fetch_one(engine, "SELECT * FROM workflow_runs WHERE id = :id", id=result.workflow_run_id)
    assert (run["status"], run["workflow_type"], run["workflow_version"]) == (
        "running",
        "enquiry",
        1,
    )

    steps = steps_of(engine, result.workflow_run_id)
    assert [s["name"] for s in steps] == ["extract", "validate", "enrich", "crm_upsert"]
    assert [s["status"] for s in steps] == ["ready", "pending", "pending", "pending"]
    assert steps[0]["input"] == VALID_ENQUIRY and steps[0]["next_eligible_at"] is not None
    # Only the external-effect step gets a stable idempotency key, fixed at creation.
    assert [s["idempotency_key"] for s in steps[:3]] == [None, None, None]
    assert steps[3]["idempotency_key"] == f"relay:{result.workflow_run_id}:crm_upsert"
    assert event_types(engine, result.workflow_run_id) == ["workflow.created"]


def test_same_key_same_body_returns_the_original_run(engine: Engine, registry: Registry) -> None:
    first = submit_workflow(
        engine,
        registry,
        workflow_type="enquiry",
        workflow_version=1,
        input=VALID_ENQUIRY,
        submission_key="submit-key-0001",
    )
    again = submit_workflow(
        engine,
        registry,
        workflow_type="enquiry",
        workflow_version=1,
        input=dict(VALID_ENQUIRY),
        submission_key="submit-key-0001",
    )
    assert (again.workflow_run_id, again.created) == (first.workflow_run_id, False)
    assert fetch_one(engine, "SELECT count(*) AS n FROM workflow_runs")["n"] == 1


def test_same_key_different_body_is_a_conflict(engine: Engine, registry: Registry) -> None:
    first = submit_workflow(
        engine,
        registry,
        workflow_type="enquiry",
        workflow_version=1,
        input=VALID_ENQUIRY,
        submission_key="submit-key-0002",
    )
    with pytest.raises(SubmissionConflict) as info:
        submit_workflow(
            engine,
            registry,
            workflow_type="enquiry",
            workflow_version=1,
            input={**VALID_ENQUIRY, "company": "Someone Else"},
            submission_key="submit-key-0002",
        )
    assert info.value.workflow_run_id == first.workflow_run_id
    assert fetch_one(engine, "SELECT count(*) AS n FROM workflow_runs")["n"] == 1


def test_concurrent_submissions_with_one_key_create_one_run(
    engine: Engine, registry: Registry
) -> None:
    n = 8
    barrier = Barrier(n)

    def submit(_: int):
        barrier.wait()
        return submit_workflow(
            engine,
            registry,
            workflow_type="enquiry",
            workflow_version=1,
            input=VALID_ENQUIRY,
            submission_key="submit-key-race",
        )

    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(submit, range(n)))
    assert len({r.workflow_run_id for r in results}) == 1
    assert sum(r.created for r in results) == 1
    assert fetch_one(engine, "SELECT count(*) AS n FROM step_runs")["n"] == 4
