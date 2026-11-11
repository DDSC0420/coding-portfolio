"""Read Terraform plan JSON into plain records. No I/O, no framework.

Input is what `terraform show -json plan.out` prints. Two lists matter:

- resource_drift: objects that changed outside Terraform since the last apply
- resource_changes: what applying this plan would do
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .diff import AttrChange, attribute_changes


class PlanError(ValueError):
    """The input isn't a plan this tool can read. The message says what to do instead."""


SYMBOLS = {
    "create": "+",
    "update": "~",
    "delete": "-",
    "replace": "-/+",
    "move": ">",
    "import": "<-",
    "forget": "x",
}

# Why Terraform chose an action, in plain words. Unlisted reasons are shown as given.
REASONS = {
    "replace_because_tainted": "tainted",
    "replace_because_cannot_update": "a changed attribute forces replacement",
    "replace_by_request": "replacement requested with -replace",
    "replace_by_triggers": "replace_triggered_by",
    "delete_because_no_resource_config": "removed from the configuration",
    "delete_because_no_module": "its module was removed",
    "delete_because_wrong_repetition": "count/for_each changed",
    "delete_because_count_index": "count index no longer exists",
    "delete_because_each_key": "for_each key no longer exists",
    "delete_because_no_move_target": "moved block points nowhere",
}


@dataclass(frozen=True)
class Change:
    address: str
    action: str
    resource_type: str
    attributes: tuple[AttrChange, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def symbol(self) -> str:
        return SYMBOLS.get(self.action, "?")


@dataclass(frozen=True)
class Plan:
    format_version: str
    terraform_version: str | None
    timestamp: str | None
    drift: tuple[Change, ...]
    changes: tuple[Change, ...]
    complete: bool = True


def decode(raw: bytes, source: str = "input") -> Any:
    """Bytes to JSON, with an error that says what to do if it's the wrong kind of file."""
    if raw[:2] == b"PK":
        raise PlanError(
            f"{source} is a binary plan file, not JSON. Convert it first with "
            f"`terraform show -json {source} > plan.json`, or let plandrift run Terraform: `plandrift run`."
        )
    if not raw.strip():
        raise PlanError(f"{source} is empty.")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise PlanError(f"{source} isn't UTF-8 text, so it can't be plan JSON.") from exc
    except json.JSONDecodeError as exc:
        raise PlanError(f"{source} isn't valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}).") from exc


_PLAN_KEYS = ("planned_values", "resource_changes", "resource_drift", "prior_state", "configuration")


def parse_plan(data: Any) -> Plan:
    if not isinstance(data, dict):
        raise PlanError("expected a JSON object from `terraform show -json`, got something else.")
    if "values" in data and not any(k in data for k in _PLAN_KEYS):
        raise PlanError(
            "this is state JSON, not a plan. plandrift needs a plan: "
            "`terraform plan -refresh-only -out=plan.out && terraform show -json plan.out > plan.json`."
        )
    version = data.get("format_version")
    if not isinstance(version, str) or not any(k in data for k in _PLAN_KEYS):
        raise PlanError("this doesn't look like Terraform plan JSON (no format_version or plan sections).")
    if version.split(".")[0] not in ("0", "1"):
        raise PlanError(f"plan format_version {version} is newer than this tool reads (0.x and 1.x).")
    if data.get("errored") is True:
        raise PlanError("Terraform says this plan errored, so a report would be incomplete. Fix the plan first.")

    return Plan(
        format_version=version,
        terraform_version=_str(data.get("terraform_version")),
        timestamp=_str(data.get("timestamp")),
        drift=_changes(data, "resource_drift"),
        changes=_changes(data, "resource_changes"),
        complete=data.get("complete") is not False,
    )


def _changes(data: dict, key: str) -> tuple[Change, ...]:
    entries = data.get(key) or []
    if not isinstance(entries, list):
        raise PlanError(f"`{key}` should be a list.")
    out = []
    for i, entry in enumerate(entries):
        change = _change(entry, f"{key}[{i}]")
        if change is not None:
            out.append(change)
    return tuple(out)


def _change(entry: Any, where: str) -> Change | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("change"), dict):
        raise PlanError(f"{where} is malformed: expected an object with a `change`.")
    if entry.get("mode") == "data":
        return None  # data sources are reads, not infrastructure

    change = entry["change"]
    actions = tuple(change.get("actions") or ())
    address = _str(entry.get("address")) or "(unnamed)"
    previous = _str(entry.get("previous_address"))
    importing = isinstance(change.get("importing"), dict)

    action = _action(actions, moved=bool(previous and previous != address), importing=importing)
    if action is None:
        return None

    notes = []
    if entry.get("deposed"):
        notes.append(f"deposed object {entry['deposed']}")
    if previous and previous != address:
        notes.append(f"from {previous}" if action == "move" else f"moved from {previous}")
    if importing and action != "import":
        notes.append("importing")
    reason = _str(entry.get("action_reason"))
    if reason:
        notes.append(REASONS.get(reason, reason.replace("_", " ")))

    attributes: list[AttrChange] = []
    if action in ("update", "replace", "import"):
        attributes = attribute_changes(change)

    return Change(
        address=address,
        action=action,
        resource_type=_str(entry.get("type")) or "",
        attributes=tuple(attributes),
        notes=tuple(notes),
    )


def _action(actions: tuple, moved: bool, importing: bool) -> str | None:
    if actions == ("create",):
        return "create"
    if actions == ("update",):
        return "update"
    if actions == ("delete",):
        return "delete"
    if actions in (("delete", "create"), ("create", "delete")):
        return "replace"
    if actions == ("forget",):
        return "forget"
    if actions in ((), ("no-op",), ("read",)):
        if importing:
            return "import"
        if moved:
            return "move"
        return None
    return "+".join(str(a) for a in actions)


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
