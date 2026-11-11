"""Extract: pull half-hourly data from the GB Carbon Intensity API.

The API is free and needs no key. It limits how wide a date range one request
may cover, so long ranges are split into windows, and each request is retried
with exponential backoff because public APIs have bad minutes.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

BASE_URL = "https://api.carbonintensity.org.uk"
KINDS = ("intensity", "generation")
WINDOW_DAYS = 7
TIMEOUT_SECONDS = 30
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

Fetcher = Callable[[str], dict]


class ExtractError(RuntimeError):
    pass


def stamp(moment: datetime) -> str:
    """The API's timestamp format, always UTC."""
    return moment.strftime("%Y-%m-%dT%H:%MZ")


def windows(start: datetime, end: datetime, days: int = WINDOW_DAYS) -> Iterator[tuple[datetime, datetime]]:
    """Split [start, end] into consecutive windows of at most `days` days."""
    if end <= start:
        raise ValueError("end must be after start")
    step = timedelta(days=days)
    cursor = start
    while cursor < end:
        upper = min(cursor + step, end)
        yield cursor, upper
        cursor = upper


def fetch_json(
    url: str,
    retries: int = 4,
    backoff: float = 1.0,
    opener: Callable = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """GET a JSON document. Retries timeouts, connection errors and 429/5xx; fails fast on other 4xx."""
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "gridwatch/1.0"})
    for attempt in range(retries + 1):
        try:
            with opener(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code not in RETRYABLE_STATUS:
                raise ExtractError(f"{url} returned HTTP {err.code}") from err
            reason = f"HTTP {err.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            reason = str(err)
        except ValueError as err:
            raise ExtractError(f"{url} did not return valid JSON") from err
        if attempt < retries:
            sleep(backoff * 2**attempt)
    raise ExtractError(f"{url} failed after {retries + 1} attempts ({reason})")


def fetch_range(kind: str, start: datetime, end: datetime, fetch: Fetcher = fetch_json) -> list[dict]:
    """All half-hourly records of one kind between start and end, as the API returned them."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    records: list[dict] = []
    for lower, upper in windows(start, end):
        payload = fetch(f"{BASE_URL}/{kind}/{stamp(lower)}/{stamp(upper)}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ExtractError(f"Unexpected {kind} response shape: no 'data' list")
        records.extend(data)
    return records
