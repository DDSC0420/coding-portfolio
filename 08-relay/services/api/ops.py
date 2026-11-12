"""Operations endpoints: approvals, the live overview the dashboard polls, metrics, the event
feed, demo batches and a pass-through to the demo CRM's fault controls.

Everything here reads engine state or calls an engine transaction; nothing executes steps.
"""

import json
import re
import secrets
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import Connection, Engine, text

from relay.config import engine_settings
from relay.engine.approvals import ApprovalConflict, ApprovalNotFound, decide
from relay.engine.submission import submit_workflow
from services.api.schemas import (
    ApprovalView,
    BatchRequest,
    BatchResponse,
    DecisionRequest,
    DecisionResponse,
    EventPage,
    EventView,
    LeaseView,
    Metrics,
    Overview,
    WorkflowDefinitionView,
)

settings = engine_settings()
router = APIRouter()
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "enquiries"
_MONEY = re.compile(r"£\s?\d[\d,]*(?:\.\d+)?\s*[kKmM]?")


def _engine(request: Request) -> Engine:
    return request.app.state.engine


# --- approvals ----------------------------------------------------------------

_APPROVAL_COLUMNS = """
    a.id, a.workflow_run_id, a.step_run_id, a.status, a.reason, a.requested_at,
    a.decided_at, a.decided_by, a.comment,
    COALESCE(w.input->>'enquiry_ref', w.input->>'company') AS label,
    (a.proposed_output->'enquiry'->>'company') AS company,
    (a.proposed_output->'enquiry'->>'project_value_gbp')::bigint AS project_value_gbp
"""


def _approvals(conn: Connection, where: str, params: dict[str, Any]) -> list[ApprovalView]:
    rows = conn.execute(
        text(
            f"SELECT {_APPROVAL_COLUMNS} FROM approvals a "
            f"JOIN workflow_runs w ON w.id = a.workflow_run_id WHERE {where} "
            "ORDER BY a.requested_at, a.id LIMIT :limit"
        ),
        params,
    ).all()
    return [ApprovalView(**r._mapping) for r in rows]


@router.get("/approvals", response_model=list[ApprovalView], tags=["approvals"])
def list_approvals(
    request: Request,
    status: Literal["pending", "approved", "rejected"] | None = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ApprovalView]:
    where = "a.status = :status" if status else "TRUE"
    with _engine(request).connect() as conn:
        return _approvals(conn, where, {"status": status, "limit": limit})


def _decide(
    run_id: UUID, body: DecisionRequest, request: Request, response: Response, approve: bool
):
    try:
        d = decide(
            _engine(request),
            run_id,
            approve=approve,
            decided_by=body.reviewer,
            comment=body.comment,
        )
    except ApprovalNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if d.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return DecisionResponse(
        workflow_run_id=d.workflow_run_id,
        status=d.status,
        decided_by=d.decided_by,
        decided_at=d.decided_at,
        comment=d.comment,
        replayed=d.replayed,
    )


@router.post(
    "/workflows/{run_id}/approve",
    response_model=DecisionResponse,
    tags=["approvals"],
    responses={
        404: {"description": "No approval request"},
        409: {"description": "Already rejected"},
    },
)
def approve(run_id: UUID, body: DecisionRequest, request: Request, response: Response):
    """Approve: the paused step succeeds and the next step becomes eligible, in one transaction.
    Repeating an approval returns the original decision."""
    return _decide(run_id, body, request, response, True)


@router.post(
    "/workflows/{run_id}/reject",
    response_model=DecisionResponse,
    tags=["approvals"],
    responses={
        404: {"description": "No approval request"},
        409: {"description": "Already approved"},
    },
)
def reject(run_id: UUID, body: DecisionRequest, request: Request, response: Response):
    """Reject: the workflow ends as `rejected`; the CRM is never called."""
    return _decide(run_id, body, request, response, False)


# --- metrics, leases, events --------------------------------------------------

_STATUSES = ("running", "awaiting_approval", "succeeded", "failed", "outcome_unknown", "rejected")


def _metrics(conn: Connection) -> Metrics:
    by_status = dict.fromkeys(_STATUSES, 0)
    for r in conn.execute(text("SELECT status, count(*) AS n FROM workflow_runs GROUP BY status")):
        by_status[r.status] = r.n
    row = conn.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM workflow_runs
                 WHERE completed_at > now() - interval '60 seconds') AS completed_last_minute,
              (SELECT count(*) FROM step_runs WHERE status = 'ready'
                 AND next_eligible_at <= now()) AS steps_ready,
              (SELECT count(*) FROM step_runs WHERE status = 'ready'
                 AND next_eligible_at > now()) AS steps_waiting_retry,
              (SELECT count(*) FROM step_runs WHERE status = 'running') AS steps_running,
              (SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(next_eligible_at)), 0)
                 FROM step_runs WHERE status = 'ready' AND next_eligible_at <= now())
                 AS oldest_ready_age_seconds,
              (SELECT count(*) FROM step_attempts) AS attempts_total,
              (SELECT count(*) FROM step_attempts WHERE attempt_number > 1) AS retries_total,
              (SELECT count(*) FROM step_attempts WHERE outcome = 'lease_expired')
                 AS leases_expired_total,
              (SELECT count(*) FROM workflow_events WHERE type = 'step.stale_write_rejected')
                 AS stale_writes_rejected_total,
              (SELECT percentile_cont(0.5) WITHIN GROUP
                 (ORDER BY EXTRACT(EPOCH FROM completed_at - created_at))
                 FROM workflow_runs WHERE status = 'succeeded'
                 AND completed_at > now() - interval '10 minutes') AS p50_seconds,
              (SELECT percentile_cont(0.95) WITHIN GROUP
                 (ORDER BY EXTRACT(EPOCH FROM completed_at - created_at))
                 FROM workflow_runs WHERE status = 'succeeded'
                 AND completed_at > now() - interval '10 minutes') AS p95_seconds
            """
        )
    ).one()
    return Metrics(workflows=by_status, total=sum(by_status.values()), **row._mapping)


@router.get("/metrics", response_model=Metrics, tags=["operations"])
def metrics(request: Request) -> Metrics:
    """Counts straight from the tables (JSON, not Prometheus format)."""
    with _engine(request).connect() as conn:
        return _metrics(conn)


def _leases(conn: Connection) -> list[LeaseView]:
    rows = conn.execute(
        text(
            """
            SELECT s.id AS step_run_id, s.workflow_run_id, s.name AS step, s.lease_owner,
                   s.lease_generation, s.lease_expires_at, s.attempt_count, s.max_attempts,
                   EXTRACT(EPOCH FROM s.lease_expires_at - now())::float AS seconds_left,
                   COALESCE(w.input->>'enquiry_ref', w.input->>'company') AS label
            FROM step_runs s JOIN workflow_runs w ON w.id = s.workflow_run_id
            WHERE s.status = 'running'
            ORDER BY s.lease_owner, s.lease_expires_at LIMIT 200
            """
        )
    ).all()
    return [LeaseView(**r._mapping) for r in rows]


_EVENT_SQL = """
    SELECT e.id, e.type, e.workflow_run_id, e.step_run_id, s.name AS step, e.created_at,
           e.details, COALESCE(w.input->>'enquiry_ref', w.input->>'company') AS label
    FROM workflow_events e
    JOIN workflow_runs w ON w.id = e.workflow_run_id
    LEFT JOIN step_runs s ON s.id = e.step_run_id
"""


@router.get("/events", response_model=EventPage, tags=["operations"])
def event_feed(
    request: Request,
    after: Annotated[int | None, Query(ge=0, description="Return events with id > after")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    type_prefix: Annotated[str | None, Query(max_length=40, pattern=r"^[a-z_.]+$")] = None,
) -> EventPage:
    """All workflows' events. Without `after`, the newest `limit` events (oldest first)."""
    extra = " AND e.type LIKE :prefix" if type_prefix else ""
    params: dict[str, Any] = {"n": limit + 1, "prefix": f"{type_prefix}%"}
    with _engine(request).connect() as conn:
        if after is None:
            rows = conn.execute(
                text(f"{_EVENT_SQL} WHERE TRUE{extra} ORDER BY e.id DESC LIMIT :n"), params
            ).all()[:limit][::-1]
            more = False
        else:
            rows = conn.execute(
                text(f"{_EVENT_SQL} WHERE e.id > :after{extra} ORDER BY e.id LIMIT :n"),
                {**params, "after": after},
            ).all()
            more = len(rows) > limit
            rows = rows[:limit]
    items = [EventView(**r._mapping) for r in rows]
    return EventPage(items=items, next_cursor=items[-1].id if more else None)


@router.get("/overview", response_model=Overview, tags=["operations"])
def overview(request: Request) -> Overview:
    """One consistent snapshot for the dashboard: metrics, live leases, pending approvals."""
    with _engine(request).connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return Overview(
            server_time=conn.execute(text("SELECT now()")).scalar_one(),
            lease_seconds=settings.lease_seconds,
            metrics=_metrics(conn),
            leases=_leases(conn),
            pending_approvals=_approvals(conn, "a.status = 'pending'", {"limit": 50}),
        )


@router.get(
    "/workflow-definitions", response_model=list[WorkflowDefinitionView], tags=["operations"]
)
def definitions(request: Request) -> list[WorkflowDefinitionView]:
    registry = request.app.state.registry
    return [
        WorkflowDefinitionView(
            type=d.type,
            version=d.version,
            description=d.description,
            steps=[s.name for s in d.steps],
        )
        for d in registry.definitions()
    ]


# --- demo helpers -------------------------------------------------------------


def _fixtures(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@router.post("/batches", status_code=201, response_model=BatchResponse, tags=["demo"])
def create_batch(body: BatchRequest, request: Request) -> BatchResponse:
    """Submit a batch of synthetic enquiries from the bundled fixtures.

    `needs_approval` of them are made to hit the approval gate (alternately a high
    project value and an explicit review request); `invalid` of them fail validation.
    """
    total = body.count + body.invalid
    if total > settings.max_batch_size:
        raise HTTPException(422, f"at most {settings.max_batch_size} workflows per batch")
    if body.needs_approval > body.count:
        raise HTTPException(422, "needs_approval cannot exceed count")
    batch_id = body.batch_id or f"batch-{secrets.token_hex(3)}"
    golden, invalid = _fixtures("golden.json"), _fixtures("invalid.json")
    enquiries: list[dict[str, Any]] = []
    for i in range(body.count):
        e = dict(golden[i % len(golden)])
        if i < body.needs_approval:
            if i % 2 == 0 and _MONEY.search(e["message"]):
                e["message"] = _MONEY.sub(f"£{120_000 + 5_000 * i:,}", e["message"], count=1)
            else:
                e["approval_required"] = True
        enquiries.append(e)
    enquiries += [dict(invalid[i % len(invalid)]) for i in range(body.invalid)]
    ids: list[UUID] = []
    for n, e in enumerate(enquiries):
        e["enquiry_ref"] = f"{batch_id}-{n + 1:03d}"
        result = submit_workflow(
            _engine(request),
            request.app.state.registry,
            workflow_type="enquiry",
            workflow_version=body.workflow_version,
            input=e,
            submission_key=f"{batch_id}:{n}",
            batch_id=batch_id,
        )
        ids.append(result.workflow_run_id)
    return BatchResponse(batch_id=batch_id, submitted=len(ids), workflow_run_ids=ids)


def _crm(method: str, path: str, body: Any = None) -> Any:
    try:
        r = httpx.request(method, f"{settings.crm_base_url}{path}", json=body, timeout=5)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"demo CRM unreachable: {type(exc).__name__}") from exc
    if r.status_code == 404:
        raise HTTPException(
            404, "CRM fault controls are off (start the CRM with CRM_DEMO_MODE=true)"
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:500])
    return r.json()


@router.get("/demo/crm", tags=["demo"])
def crm_state() -> dict[str, Any]:
    """The demo CRM's mutation counts and, in demo mode, its fault settings."""
    summary = _crm("GET", "/mutations/summary")
    try:
        faults = _crm("GET", "/demo/faults")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        faults = {"demo_mode": False}
    return {**summary, "faults": faults}


@router.put("/demo/crm/faults", tags=["demo"])
def set_crm_faults(body: dict[str, Any]) -> dict[str, Any]:
    return _crm("PUT", "/demo/faults", body)


@router.post("/demo/crm/faults/reset", tags=["demo"])
def reset_crm_faults() -> dict[str, Any]:
    return _crm("POST", "/demo/faults/reset")
