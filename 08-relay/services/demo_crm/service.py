"""The demo CRM's idempotent contact upsert (transaction T8, docs/architecture.md §6).

Everything below happens in ONE transaction in the CRM database:

1. Claim the idempotency key: INSERT ... ON CONFLICT DO NOTHING. If another
   request with the same key is in flight, this INSERT waits on the unique
   index until that transaction commits or rolls back.
2. If the key already existed: compare request hashes. Different payload ->
   conflict. Same payload -> return the stored response; apply nothing.
3. Otherwise: upsert the contact, append a mutation row, store the response.

So for one key there is at most one mutation (also enforced by a unique
constraint on mutations.idempotency_key), and every caller with that key and
payload gets the same response.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text

from relay.db.session import to_json
from relay.engine.submission import canonical_hash


class ContactUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    details: dict[str, Any] = Field(default_factory=dict)


class IdempotencyConflict(Exception):
    pass


@dataclass(frozen=True)
class ApplyResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool


def apply_upsert(engine: Engine, key: str, request: ContactUpsert) -> ApplyResult:
    payload = request.model_dump(mode="json")
    request_hash = canonical_hash(payload)

    with engine.begin() as conn:
        claimed = conn.execute(
            text(
                "INSERT INTO idempotency_keys (key, request_hash) VALUES (:key, :hash) "
                "ON CONFLICT (key) DO NOTHING RETURNING key"
            ),
            {"key": key, "hash": request_hash},
        ).first()

        if claimed is None:
            stored = conn.execute(
                text(
                    "SELECT request_hash, response_status, response_body "
                    "FROM idempotency_keys WHERE key = :key"
                ),
                {"key": key},
            ).one()
            if stored.request_hash != request_hash:
                raise IdempotencyConflict(key)
            return ApplyResult(stored.response_status, stored.response_body, replayed=True)

        contact = conn.execute(
            text(
                """
                INSERT INTO contacts (email, name, company, details)
                VALUES (:email, :name, :company, CAST(:details AS jsonb))
                ON CONFLICT (email) DO UPDATE
                SET name = EXCLUDED.name, company = EXCLUDED.company,
                    details = EXCLUDED.details, revision = contacts.revision + 1,
                    updated_at = now()
                RETURNING id, revision
                """
            ),
            {
                "email": request.email.lower(),
                "name": request.name,
                "company": request.company,
                "details": to_json(request.details),
            },
        ).one()
        operation = "created" if contact.revision == 1 else "updated"
        mutation_id = conn.execute(
            text(
                "INSERT INTO mutations (idempotency_key, contact_id, operation, payload) "
                "VALUES (:key, :contact, :op, CAST(:payload AS jsonb)) RETURNING id"
            ),
            {"key": key, "contact": contact.id, "op": operation, "payload": to_json(payload)},
        ).scalar_one()
        body = {
            "contact_id": str(contact.id),
            "operation": operation,
            "revision": contact.revision,
            "mutation_id": mutation_id,
        }
        conn.execute(
            text(
                "UPDATE idempotency_keys SET response_status = 200, "
                "response_body = CAST(:body AS jsonb), completed_at = now() WHERE key = :key"
            ),
            {"key": key, "body": to_json(body)},
        )
    return ApplyResult(200, body, replayed=False)
