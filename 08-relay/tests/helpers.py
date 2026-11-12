"""Small helpers for reading engine state in tests."""

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text


def fetch_all(engine: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def fetch_one(engine: Engine, sql: str, **params: Any) -> dict[str, Any]:
    rows = fetch_all(engine, sql, **params)
    assert len(rows) == 1, rows
    return rows[0]


def steps_of(engine: Engine, run_id: UUID) -> list[dict[str, Any]]:
    return fetch_all(
        engine, "SELECT * FROM step_runs WHERE workflow_run_id = :id ORDER BY position", id=run_id
    )


def event_types(engine: Engine, run_id: UUID) -> list[str]:
    return [
        r["type"]
        for r in fetch_all(
            engine,
            "SELECT type FROM workflow_events WHERE workflow_run_id = :id ORDER BY id",
            id=run_id,
        )
    ]


def expire_lease(engine: Engine, step_id: UUID) -> None:
    """Move a lease's expiry into the past (what waiting lease_seconds would do)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE step_runs SET lease_expires_at = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": step_id},
        )
