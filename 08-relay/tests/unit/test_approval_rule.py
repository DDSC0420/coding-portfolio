"""The approval rule, on plain data."""

from relay.steps.approval import approval_reason


def enquiry(**overrides):
    return {
        "company": "Northwind",
        "project_value_gbp": None,
        "approval_requested": False,
    } | overrides


def test_below_the_threshold_needs_no_approval() -> None:
    assert approval_reason(enquiry(project_value_gbp=49_999), 50_000) is None
    assert approval_reason(enquiry(), 50_000) is None  # no value mentioned at all


def test_at_or_above_the_threshold_needs_approval_and_says_why() -> None:
    assert approval_reason(enquiry(project_value_gbp=50_000), 50_000) == (
        "project value £50,000 is at or above the £50,000 threshold"
    )
    assert "£1,200,000" in approval_reason(enquiry(project_value_gbp=1_200_000), 50_000)


def test_the_submitter_can_ask_for_a_review_whatever_the_value() -> None:
    assert approval_reason(enquiry(project_value_gbp=10, approval_requested=True), 50_000) == (
        "review requested by the submitter"
    )
