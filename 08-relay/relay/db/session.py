"""Database engine construction and small helpers shared by the API and workers.

RELAY uses SQLAlchemy Core connections with explicit transactions
(`with engine.begin() as conn:`) for every state change, so each transaction
boundary from docs/architecture.md §3 is visible in the code as one `with` block.
"""

import json
from typing import Any

from sqlalchemy import Engine, create_engine


def make_engine(url: str, *, pool_size: int = 5, application_name: str = "relay") -> Engine:
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args={
            "application_name": application_name,
            # A statement that runs this long is a bug, not a workload.
            "options": "-c statement_timeout=10000 -c lock_timeout=5000",
        },
    )


def to_json(value: Any) -> str:
    """Serialise a value for a `CAST(:param AS jsonb)` bind parameter."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
