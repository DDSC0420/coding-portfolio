"""Validation step: business checks on extracted fields.

Failures here are permanent: the same input will fail the same way, so the
engine does not retry. Errors are structured so the dashboard can show them.
"""

import re
from typing import Any

from relay.engine.errors import PermanentStepError
from relay.workflows.registry import StepContext

_EMAIL = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
MAX_PROJECT_VALUE_GBP = 10_000_000


def validation_errors(enquiry: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    def err(field: str, code: str, message: str) -> None:
        errors.append({"field": field, "code": code, "message": message})

    for field in ("name", "email", "company"):
        if not enquiry.get(field):
            err(field, "missing", f"{field} is required")
    email = enquiry.get("email")
    if email and not _EMAIL.match(email):
        err("email", "invalid_format", f"{email!r} is not a valid email address")
    if len(enquiry.get("request") or "") < 10:
        err("request", "too_short", "request must be at least 10 characters")
    value = enquiry.get("project_value_gbp")
    if value is not None and value > MAX_PROJECT_VALUE_GBP:
        err("project_value_gbp", "out_of_range", f"project value above £{MAX_PROJECT_VALUE_GBP:,}")
    return errors


def run(ctx: StepContext) -> dict[str, Any]:
    errors = validation_errors(ctx.input["enquiry"])
    if errors:
        fields = ", ".join(sorted({e["field"] for e in errors}))
        raise PermanentStepError(f"validation failed: {fields}", details={"errors": errors})
    return {**ctx.input, "validation": {"passed": True}}
