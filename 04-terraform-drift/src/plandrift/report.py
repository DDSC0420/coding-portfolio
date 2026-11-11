"""Turn a parsed plan into a report: apply ignore rules, count, decide the exit code."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .plan import Change, Plan

EXIT_CLEAN = 0
EXIT_ERROR = 1
EXIT_FOUND = 2

FAIL_ON = ("any", "drift", "never")


@dataclass(frozen=True)
class IgnoreRule:
    """`ADDRESS` or `ADDRESS:ATTRIBUTE`. Only * and ? are wildcards; [ ] and . are literal."""

    text: str
    address: re.Pattern
    attribute: re.Pattern | None

    def matches_resource(self, address: str) -> bool:
        return self.attribute is None and bool(self.address.fullmatch(address))

    def matches_attribute(self, address: str, attribute: str) -> bool:
        if self.attribute is None or not self.address.fullmatch(address):
            return False
        # tags matches tags.Owner too: a rule on a parent covers everything under it.
        return any(self.attribute.fullmatch(p) for p in _parents(attribute))


def parse_rule(text: str) -> IgnoreRule:
    text = text.strip()
    split = _rule_colon(text)
    address, attribute = (text, None) if split < 0 else (text[:split], text[split + 1 :])
    if not address or attribute == "":
        raise ValueError(f"ignore rule {text!r} should be ADDRESS or ADDRESS:ATTRIBUTE")
    return IgnoreRule(text, _glob(address), _glob(attribute) if attribute else None)


def _rule_colon(text: str) -> int:
    """Index of the colon that separates address from attribute, skipping any inside [ "..." ]."""
    depth, quoted, escaped = 0, False, False
    for i, ch in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(depth - 1, 0)
        elif ch == ":" and depth == 0:
            return i
    return -1


def _glob(pattern: str) -> re.Pattern:
    parts = []
    for ch in pattern:
        parts.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
    return re.compile("".join(parts), re.DOTALL)


def _parents(name: str) -> list[str]:
    """tags["a.b"].x -> [tags, tags["a.b"], tags["a.b"].x], splitting only outside brackets."""
    cuts = []
    depth, quoted = 0, False
    for i, ch in enumerate(name):
        if quoted:
            quoted = ch != '"' or name[i - 1] == "\\"
        elif ch == '"':
            quoted = True
        elif ch == "[":
            if depth == 0 and i:
                cuts.append(i)
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "." and depth == 0:
            cuts.append(i)
    return [name[:c] for c in cuts] + [name]


@dataclass(frozen=True)
class Report:
    plan: Plan
    drift: tuple[Change, ...]
    changes: tuple[Change, ...]
    ignored: tuple[str, ...] = ()

    @property
    def has_drift(self) -> bool:
        return bool(self.drift)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


def build_report(plan: Plan, rules: list[IgnoreRule] | tuple[IgnoreRule, ...] = ()) -> Report:
    ignored: list[str] = []
    drift = _apply(plan.drift, rules, ignored)
    changes = _apply(plan.changes, rules, ignored)
    return Report(plan, drift, changes, tuple(ignored))


def _apply(items: tuple[Change, ...], rules, ignored: list[str]) -> tuple[Change, ...]:
    kept = []
    for change in items:
        if any(r.matches_resource(change.address) for r in rules):
            ignored.append(change.address)
            continue
        attrs = tuple(
            a for a in change.attributes if not any(r.matches_attribute(change.address, a.name) for r in rules)
        )
        dropped = len(change.attributes) - len(attrs)
        ignored.extend(f"{change.address}:{a.name}" for a in change.attributes if a not in attrs)
        if dropped and not attrs and change.action == "update":
            continue  # every difference was ignored, so nothing is left to report
        kept.append(replace(change, attributes=attrs) if dropped else change)
    return tuple(kept)


def exit_code(report: Report, fail_on: str = "any") -> int:
    if fail_on == "never":
        return EXIT_CLEAN
    if fail_on == "drift":
        return EXIT_FOUND if report.has_drift else EXIT_CLEAN
    return EXIT_FOUND if report.has_drift or report.has_changes else EXIT_CLEAN
