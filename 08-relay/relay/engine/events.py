"""Append-only workflow audit events.

Events are written in the same transaction as the state change they describe,
so the log never claims something happened that was rolled back. They are an
audit trail, not the source of truth: no state is rebuilt from them.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from relay.db.session import to_json

_INSERT = text(
    "INSERT INTO workflow_events (workflow_run_id, step_run_id, type, details) "
    "VALUES (:wf, :step, :type, CAST(:details AS jsonb))"
)


def append_event(
    conn: Connection,
    workflow_run_id: UUID | str,
    event_type: str,
    *,
    step_run_id: UUID | str | None = None,
    **details: Any,
) -> None:
    conn.execute(
        _INSERT,
        {
            "wf": workflow_run_id,
            "step": step_run_id,
            "type": event_type,
            "details": to_json(details),
        },
    )
