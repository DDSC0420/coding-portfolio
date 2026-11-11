import json
import re
from datetime import datetime, timedelta

import duckdb
import pytest

from gridwatch import pipeline
from gridwatch.__main__ import main
from gridwatch.load import parse_ts
from gridwatch.pipeline import QualityError, plan_window, run
from gridwatch.report import render

from .conftest import make_generation, make_intensity

NOW = datetime(2026, 3, 15)
URL = re.compile(r"/(intensity|generation)/(.+)/(.+)$")


class FakeApi:
    """Answers like the real API: returns the half-hours inside the requested range."""

    def __init__(self, days=20, start=datetime(2026, 2, 25)):
        self.intensity, self.generation = make_intensity(days * 48, start), make_generation(days * 48, start)
        self.calls = []

    def __call__(self, url):
        kind, lower, upper = URL.search(url).groups()
        self.calls.append((kind, lower, upper))
        source = self.intensity if kind == "intensity" else self.generation
        return {"data": [r for r in source if parse_ts(lower) <= parse_ts(r["from"]) < parse_ts(upper)]}


def go(tmp_path, api, now=NOW, **kwargs):
    return run(tmp_path / "grid.duckdb", tmp_path / "site", fetch=api, now=lambda: now, say=lambda _: None, **kwargs)


def report_data(path):
    embedded = re.search(r"const DATA = (.*?);\n", path.read_text(encoding="utf-8")).group(1)
    return json.loads(embedded.replace("<\\/", "</"))


def test_first_run_fetches_the_default_window_and_publishes_a_report(tmp_path):
    api = FakeApi()
    result = go(tmp_path, api)
    assert result.window == (NOW - timedelta(days=14), NOW)
    assert result.loaded["intensity_rows"] == 14 * 48
    data = report_data(result.report)
    assert data["meta"]["source"] == "live" and data["meta"]["periods"] == 14 * 48
    assert len(data["daily"]) == 14 and len(data["hourly"]) == 24 and len(data["mix"]) == 9
    assert data["summary"]["zero_carbon_share"] == 51.0
    assert all(c["passed"] for c in data["checks"])


def test_partial_first_and_last_days_are_kept_off_the_daily_chart(tmp_path):
    result = go(tmp_path, FakeApi(), now=NOW + timedelta(hours=9))  # window runs 09:00 to 09:00
    days = [row["day"] for row in report_data(result.report)["daily"]]
    assert len(days) == 13 and days[0] == "2026-03-02" and days[-1] == "2026-03-14"


def test_second_run_is_incremental(tmp_path):
    api = FakeApi()
    go(tmp_path, api)
    api.calls.clear()
    later = NOW + timedelta(days=2)
    result = go(tmp_path, api, now=later)
    # Only the new two days plus a one-day overlap: one request per endpoint, not a full reload.
    assert result.window == (NOW - timedelta(minutes=30) - timedelta(days=1), later)
    assert [c[0] for c in api.calls] == ["intensity", "generation"]
    con = duckdb.connect(str(tmp_path / "grid.duckdb"))
    counts = con.execute("SELECT count(*), count(DISTINCT period_start) FROM raw_intensity").fetchone()
    assert counts == (16 * 48, 16 * 48)
    con.close()


def test_plan_window_rules():
    assert plan_window(None, NOW, None) == (NOW - timedelta(days=14), NOW)
    assert plan_window(NOW - timedelta(days=3), NOW, 30) == (NOW - timedelta(days=30), NOW)  # --days forces a backfill
    assert plan_window(NOW - timedelta(days=3), NOW, None) == (NOW - timedelta(days=4), NOW)


def test_failed_quality_check_blocks_the_report(tmp_path):
    api = FakeApi()
    api.generation[10 * 48]["generationmix"][0]["perc"] = 95.0  # inside the fetched window
    with pytest.raises(QualityError):
        go(tmp_path, api)
    assert not (tmp_path / "site" / "index.html").exists()


def test_save_raw_writes_what_the_api_returned(tmp_path):
    go(tmp_path, FakeApi(), days=1, save_raw=tmp_path / "raw")
    assert len(json.loads((tmp_path / "raw" / "intensity.json").read_text())) == 48


def test_bundled_sample_runs_offline_and_passes_every_check(tmp_path):
    def no_network(url):
        raise AssertionError("sample mode must not touch the network")

    result = go(tmp_path, no_network, sample=True)
    data = report_data(result.report)
    assert data["meta"]["source"] == "sample" and data["meta"]["periods"] > 300
    assert all(c["passed"] for c in data["checks"])


def test_report_data_cannot_break_out_of_its_script_block():
    html = render({"x": "</script><script>alert(1)</script>"})
    assert "</script><script>alert(1)" not in html
    assert html.count("</script>") == 1


def test_command_line_exit_codes(tmp_path, monkeypatch, capsys):
    args = ["run", "--db", str(tmp_path / "g.duckdb"), "--out", str(tmp_path / "site")]
    assert main([*args, "--sample"]) == 0
    assert "5/5 report" in capsys.readouterr().out

    def broken(*a, **k):
        raise pipeline.extract.ExtractError("API unreachable")

    monkeypatch.setattr("gridwatch.__main__.run", broken)
    assert main(args) == 1
    assert "API unreachable" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main([*args, "--days", "0"])
