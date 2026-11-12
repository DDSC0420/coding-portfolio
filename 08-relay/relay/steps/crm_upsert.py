"""CRM update step: upsert the contact in the separate demo CRM over HTTP.

Two rules make retries safe here:

1. Every attempt sends the same Idempotency-Key, assigned when the step row
   was created (`relay:<run id>:crm_upsert`).
2. The request body is built only from the persisted step input, so every
   attempt sends an identical payload. No attempt numbers, no timestamps.

The CRM stores the response for each key, so a retry after "the CRM committed
but we never heard back" returns the original result instead of applying a
second mutation. Fencing in the engine cannot provide this; only the receiver can.
"""

from typing import Any

import httpx

from relay.engine.errors import AmbiguousStepError, PermanentStepError, TransientStepError
from relay.workflows.registry import StepContext


def build_payload(step_input: dict[str, Any], workflow_run_id: str) -> dict[str, Any]:
    enquiry = step_input["enquiry"]
    enrichment = step_input.get("enrichment", {})
    return {
        "email": enquiry["email"],
        "name": enquiry["name"],
        "company": enquiry["company"],
        "details": {
            "request": enquiry["request"],
            "project_value_gbp": enquiry.get("project_value_gbp"),
            "source": enquiry["source"],
            "enquiry_ref": enquiry.get("enquiry_ref"),
            "account_tier": enrichment.get("account_tier"),
            "industry": enrichment.get("industry"),
            "relay_workflow_run_id": workflow_run_id,
        },
    }


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form not supported; fall back to normal backoff


def classify_response(response: httpx.Response) -> None:
    """Raise a classified StepError for any non-success response.

    Retryability table (also in docs/guarantees-and-limitations.md):
      2xx        success
      400/413/422 permanent: the request itself is wrong
      409        permanent: idempotency key reused with a different payload (a bug)
      429        transient, honours Retry-After
      503        transient: the demo CRM's contract is that 503 means rolled back
      other 5xx  ambiguous: the server may have applied the change
      other 4xx  permanent
    """
    status = response.status_code
    if 200 <= status < 300:
        return
    detail = response.text[:300]
    if status == 429 or status == 503:
        raise TransientStepError(
            f"CRM returned {status}",
            details={"status": status, "body": detail},
            retry_after_seconds=_retry_after(response),
        )
    if status >= 500:
        raise AmbiguousStepError(
            f"CRM returned {status}; change may have been applied",
            details={"status": status, "body": detail},
        )
    raise PermanentStepError(
        f"CRM rejected request with {status}", details={"status": status, "body": detail}
    )


def call_crm(
    client: httpx.Client, base_url: str, key: str, payload: dict[str, Any]
) -> httpx.Response:
    try:
        return client.post(
            f"{base_url}/contacts/upsert", json=payload, headers={"Idempotency-Key": key}
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        # The request never left this process: definitely not applied.
        raise TransientStepError(f"CRM unreachable: {type(exc).__name__}") from exc
    except (
        httpx.TimeoutException,
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
    ) as exc:
        # Sent (or partly sent) and no complete answer: it may have been applied.
        raise AmbiguousStepError(f"no complete CRM response: {type(exc).__name__}") from exc


def run(ctx: StepContext) -> dict[str, Any]:
    assert ctx.http is not None and ctx.idempotency_key is not None
    payload = build_payload(ctx.input, ctx.workflow_run_id)
    response = call_crm(ctx.http, ctx.settings.crm_base_url, ctx.idempotency_key, payload)
    classify_response(response)
    body = response.json()
    return {
        "crm": {
            "contact_id": body["contact_id"],
            "operation": body["operation"],
            "mutation_id": body["mutation_id"],
            "replayed": response.headers.get("Idempotent-Replayed") == "true",
            "idempotency_key": ctx.idempotency_key,
        },
        "enquiry_ref": ctx.input["enquiry"].get("enquiry_ref"),
    }
