"""Validation: the rules that hold whichever extractor produced the draft."""

import pytest

from intake import validate
from intake.pipeline import extract
from intake.schema import Draft, DraftEducation, DraftRole, Extracted, Hint

from .conftest import SAMPLES, TODAY

CV = """Jordan Example
Support Analyst
Bristol | 07700 900321 | jordan.example@example.com

EXPERIENCE
Support Analyst | Example Ltd | Jan 2021 - Present
- Answered 60 tickets a day
Helpdesk Assistant | Sample Co | 2018 - 2020

SKILLS
Excel, SQL, Zendesk
"""


def run(draft: Draft, text: str = CV):
    return validate.finalise(draft, text, TODAY)


def test_values_not_in_the_cv_are_discarded_and_listed():
    draft = Draft(name="Jordan Sample", email="jordan@example.org", skills=["Excel", "Kotlin"],
                  experience=[DraftRole(title="Support Analyst", company="Google", dates="Jan 2021 - Present")])
    candidate, review, rejected = run(draft)
    assert candidate.name.value is None and candidate.name.note.startswith("Discarded")
    assert candidate.email.value is None
    assert [s.value for s in candidate.skills] == ["Excel"]
    assert candidate.experience[0].company is None
    assert {(r.field, r.value) for r in rejected} == {
        ("name", "Jordan Sample"), ("email", "jordan@example.org"), ("skills", "Kotlin"),
        ("experience[0].company", "Google")}


def test_values_are_taken_from_the_cv_not_the_draft():
    candidate, _, _ = run(Draft(name="JORDAN   example", headline="support analyst"))
    assert candidate.name.value == "Jordan Example"  # the CV's spelling and spacing
    assert candidate.headline.value == "Support Analyst"
    span = candidate.name.evidence[0]
    assert CV[span.start:span.end] == "Jordan Example"


def test_missing_stays_missing():
    candidate, review, _ = run(Draft())
    assert all(getattr(candidate, f).status == "missing" for f in ("name", "email", "phone", "location"))
    assert candidate.years_experience.value is None
    assert [r.field for r in review] == ["name", "contact"]


def test_doubtful_values_are_kept_but_flagged():
    text = "Rowan Example\nTel: 07700 9001\nE-mail: rowan@example\n"
    candidate, review, _ = run(Draft(name="Rowan Example", phone="07700 9001", email="rowan@example"), text)
    assert candidate.phone.value == "07700 9001" and candidate.phone.status == "needs_review"
    assert candidate.email.status == "needs_review"
    assert {r.field for r in review} == {"phone", "email"}


def test_low_confidence_hints_become_review_items():
    candidate, review, _ = run(Draft(headline="Support Analyst", hints={"headline": Hint(confidence=0.5,
                                                                                         reason="unsure")}))
    assert candidate.headline.status == "needs_review" and candidate.headline.note == "unsure"
    assert review[0].field == "headline"


def test_all_caps_names_are_tidied():
    text = "MARY-JANE O'BRIEN\n"
    candidate, _, _ = run(Draft(name="MARY-JANE O'BRIEN"), text)
    assert candidate.name.value == "Mary-Jane O'Brien"


def test_an_llm_style_reformatted_phone_is_still_found_in_the_cv():
    candidate, _, rejected = run(Draft(phone="+44 7700 900 321"))
    assert candidate.phone.value == "+447700900321" and candidate.phone.evidence[0].text == "07700 900321"
    assert rejected == []


def test_roles_get_dates_from_code_and_problems_are_flagged():
    text = CV + "\nOld Job | Past Ltd | 06/2016 - 03/2015\nNo Dates Job | Nowhere Ltd\n"
    draft = Draft(experience=[
        DraftRole(title="Support Analyst", company="Example Ltd", dates="Jan 2021 - Present"),
        DraftRole(title="Old Job", company="Past Ltd", dates="06/2016 - 03/2015"),
        DraftRole(title="No Dates Job", company="Nowhere Ltd"),
    ])
    candidate, review, _ = run(draft, text)
    first, backwards, undated = candidate.experience
    assert (first.start, first.end, first.current, first.status) == ("2021-01", None, True, "found")
    assert backwards.status == "needs_review" and backwards.note == "Ends before it starts"
    assert undated.note == "No dates found"
    assert candidate.years_experience.value == 5.9  # Jan 2021 to Nov 2026 only
    assert "2 roles with unusable dates left out" in candidate.years_experience.note


def test_role_parts_are_matched_next_to_their_dates_not_in_the_summary():
    text = "Sam Example\nPROFILE\nTeam Leader at heart.\nEXPERIENCE\nTeam Leader\nAcme Ltd\n2019 - 2021\n"
    candidate, _, _ = run(Draft(experience=[DraftRole(title="Team Leader", company="Acme Ltd", dates="2019 - 2021")]),
                          text)
    assert candidate.experience[0].evidence[0].start == text.index("Team Leader\nAcme")


def test_education_year_comes_from_the_quoted_dates():
    text = "MSc Data Science (2025)\nUniversity of Sheffield\n"
    candidate, _, _ = run(Draft(education=[DraftEducation(qualification="MSc Data Science",
                                                          institution="University of Sheffield", dates="2025")]), text)
    assert candidate.education[0].year == 2025 and candidate.education[0].status == "found"


def test_the_schema_refuses_a_value_without_evidence():
    with pytest.raises(ValueError, match="evidence"):
        Extracted[str](value="Invented", status="found")


@pytest.mark.parametrize("name", ["amara-osei.pdf", "tom-brennan.docx", "sofia-marlowe.txt", "rowan-hale.txt"])
def test_every_value_in_every_sample_points_at_real_cv_text(name):
    result = extract((SAMPLES / name).read_bytes(), name, today=TODAY)
    c = result.candidate
    spans = [s for f in ("name", "email", "phone", "location", "headline", "years_experience")
             for s in getattr(c, f).evidence]
    spans += [i.evidence for i in c.skills + c.links]
    spans += [s for entry in c.experience + c.education for s in entry.evidence]
    assert spans
    for span in spans:
        assert result.text[span.start:span.end] == span.text
    for field in ("name", "headline", "location"):
        value = getattr(c, field).value
        if value:
            assert value.casefold() == " ".join(getattr(c, field).evidence[0].text.split()).casefold()
    assert result.rejected == []
