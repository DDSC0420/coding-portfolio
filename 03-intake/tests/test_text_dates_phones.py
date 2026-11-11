"""The small shared pieces: text cleaning, finding text, dates and phone numbers."""

from datetime import date

import pytest

from intake import dates, phones
from intake.textutil import clean, locate, locate_list

# --- text --------------------------------------------------------------------------------


def test_clean_normalises_dashes_quotes_bullets_and_spaces():
    raw = "Jan 2020 – Present\r\n•  Led a “night” shift  team\n\n\n\nSkills​"
    assert clean(raw) == 'Jan 2020 - Present\n- Led a "night" shift team\n\nSkills'


def test_locate_ignores_case_and_line_breaks_but_respects_words():
    text = "Languages: Python, Bash, Go\nGoogle Cloud"
    span = locate(text, "go")
    assert (span.text, text[span.start:span.end]) == ("Go", "Go")
    assert locate("Senior DevOps\nEngineer", "senior devops engineer").text == "Senior DevOps\nEngineer"
    assert locate("Javascript", "Java") is None


def test_locate_list_prefers_items_that_sit_together():
    text = "Profile: I use Terraform daily.\n" + "x" * 300 + "\nSkills: AWS, Terraform, Docker"
    spans = locate_list(text, ["AWS", "Terraform", "Docker", "Cobol"])
    assert spans[1].start > text.index("Skills")
    assert spans[3] is None


# --- dates -------------------------------------------------------------------------------


@pytest.mark.parametrize("text, start, end", [
    ("Mar 2019 - Present", "2019-03", None),
    ("March 2019 to June 2021", "2019-03", "2021-06"),
    ("Sept 2017 - Aug 2019", "2017-09", "2019-08"),
    ("03/2019 - 06/2021", "2019-03", "2021-06"),
    ("2018 - 2021", "2018", "2021"),
    ("2018-present", "2018", None),
    ("January 2020 until now", "2020-01", None),
    ("Engineer | Acme | Jun. 2018 - Feb. 2021", "2018-06", "2021-02"),
])
def test_date_ranges_people_actually_write(text, start, end):
    span = dates.parse_range(text)
    assert str(span.start) == start
    assert (str(span.end) if span.end else None) == end


@pytest.mark.parametrize("text", ["Marketing 2019 - 2020 budget", "13/2019 - 06/2021", "no dates here", "2019"])
def test_things_that_are_not_date_ranges(text):
    span = dates.parse_range(text)
    if text.startswith("Marketing"):
        assert str(span.start) == "2019"  # the year range is real; "Mar" inside a word isn't a month
    else:
        assert span is None


def test_impossible_ranges_are_reported():
    today = date(2026, 11, 11)
    assert dates.problems(dates.parse_range("06/2016 - 03/2015"), today) == ["ends before it starts"]
    assert dates.problems(dates.parse_range("Jan 2027 - Present"), today) == ["starts in the future"]
    assert dates.problems(dates.parse_range("Jan 2020 - Dec 2030"), today) == ["ends in the future"]
    assert dates.problems(dates.parse_range("2019 - present"), today) == []


def test_months_worked_counts_overlapping_jobs_once():
    today = date(2026, 11, 11)
    r = [dates.parse_range(t) for t in ["Jan 2020 - Dec 2020", "Jun 2020 - Jun 2021", "Jan 2023 - Dec 2023"]]
    assert dates.months_worked(r, today) == 18 + 12
    assert dates.months_worked([dates.parse_range("Mar 2026 - Present")], today) == 9  # March to November


def test_year_only_dates_count_as_mid_year():
    assert dates.months_worked([dates.parse_range("2019 - 2021")], date(2026, 1, 1)) == 24


def test_years_round_half_up():
    assert dates.to_years(123) == 10.3  # 10.25 rounds up, not to even
    assert dates.to_years(12) == 1.0


# --- phones ------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, value", [
    ("07700 900123", "+447700900123"),
    ("+44 7700 900123", "+447700900123"),
    ("+44 (0)7700 900123", "+447700900123"),
    ("0044 7700 900123", "+447700900123"),
    ("0113 496 0000", "+441134960000"),
    ("+1 415 555 0100", "+14155550100"),
])
def test_phone_numbers_are_normalised(raw, value):
    assert phones.normalise(raw) == (value, None)


@pytest.mark.parametrize("raw, problem", [
    ("07700 9001", "a UK number has 11 digits, this has 9"),
    ("09700 900123", "isn't a UK landline or mobile number"),
    ("7700 900123", "doesn't start with 0 or a + country code"),
    ("07700 9OO123", "contains characters that aren't part of a phone number"),
])
def test_bad_phone_numbers_are_kept_and_explained(raw, problem):
    value, reason = phones.normalise(raw)
    assert value == raw and reason == problem


def test_same_number_written_two_ways():
    assert phones.same_number("07700 900123", "+44 7700 900 123")
    assert not phones.same_number("07700 900123", "07700 900124")
