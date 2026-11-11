"""Data quality checks. The pipeline refuses to publish a report if any check fails."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

MAX_MISSING_SHARE = 0.05  # tolerate up to 5% missing half-hours before failing


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def run_checks(con: duckdb.DuckDBPyConnection) -> list[Check]:
    one = lambda sql: con.execute(sql).fetchone()  # noqa: E731
    results = []

    (rows,) = one("SELECT count(*) FROM stg_intensity")
    results.append(Check("intensity has measured rows", rows > 0, f"{rows} half-hours with an actual value"))

    (bad,) = one(
        "SELECT count(*) FROM raw_intensity WHERE actual < 0 OR actual > 1000 OR forecast < 0 OR forecast > 1000"
    )
    results.append(Check("intensity within 0-1000 gCO2/kWh", bad == 0, f"{bad} out-of-range rows"))

    (bad,) = one("SELECT count(*) FROM raw_intensity WHERE period_end <> period_start + INTERVAL 30 MINUTE")
    results.append(Check("every period is 30 minutes", bad == 0, f"{bad} periods with another length"))

    (bad,) = one("SELECT count(*) FROM raw_generation WHERE perc < 0 OR perc > 100")
    results.append(Check("fuel shares within 0-100%", bad == 0, f"{bad} out-of-range rows"))

    (bad,) = one(
        "SELECT count(*) FROM (SELECT period_start FROM raw_generation GROUP BY 1 HAVING abs(sum(perc) - 100) > 1.5)"
    )
    results.append(Check("fuel shares sum to 100% per period", bad == 0, f"{bad} periods off by more than 1.5 points"))

    first, last, present = one("SELECT min(period_start), max(period_start), count(*) FROM raw_intensity")
    if present:
        expected = int((last - first).total_seconds() // 1800) + 1
        missing = expected - present
        share = missing / expected
        results.append(
            Check(
                "few missing half-hours",
                share <= MAX_MISSING_SHARE,
                f"{missing} of {expected} missing ({share:.1%}; limit {MAX_MISSING_SHARE:.0%})",
            )
        )
    return results
