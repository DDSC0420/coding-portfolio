"""Reading date ranges as CVs write them, and adding up time in work.

Both extractors only *quote* dates; this module is the single place that decides what a
date means, so the rule-based and LLM paths can't disagree.
"""

import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_POINT = rf"(?:{_MONTH}\.?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
_OPEN = r"(?:present|current|now|date|today|ongoing)"
RANGE = re.compile(
    rf"(?<![\w/])(?P<start>{_POINT})\s*(?:-|to|until|till)\s*(?P<end>{_POINT}|{_OPEN})(?![\w/])",
    re.IGNORECASE,
)
YEAR = re.compile(r"(?<![\d/])(?:19[5-9]\d|20\d\d)(?![\d/])")


@dataclass(frozen=True, order=True)
class YearMonth:
    year: int
    month: int | None = None  # None when the CV only gives a year

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}" if self.month else f"{self.year:04d}"


@dataclass(frozen=True)
class DateRange:
    start: YearMonth
    end: YearMonth | None  # None means "to present"

    @property
    def current(self) -> bool:
        return self.end is None

    @property
    def year_only(self) -> bool:
        return self.start.month is None or (self.end is not None and self.end.month is None)


def parse_point(text: str) -> YearMonth | None:
    text = text.strip().rstrip(".").casefold()
    if m := re.fullmatch(r"([a-z]+)\.?\s+(\d{4})", text):
        month = MONTHS.get(m.group(1))
        return YearMonth(int(m.group(2)), month) if month else None
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", text):
        month = int(m.group(1))
        return YearMonth(int(m.group(2)), month) if 1 <= month <= 12 else None
    if re.fullmatch(r"\d{4}", text):
        return YearMonth(int(text))
    return None


def parse_range(text: str) -> DateRange | None:
    """Parse the first date range in ``text``, or None if there isn't a readable one."""
    match = RANGE.search(text)
    if not match:
        return None
    start = parse_point(match.group("start"))
    end_text = match.group("end")
    end = None if re.fullmatch(_OPEN, end_text, re.IGNORECASE) else parse_point(end_text)
    if start is None or (end is None and not re.fullmatch(_OPEN, end_text, re.IGNORECASE)):
        return None
    return DateRange(start, end)


def problems(span: DateRange, today: date) -> list[str]:
    """Reasons a parsed range can't be right. Empty means it's plausible."""
    out = []
    if span.start.year < 1950:
        out.append("starts before 1950")
    if _first(span.start) > _index(today.year, today.month):
        out.append("starts in the future")
    if span.end is not None:
        if _last(span.end) < _first(span.start):
            out.append("ends before it starts")
        elif _last(span.end) > _index(today.year, today.month) and span.end.month is not None:
            out.append("ends in the future")
    return out


def _index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


# A year on its own is read as the middle of that year, so "2019 - 2021" counts as two years.
def _first(point: YearMonth) -> int:
    return _index(point.year, point.month or 7)


def _last(point: YearMonth) -> int:
    return _index(point.year, point.month or 6)


def months_worked(ranges: list[DateRange], today: date) -> int:
    """Total months covered by the ranges, counting overlapping jobs once."""
    now = _index(today.year, today.month)
    spans = sorted((_first(r.start), now if r.end is None else _last(r.end)) for r in ranges)
    total, cur_start, cur_end = 0, None, None
    for start, end in spans:
        if end < start:
            continue
        if cur_end is None or start > cur_end + 1:
            if cur_end is not None:
                total += cur_end - cur_start + 1
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        total += cur_end - cur_start + 1
    return total


def to_years(months: int) -> float:
    """Months to years, one decimal place, halves rounded up."""
    return int(months * 10 / 12 + 0.5) / 10
