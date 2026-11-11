"""Transform: build the staging views and marts by running the SQL models in order."""

from __future__ import annotations

from pathlib import Path

import duckdb

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def transform(con: duckdb.DuckDBPyConnection, sql_dir: Path = SQL_DIR) -> list[str]:
    """Models are numbered files; a model may only depend on lower numbers."""
    ran = []
    for path in sorted(sql_dir.glob("*.sql")):
        con.execute(path.read_text(encoding="utf-8"))
        ran.append(path.stem)
    return ran
