"""Load: land raw API records in DuckDB, unchanged in meaning.

Loads are idempotent. Re-running the same window, or overlapping windows,
never creates duplicates: each half-hour (and each half-hour + fuel) is a
primary key and a repeat load updates the row in place.
"""

from __future__ import annotations

from datetime import datetime

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_intensity (
    period_start TIMESTAMP PRIMARY KEY,
    period_end   TIMESTAMP NOT NULL,
    forecast     INTEGER,
    actual       INTEGER,
    band         VARCHAR,
    loaded_at    TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_generation (
    period_start TIMESTAMP NOT NULL,
    fuel         VARCHAR   NOT NULL,
    perc         DOUBLE    NOT NULL,
    loaded_at    TIMESTAMP NOT NULL,
    PRIMARY KEY (period_start, fuel)
);
"""

# A later fetch may fill in an "actual" that was missing, but must never erase one.
UPSERT_INTENSITY = """
INSERT INTO raw_intensity VALUES {values}
ON CONFLICT (period_start) DO UPDATE SET
    period_end = excluded.period_end,
    forecast   = COALESCE(excluded.forecast, raw_intensity.forecast),
    actual     = COALESCE(excluded.actual, raw_intensity.actual),
    band       = COALESCE(excluded.band, raw_intensity.band),
    loaded_at  = excluded.loaded_at
"""
UPSERT_GENERATION = """
INSERT INTO raw_generation VALUES {values}
ON CONFLICT (period_start, fuel) DO UPDATE SET perc = excluded.perc, loaded_at = excluded.loaded_at
"""


BATCH_ROWS = 500


class LoadError(ValueError):
    pass


def parse_ts(value: object) -> datetime:
    if not isinstance(value, str):
        raise LoadError(f"Expected a timestamp string, got {value!r}")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%MZ")
    except ValueError as err:
        raise LoadError(f"Unrecognised timestamp {value!r}") from err


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LoadError(f"{field} must be a number or null, got {value!r}")
    return int(round(value))


def intensity_rows(records: list[dict], loaded_at: datetime) -> list[tuple]:
    rows = []
    for record in records:
        try:
            body = record["intensity"] or {}
            rows.append(
                (
                    parse_ts(record["from"]),
                    parse_ts(record["to"]),
                    _optional_int(body.get("forecast"), "forecast"),
                    _optional_int(body.get("actual"), "actual"),
                    body.get("index"),
                    loaded_at,
                )
            )
        except (KeyError, TypeError, AttributeError) as err:
            raise LoadError(f"Malformed intensity record: {record!r}") from err
    return rows


def generation_rows(records: list[dict], loaded_at: datetime) -> list[tuple]:
    rows = []
    for record in records:
        try:
            start = parse_ts(record["from"])
            for part in record["generationmix"]:
                perc = part["perc"]
                if isinstance(perc, bool) or not isinstance(perc, (int, float)):
                    raise LoadError(f"perc must be a number, got {perc!r}")
                rows.append((start, str(part["fuel"]).lower(), float(perc), loaded_at))
        except (KeyError, TypeError) as err:
            raise LoadError(f"Malformed generation record: {record!r}") from err
    return rows


def _upsert(con: duckdb.DuckDBPyConnection, statement: str, rows: list[tuple], key_width: int) -> None:
    """Multi-row upserts in batches: far faster than one statement per row.

    A single statement may not touch the same key twice, so repeats inside the
    batch are collapsed first (the last one wins, as it would row by row).
    """
    unique = list({row[:key_width]: row for row in rows}.values())
    for offset in range(0, len(unique), BATCH_ROWS):
        batch = unique[offset : offset + BATCH_ROWS]
        one = "(" + ", ".join("?" * len(batch[0])) + ")"
        con.execute(statement.format(values=", ".join([one] * len(batch))), [v for row in batch for v in row])


def load(con: duckdb.DuckDBPyConnection, intensity: list[dict], generation: list[dict], loaded_at: datetime) -> dict:
    """Validate everything first, then write both tables in one transaction."""
    i_rows = intensity_rows(intensity, loaded_at)
    g_rows = generation_rows(generation, loaded_at)
    con.execute(SCHEMA)
    con.execute("BEGIN")
    try:
        _upsert(con, UPSERT_INTENSITY, i_rows, key_width=1)
        _upsert(con, UPSERT_GENERATION, g_rows, key_width=2)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"intensity_rows": len(i_rows), "generation_rows": len(g_rows)}


def latest_period(con: duckdb.DuckDBPyConnection) -> datetime | None:
    con.execute(SCHEMA)
    return con.execute("SELECT max(period_start) FROM raw_intensity").fetchone()[0]
