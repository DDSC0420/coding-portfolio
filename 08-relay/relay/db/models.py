"""Engine database schema.

These ORM models describe the schema for reads and for `alembic check`. The
schema itself is created by the hand-written migration in
migrations/engine/versions/, which also adds the state-transition triggers.
Critical writes (claim, fenced completion) use explicit SQL in relay/engine/.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _ts(**kw: Any) -> Any:
    return mapped_column(DateTime(timezone=True), **kw)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    workflow_type: Mapped[str] = mapped_column(Text)
    workflow_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    submission_key: Mapped[str | None] = mapped_column(Text, unique=True)
    request_hash: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    updated_at: Mapped[datetime] = _ts(server_default=func.now())
    completed_at: Mapped[datetime | None] = _ts()

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','awaiting_approval','succeeded','failed',"
            "'outcome_unknown','rejected')",
            name="status_valid",
        ),
        CheckConstraint(
            "(submission_key IS NULL) = (request_hash IS NULL)", name="submission_key_has_hash"
        ),
        CheckConstraint(
            "(completed_at IS NOT NULL) = (status IN ('succeeded','failed',"
            "'outcome_unknown','rejected'))",
            name="completed_iff_terminal",
        ),
        Index(None, "status", "created_at"),
        Index(None, "batch_id"),
        Index(None, "created_at"),
    )


class StepRun(Base):
    __tablename__ = "step_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer)
    next_eligible_at: Mapped[datetime | None] = _ts()
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = _ts()
    lease_generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    error_class: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = _ts()
    finished_at: Mapped[datetime | None] = _ts()
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    updated_at: Mapped[datetime] = _ts(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("workflow_run_id", "position"),
        UniqueConstraint("workflow_run_id", "name"),
        CheckConstraint(
            "status IN ('pending','ready','running','awaiting_approval','succeeded',"
            "'failed','outcome_unknown','rejected','cancelled')",
            name="status_valid",
        ),
        # I2: running <=> leased
        CheckConstraint(
            "(status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="running_iff_leased",
        ),
        CheckConstraint(
            "status <> 'ready' OR next_eligible_at IS NOT NULL", name="ready_has_eligible_time"
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts", name="attempts_in_budget"
        ),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("lease_generation >= 0", name="generation_non_negative"),
        CheckConstraint(
            "error_class IS NULL OR error_class IN "
            "('permanent','transient','ambiguous','lease_expired')",
            name="error_class_valid",
        ),
        # I1: at most one active step per workflow (sequential workflows)
        Index(
            "uq_step_runs_one_active",
            "workflow_run_id",
            unique=True,
            postgresql_where=text("status IN ('ready','running','awaiting_approval')"),
        ),
        # The claim query's index
        Index(
            "ix_step_runs_claimable",
            "next_eligible_at",
            "id",
            postgresql_where=text("status = 'ready'"),
        ),
        # The recovery sweep's index
        Index(
            "ix_step_runs_leased",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
    )


class StepAttempt(Base):
    __tablename__ = "step_attempts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    step_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("step_runs.id", ondelete="CASCADE"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(Text)
    lease_generation: Mapped[int] = mapped_column(BigInteger)
    outcome: Mapped[str] = mapped_column(Text, server_default=text("'in_progress'"))
    error_class: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _ts(server_default=func.now())
    finished_at: Mapped[datetime | None] = _ts()

    __table_args__ = (
        UniqueConstraint("step_run_id", "attempt_number"),
        UniqueConstraint("step_run_id", "lease_generation"),
        CheckConstraint(
            "outcome IN ('in_progress','succeeded','failed','lease_expired','paused_for_approval')",
            name="outcome_valid",
        ),
        CheckConstraint(
            "(outcome = 'in_progress') = (finished_at IS NULL)", name="finished_iff_closed"
        ),
    )


class Approval(Base):
    """A human decision on a paused step. The step holds no lease while this is pending."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE")
    )
    step_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("step_runs.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    reason: Mapped[str] = mapped_column(Text)
    proposed_output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requested_at: Mapped[datetime] = _ts(server_default=func.now())
    decided_at: Mapped[datetime | None] = _ts()
    decided_by: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected')", name="status_valid"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)", name="decided_iff_not_pending"
        ),
        CheckConstraint("(status = 'pending') = (decided_by IS NULL)", name="decider_iff_decided"),
        Index("ix_approvals_pending", "requested_at", postgresql_where=text("status = 'pending'")),
        Index(None, "workflow_run_id"),
    )


class WorkflowEvent(Base):
    """Transactional audit log. Not the source of truth: state lives in the rows above."""

    __tablename__ = "workflow_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE")
    )
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("step_runs.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _ts(server_default=func.now())

    __table_args__ = (
        Index(None, "workflow_run_id", "id"),
        Index(None, "type", "id"),
    )


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # instance id: name/hex
    name: Mapped[str] = mapped_column(Text)
    hostname: Mapped[str] = mapped_column(Text)
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = _ts(server_default=func.now())
    last_heartbeat_at: Mapped[datetime] = _ts(server_default=func.now())
    stopped_at: Mapped[datetime | None] = _ts()

    __table_args__ = (Index(None, "name", "started_at"),)
