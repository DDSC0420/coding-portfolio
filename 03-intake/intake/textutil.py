"""One way to clean CV text, and one way to find a value in it.

Every stage works on the same cleaned text, so evidence spans always point at what the
user sees in the page's "From the CV" pane.
"""

import re
import unicodedata

from .schema import Span

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'}
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿\x00"), None)
_BULLET = re.compile(r"^[•●▪◦‣⁃·■►➢*]\s*")
_SPACES = re.compile(r"[ \t\f\v  -   　]+")


def clean(text: str) -> str:
    """Normalise unicode, dashes, quotes, bullets and whitespace. Keeps line breaks."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_DASHES).translate(_QUOTES).translate(_INVISIBLE)
    lines = []
    for raw in text.split("\n"):
        line = _SPACES.sub(" ", raw).strip()
        line = _BULLET.sub("- ", line)
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _pattern(needle: str) -> re.Pattern | None:
    words = clean(needle).split()
    if not words:
        return None
    body = r"\s+".join(re.escape(word) for word in words)
    head = r"(?<!\w)" if re.match(r"\w", words[0]) else ""
    tail = r"(?!\w)" if re.search(r"\w$", words[-1]) else ""
    return re.compile(head + body + tail, re.IGNORECASE)


def locate(text: str, needle: str, start: int = 0) -> Span | None:
    """Find ``needle`` in ``text``: case-insensitive, whole words, any whitespace.

    Searches from ``start`` first, then from the top, so callers can walk a list of roles
    down the page without losing a value that appears out of order.
    """
    pattern = _pattern(needle)
    if pattern is None:
        return None
    match = pattern.search(text, start) or (pattern.search(text) if start else None)
    if match is None:
        return None
    return Span(start=match.start(), end=match.end(), text=match.group())


def locate_all(text: str, needle: str) -> list[Span]:
    """Every place ``needle`` appears, in order."""
    pattern = _pattern(needle)
    if pattern is None:
        return []
    return [Span(start=m.start(), end=m.end(), text=m.group()) for m in pattern.finditer(text)]


def locate_near(text: str, needle: str, anchor: int, start: int = 0) -> Span | None:
    """The occurrence of ``needle`` closest to ``anchor``, preferring ones at or after ``start``.

    A job title can also appear in the summary at the top; the right one is next to its dates.
    """
    spans = locate_all(text, needle)
    if not spans:
        return None
    later = [s for s in spans if s.start >= start] or spans
    return min(later, key=lambda s: abs(s.start - anchor))



def locate_list(text: str, needles: list[str], max_options: int = 20) -> list[Span | None]:
    """Find a list of items (such as skills), choosing occurrences that sit together.

    Skills are often also mentioned in the summary or a job bullet. Items in a list appear
    one after another, so this picks the occurrence of each item that keeps the whole list
    as tight as possible (a small dynamic programme over the candidates).
    """
    options = [locate_all(text, n)[:max_options] for n in needles]
    present = [i for i, opts in enumerate(options) if opts]
    if not present:
        return [None] * len(needles)
    cost = [0] * len(options[present[0]])
    pointers: list[list[int]] = []
    for prev_i, i in zip(present, present[1:], strict=False):
        prev, cur = options[prev_i], options[i]
        step_cost, step_ptr = [], []
        for span in cur:
            j = min(range(len(prev)), key=lambda k: cost[k] + abs(span.start - prev[k].end))
            step_cost.append(cost[j] + abs(span.start - prev[j].end))
            step_ptr.append(j)
        cost = step_cost
        pointers.append(step_ptr)
    j = min(range(len(cost)), key=cost.__getitem__)
    chosen: dict[int, Span] = {}
    for n in range(len(present) - 1, 0, -1):
        chosen[present[n]] = options[present[n]][j]
        j = pointers[n - 1][j]
    chosen[present[0]] = options[present[0]][j]
    return [chosen.get(i) for i in range(len(needles))]
