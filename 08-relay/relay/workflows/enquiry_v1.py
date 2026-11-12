"""The `enquiry` workflow, version 1.

    extract -> validate -> enrich -> crm_upsert

Each step's output becomes the next step's input. Version 2 (enquiry_v2.py)
adds the approval gate; this version stays registered so existing runs finish as v1.
"""

from pydantic import BaseModel, ConfigDict, Field

from relay.steps import crm_upsert, enrich, extract, validate
from relay.workflows.registry import StepDefinition, WorkflowDefinition


class EnquiryInput(BaseModel):
    """Submission schema. Deliberately lenient: shape and size only.

    Business validation (required fields, email format) happens in the
    `validate` step, so an invalid enquiry becomes a visible, recorded
    workflow failure instead of an API error.
    """

    model_config = ConfigDict(extra="forbid")

    enquiry_ref: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    company: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1, max_length=5000)
    source: str = Field(default="web_form", max_length=50)
    approval_required: bool = False


DEFINITION = WorkflowDefinition(
    type="enquiry",
    version=1,
    description="Extract, validate and enrich an enquiry, then upsert the contact in the CRM",
    steps=(
        StepDefinition("extract", extract.run, max_attempts=3),
        StepDefinition("validate", validate.run, max_attempts=1),
        StepDefinition("enrich", enrich.run, max_attempts=3),
        StepDefinition("crm_upsert", crm_upsert.run, max_attempts=6, external_effect=True),
    ),
)

INPUT_SCHEMAS: dict[str, type[BaseModel]] = {"enquiry": EnquiryInput}
