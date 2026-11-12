"""RELAY control API. Submits and reads workflows; never executes steps.

uvicorn services.api.app:app --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import Engine, text

from relay.config import engine_settings
from relay.db.session import make_engine
from relay.engine.errors import UnknownWorkflow
from relay.engine.submission import SubmissionConflict, submit_workflow
from relay.http_limits import BodySizeLimit
from relay.workflows.enquiry_v1 import INPUT_SCHEMAS
from relay.workflows.registry import default_registry
from services.api.ops import _APPROVAL_COLUMNS
from services.api.ops import router as ops_router
from services.api.schemas import (
    ApprovalView,
    AttemptView,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    EventPage,
    EventView,
    StepView,
    WorkerView,
    WorkflowDetail,
    WorkflowPage,
    WorkflowSummary,
)

settings = engine_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = make_engine(
        settings.database_url, pool_size=10, application_name="relay-api"
    )
    app.state.registry = default_registry()
    yield
    app.state.engine.dispose()


app = FastAPI(
    title="RELAY control API",
    version="1.0.0",
    description="Submit and inspect workflows. Workers, not this API, execute steps.",
    lifespan=lifespan,
)
app.add_middleware(BodySizeLimit, max_bytes=settings.max_request_bytes)
app.include_router(ops_router)


def _engine(request: Request) -> Engine:
    return request.app.state.engine


# --- health -------------------------------------------------------------------


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready(request: Request) -> dict[str, str]:
    try:
        with _engine(request).connect() as conn:
            conn.execute(text("SELECT 1 FROM workflow_runs LIMIT 1"))
    except Exception as exc:
        raise HTTPException(503, f"database not ready: {type(exc).__name__}") from exc
    return {"status": "ready"}


# --- workflows ----------------------------------------------------------------


@app.post(
    "/workflows",
    status_code=201,
    response_model=CreateWorkflowResponse,
    tags=["workflows"],
    responses={
        200: {"description": "Replay of an earlier submission with this key"},
        409: {"description": "Idempotency-Key reused with a different body"},
    },
)
def create_workflow(
    body: CreateWorkflowRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(min_length=8, max_length=200)] = None,
) -> CreateWorkflowResponse:
    schema = INPUT_SCHEMAS.get(body.workflow_type)
    if schema is None:
        raise HTTPException(422, f"unknown workflow type {body.workflow_type!r}")
    try:
        validated = schema.model_validate(body.input).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
    try:
        result = submit_workflow(
            _engine(request),
            request.app.state.registry,
            workflow_type=body.workflow_type,
            workflow_version=body.workflow_version,
            input=validated,
            submission_key=idempotency_key,
            batch_id=body.batch_id,
        )
    except UnknownWorkflow as exc:
        raise HTTPException(422, str(exc)) from exc
    except SubmissionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if not result.created:
        response.status_code = 200
    return CreateWorkflowResponse(workflow_run_id=result.workflow_run_id, created=result.created)


_SUMMARY_COLUMNS = """
    w.id, w.workflow_type, w.workflow_version, w.status, w.batch_id,
    COALESCE(w.input->>'enquiry_ref', w.input->>'company') AS label,
    (SELECT s.name FROM step_runs s WHERE s.workflow_run_id = w.id
       AND s.status IN ('ready', 'running', 'awaiting_approval')) AS current_step,
    w.created_at, w.updated_at, w.completed_at
"""


@app.get("/workflows", response_model=WorkflowPage, tags=["workflows"])
def list_workflows(
    request: Request,
    status: str | None = None,
    batch_id: str | None = None,
    q: Annotated[
        str | None, Query(max_length=100, description="Search ref, email, company")
    ] = None,
    sort: Literal["created_desc", "created_asc", "updated_desc"] = "created_desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> WorkflowPage:
    where, params = ["TRUE"], {"limit": limit, "offset": offset}
    if status:
        where.append("w.status = :status")
        params["status"] = status
    if batch_id:
        where.append("w.batch_id = :batch_id")
        params["batch_id"] = batch_id
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            "(w.input->>'enquiry_ref' ILIKE :q OR w.input->>'email' ILIKE :q "
            "OR w.input->>'company' ILIKE :q OR w.id::text LIKE :q)"
        )
        params["q"] = pattern
    order = {
        "created_desc": "w.created_at DESC",
        "created_asc": "w.created_at ASC",
        "updated_desc": "w.updated_at DESC",
    }[sort]
    clause = " AND ".join(where)
    with _engine(request).connect() as conn:
        total = conn.execute(
            text(f"SELECT count(*) FROM workflow_runs w WHERE {clause}"), params
        ).scalar_one()
        rows = conn.execute(
            text(
                f"SELECT {_SUMMARY_COLUMNS} FROM workflow_runs w WHERE {clause} "
                f"ORDER BY {order}, w.id LIMIT :limit OFFSET :offset"
            ),
            params,
        ).all()
    return WorkflowPage(
        items=[WorkflowSummary(**r._mapping) for r in rows], total=total, limit=limit, offset=offset
    )


@app.get("/workflows/{run_id}", response_model=WorkflowDetail, tags=["workflows"])
def get_workflow(run_id: UUID, request: Request) -> WorkflowDetail:
    with _engine(request).connect() as conn:
        run = conn.execute(
            text(
                f"SELECT {_SUMMARY_COLUMNS}, w.input, w.result, w.failure, now() AS server_time "
                "FROM workflow_runs w WHERE w.id = :id"
            ),
            {"id": run_id},
        ).first()
        if run is None:
            raise HTTPException(404, "workflow run not found")
        steps = conn.execute(
            text("SELECT * FROM step_runs WHERE workflow_run_id = :id ORDER BY position"),
            {"id": run_id},
        ).all()
        attempts = conn.execute(
            text(
                "SELECT a.* FROM step_attempts a JOIN step_runs s ON s.id = a.step_run_id "
                "WHERE s.workflow_run_id = :id ORDER BY a.step_run_id, a.attempt_number"
            ),
            {"id": run_id},
        ).all()
        approval = conn.execute(
            text(
                f"SELECT {_APPROVAL_COLUMNS} FROM approvals a "
                "JOIN workflow_runs w ON w.id = a.workflow_run_id "
                "WHERE a.workflow_run_id = :id ORDER BY a.id DESC LIMIT 1"
            ),
            {"id": run_id},
        ).first()
    by_step: dict[UUID, list[AttemptView]] = {}
    for a in attempts:
        by_step.setdefault(a.step_run_id, []).append(AttemptView(**a._mapping))
    step_views = [
        StepView(
            **{k: v for k, v in s._mapping.items() if k in StepView.model_fields},
            attempts=by_step.get(s.id, []),
        )
        for s in steps
    ]
    return WorkflowDetail(
        **run._mapping,
        steps=step_views,
        approval=ApprovalView(**approval._mapping) if approval else None,
    )


@app.get("/workflows/{run_id}/events", response_model=EventPage, tags=["workflows"])
def list_events(
    run_id: UUID,
    request: Request,
    after: Annotated[int, Query(ge=0, description="Cursor: return events with id > after")] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> EventPage:
    with _engine(request).connect() as conn:
        if (
            conn.execute(text("SELECT 1 FROM workflow_runs WHERE id = :id"), {"id": run_id}).first()
            is None
        ):
            raise HTTPException(404, "workflow run not found")
        rows = conn.execute(
            text(
                "SELECT e.id, e.type, e.step_run_id, s.name AS step, e.created_at, e.details "
                "FROM workflow_events e LEFT JOIN step_runs s ON s.id = e.step_run_id "
                "WHERE e.workflow_run_id = :id AND e.id > :after ORDER BY e.id LIMIT :n"
            ),
            {"id": run_id, "after": after, "n": limit + 1},
        ).all()
    more = len(rows) > limit
    items = [EventView(**r._mapping) for r in rows[:limit]]
    return EventPage(items=items, next_cursor=items[-1].id if more else None)


# --- workers ------------------------------------------------------------------


@app.get("/workers", response_model=list[WorkerView], tags=["workers"])
def list_workers(
    request: Request,
    include_history: bool = Query(False, description="Include earlier instances of each worker"),
) -> list[WorkerView]:
    latest_only = (
        ""
        if include_history
        else (
            "WHERE (name, started_at) IN (SELECT name, max(started_at) FROM workers GROUP BY name)"
        )
    )
    with _engine(request).connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT *, EXTRACT(EPOCH FROM now() - last_heartbeat_at)::float AS age,
                       CASE WHEN stopped_at IS NOT NULL THEN 'stopped'
                            WHEN now() - last_heartbeat_at
                                 <= make_interval(secs => :offline_after) THEN 'online'
                            ELSE 'offline' END AS derived_status
                FROM workers {latest_only} ORDER BY name, started_at DESC
                """
            ),
            {"offline_after": settings.worker_offline_after_seconds},
        ).all()
    result: list[WorkerView] = []
    for r in rows:
        data: dict[str, Any] = dict(r._mapping)
        result.append(
            WorkerView(
                heartbeat_age_seconds=data.pop("age"), status=data.pop("derived_status"), **data
            )
        )
    return result
