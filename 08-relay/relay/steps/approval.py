"""Approval gate step: decide whether a person must sign off before the CRM is touched.

The rule is deliberately simple and deterministic: the enquiry's project value
is at or above the threshold, or the submitter asked for a review. Below the
threshold the step completes like any other. Otherwise it raises
`ApprovalRequired`, and the engine parks the workflow (relay/engine/approvals.py).
"""

from typing import Any

from relay.engine.errors import ApprovalRequired
from relay.workflows.registry import StepContext


def approval_reason(enquiry: dict[str, Any], threshold_gbp: int) -> str | None:
    value = enquiry.get("project_value_gbp")
    if value is not None and value >= threshold_gbp:
        return f"project value £{value:,} is at or above the £{threshold_gbp:,} threshold"
    if enquiry.get("approval_requested"):
        return "review requested by the submitter"
    return None


def run(ctx: StepContext) -> dict[str, Any]:
    threshold = ctx.settings.approval_threshold_gbp
    reason = approval_reason(ctx.input["enquiry"], threshold)
    if reason is None:
        return {**ctx.input, "approval": {"required": False}}
    raise ApprovalRequired(
        reason,
        {**ctx.input, "approval": {"required": True, "reason": reason}},
    )
