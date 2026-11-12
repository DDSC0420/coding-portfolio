"""Demo CRM HTTP service. A stand-in for an external system that honours idempotency keys.

    uvicorn services.demo_crm.app:app --port 8100

With CRM_DEMO_MODE=true it also exposes /demo/faults to inject failures
(services/demo_crm/faults.py). Without it, those routes do not exist.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from starlette.types import Receive, Scope, Send

from relay.config import crm_settings
from relay.db.session import make_engine
from relay.http_limits import BodySizeLimit
from services.demo_crm.faults import FaultInjector, FaultSettings
from services.demo_crm.service import ApplyResult, ContactUpsert, IdempotencyConflict, apply_upsert

log = logging.getLogger("relay.demo_crm")
settings = crm_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = make_engine(
        settings.database_url, pool_size=10, application_name="relay-demo-crm"
    )
    if settings.demo_mode:
        log.warning("CRM DEMO MODE: fault injection endpoints are enabled at /demo/faults")
    yield
    app.state.engine.dispose()


app = FastAPI(title="RELAY demo CRM", version="0.1.0", lifespan=lifespan)
app.add_middleware(BodySizeLimit, max_bytes=settings.max_request_bytes)
app.state.faults = FaultInjector()


class DemoConnectionDropped(Exception):
    """Raised mid-response so the server closes the connection (demo fault)."""


class DroppedResponse(Response):
    """Starts a 200 response, sends part of the body, then kills the connection."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json"), (b"content-length", b"4096")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"contact_id": "', "more_body": True})
        raise DemoConnectionDropped("demo fault: connection dropped after commit")


@app.get("/health/live")
def live() -> dict[str, Any]:
    return {"status": "ok", "demo_mode": settings.demo_mode}


@app.get("/health/ready")
def ready(request: Request) -> dict[str, str]:
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM idempotency_keys LIMIT 1"))
    except Exception as exc:
        raise HTTPException(503, f"database not ready: {type(exc).__name__}") from exc
    return {"status": "ready"}


@app.post("/contacts/upsert")
def upsert_contact(
    body: ContactUpsert,
    request: Request,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=200)],
) -> Response:
    fault = request.app.state.faults.decide() if settings.demo_mode else None
    if fault and fault.delay_ms:
        time.sleep(fault.delay_ms / 1000)
    if fault and fault.fail_before_commit:
        headers = {"X-Demo-Fault": "fail_before_commit"}
        if fault.retry_after_seconds is not None:
            headers["Retry-After"] = str(fault.retry_after_seconds)
        return JSONResponse(
            {"detail": "demo fault: unavailable, nothing was applied"},
            status_code=503,
            headers=headers,
        )
    try:
        result: ApplyResult = apply_upsert(request.app.state.engine, idempotency_key, body)
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "Idempotency-Key was already used with a different request payload"
        ) from exc

    # --- the change is committed from here on ---
    if fault and fault.after_commit == "error":
        return JSONResponse(
            {"detail": "demo fault: error after commit"},
            status_code=500,
            headers={"X-Demo-Fault": "error_after_commit"},
        )
    if fault and fault.after_commit == "drop":
        return DroppedResponse()
    if fault and fault.after_commit == "hang":
        time.sleep(fault.hang_ms / 1000)
    headers = {"Idempotent-Replayed": "true" if result.replayed else "false"}
    return JSONResponse(result.body, status_code=result.status_code, headers=headers)


@app.get("/contacts")
def list_contacts(
    request: Request, email: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[dict[str, Any]]:
    sql = "SELECT id, email, name, company, revision, updated_at FROM contacts"
    params: dict[str, Any] = {"limit": limit}
    if email:
        sql += " WHERE email = :email"
        params["email"] = email.lower()
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(text(sql + " ORDER BY updated_at DESC LIMIT :limit"), params)
        return [dict(r._mapping) for r in rows]


@app.get("/mutations/summary")
def mutation_summary(request: Request) -> dict[str, Any]:
    """Counts used by verification: total mutations and any key with more than one."""
    with request.app.state.engine.connect() as conn:
        total = conn.execute(text("SELECT count(*) FROM mutations")).scalar_one()
        dupes = conn.execute(
            text(
                "SELECT idempotency_key, count(*) AS n FROM mutations "
                "GROUP BY idempotency_key HAVING count(*) > 1"
            )
        ).all()
    return {"mutations": total, "keys_with_duplicates": [dict(r._mapping) for r in dupes]}


# --- demo-mode-only fault controls --------------------------------------------

demo = APIRouter(prefix="/demo", tags=["demo faults (CRM_DEMO_MODE only)"])


def _fault_state(request: Request) -> dict[str, Any]:
    injector: FaultInjector = request.app.state.faults
    return {
        "demo_mode": True,
        "settings": injector.get().model_dump(),
        "injected": dict(injector.injected),
    }


@demo.get("/faults")
def get_faults(request: Request) -> dict[str, Any]:
    return _fault_state(request)


@demo.put("/faults")
def put_faults(body: FaultSettings, request: Request) -> dict[str, Any]:
    request.app.state.faults.set(body)
    log.warning("demo faults set: %s", body.model_dump(exclude_defaults=True))
    return _fault_state(request)


@demo.post("/faults/reset")
def reset_faults(request: Request) -> dict[str, Any]:
    request.app.state.faults.reset()
    log.warning("demo faults reset: CRM healthy")
    return _fault_state(request)


if settings.demo_mode:
    app.include_router(demo)
