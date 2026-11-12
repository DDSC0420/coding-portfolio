"""Business step logic: deterministic extraction, structured validation, fixture enrichment."""

import pytest

from relay.steps.enrich import lookup
from relay.steps.extract import ExtractedEnquiry, extract_fields, parse_money
from relay.steps.validate import validation_errors


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Budget about £25,000.", 25_000),
        ("roughly £45k all in", 45_000),
        ("up to £1.2m over two years", 1_200_000),
        ("around 30,000 GBP", 30_000),
        ("between £10k and £15k", 15_000),  # largest amount wins
        ("no budget mentioned", None),
    ],
)
def test_parse_money(text: str, expected: int | None) -> None:
    assert parse_money(text) == expected


def test_extraction_prefers_structured_fields_then_labelled_lines() -> None:
    raw = {
        "name": "Structured Name",
        "message": "Help please. £5,000.\nName: Labelled Name\nCompany: Kestrel Robotics\n"
        "Email: Someone@Kestrel-Robotics.example",
        "source": "email",
    }
    out = extract_fields(raw)
    assert out.name == "Structured Name"
    assert out.company == "Kestrel Robotics"
    assert out.email == "someone@kestrel-robotics.example"  # normalised to lower case
    assert out.project_value_gbp == 5_000


def test_extraction_output_must_match_strict_schema() -> None:
    with pytest.raises(ValueError):
        ExtractedEnquiry.model_validate({"request": "x", "source": "web", "unexpected": 1})


def test_validation_reports_every_problem_with_field_and_code() -> None:
    errors = validation_errors(
        {
            "name": None,
            "email": "not-an-email",
            "company": "",
            "request": "hi",
            "project_value_gbp": 50_000_000,
        }
    )
    assert {(e["field"], e["code"]) for e in errors} == {
        ("name", "missing"),
        ("company", "missing"),
        ("email", "invalid_format"),
        ("request", "too_short"),
        ("project_value_gbp", "out_of_range"),
    }


def test_validation_accepts_a_complete_enquiry() -> None:
    assert (
        validation_errors(
            {
                "name": "A",
                "email": "a@b.example",
                "company": "C",
                "request": "a long enough request",
                "project_value_gbp": None,
            }
        )
        == []
    )


def test_enrichment_matches_on_domain_then_company_name_then_reports_no_match() -> None:
    by_domain = lookup("x@kestrel-robotics.example", "Anything")
    assert by_domain["matched"] and by_domain["matched_on"] == "email_domain"
    by_name = lookup("x@gmail.example", "Kestrel Robotics Ltd")
    assert by_name["matched"] and by_name["matched_on"] == "company_name"
    assert lookup("x@unknown.example", "Unknown Co") == {"matched": False, "source": "fixtures"}
