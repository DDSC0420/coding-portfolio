"""How CRM responses and transport errors are classified decides what is safe to retry."""

import httpx
import pytest

from relay.engine.errors import AmbiguousStepError, PermanentStepError, TransientStepError
from relay.steps.crm_upsert import build_payload, call_crm, classify_response


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, PermanentStepError),
        (409, PermanentStepError),
        (422, PermanentStepError),
        (429, TransientStepError),
        (503, TransientStepError),
        (500, AmbiguousStepError),
        (502, AmbiguousStepError),
    ],
)
def test_http_status_classification(status: int, expected: type) -> None:
    with pytest.raises(expected):
        classify_response(httpx.Response(status, request=httpx.Request("POST", "http://crm")))


def test_success_is_not_an_error() -> None:
    classify_response(httpx.Response(200, request=httpx.Request("POST", "http://crm")))


def test_retry_after_is_captured() -> None:
    response = httpx.Response(
        503, headers={"Retry-After": "7"}, request=httpx.Request("POST", "http://crm")
    )
    with pytest.raises(TransientStepError) as info:
        classify_response(response)
    assert info.value.retry_after_seconds == 7.0


def _raise(exc_type):
    def handler(request: httpx.Request):
        raise exc_type("simulated", request=request)

    return handler


def test_connection_refused_is_transient_because_nothing_was_sent() -> None:
    with pytest.raises(TransientStepError):
        call_crm(_client(_raise(httpx.ConnectError)), "http://crm", "k" * 10, {})


@pytest.mark.parametrize(
    "exc_type", [httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ReadError]
)
def test_lost_response_is_ambiguous_because_the_crm_may_have_committed(exc_type) -> None:
    with pytest.raises(AmbiguousStepError):
        call_crm(_client(_raise(exc_type)), "http://crm", "k" * 10, {})


def test_payload_is_a_pure_function_of_persisted_input() -> None:
    """Retries must send an identical body, or the CRM would reject them as a key conflict."""
    step_input = {
        "enquiry": {
            "email": "a@b.example",
            "name": "A",
            "company": "C",
            "request": "r",
            "source": "web_form",
            "project_value_gbp": 5,
            "enquiry_ref": "X",
        },
        "enrichment": {"matched": True, "account_tier": "growth", "industry": "Software"},
    }
    assert build_payload(step_input, "run-1") == build_payload(step_input, "run-1")
