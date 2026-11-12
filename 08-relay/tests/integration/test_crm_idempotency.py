"""The demo CRM's receiver-side idempotency contract (T8), against its own database."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Engine

from services.demo_crm.service import ContactUpsert, IdempotencyConflict, apply_upsert
from tests.helpers import fetch_all, fetch_one

PAYLOAD = ContactUpsert(
    email="Priya.Shah@northwind-logistics.example",
    name="Priya Shah",
    company="Northwind Logistics Ltd",
    details={"source": "web_form"},
)


def test_first_request_applies_one_mutation_and_stores_the_response(crm_engine: Engine) -> None:
    result = apply_upsert(crm_engine, "relay:run-1:crm_upsert", PAYLOAD)
    assert not result.replayed and result.body["operation"] == "created"
    contact = fetch_one(crm_engine, "SELECT email, revision FROM contacts")
    assert contact == {"email": "priya.shah@northwind-logistics.example", "revision": 1}
    stored = fetch_one(crm_engine, "SELECT response_body FROM idempotency_keys")
    assert stored["response_body"] == result.body


def test_retry_with_same_key_and_payload_returns_stored_result_without_reapplying(
    crm_engine: Engine,
) -> None:
    first = apply_upsert(crm_engine, "relay:run-1:crm_upsert", PAYLOAD)
    retry = apply_upsert(crm_engine, "relay:run-1:crm_upsert", PAYLOAD.model_copy())
    assert retry.replayed and retry.body == first.body
    assert fetch_one(crm_engine, "SELECT count(*) AS n FROM mutations")["n"] == 1
    assert fetch_one(crm_engine, "SELECT revision FROM contacts")["revision"] == 1


def test_same_key_with_different_payload_is_rejected(crm_engine: Engine) -> None:
    """Spec test 16."""
    apply_upsert(crm_engine, "relay:run-1:crm_upsert", PAYLOAD)
    with pytest.raises(IdempotencyConflict):
        apply_upsert(
            crm_engine,
            "relay:run-1:crm_upsert",
            PAYLOAD.model_copy(update={"name": "Someone Else"}),
        )
    assert fetch_one(crm_engine, "SELECT name FROM contacts")["name"] == "Priya Shah"
    assert fetch_one(crm_engine, "SELECT count(*) AS n FROM mutations")["n"] == 1


def test_concurrent_requests_sharing_a_key_apply_exactly_one_mutation(crm_engine: Engine) -> None:
    """Spec test 15: ten simultaneous requests, one key -> one mutation, identical responses."""
    n = 10
    barrier = Barrier(n)

    def send(_: int):
        barrier.wait()
        return apply_upsert(crm_engine, "relay:run-race:crm_upsert", PAYLOAD)

    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(send, range(n)))
    assert len({tuple(sorted(r.body.items())) for r in results}) == 1
    assert sum(not r.replayed for r in results) == 1
    assert fetch_one(crm_engine, "SELECT count(*) AS n FROM mutations")["n"] == 1


def test_different_keys_for_the_same_contact_are_separate_logical_changes(
    crm_engine: Engine,
) -> None:
    apply_upsert(crm_engine, "relay:run-1:crm_upsert", PAYLOAD)
    second = apply_upsert(
        crm_engine,
        "relay:run-2:crm_upsert",
        PAYLOAD.model_copy(update={"details": {"source": "email"}}),
    )
    assert second.body["operation"] == "updated" and second.body["revision"] == 2
    ops = fetch_all(crm_engine, "SELECT operation FROM mutations ORDER BY id")
    assert [o["operation"] for o in ops] == ["created", "updated"]
