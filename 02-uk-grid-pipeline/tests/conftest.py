"""Synthetic API responses, shaped exactly like the real API's, for tests only."""

import math
from datetime import datetime, timedelta

import duckdb
import pytest

from gridwatch.extract import stamp

FUELS = {"gas": 30.0, "wind": 28.0, "nuclear": 14.0, "imports": 10.0, "biomass": 7.0,
         "solar": 6.0, "hydro": 3.0, "other": 1.5, "coal": 0.5}  # sums to 100
START = datetime(2026, 3, 1)


def make_intensity(periods: int, start: datetime = START, actual: bool = True) -> list[dict]:
    out = []
    for n in range(periods):
        begin = start + timedelta(minutes=30 * n)
        value = int(150 + 60 * math.sin(2 * math.pi * begin.hour / 24) + (n // 48) * 5)
        out.append({
            "from": stamp(begin),
            "to": stamp(begin + timedelta(minutes=30)),
            "intensity": {"forecast": value + 3, "actual": value if actual else None, "index": "moderate"},
        })
    return out


def make_generation(periods: int, start: datetime = START) -> list[dict]:
    return [
        {
            "from": stamp(start + timedelta(minutes=30 * n)),
            "to": stamp(start + timedelta(minutes=30 * (n + 1))),
            "generationmix": [{"fuel": fuel, "perc": perc} for fuel, perc in FUELS.items()],
        }
        for n in range(periods)
    ]


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    yield connection
    connection.close()
