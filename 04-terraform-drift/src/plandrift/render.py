"""Render a report as a terminal table, Markdown (for a PR comment or job summary) or JSON.

Every renderer goes through diff.show_value or _json_value, neither of which can reveal a
sensitive value: diff.py has already replaced it with HIDDEN.
"""

from __future__ import annotations

import html
import json
from typing import Any

from . import __version__
from .diff import ABSENT, HIDDEN, UNKNOWN, AttrChange, show_value
from .plan import Change
from .report import Report

DRIFT_WORDS = {"update": "changed", "delete": "deleted", "create": "created", "replace": "replaced"}
CHANGE_WORDS = {
    "create": "create",
    "update": "update in place",
    "delete": "delete",
    "replace": "replace",
    "move": "move",
    "import": "import",
    "forget": "forget (remove from state)",
}
MAX_TABLE_ATTRS = 12

_COLOURS = {"+": "32", "-": "31", "~": "33", "-/+": "35", ">": "36", "<-": "36", "x": "31"}


def headline(report: Report) -> str:
    d, c = len(report.drift), len(report.changes)
    drift = f"{d} changed outside Terraform"
    changes = f"{c} pending change{'s' if c != 1 else ''}"
    if d and c:
        return f"Drift found: {drift}, {changes}."
    if d:
        return f"Drift found: {drift}."
    if c:
        return f"No drift outside Terraform; {changes} in the plan."
    return "No drift. Infrastructure matches the state, and the plan has no changes."


def _context(report: Report) -> str:
    plan = report.plan
    parts = [f"plandrift {__version__}"]
    if plan.terraform_version:
        parts.append(f"Terraform {plan.terraform_version}")
    if plan.timestamp:
        parts.append(f"plan {plan.timestamp.replace('T', ' ').replace('Z', ' UTC')}")
    return ", ".join(parts)


# --- terminal ---------------------------------------------------------------------------


def table(report: Report, exit_code: int, color: bool = False) -> str:
    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color and code else text

    lines = [paint(_context(report), "2"), ""]
    if not report.plan.complete:
        lines += ["Note: Terraform marked this plan incomplete (deferred changes), so it may not show everything.", ""]

    for title, items, words in (
        ("Changed outside Terraform", report.drift, DRIFT_WORDS),
        ("Pending changes", report.changes, CHANGE_WORDS),
    ):
        if not items:
            lines += [paint(f"{title}: none", "1"), ""]
            continue
        lines.append(paint(f"{title} ({len(items)})", "1"))
        width = min(max(len(c.address) for c in items), 60)
        for change in items:
            what = words.get(change.action, change.action)
            if change.notes:
                what += f" ({'; '.join(change.notes)})"
            symbol = paint(f"{change.symbol:<3}", _COLOURS.get(change.symbol, ""))
            lines.append(f"  {symbol} {change.address:<{width}}  {what}")
            lines += _table_attrs(change, paint)
        lines.append("")

    if report.ignored:
        more = len(report.ignored) - 5
        shown = ", ".join(report.ignored[:5]) + (f" and {more} more" if more > 0 else "")
        lines += [paint(f"Ignored by rule: {shown}", "2"), ""]

    found = report.has_drift or report.has_changes
    lines.append(paint(f"{headline(report)} Exit {exit_code}.", "1;31" if found else "1;32"))
    return "\n".join(lines) + "\n"


def _table_attrs(change: Change, paint) -> list[str]:
    if change.action == "update" and not change.attributes:
        return [paint("        (no attribute-level differences to show)", "2")]
    attrs = change.attributes[:MAX_TABLE_ATTRS]
    if not attrs:
        return []
    width = min(max(len(a.name) for a in attrs), 40)
    out = []
    for a in attrs:
        before = paint(show_value(a.before), "2" if a.sensitive else "31")
        after = paint(show_value(a.after), "2" if a.sensitive else "32")
        tail = paint("  forces replacement", "35") if a.forces_replacement else ""
        out.append(f"        {a.name:<{width}}  {before} -> {after}{tail}")
    hidden = len(change.attributes) - len(attrs)
    if hidden:
        out.append(paint(f"        ... and {hidden} more (--format json shows all)", "2"))
    return out


# --- markdown ---------------------------------------------------------------------------


def _code(text: str) -> str:
    # HTML-escaped <code> survives pipes, backticks and angle brackets inside table cells.
    return "<code>" + html.escape(text, quote=False).replace("|", "&#124;") + "</code>"


def markdown(report: Report, exit_code: int) -> str:
    out = [f"## {html.escape(headline(report))}", "", f"<sub>{html.escape(_context(report))}. Exit {exit_code}.</sub>"]
    if not report.plan.complete:
        out += ["", "> Terraform marked this plan incomplete (deferred changes), so it may not show everything."]

    for title, items, words in (
        ("Changed outside Terraform", report.drift, DRIFT_WORDS),
        ("Pending changes", report.changes, CHANGE_WORDS),
    ):
        out += ["", f"### {title} ({len(items)})", ""]
        if not items:
            out.append("None.")
            continue
        out += ["| | Resource | What | Attributes |", "| --- | --- | --- | --- |"]
        for change in items:
            what = words.get(change.action, change.action)
            if change.notes:
                what += f" ({'; '.join(change.notes)})"
            count = len(change.attributes)
            out.append(
                f"| {_code(change.symbol)} | {_code(change.address)} | {html.escape(what)} | {count or ''} |"
            )
        for change in items:
            if change.attributes:
                out += ["", *_md_attrs(change)]

    if report.ignored:
        out += ["", f"<sub>Ignored by rule: {html.escape(', '.join(report.ignored))}</sub>"]
    out += ["", "<sub>Sensitive values are never shown, only that they changed.</sub>"]
    return "\n".join(out) + "\n"


def _md_attrs(change: Change) -> list[str]:
    n = len(change.attributes)
    rows = [
        f"<details><summary>{_code(change.address)}: {n} attribute{'s' if n != 1 else ''}</summary>",
        "",
        "| Attribute | Before | After | |",
        "| --- | --- | --- | --- |",
    ]
    for a in change.attributes:
        flag = "forces replacement" if a.forces_replacement else ""
        rows.append(
            f"| {_code(a.name)} | {_code(show_value(a.before, 120))} | {_code(show_value(a.after, 120))} | {flag} |"
        )
    rows += ["", "</details>"]
    return rows


# --- json -------------------------------------------------------------------------------


def as_json(report: Report, exit_code: int) -> str:
    doc = {
        "schema_version": 1,
        "tool": {"name": "plandrift", "version": __version__},
        "terraform_version": report.plan.terraform_version,
        "plan_format_version": report.plan.format_version,
        "plan_timestamp": report.plan.timestamp,
        "plan_complete": report.plan.complete,
        "summary": {
            "drift": len(report.drift),
            "changes": len(report.changes),
            "ignored": len(report.ignored),
            "exit_code": exit_code,
            "headline": headline(report),
        },
        "drift": [_json_change(c) for c in report.drift],
        "changes": [_json_change(c) for c in report.changes],
        "ignored": list(report.ignored),
    }
    return json.dumps(doc, indent=2) + "\n"


def _json_change(change: Change) -> dict:
    return {
        "address": change.address,
        "type": change.resource_type,
        "action": change.action,
        "notes": list(change.notes),
        "attributes": [_json_attr(a) for a in change.attributes],
    }


def _json_attr(a: AttrChange) -> dict:
    return {
        "path": a.name,
        "path_parts": list(a.path),
        "kind": a.kind,
        "before": _json_value(a.before),
        "after": _json_value(a.after),
        "sensitive": a.sensitive,
        "unknown": a.after is UNKNOWN,
        "forces_replacement": a.forces_replacement,
    }


def _json_value(value: Any) -> Any:
    return None if value is ABSENT or value is UNKNOWN or value is HIDDEN else value


RENDERERS = {"table": table, "markdown": markdown, "json": as_json}
