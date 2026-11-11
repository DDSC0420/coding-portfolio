"""Phone numbers: find them, and turn UK numbers into one standard form (E.164)."""

import re

# A + or 0, then digits with the separators people actually type.
CANDIDATE = re.compile(r"(?<![\w+])(?:\+\d|0)[\d ().-]{6,18}\d(?!\w)")


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def normalise(raw: str) -> tuple[str, str | None]:
    """Return ``(value, problem)``. UK numbers become ``+44...``; problem is None when valid."""
    compact = re.sub(r"[\s().-]", "", raw)
    if not re.fullmatch(r"\+?\d+", compact):
        return raw.strip(), "contains characters that aren't part of a phone number"
    if compact.startswith("+44") or compact.startswith("0044"):
        national = compact[3:] if compact.startswith("+44") else compact[4:]
        national = "0" + national.removeprefix("0")  # "+44 (0)7700..." is common
    elif compact.startswith("+"):
        if 8 <= len(compact) - 1 <= 15:
            return compact, None
        return raw.strip(), f"an international number should have 8 to 15 digits, this has {len(compact) - 1}"
    elif compact.startswith("0"):
        national = compact
    else:
        return raw.strip(), "doesn't start with 0 or a + country code"
    if len(national) != 11:
        return raw.strip(), f"a UK number has 11 digits, this has {len(national)}"
    if national[1] not in "12378":
        return raw.strip(), "isn't a UK landline or mobile number"
    return "+44" + national[1:], None


def same_number(a: str, b: str) -> bool:
    """True when two written forms are the same number (e.g. ``07700 900123`` and ``+44 7700 900123``)."""
    (na, pa), (nb, pb) = normalise(a), normalise(b)
    if pa is None and pb is None:
        return na == nb
    return digits(a) == digits(b)
