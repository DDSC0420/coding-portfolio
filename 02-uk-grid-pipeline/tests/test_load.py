from datetime import datetime

import pytest

from gridwatch.load import LoadError, generation_rows, intensity_rows, latest_period, load, parse_ts

from .conftest import FUELS, START, make_generation, make_intensity

NOW = datetime(2026, 3, 10)


def count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_records_become_typed_rows():
    rows = intensity_rows(make_intensity(1), NOW)
    assert rows == [(START, datetime(2026, 3, 1, 0, 30), 153, 150, "moderate", NOW)]
    assert len(generation_rows(make_generation(1), NOW)) == len(FUELS)


def test_loading_the_same_data_twice_changes_nothing(con):
    intensity, generation = make_intensity(96), make_generation(96)
    first = load(con, intensity, generation, NOW)
    load(con, intensity, generation, NOW)
    assert first == {"intensity_rows": 96, "generation_rows": 96 * len(FUELS)}
    assert count(con, "raw_intensity") == 96
    assert count(con, "raw_generation") == 96 * len(FUELS)


def test_overlapping_windows_do_not_duplicate(con):
    load(con, make_intensity(48), make_generation(48), NOW)
    load(con, make_intensity(48, start=datetime(2026, 3, 1, 12)), [], NOW)
    assert count(con, "raw_intensity") == 72  # 24h + 12h of new periods


def test_a_late_actual_value_fills_in(con):
    load(con, make_intensity(2, actual=False), [], NOW)
    assert con.execute("SELECT count(actual) FROM raw_intensity").fetchone()[0] == 0
    load(con, make_intensity(2, actual=True), [], NOW)
    assert con.execute("SELECT count(actual) FROM raw_intensity").fetchone()[0] == 2


def test_a_refetch_never_erases_an_actual_value(con):
    load(con, make_intensity(2, actual=True), [], NOW)
    load(con, make_intensity(2, actual=False), [], NOW)
    assert con.execute("SELECT count(actual) FROM raw_intensity").fetchone()[0] == 2


@pytest.mark.parametrize(
    "record",
    [
        {"to": "2026-03-01T00:30Z", "intensity": {}},
        {"from": "2026-03-01 00:00", "to": "2026-03-01T00:30Z", "intensity": {}},
        {"from": "2026-03-01T00:00Z", "to": "2026-03-01T00:30Z", "intensity": {"actual": "high"}},
        {"from": "2026-03-01T00:00Z", "to": "2026-03-01T00:30Z", "intensity": {"actual": True}},
        {"from": 5, "to": "2026-03-01T00:30Z", "intensity": {}},
        "not a record",
    ],
)
def test_malformed_intensity_is_rejected(record):
    with pytest.raises(LoadError):
        intensity_rows([record], NOW)


@pytest.mark.parametrize(
    "record",
    [
        {"from": "2026-03-01T00:00Z"},
        {"from": "2026-03-01T00:00Z", "generationmix": [{"fuel": "gas"}]},
        {"from": "2026-03-01T00:00Z", "generationmix": [{"fuel": "gas", "perc": "lots"}]},
        {"from": "2026-03-01T00:00Z", "generationmix": None},
    ],
)
def test_malformed_generation_is_rejected(record):
    with pytest.raises(LoadError):
        generation_rows([record], NOW)


def test_a_bad_batch_writes_nothing_at_all(con):
    load(con, make_intensity(4), make_generation(4), NOW)
    bad_generation = make_generation(4, start=datetime(2026, 3, 2)) + [{"from": "garbage"}]
    with pytest.raises(LoadError):
        load(con, make_intensity(4, start=datetime(2026, 3, 2)), bad_generation, NOW)
    assert count(con, "raw_intensity") == 4  # the good half of the bad batch was not written either


def test_latest_period_on_empty_and_loaded_databases(con):
    assert latest_period(con) is None
    load(con, make_intensity(48), [], NOW)
    assert latest_period(con) == datetime(2026, 3, 1, 23, 30)


def test_parse_ts_round_trip():
    assert parse_ts("2026-03-01T23:30Z") == datetime(2026, 3, 1, 23, 30)


def test_a_repeated_key_inside_one_batch_is_collapsed_not_an_error(con):
    doubled = make_intensity(3) + make_intensity(3)
    assert load(con, doubled, make_generation(2) + make_generation(2), NOW)["intensity_rows"] == 6
    assert count(con, "raw_intensity") == 3 and count(con, "raw_generation") == 2 * len(FUELS)


def test_large_loads_span_several_batches(con):
    load(con, make_intensity(1300), make_generation(1300), NOW)
    assert count(con, "raw_intensity") == 1300 and count(con, "raw_generation") == 1300 * len(FUELS)
