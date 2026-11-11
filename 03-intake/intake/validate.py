"""Turn a Draft from either extractor into a validated candidate record.

Three rules, applied the same way whichever extractor ran:

1. **Grounding.** Every value must be found in the CV text. If it isn't, it's discarded and
   listed under ``rejected``. Values are then taken from the CV itself, not from the draft.
2. **Interpretation happens here.** Dates, phone numbers and the years-of-experience total
   are worked out by code from the quoted text, never taken from an extractor.
3. **Flags, not guesses.** Anything doubtful keeps its value but is marked ``needs_review``
   with a plain reason. A missing field stays missing.
"""

import re
from datetime import date

from . import dates as dt
from . import phones
from .rules import town_only
from .schema import Candidate, Draft, Education, Extracted, Hint, Item, Rejected, ReviewItem, Role, Span
from .textutil import locate, locate_list, locate_near

REVIEW_BELOW = 0.7
UNHINTED = 0.8  # LLM drafts carry no confidence; grounded values start here
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
NAME = re.compile(r"[^\W\d_][^\W\d_'.-]*(?: [^\W\d_][^\W\d_'.-]*){1,4}")


def finalise(draft: Draft, text: str, today: date) -> tuple[Candidate, list[ReviewItem], list[Rejected]]:
    return _Validator(text, today).run(draft)


def _sentence(notes: list[str]) -> str | None:
    text = "; ".join(notes)
    return text[:1].upper() + text[1:] if text else None


def _title_case(name: str) -> str:
    def fix(word: str) -> str:
        return "-".join("'".join(p.capitalize() for p in part.split("'")) for part in word.split("-"))
    return " ".join(fix(w) for w in name.split())


def check_name(value: str) -> tuple[str, str | None]:
    if value.isupper():
        value = _title_case(value)
    if not NAME.fullmatch(value):
        return value, "doesn't look like a person's name"
    return value, None


def check_email(value: str) -> tuple[str, str | None]:
    if not EMAIL.fullmatch(value):
        return value, "doesn't look like a complete email address"
    return value.lower(), None


def check_headline(value: str) -> tuple[str, str | None]:
    return value, ("too long for a job title" if len(value) > 80 else None)


def check_location(value: str) -> tuple[str, str | None]:
    return value, ("too long for a town or city" if len(value) > 60 else None)


def normalise_link(value: str) -> str:
    value = value.rstrip("/.")
    return value if re.match(r"https?://", value, re.IGNORECASE) else f"https://{value}"


class _Validator:
    def __init__(self, text: str, today: date):
        self.text = text
        self.today = today
        self.review: list[ReviewItem] = []
        self.rejected: list[Rejected] = []

    def run(self, draft: Draft) -> tuple[Candidate, list[ReviewItem], list[Rejected]]:
        hints = draft.hints
        location = town_only(draft.location) if draft.location else None
        candidate = Candidate(
            name=self.scalar("name", draft.name, hints.get("name"), check_name),
            email=self.scalar("email", draft.email, hints.get("email"), check_email),
            phone=self.phone(draft.phone, hints.get("phone")),
            location=self.scalar("location", location, hints.get("location"), check_location),
            headline=self.scalar("headline", draft.headline, hints.get("headline"), check_headline),
            # Match links without "https://www." since models often add it and CVs often don't.
            links=self.items("links", [re.sub(r"^(https?://)?(www\.)?", "", u, flags=re.I) for u in draft.links],
                             normalise_link),
            skills=self.items("skills", draft.skills),
        )
        candidate.experience, ranges = self.roles(draft)
        candidate.education = self.education(draft)
        candidate.years_experience = self.years(ranges)
        if candidate.name.value is None:
            self.review.append(ReviewItem(field="name", reason="No name found"))
        if candidate.email.value is None and candidate.phone.value is None:
            self.review.append(ReviewItem(field="contact", reason="No email address or phone number found"))
        return candidate, self.review, self.rejected

    # --- grounding --------------------------------------------------------------------------

    def ground(self, field: str, raw: str, start: int = 0, anchor: int | None = None) -> Span | None:
        span = locate(self.text, raw, start) if anchor is None else locate_near(self.text, raw, anchor, start)
        if span is None:
            self.rejected.append(Rejected(field=field, value=raw[:200], reason="not found in the CV text"))
        return span

    def take(self, field: str, raw: str | None, cursor: int, spans: list[Span], anchor: int | None) -> str | None:
        """Ground one part of a role or qualification; keep its span as evidence."""
        if not raw or not raw.strip():
            return None
        span = self.ground(field, raw, cursor, anchor)
        if span is None:
            return None
        spans.append(span)
        return " ".join(span.text.split())

    def settle(self, field: str, value, span: Span | list[Span], hint: Hint | None,
               problem: str | None) -> Extracted:
        confidence = hint.confidence if hint else UNHINTED
        note = hint.reason if hint else None
        if problem:
            confidence, note = min(confidence, 0.4), problem
        status = "needs_review" if problem or confidence < REVIEW_BELOW else "found"
        if status == "needs_review":
            note = note or "low confidence"
            self.review.append(ReviewItem(field=field, reason=note))
        evidence = span if isinstance(span, list) else [span]
        return Extracted(value=value, status=status, confidence=round(confidence, 2), evidence=evidence, note=note)

    def scalar(self, field: str, raw: str | None, hint: Hint | None, check) -> Extracted:
        if not raw or not raw.strip():
            return Extracted()
        span = self.ground(field, raw)
        if span is None:
            return Extracted(note="Discarded: the value proposed wasn't in the CV")
        value, problem = check(" ".join(span.text.split()))
        return self.settle(field, value, span, hint, problem)

    def phone(self, raw: str | None, hint: Hint | None) -> Extracted:
        if not raw or not raw.strip():
            return Extracted()
        span = locate(self.text, raw)
        if span is None:  # an LLM may reformat the number; match on the number itself
            for match in phones.CANDIDATE.finditer(self.text):
                if phones.same_number(match.group(), raw):
                    span = Span(start=match.start(), end=match.end(), text=match.group())
                    break
        if span is None:
            self.rejected.append(Rejected(field="phone", value=raw[:200], reason="not found in the CV text"))
            return Extracted(note="Discarded: the value proposed wasn't in the CV")
        value, problem = phones.normalise(span.text)
        return self.settle("phone", value, span, hint, problem)

    def items(self, field: str, raws: list[str], transform=None) -> list[Item]:
        out, seen = [], set()
        raws = [r for r in raws if r.strip()]
        for raw, span in zip(raws, locate_list(self.text, raws), strict=True):
            if span is None:
                self.rejected.append(Rejected(field=field, value=raw[:200], reason="not found in the CV text"))
                continue
            value = " ".join(span.text.split())
            value = transform(value) if transform else value
            if value.casefold() not in seen:
                seen.add(value.casefold())
                out.append(Item(value=value, evidence=span))
        return out

    # --- experience -------------------------------------------------------------------------

    def roles(self, draft: Draft) -> tuple[list[Role], list[tuple[dt.DateRange, Span]]]:
        roles, ranges, cursor = [], [], 0
        self.excluded = 0
        for raw in draft.experience:
            label = f"experience[{len(roles)}]"
            spans: list[Span] = []
            notes: list[str] = []
            # Dates are the most distinctive part of a role, so find them first and look
            # for the title and employer nearest to them.
            period = None
            date_span = self.ground(f"{label}.dates", raw.dates, cursor) if raw.dates else None
            anchor = date_span.start if date_span else None
            title = self.take(f"{label}.title", raw.title, cursor, spans, anchor)
            company = self.take(f"{label}.company", raw.company, cursor, spans, anchor)
            if date_span:
                spans.append(date_span)
                period = dt.parse_range(date_span.text)
                if period is None:
                    notes.append("couldn't read the dates")
            elif not raw.dates:
                notes.append("no dates found")
            if not spans:
                continue
            if period:
                notes.extend(dt.problems(period, self.today))
            if not title:
                notes.append("no job title found")
            if not company:
                notes.append("no employer found")
            near = max(sp.end for sp in spans)
            highlights = [" ".join(hs.text.split()) for h in raw.highlights[:6]
                          if (hs := self.ground(f"{label}.highlights", h, cursor, near))]

            confidence = raw.hint.confidence if raw.hint else 0.9
            if notes:
                confidence = min(confidence, 0.5)
            elif raw.hint and raw.hint.reason and confidence < REVIEW_BELOW:
                notes.append(raw.hint.reason)
            status = "needs_review" if notes or confidence < REVIEW_BELOW else "found"
            note = _sentence(notes)
            if status == "needs_review":
                self.review.append(ReviewItem(field=label, reason=note or "low confidence"))
            if period and not dt.problems(period, self.today):
                ranges.append((period, date_span))
            else:
                self.excluded += 1
            cursor = max(s.end for s in spans)
            roles.append(Role(
                title=title, company=company,
                start=str(period.start) if period else None,
                end=str(period.end) if period and period.end else None,
                current=bool(period and period.current), highlights=highlights,
                status=status, confidence=round(confidence, 2), evidence=sorted(spans, key=lambda s: s.start),
                note=note,
            ))
        return roles, ranges

    def years(self, ranges: list[tuple[dt.DateRange, Span]]) -> Extracted:
        if not ranges:
            return Extracted()
        value = dt.to_years(dt.months_worked([r for r, _ in ranges], self.today))
        notes = []
        if any(r.year_only for r, _ in ranges):
            notes.append("some dates are year-only, so this is approximate")
        if self.excluded:
            notes.append(f"{self.excluded} role{'s' if self.excluded > 1 else ''} with unusable dates left out")
        hint = Hint(confidence=0.6, reason=_sentence(notes)) if notes else Hint(confidence=0.9)
        return self.settle("years_experience", value, [s for _, s in ranges], hint, None)

    # --- education --------------------------------------------------------------------------

    def education(self, draft: Draft) -> list[Education]:
        out, cursor = [], 0
        for raw in draft.education:
            label = f"education[{len(out)}]"
            spans: list[Span] = []
            qualification = self.take(f"{label}.qualification", raw.qualification, cursor, spans, None)
            institution = self.take(f"{label}.institution", raw.institution, cursor, spans, None)
            year = None
            if not spans:
                continue
            notes = []
            if raw.dates and (s := self.ground(f"{label}.dates", raw.dates, cursor)):
                spans.append(s)
                years = dt.YEAR.findall(s.text)
                year = int(years[-1]) if years else None
                if year and year > self.today.year + 6:
                    notes.append("year is too far in the future")
            if not qualification:
                notes.append("no qualification found")
            status = "needs_review" if notes else "found"
            note = _sentence(notes)
            if notes:
                self.review.append(ReviewItem(field=label, reason=note))
            cursor = max(s.end for s in spans)
            out.append(Education(qualification=qualification, institution=institution, year=year, status=status,
                                 confidence=0.5 if notes else 0.9, evidence=sorted(spans, key=lambda s: s.start),
                                 note=note))
        return out
