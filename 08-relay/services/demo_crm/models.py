"""Demo CRM schema. Lives in its own database, separate from the engine's.

- contacts: the business data the workflow updates.
- idempotency_keys: one row per logical request, with the stored response.
- mutations: an append-only log of every change actually applied. The unique
  constraint on idempotency_key makes "two mutations for one key" impossible,
  and the verifier reads this table to prove it.
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
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from relay.db.models import NAMING


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # A committed key always carries its response (it is written in the
        # same transaction that inserts the key).
        CheckConstraint(
            "(response_status IS NULL) = (completed_at IS NULL)", name="response_iff_completed"
        ),
    )


class Mutation(Base):
    __tablename__ = "mutations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(ForeignKey("idempotency_keys.key"), unique=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"))
    operation: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("operation IN ('created','updated')", name="operation_valid"),
        Index(None, "contact_id"),
    )
