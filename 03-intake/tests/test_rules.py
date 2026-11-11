"""The rule-based extractor, one rule at a time. Pure functions, no server, no files."""

import pytest

from intake import rules
from intake.textutil import clean


def draft(text: str):
    return rules.extract(clean(text))


# --- header ------------------------------------------------------------------------------


def test_name_is_the_first_line_and_matching_email_raises_confidence():
    d = draft("Priya Example\nData Engineer\npriya.example@example.com\n")
    assert d.name == "Priya Example" and d.hints["name"].confidence == 0.95
    d = draft("Priya Example\nData Engineer\nhello@example.com\n")
    assert d.hints["name"].confidence == 0.8


def test_a_cv_heading_is_skipped_and_a_labelled_name_wins():
    assert draft("CURRICULUM VITAE\n\nName: Rowan Example\n").name == "Rowan Example"
    assert draft("Résumé\nAlex Example\n").name == "Alex Example"


@pytest.mark.parametrize("first_line", ["Senior DevOps Engineer", "alex@example.com", "Experience",
                                        "Alex Example BSc 2020"])
def test_a_first_line_that_isnt_a_name_gives_no_name(first_line):
    assert draft(f"{first_line}\nSomething else\n").name is None


def test_headline_is_the_title_line_under_the_name():
    d = draft("Sam Example\nWarehouse Team Leader\n07700 900111\n")
    assert d.headline == "Warehouse Team Leader" and d.hints["headline"].confidence == 0.75
    d = draft("Sam Example\nKeen and reliable\n")
    assert d.headline == "Keen and reliable" and d.hints["headline"].confidence < 0.7  # flagged, not trusted


def test_location_keeps_the_town_and_drops_the_street_and_postcode():
    assert draft("Sam Example\nAddress: 12 Oak Road, Salford, M6 5AB\n").location == "Salford"
    assert draft("Sam Example\nAddress: Flat 3, Kings Court, Leeds, LS1 4AP\n").location == "Leeds"
    assert draft("Sam Example\nLeeds, West Yorkshire | 07700 900123\n").location == "Leeds, West Yorkshire"
    assert rules.town_only("Mill Lane, Bath") == "Bath"


def test_a_name_that_is_also_a_place_isnt_taken_as_the_location():
    assert draft("Chester Example\nNurse\n").location is None


def test_phone_prefers_a_labelled_number_and_ignores_years():
    assert draft("Sam Example\n2013 - 2016\nMobile: +44 7700 900456\n").phone == "+44 7700 900456"
    assert draft("Sam Example\nWorked 2013 - 2016 and 2016 - 2020\n").phone is None


def test_links_are_found_and_deduplicated():
    d = draft("Sam\nlinkedin.com/in/sam-x | https://www.linkedin.com/in/sam-x/ | github.com/samx\n")
    assert d.links == ["linkedin.com/in/sam-x", "github.com/samx"]


# --- sections and skills -----------------------------------------------------------------


def test_headings_are_recognised_with_colons_and_capitals():
    assert rules.heading_kind("WORK EXPERIENCE") == "experience"
    assert rules.heading_kind("Key Skills:") == "skills"
    assert rules.heading_kind("Experience building pipelines in Python") is None


def test_skills_split_on_separators_but_not_inside_brackets():
    lines = ["Cloud: AWS, GCP", "Infrastructure as code: Terraform; Ansible", "Python (pandas, numpy) | SQL",
             "- Power BI", "AWS"]
    assert rules.find_skills(lines) == ["AWS", "GCP", "Terraform", "Ansible", "Python (pandas, numpy)", "SQL",
                                        "Power BI"]


def test_sentences_after_the_skills_are_not_skills():
    lines = ["Excel, SQL", "References available on request.", "I am a hard worker. I learn fast."]
    assert rules.find_skills(lines) == ["Excel", "SQL"]


# --- experience --------------------------------------------------------------------------


def roles(text: str):
    return [(r.title, r.company, r.dates) for r in rules.find_roles(clean(text).split("\n"))]


def test_role_on_one_line_with_pipes():
    assert roles("Platform Engineer | Acme Ltd | Mar 2021 - Present\n- Built things") == [
        ("Platform Engineer", "Acme Ltd", "Mar 2021 - Present")]


def test_role_with_title_company_and_dates_on_separate_lines():
    text = "Warehouse Operative\nFernvale Ltd, Trafford Park\nAugust 2017 to December 2019\n- Picked orders"
    assert roles(text) == [("Warehouse Operative", "Fernvale Ltd", "August 2017 to December 2019")]


def test_company_first_then_title_is_swapped():
    text = "Fernvale Ltd\nWarehouse Operative\n2017 - 2019"
    assert roles(text) == [("Warehouse Operative", "Fernvale Ltd", "2017 - 2019")]


def test_title_at_company_and_comma_layouts():
    text = ("Contact Centre Supervisor at Clifton Telecom\n06/2016 - 03/2018\n"
            "Data Analyst Intern, Pennine Water Trust, Jul 2024 - Sep 2024")
    assert roles(text) == [("Contact Centre Supervisor", "Clifton Telecom", "06/2016 - 03/2018"),
                           ("Data Analyst Intern", "Pennine Water Trust", "Jul 2024 - Sep 2024")]


def test_bracketed_notes_are_dropped_from_the_employer_but_kept_in_the_title():
    text = "Retail Assistant (part-time) - Brightmoor Stores (Salford branch)\n2015 - 2017"
    assert roles(text) == [("Retail Assistant (part-time)", "Brightmoor Stores", "2015 - 2017")]


def test_a_role_without_an_employer_keeps_the_gap():
    assert roles("Call Centre Advisor\n2012 - 2016") == [("Call Centre Advisor", None, "2012 - 2016")]


def test_unclear_title_and_employer_get_a_low_confidence_hint():
    [role] = rules.find_roles(["Northwind | Blue Team | 2019 - 2021"])
    assert role.hint.confidence < 0.7 and "couldn't tell" in role.hint.reason


def test_bullets_belong_to_the_role_above_them():
    text = ("Engineer | A Ltd | 2020 - 2022\n- one\n- two\n\nTeam Leader\nB Ltd\n2018 - 2020\n- three")
    found = rules.find_roles(clean(text).split("\n"))
    assert [r.highlights for r in found] == [["one", "two"], ["three"]]


# --- education ---------------------------------------------------------------------------


def test_education_entries_pair_qualifications_with_institutions():
    lines = ["BSc (Hons) Computer Science, 2:1", "University of Leeds, 2013 - 2016",
             "GCSEs (9 including English and Maths), 2015", "Irwell Park High School",
             "MSc Data Science, University of Sheffield (2025)", "References available on request."]
    found = [(e.qualification, e.institution, e.dates) for e in rules.find_education(lines)]
    assert found == [
        ("BSc (Hons) Computer Science, 2:1", "University of Leeds", "2013 - 2016"),
        ("GCSEs (9 including English and Maths)", "Irwell Park High School", "2015"),
        ("MSc Data Science", "University of Sheffield", "2025"),
    ]
