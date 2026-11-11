"""Report: read the marts and publish one self-contained HTML page."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb

from .checks import Check

TEMPLATE = Path(__file__).resolve().parent / "template.html"
PLACEHOLDER = "/*DATA*/null/*END*/"


def build_data(con: duckdb.DuckDBPyConnection, checks: list[Check], source: str, generated_at: datetime) -> dict:
    rows = lambda sql: con.execute(sql).fetchall()  # noqa: E731

    # Chart complete days only; fall back to everything if the range is too short to have one.
    all_days = rows(
        "SELECT day, avg_intensity, min_intensity, max_intensity, periods, is_complete "
        "FROM daily_intensity ORDER BY day"
    )
    complete = [row for row in all_days if row[5]] or all_days
    daily = [{"day": str(d), "avg": a, "min": lo, "max": hi, "periods": n} for d, a, lo, hi, n, _ in complete]
    hourly = [{"hour": h, "avg": a} for h, a, _ in rows("SELECT * FROM hourly_profile ORDER BY hour_utc")]
    mix = [{"fuel": f, "share": s} for f, s, _ in rows("SELECT * FROM fuel_mix ORDER BY avg_share DESC, fuel")]
    if not daily or not hourly:
        raise ValueError("No measured data to report on.")

    first, last, periods, avg = rows(
        "SELECT min(period_start), max(period_start), count(*), round(avg(intensity), 0) FROM stg_intensity"
    )[0]
    (zero_carbon,) = rows(
        """SELECT round(avg(share), 1) FROM (
               SELECT coalesce(sum(perc) FILTER (WHERE is_zero_carbon), 0) AS share
               FROM stg_generation GROUP BY period_start)"""
    )[0]
    return {
        "meta": {
            "source": source,
            "first": first.isoformat(),
            "last": last.isoformat(),
            "periods": periods,
            "generated_at": generated_at.strftime("%Y-%m-%d %H:%M"),
        },
        "summary": {
            "avg_intensity": int(avg),
            "zero_carbon_share": zero_carbon if zero_carbon is not None else 0,
            "cleanest_hour": min(hourly, key=lambda r: r["avg"]),
            "dirtiest_hour": max(hourly, key=lambda r: r["avg"]),
            "best_day": min(daily, key=lambda r: r["avg"]),
            "worst_day": max(daily, key=lambda r: r["avg"]),
        },
        "daily": daily,
        "hourly": hourly,
        "mix": mix,
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
    }


def render(data: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise RuntimeError("Report template is missing its data placeholder.")
    # "</" is escaped so no value can ever close the <script> block it is embedded in.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, payload)


def write_report(data: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.html"
    path.write_text(render(data), encoding="utf-8")
    return path
