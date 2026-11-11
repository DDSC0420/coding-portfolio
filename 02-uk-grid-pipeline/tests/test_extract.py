import io
import json
import urllib.error
from datetime import datetime

import pytest

from gridwatch import extract
from gridwatch.extract import ExtractError, fetch_json, fetch_range, stamp, windows


def test_stamp_matches_the_api_format():
    assert stamp(datetime(2026, 3, 1, 9, 30)) == "2026-03-01T09:30Z"


def test_windows_cover_the_range_exactly_with_no_gaps_or_overlaps():
    start, end = datetime(2026, 3, 1), datetime(2026, 3, 18, 12)
    parts = list(windows(start, end, days=7))
    assert parts[0][0] == start and parts[-1][1] == end
    assert all(a[1] == b[0] for a, b in zip(parts, parts[1:], strict=False))
    assert [(b - a).days for a, b in parts] == [7, 7, 3]


def test_windows_reject_an_empty_range():
    with pytest.raises(ValueError):
        list(windows(datetime(2026, 3, 2), datetime(2026, 3, 1)))


def test_fetch_range_requests_each_window_and_joins_the_results():
    calls = []

    def fake(url):
        calls.append(url)
        return {"data": [{"n": len(calls)}]}

    records = fetch_range("intensity", datetime(2026, 3, 1), datetime(2026, 3, 15), fetch=fake)
    assert calls == [
        "https://api.carbonintensity.org.uk/intensity/2026-03-01T00:00Z/2026-03-08T00:00Z",
        "https://api.carbonintensity.org.uk/intensity/2026-03-08T00:00Z/2026-03-15T00:00Z",
    ]
    assert records == [{"n": 1}, {"n": 2}]


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"error": "nope"}, []])
def test_fetch_range_rejects_unexpected_shapes(payload):
    with pytest.raises(ExtractError):
        fetch_range("generation", datetime(2026, 3, 1), datetime(2026, 3, 2), fetch=lambda url: payload)


def test_fetch_range_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        fetch_range("weather", datetime(2026, 3, 1), datetime(2026, 3, 2), fetch=lambda url: {"data": []})


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def scripted(*outcomes):
    """An opener that plays back outcomes in order: exceptions are raised, bytes are returned."""
    queue, sleeps = list(outcomes), []

    def opener(request, timeout):
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    return opener, sleeps


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", None, None)


def test_fetch_json_retries_transient_failures_with_exponential_backoff():
    opener, sleeps = scripted(http_error(503), urllib.error.URLError("reset"), TimeoutError(), b'{"data": [1]}')
    assert fetch_json("http://x", opener=opener, sleep=sleeps.append) == {"data": [1]}
    assert sleeps == [1.0, 2.0, 4.0]


def test_fetch_json_fails_fast_on_client_errors():
    opener, sleeps = scripted(http_error(400))
    with pytest.raises(ExtractError, match="HTTP 400"):
        fetch_json("http://x", opener=opener, sleep=sleeps.append)
    assert sleeps == []


def test_fetch_json_gives_up_after_the_retry_budget():
    opener, sleeps = scripted(*[http_error(500)] * 3)
    with pytest.raises(ExtractError, match="after 3 attempts"):
        fetch_json("http://x", retries=2, opener=opener, sleep=sleeps.append)
    assert len(sleeps) == 2


def test_fetch_json_rejects_non_json():
    opener, _ = scripted(b"<html>maintenance</html>")
    with pytest.raises(ExtractError, match="valid JSON"):
        fetch_json("http://x", opener=opener)


def test_fetch_json_sends_a_real_request_object():
    seen = {}

    def opener(request, timeout):
        seen["url"], seen["accept"], seen["timeout"] = request.full_url, request.get_header("Accept"), timeout
        return FakeResponse(json.dumps({"data": []}).encode())

    fetch_json("https://example.test/a", opener=opener)
    assert seen == {"url": "https://example.test/a", "accept": "application/json", "timeout": extract.TIMEOUT_SECONDS}
