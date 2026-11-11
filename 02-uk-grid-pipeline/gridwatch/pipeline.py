"""The pipeline end to end: extract, load, transform, check, report."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from . import extract, load, transform
from .checks import Check, run_checks
from .report import build_data, write_report

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample"
DEFAULT_DAYS = 14
OVERLAP = timedelta(days=1)  # re-fetch the last day: "actual" values arrive after the forecast


class QualityError(RuntimeError):
    pass


@dataclass
class Result:
    source: str
    loaded: dict
    models: list[str]
    checks: list[Check]
    report: Path | None = None
    window: tuple[datetime, datetime] | None = None
    log: list[str] = field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)


def plan_window(latest: datetime | None, now: datetime, days: int | None) -> tuple[datetime, datetime]:
    """First run: the last `days` days. Later runs: only what's new, plus a one-day overlap."""
    if days is not None or latest is None:
        return now - timedelta(days=days or DEFAULT_DAYS), now
    return min(latest, now) - OVERLAP, now


def read_sample(sample_dir: Path = SAMPLE_DIR) -> tuple[list[dict], list[dict]]:
    intensity = json.loads((sample_dir / "intensity.json").read_text(encoding="utf-8"))
    generation = json.loads((sample_dir / "generation.json").read_text(encoding="utf-8"))
    return intensity, generation


def run(
    db_path: str | Path = "grid.duckdb",
    out_dir: str | Path = "site",
    days: int | None = None,
    sample: bool = False,
    save_raw: str | Path | None = None,
    fetch: extract.Fetcher = extract.fetch_json,
    now: Callable[[], datetime] = utc_now,
    say: Callable[[str], None] = print,
) -> Result:
    con = duckdb.connect(str(db_path))
    try:
        moment = now()
        window = None
        if sample:
            say("1/5 extract   reading bundled sample data (no network)")
            intensity, generation = read_sample()
        else:
            window = plan_window(load.latest_period(con), moment, days)
            say(f"1/5 extract   {extract.stamp(window[0])} to {extract.stamp(window[1])} from the Carbon Intensity API")
            intensity = extract.fetch_range("intensity", *window, fetch=fetch)
            generation = extract.fetch_range("generation", *window, fetch=fetch)
        if save_raw:
            raw_dir = Path(save_raw)
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / "intensity.json").write_text(json.dumps(intensity, separators=(",", ":")), encoding="utf-8")
            (raw_dir / "generation.json").write_text(json.dumps(generation, separators=(",", ":")), encoding="utf-8")

        loaded = load.load(con, intensity, generation, moment)
        say(f"2/5 load      {loaded['intensity_rows']} intensity rows, {loaded['generation_rows']} generation rows")

        models = transform.transform(con)
        say(f"3/5 transform {len(models)} SQL models")

        checks = run_checks(con)
        failed = [c for c in checks if not c.passed]
        say(f"4/5 check     {len(checks) - len(failed)}/{len(checks)} data quality checks passed")
        for check in failed:
            say(f"              FAILED {check.name}: {check.detail}")
        result = Result("sample" if sample else "live", loaded, models, checks, window=window)
        if failed:
            raise QualityError(f"{len(failed)} data quality check(s) failed; report not published.")

        result.report = write_report(build_data(con, checks, result.source, moment), Path(out_dir))
        say(f"5/5 report    {result.report}")
        return result
    finally:
        con.close()
