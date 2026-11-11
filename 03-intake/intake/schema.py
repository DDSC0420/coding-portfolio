"""The shapes data takes on the way through Intake.

Two families:

- ``Draft`` is what an extractor proposes: plain strings quoted from the CV. Both the
  rule-based extractor and the optional LLM produce one.
- ``Result`` is what Intake returns after validation. Every value in it carries the
  evidence (the exact span of CV text) it came from, a status and a confidence.

The schema itself refuses a value without evidence, so "nothing invented" is enforced by
the types, not only by the code that fills them.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Status = Literal["found", "needs_review", "missing"]

YEAR_MONTH = r"^\d{4}(-\d{2})?$"


class Span(BaseModel):
    """A stretch of the cleaned CV text: ``text == source[start:end]``."""

    model_config = ConfigDict(frozen=True)

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str


class Extracted[T](BaseModel):
    """One field of the record, with where it came from and how sure we are."""

    value: T | None = None
    status: Status = "missing"
    confidence: float = Field(0.0, ge=0, le=1)
    evidence: list[Span] = []
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.value is None and self.status != "missing":
            raise ValueError("a field with no value must have status 'missing'")
        if self.value is not None and self.status == "missing":
            raise ValueError("a field with a value can't have status 'missing'")
        if self.value is not None and not self.evidence:
            raise ValueError("every value needs evidence from the CV")
        return self


class Item(BaseModel):
    """A list entry such as one skill or one link."""

    value: str
    evidence: Span


class Role(BaseModel):
    title: str | None = None
    company: str | None = None
    start: str | None = Field(None, pattern=YEAR_MONTH)
    end: str | None = Field(None, pattern=YEAR_MONTH)
    current: bool = False
    highlights: list[str] = []
    status: Literal["found", "needs_review"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[Span] = Field(min_length=1)
    note: str | None = None


class Education(BaseModel):
    qualification: str | None = None
    institution: str | None = None
    year: int | None = None
    status: Literal["found", "needs_review"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[Span] = Field(min_length=1)
    note: str | None = None


class Candidate(BaseModel):
    name: Extracted[str] = Extracted()
    email: Extracted[str] = Extracted()
    phone: Extracted[str] = Extracted()
    location: Extracted[str] = Extracted()
    headline: Extracted[str] = Extracted()
    links: list[Item] = []
    skills: list[Item] = []
    experience: list[Role] = []
    education: list[Education] = []
    years_experience: Extracted[float] = Extracted()


class ReviewItem(BaseModel):
    field: str
    reason: str


class Rejected(BaseModel):
    """A value an extractor proposed that wasn't in the CV, so it was thrown away."""

    field: str
    value: str
    reason: str


class Source(BaseModel):
    filename: str
    kind: Literal["pdf", "docx", "text"]
    pages: int | None = None
    characters: int
    extractor: str


class Result(BaseModel):
    schema_version: Literal["1"] = "1"
    candidate: Candidate
    needs_review: bool
    review: list[ReviewItem] = []
    rejected: list[Rejected] = []
    warnings: list[str] = []
    source: Source
    text: str


# ---------------------------------------------------------------------------
# Drafts: what an extractor proposes, before validation.

Short = Annotated[str, StringConstraints(max_length=300)]


class Hint(BaseModel):
    """The rule-based extractor's own view of how sure it is. LLM drafts never carry hints."""

    confidence: float = Field(ge=0, le=1)
    reason: str | None = None


class DraftRole(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: Short | None = None
    company: Short | None = None
    dates: Short | None = None
    highlights: list[Short] = Field(default_factory=list, max_length=12)
    hint: Hint | None = None


class DraftEducation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    qualification: Short | None = None
    institution: Short | None = None
    dates: Short | None = None


class Draft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Short | None = None
    email: Short | None = None
    phone: Short | None = None
    location: Short | None = None
    headline: Short | None = None
    links: list[Short] = Field(default_factory=list, max_length=10)
    skills: list[Short] = Field(default_factory=list, max_length=80)
    experience: list[DraftRole] = Field(default_factory=list, max_length=30)
    education: list[DraftEducation] = Field(default_factory=list, max_length=15)
    hints: dict[str, Hint] = {}
