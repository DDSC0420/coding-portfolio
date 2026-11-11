"""The rule-based extractor: plain Python, no framework imports, no network, no API key.

It reads cleaned CV text and returns a ``Draft``: strings quoted from the CV, plus a
confidence hint for the fields where a rule is a guess. It doesn't interpret dates, phone
numbers or totals; ``validate.py`` does that for both extractors.
"""

import re
from dataclasses import dataclass, field

from .dates import RANGE, YEAR
from .phones import CANDIDATE as PHONE
from .phones import digits
from .schema import Draft, DraftEducation, DraftRole, Hint

SECTIONS = {
    "summary": {"profile", "summary", "professional summary", "personal statement", "personal profile",
                "about me", "about", "career summary", "objective", "career objective"},
    "experience": {"experience", "work experience", "professional experience", "employment",
                   "employment history", "career history", "work history", "relevant experience"},
    "education": {"education", "education and training", "education & training", "qualifications",
                  "academic background", "education and qualifications"},
    "skills": {"skills", "key skills", "technical skills", "core skills", "skills and competencies",
               "core competencies", "competencies", "skills summary"},
    "other": {"projects", "certifications", "certificates", "interests", "hobbies", "hobbies and interests",
              "references", "languages", "volunteering", "achievements", "awards", "publications",
              "additional information", "personal details", "contact", "contact details", "note", "notes"},
}
NOT_NAMES = {"curriculum vitae", "cv", "c.v.", "resume", "résumé"}
LABELS = {
    "name": {"name", "full name"},
    "email": {"email", "email address"},
    "phone": {"tel", "telephone", "phone", "mobile", "mob", "mobile number", "phone number", "contact number"},
    "location": {"location", "address", "based in", "home", "town", "city"},
}
TITLE_WORDS = re.compile(
    r"\b(engineer|developer|manager|lead|leader|analyst|assistant|officer|supervisor|consultant|intern|"
    r"director|administrator|coordinator|co-ordinator|technician|operative|associate|specialist|head|"
    r"executive|designer|architect|scientist|advisor|adviser|ambassador|apprentice|representative|agent|"
    r"clerk|accountant|nurse|teacher|driver|operator|worker|founder|programmer|tester|controller|planner|"
    r"buyer|chef|editor|writer|researcher|trainee|president|receptionist|cashier|porter|carer|"
    r"tutor|lecturer|surveyor|solicitor|paralegal|recruiter|marketer|strategist|owner)s?\b",
    re.IGNORECASE,
)
QUALIFICATION = re.compile(
    r"\b(BSc|BA|BEng|MEng|MSc|MA|MBA|PhD|DPhil|LLB|LLM|PGCE|PGDip|HND|HNC|BTEC|NVQ|GCSEs?|A-?Levels?)\b"
    r"|\b(diploma|degree|certificate|apprenticeship|level \d|foundation|bachelor|master|doctorate|hons)\b",
    re.IGNORECASE,
)
INSTITUTION = re.compile(r"\b(university|college|school|academy|institute|polytechnic)\b", re.IGNORECASE)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*")
LINK = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com/in/[\w%-]+|github\.com/[\w-]+)/?|https?://[^\s|,;<>\"']+",
    re.IGNORECASE,
)
LABEL = re.compile(r"^(?P<label>[A-Za-z][A-Za-z -]{0,20}?)\s*:\s*(?P<value>.+)$")
SKILL_LABEL = re.compile(r"^(?P<label>[^:,;|()]{1,30}):\s*(?P<value>.+)$")
STREET = re.compile(r"\b(road|rd|street|st|avenue|ave|lane|drive|close|way|crescent|court|place|grove|"
                    r"terrace|gardens|row|square|hill|flat|apartment)\b\.?$", re.IGNORECASE)
PLACES = {
    "london", "birmingham", "manchester", "salford", "leeds", "sheffield", "bradford", "liverpool", "bristol",
    "newcastle", "sunderland", "nottingham", "leicester", "coventry", "wolverhampton", "stoke-on-trent", "derby",
    "southampton", "portsmouth", "plymouth", "reading", "oxford", "cambridge", "brighton", "hull", "york", "bath",
    "exeter", "norwich", "ipswich", "milton keynes", "northampton", "peterborough", "luton", "swindon",
    "gloucester", "cheltenham", "worcester", "hereford", "lincoln", "preston", "blackpool", "bolton", "wigan",
    "stockport", "warrington", "chester", "lancaster", "carlisle", "durham", "middlesbrough", "huddersfield",
    "wakefield", "doncaster", "rotherham", "barnsley", "harrogate", "edinburgh", "glasgow", "aberdeen", "dundee",
    "inverness", "stirling", "perth", "cardiff", "swansea", "newport", "wrexham", "belfast", "derry",
    "canterbury", "maidstone", "guildford", "slough", "watford", "st albans", "chelmsford", "colchester",
    "southend", "solihull", "walsall", "dudley", "telford", "shrewsbury", "bournemouth", "poole",
}
_PLACE = re.compile(r"\b(" + "|".join(re.escape(p) for p in sorted(PLACES, key=len, reverse=True)) + r")\b",
                    re.IGNORECASE)
_SEPARATORS = " ,|-:;"


@dataclass
class Section:
    kind: str
    lines: list[str] = field(default_factory=list)


def extract(text: str) -> Draft:
    lines = text.split("\n")
    header, sections = split_sections(lines)
    draft = Draft()
    hints: dict[str, Hint] = {}

    name, name_index, hint = find_name(header)
    draft.name = name
    email, email_hint = find_email(header, text)
    draft.email = email
    if name and hint:
        hint = _boost_name(name, email, hint)
        hints["name"] = hint
    if email:
        hints["email"] = email_hint
    draft.phone, hints["phone"] = find_phone(header, text)
    draft.location, hints["location"] = find_location(header, skip=name_index)
    draft.headline, hints["headline"] = find_headline(header, name_index)
    draft.links = find_links(text)
    draft.skills = find_skills(_section_lines(sections, "skills"))
    draft.experience = find_roles(_section_lines(sections, "experience"))
    draft.education = find_education(_section_lines(sections, "education"))
    draft.hints = {k: v for k, v in hints.items() if v is not None}
    return draft


# --- sections ----------------------------------------------------------------------------

def heading_kind(line: str) -> str | None:
    key = re.sub(r"\s+", " ", line.strip().rstrip(":").strip()).casefold()
    if not key or len(key) > 40:
        return None
    for kind, names in SECTIONS.items():
        if key in names:
            return kind
    return None


def split_sections(lines: list[str]) -> tuple[list[str], list[Section]]:
    """Lines before the first heading are the header (name, contact details)."""
    header: list[str] = []
    sections: list[Section] = []
    for line in lines:
        kind = heading_kind(line)
        if kind:
            sections.append(Section(kind))
        elif sections:
            sections[-1].lines.append(line)
        else:
            header.append(line)
    return header, sections


def _section_lines(sections: list[Section], kind: str) -> list[str]:
    out: list[str] = []
    for section in sections:
        if section.kind == kind:
            out.extend(section.lines + [""])
    return out


def _labelled(lines: list[str], kind: str) -> str | None:
    for line in lines:
        match = LABEL.match(line)
        if match:
            label = re.sub(r"\s+", " ", match.group("label").replace("-", "")).strip().casefold()
            if label in LABELS[kind]:
                return match.group("value").strip()
    return None


# --- header fields -----------------------------------------------------------------------

_NAME_WORD = re.compile(r"[^\W\d_][^\W\d_'.-]*")


def looks_like_name(line: str) -> bool:
    words = line.split()
    return (2 <= len(words) <= 4 and all(_NAME_WORD.fullmatch(w) for w in words)
            and not TITLE_WORDS.search(line) and heading_kind(line) is None)


def find_name(header: list[str]) -> tuple[str | None, int, Hint | None]:
    labelled = _labelled(header, "name")
    if labelled:
        return labelled, -1, Hint(confidence=0.95)
    for index, line in enumerate(header):
        if not line or line.casefold() in NOT_NAMES:
            continue
        if looks_like_name(line):
            return line, index, Hint(confidence=0.8, reason="taken from the first line of the CV")
        break  # only the first real line can be the name; don't go hunting
    return None, -1, None


def _boost_name(name: str, email: str | None, hint: Hint) -> Hint:
    if not email or hint.confidence >= 0.95:
        return hint
    local = re.sub(r"[^a-z]", "", email.split("@")[0].casefold())
    parts = [re.sub(r"[^a-z]", "", w.casefold()) for w in name.split()]
    if any(len(p) > 2 and p in local for p in parts):
        return Hint(confidence=0.95)
    return hint


def find_email(header: list[str], text: str) -> tuple[str | None, Hint | None]:
    labelled = _labelled(header, "email")
    match = EMAIL.search(labelled) if labelled else None
    match = match or EMAIL.search(text)
    return (match.group().rstrip("."), Hint(confidence=0.95)) if match else (None, None)


def find_phone(header: list[str], text: str) -> tuple[str | None, Hint | None]:
    labelled = _labelled(header, "phone")
    if labelled:
        found = re.search(r"[+\d][\d ().-]*\d", labelled)
        if found:
            return found.group(), Hint(confidence=0.9)
    for match in PHONE.finditer(text):
        if 10 <= len(digits(match.group())) <= 15:
            return match.group(), Hint(confidence=0.9)
    return None, None


def town_only(value: str) -> str | None:
    """Keep the town and county from an address; drop house numbers, streets and postcodes."""
    parts = [p.strip() for p in value.split(",")]
    keep = [p for p in parts if p and not re.search(r"\d", p) and not STREET.search(p)]
    return ", ".join(keep[-2:]) or None


def find_location(header: list[str], skip: int = -1) -> tuple[str | None, Hint | None]:
    labelled = _labelled(header, "location")
    if labelled:
        town = town_only(labelled)
        return (town, Hint(confidence=0.9)) if town else (None, None)
    for index, line in enumerate(header):
        if index == skip:  # "Chester Bennett" is a name, not a place
            continue
        for segment in re.split(r"\s*[|•·]\s*", line):
            if _PLACE.search(segment) and "@" not in segment:
                town = town_only(segment)
                if town:
                    return town, Hint(confidence=0.8)
    return None, None


def _is_contact(line: str) -> bool:
    return bool("@" in line or "http" in line.casefold() or "linkedin" in line.casefold()
                or "github" in line.casefold() or PHONE.search(line) or LABEL.match(line))


def find_headline(header: list[str], name_index: int) -> tuple[str | None, Hint | None]:
    if name_index < 0:
        return None, None
    for line in header[name_index + 1:]:
        if not line or _is_contact(line):
            continue
        if len(line.split()) > 8 or re.search(r"\d", line) or _PLACE.search(line):
            return None, None
        if TITLE_WORDS.search(line):
            return line, Hint(confidence=0.75)
        return line, Hint(confidence=0.55, reason="may not be a job title")
    return None, None


def find_links(text: str) -> list[str]:
    out, seen = [], set()
    for match in LINK.finditer(text):
        link = match.group().rstrip("/.")
        key = re.sub(r"^(https?://)?(www\.)?", "", link.casefold())
        if key not in seen:
            seen.add(key)
            out.append(link)
    return out


# --- skills --------------------------------------------------------------------------------

def _split_top_level(text: str) -> list[str]:
    """Split on , ; | and bullets, but not inside brackets: 'Python (pandas, numpy)' is one skill."""
    parts, depth, current = [], 0, []
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and char in ",;|•·":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts]


def _is_prose(line: str) -> bool:
    """Skill lists don't end in full stops; sentences such as 'References available on request.' do."""
    return bool(re.search(r"[.!?]\s+[A-Z]", line)) or (line.endswith(".") and len(line.split()) >= 3)


def find_skills(lines: list[str]) -> list[str]:
    out, seen = [], set()
    for line in lines:
        line = line.removeprefix("- ").strip()
        if not line or _is_prose(line):
            continue
        label = SKILL_LABEL.match(line)
        if label and len(label.group("label").split()) <= 4:  # "Cloud: AWS, GCP"
            line = label.group("value")
        for item in _split_top_level(line):
            item = item.strip(" .")
            if not item or len(item) > 60 or len(item.split()) > 6:
                continue
            if item.casefold() not in seen:
                seen.add(item.casefold())
                out.append(item)
    return out


# --- experience ----------------------------------------------------------------------------

def _is_bullet(line: str) -> bool:
    return line.startswith("- ")


def _strip_seps(text: str) -> str:
    return text.strip(_SEPARATORS + " ")


def _drop_trailing_brackets(text: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()


def split_title_company(parts: list[str]) -> tuple[str | None, str | None, Hint | None]:
    """Decide which part is the job title and which the employer."""
    parts = [p for p in (_strip_seps(p) for p in parts) if p]
    if not parts:
        return None, None, None
    if len(parts) == 1:
        text = parts[0]
        if m := re.match(r"^(?P<title>.+?)\s+at\s+(?P<company>.+)$", text, re.IGNORECASE):
            return m.group("title"), _drop_trailing_brackets(m.group("company")), None
        for sep in (" | ", " - ", ", "):
            if sep in text:
                return split_title_company(text.split(sep))
        if TITLE_WORDS.search(text):
            return text, None, None
        return None, _drop_trailing_brackets(text), None
    titled = [i for i, p in enumerate(parts) if TITLE_WORDS.search(p)]
    if titled:
        t = titled[0]
        others = [p for i, p in enumerate(parts) if i != t]
        return parts[t], _drop_trailing_brackets(others[0]), None
    hint = Hint(confidence=0.6, reason="couldn't tell the job title from the employer")
    return parts[0], _drop_trailing_brackets(parts[1]), hint


def find_roles(lines: list[str]) -> list[DraftRole]:
    dated = [i for i, line in enumerate(lines) if not _is_bullet(line) and RANGE.search(line)]
    roles: list[tuple[int, int, DraftRole]] = []  # (header start, date line, role)
    for k, i in enumerate(dated):
        match = RANGE.search(lines[i])
        rest = _strip_seps(lines[i][:match.start()] + " " + lines[i][match.end():])
        rest = re.sub(r"\(\s*\)", "", rest).strip()
        boundary = dated[k - 1] if k else -1
        back: list[str] = []
        j = i - 1
        while j > boundary and len(back) < 2 and lines[j] and not _is_bullet(lines[j]):
            back.insert(0, lines[j])
            j -= 1
        rest_parts = [p for p in re.split(r" \| | - |, | at ", rest) if p.strip()] if rest else []
        if len(rest_parts) >= 2 or (rest and not back):
            title, company, hint = split_title_company([rest])
            start = i
        elif len(back) == 2 and not rest:
            first, second = back
            if TITLE_WORDS.search(second) and not TITLE_WORDS.search(first):
                first, second = second, first
            title, company, hint = first, _drop_trailing_brackets(second.split(",")[0]), None
            start = j + 1
        else:
            title, company, hint = split_title_company([*back[-1:], rest] if rest else back[-1:])
            start = i - len(back[-1:])
        roles.append((start, i, DraftRole(title=title, company=company, dates=match.group(), hint=hint)))

    out = []
    for n, (_, date_line, role) in enumerate(roles):
        stop = roles[n + 1][0] if n + 1 < len(roles) else len(lines)
        role.highlights = [line[2:].strip() for line in lines[date_line + 1:stop] if _is_bullet(line)][:6]
        out.append(role)
    return out


# --- education -----------------------------------------------------------------------------

def _take_dates(text: str) -> tuple[str | None, str]:
    match = RANGE.search(text) or YEAR.search(text)
    if not match:
        return None, text
    rest = text[:match.start()] + text[match.end():]
    rest = re.sub(r"\(\s*\)", "", rest)
    return match.group(), _strip_seps(rest)


def find_education(lines: list[str]) -> list[DraftEducation]:
    entries: list[DraftEducation] = []
    current: DraftEducation | None = None
    for raw in lines:
        line = raw.removeprefix("- ").strip()
        if not line:
            continue
        dates, body = _take_dates(line)
        has_q, has_i = bool(QUALIFICATION.search(body)), bool(INSTITUTION.search(body))
        if not (has_q or has_i):
            if current and dates and not body and current.dates is None:
                current.dates = dates
            continue
        if current is None or (has_q and current.qualification) or (has_i and not has_q and current.institution):
            current = DraftEducation()
            entries.append(current)
        if has_q and has_i:
            pieces = [p.strip() for p in re.split(r",| - | \| ", body) if p.strip()]
            inst = next((p for p in pieces if INSTITUTION.search(p)), None)
            qual = ", ".join(p for p in pieces if p != inst)
            current.institution, current.qualification = inst, qual or None
        elif has_q:
            current.qualification = body
        else:
            current.institution = body
        if dates:
            current.dates = dates
    return entries
