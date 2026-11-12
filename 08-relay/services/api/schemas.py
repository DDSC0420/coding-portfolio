"""Typed request and response models for the control API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_type: str = Field(default="enquiry", max_length=50)
    workflow_version: int | None = Field(
        default=None, ge=1, description="Omit for the latest version"
    )
    input: dict[str, Any]
    batch_id: str | None = Field(default=None, max_length=100)


class CreateWorkflowResponse(BaseModel):
    workflow_run_id: UUID
    created: bool = Field(description="False if this Idempotency-Key was seen before")


class AttemptView(BaseModel):
    attempt_number: int
    worker_id: str
    lease_generation: int
    outcome: str
    error_class: str | None
    error_summary: str | None
    started_at: datetime
    finished_at: datetime | None


class StepView(BaseModel):
    id: UUID
    name: str
    position: int
    status: str
    attempt_count: int
    max_attempts: int
    next_eligible_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    lease_generation: int
    idempotency_key: str | None
    error_class: str | None
    error_summary: str | None
    started_at: datetime | None
    finished_at: datetime | None
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    attempts: list[AttemptView]


class WorkflowSummary(BaseModel):
    id: UUID
    workflow_type: str
    workflow_version: int
    status: str
    batch_id: str | None
    label: str | None = Field(description="enquiry_ref, or the company name if none")
    current_step: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ApprovalView(BaseModel):
    id: int
    workflow_run_id: UUID
    step_run_id: UUID
    status: Literal["pending", "approved", "rejected"]
    reason: str
    requested_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    comment: str | None
    label: str | None = None
    company: str | None = None
    project_value_gbp: int | None = None


class WorkflowDetail(WorkflowSummary):
    approval: ApprovalView | None = None
    input: dict[str, Any]
    result: dict[str, Any] | None
    failure: dict[str, Any] | None
    steps: list[StepView]
    server_time: datetime = Field(
        description="Database time; use it for countdowns, not the browser clock"
    )


class WorkflowPage(BaseModel):
    items: list[WorkflowSummary]
    total: int
    limit: int
    offset: int


class EventView(BaseModel):
    id: int
    type: str
    workflow_run_id: UUID | None = None
    label: str | None = None
    step_run_id: UUID | None
    step: str | None
    created_at: datetime
    details: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventView]
    next_cursor: int | None = Field(
        description="Pass as ?after= to continue; null when no more events"
    )


class WorkerView(BaseModel):
    id: str
    name: str
    hostname: str
    pid: int
    started_at: datetime
    last_heartbeat_at: datetime
    stopped_at: datetime | None
    heartbeat_age_seconds: float
    status: Literal["online", "offline", "stopped"]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A label, not an identity: there is no authentication (see docs/guarantees-and-limitations.md).
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str | None = Field(default=None, max_length=1000)


class DecisionResponse(BaseModel):
    workflow_run_id: UUID
    status: Literal["approved", "rejected"]
    decided_by: str
    decided_at: datetime
    comment: str | None
    replayed: bool = Field(description="True if this repeated an earlier identical decision")


class Metrics(BaseModel):
    workflows: dict[str, int] = Field(description="Workflow runs by status")
    total: int
    completed_last_minute: int
    steps_ready: int
    steps_waiting_retry: int
    steps_running: int
    oldest_ready_age_seconds: float
    attempts_total: int
    retries_total: int
    leases_expired_total: int
    stale_writes_rejected_total: int
    p50_seconds: float | None = Field(description="Median submit-to-success, last 10 minutes")
    p95_seconds: float | None


class LeaseView(BaseModel):
    step_run_id: UUID
    workflow_run_id: UUID
    label: str | None
    step: str
    lease_owner: str
    lease_generation: int
    lease_expires_at: datetime
    seconds_left: float
    attempt_count: int
    max_attempts: int


class Overview(BaseModel):
    server_time: datetime
    lease_seconds: float
    metrics: Metrics
    leases: list[LeaseView]
    pending_approvals: list[ApprovalView]


class WorkflowDefinitionView(BaseModel):
    type: str
    version: int
    description: str
    steps: list[str]


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=20, ge=0, le=500, description="Valid enquiries")
    invalid: int = Field(default=0, ge=0, le=100, description="Enquiries that fail validation")
    needs_approval: int = Field(default=0, ge=0, le=500, description="How many of `count` pause")
    workflow_version: int | None = Field(default=None, ge=1)
    batch_id: str | None = Field(default=None, max_length=60, pattern=r"^[A-Za-z0-9_.-]+$")


class BatchResponse(BaseModel):
    batch_id: str
    submitted: int
    workflow_run_ids: list[UUID]
