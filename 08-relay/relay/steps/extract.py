"""Extraction step: turn a raw enquiry into structured fields.

The default provider is deterministic rule-based parsing of the synthetic
fixture text. It is labelled as such in its output and involves no AI. An
optional real AI provider (Stage 3+) would have to return output that passes
`ExtractedEnquiry` validation before it is accepted, and the accepted output is
persisted like any step output, so a completed extraction is never repeated.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from relay.workflows.registry import StepContext

PROVIDER = "fixture-rules"
PROVIDER_LABEL = "Fixture-based extraction (deterministic rules, no AI)"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# £45,000 · £45k · £1.2m · 45,000 GBP
_MONEY = re.compile(
    r"(?:£\s?(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?P<sa>[kKmM])?\b)"
    r"|(?:\b(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P<sb>[kKmM])?\s*GBP\b)"
)
_LABELLED = re.compile(r"^\s*(?P<label>name|company|email)\s*:\s*(?P<value>.+?)\s*$", re.I | re.M)


class ExtractedEnquiry(BaseModel):
    """The strict schema any extraction provider's output must satisfy."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    company: str | None = Field(default=None, max_length=200)
    request: str = Field(max_length=2000)
    project_value_gbp: int | None = Field(default=None, ge=0)
    source: str = Field(max_length=50)
    enquiry_ref: str | None = Field(default=None, max_length=100)
    approval_requested: bool = False


def parse_money(text: str) -> int | None:
    """Largest money amount mentioned in the text, in whole pounds."""
    best: int | None = None
    for m in _MONEY.finditer(text):
        raw = m.group("a") or m.group("b")
        suffix = (m.group("sa") or m.group("sb") or "").lower()
        value = float(raw.replace(",", ""))
        value *= {"k": 1_000, "m": 1_000_000}.get(suffix, 1)
        amount = int(round(value))
        best = amount if best is None else max(best, amount)
    return best


def extract_fields(raw: dict[str, Any]) -> ExtractedEnquiry:
    message: str = raw.get("message", "")
    labelled = {m.group("label").lower(): m.group("value") for m in _LABELLED.finditer(message)}
    email_in_text = _EMAIL.search(message)

    def pick(key: str) -> str | None:
        value = raw.get(key) or labelled.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    email = pick("email") or (email_in_text.group(0) if email_in_text else None)
    return ExtractedEnquiry(
        name=pick("name"),
        email=email.lower() if email else None,
        company=pick("company"),
        request=" ".join(message.split())[:2000],
        project_value_gbp=parse_money(message),
        source=raw.get("source") or "unknown",
        enquiry_ref=raw.get("enquiry_ref"),
        approval_requested=bool(raw.get("approval_required", False)),
    )


def run(ctx: StepContext) -> dict[str, Any]:
    extracted = extract_fields(ctx.input)
    return {
        "enquiry": extracted.model_dump(mode="json"),
        "extraction": {"provider": PROVIDER, "label": PROVIDER_LABEL},
    }
