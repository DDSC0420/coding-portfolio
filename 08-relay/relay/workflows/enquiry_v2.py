"""The `enquiry` workflow, version 2: version 1 plus a human approval gate.

    extract -> validate -> enrich -> approval -> crm_upsert

Published as a new version instead of editing v1. Runs submitted as v1 keep
v1's four steps for their whole life (invariant I9); both stay registered.
"""

from relay.steps import approval, crm_upsert, enrich, extract, validate
from relay.workflows.registry import StepDefinition, WorkflowDefinition

DEFINITION = WorkflowDefinition(
    type="enquiry",
    version=2,
    description=(
        "Extract, validate and enrich an enquiry; pause for approval when the project "
        "value is high or a review was requested; then upsert the contact in the CRM"
    ),
    steps=(
        StepDefinition("extract", extract.run, max_attempts=3),
        # A validation failure is permanent and never retried; the budget of 3 is for crashes.
        StepDefinition("validate", validate.run, max_attempts=3),
        StepDefinition("enrich", enrich.run, max_attempts=3),
        StepDefinition("approval", approval.run, max_attempts=3),
        StepDefinition("crm_upsert", crm_upsert.run, max_attempts=6, external_effect=True),
    ),
)
