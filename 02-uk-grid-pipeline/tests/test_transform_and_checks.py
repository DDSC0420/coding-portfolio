from datetime import datetime, timedelta

import pytest

from gridwatch.checks import run_checks
from gridwatch.load import load
from gridwatch.transform import transform

from .conftest import START, make_generation, make_intensity

NOW = datetime(2026, 3, 10)


def record(start, actual, forecast=100):
    return {
        "from": start.strftime("%Y-%m-%dT%H:%MZ"),
        "to": (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%MZ"),
        "intensity": {"forecast": forecast, "actual": actual, "index": "low"},
    }


def build(con, intensity, generation):
    load(con, intensity, generation, NOW)
    transform(con)
    return {c.name: c for c in run_checks(con)}


def test_models_run_in_order(con):
    load(con, make_intensity(2), make_generation(2), NOW)
    assert transform(con) == [
        "10_stg_intensity", "11_stg_generation", "20_daily_intensity",
        "21_hourly_profile", "22_fuel_mix", "23_daily_zero_carbon",
    ]


def test_daily_mart_aggregates_each_day(con):
    day1 = [record(START + timedelta(minutes=30 * n), v) for n, v in enumerate([100, 200, 300])]
    day2 = [record(START + timedelta(days=1), 50)]
    build(con, day1 + day2, [])
    assert con.execute("SELECT * FROM daily_intensity").fetchall() == [
        (START.date(), 200.0, 100, 300, 3),
        ((START + timedelta(days=1)).date(), 50.0, 50, 50, 1),
    ]


def test_forecast_only_periods_are_left_out_of_the_marts(con):
    build(con, [record(START, 100), record(START + timedelta(minutes=30), None, forecast=900)], [])
    assert con.execute("SELECT avg_intensity, periods FROM daily_intensity").fetchall() == [(100.0, 1)]


def test_hourly_profile_groups_by_hour_across_days(con):
    rows = [record(START.replace(hour=9), 100), record(START.replace(hour=9) + timedelta(days=1), 300),
            record(START.replace(hour=17), 400)]
    build(con, rows, [])
    assert con.execute("SELECT hour_utc, avg_intensity, periods FROM hourly_profile").fetchall() == [
        (9, 200.0, 2), (17, 400.0, 1),
    ]


def test_fuel_mix_and_zero_carbon_share(con):
    build(con, make_intensity(48), make_generation(48))
    mix = dict(con.execute("SELECT fuel, avg_share FROM fuel_mix").fetchall())
    assert mix["gas"] == 30.0 and mix["wind"] == 28.0 and len(mix) == 9
    # wind 28 + nuclear 14 + solar 6 + hydro 3; biomass and imports are not counted
    assert con.execute("SELECT zero_carbon_share FROM daily_zero_carbon").fetchall() == [(51.0,)]


def test_good_data_passes_every_check(con):
    checks = build(con, make_intensity(96), make_generation(96))
    assert len(checks) == 6 and all(c.passed for c in checks.values())


def test_empty_data_fails(con):
    assert not build(con, [], [])["intensity has measured rows"].passed


@pytest.mark.parametrize("value", [-1, 1001])
def test_out_of_range_intensity_fails(con, value):
    assert not build(con, [record(START, value)], [])["intensity within 0-1000 gCO2/kWh"].passed


def test_wrong_period_length_fails(con):
    bad = record(START, 100)
    bad["to"] = "2026-03-01T01:00Z"
    assert not build(con, [bad], [])["every period is 30 minutes"].passed


def test_fuel_shares_that_do_not_sum_to_100_fail(con):
    generation = make_generation(1)
    generation[0]["generationmix"][0]["perc"] = 60.0
    checks = build(con, make_intensity(1), generation)
    assert not checks["fuel shares sum to 100% per period"].passed
    assert checks["fuel shares within 0-100%"].passed


def test_out_of_range_fuel_share_fails(con):
    generation = make_generation(1)
    generation[0]["generationmix"][0]["perc"] = 130.0
    assert not build(con, make_intensity(1), generation)["fuel shares within 0-100%"].passed


def test_gaps_are_tolerated_up_to_a_limit(con):
    full = make_intensity(100)
    assert build(con, full[:50] + full[52:], [])["few missing half-hours"].passed  # 2% missing


def test_too_many_gaps_fail(con):
    full = make_intensity(100)
    check = build(con, full[:50] + full[70:], [])["few missing half-hours"]  # 20% missing
    assert not check.passed and "20 of 100" in check.detail
