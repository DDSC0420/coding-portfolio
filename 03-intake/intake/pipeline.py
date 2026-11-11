"""File in, validated record out. The one function the CLI and the web page both call."""

from datetime import date

from . import readers, rules, validate
from .llm import LlmError, LlmExtractor
from .schema import Result, Source


def extract(data: bytes, filename: str, *, llm: LlmExtractor | None = None, today: date | None = None) -> Result:
    """Read a CV and return a validated record. Raises ``readers.ReadError`` for unreadable files."""
    doc = readers.read(data, filename)
    warnings = list(doc.warnings)
    draft, extractor = None, "rules"
    if llm is not None:
        try:
            draft, extractor = llm.extract(doc.text), llm.label
        except LlmError as exc:
            warnings.append(f"The LLM extractor failed ({exc}), so the rule-based extractor was used instead.")
    if draft is None:
        draft = rules.extract(doc.text)
    candidate, review, rejected = validate.finalise(draft, doc.text, today or date.today())
    return Result(
        candidate=candidate,
        needs_review=bool(review),
        review=review,
        rejected=rejected,
        warnings=warnings,
        source=Source(filename=filename or "upload", kind=doc.kind, pages=doc.pages,
                      characters=len(doc.text), extractor=extractor),
        text=doc.text,
    )
