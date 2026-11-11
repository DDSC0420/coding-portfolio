"""Attribute-level differences between the before and after values of one change.

Pure functions over the JSON Terraform emits. Two rules matter more than the rest:

- A value marked sensitive never leaves this module. Only the fact that it changed does.
- A value Terraform won't know until apply is reported as unknown, not as null.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

Step = str | int
Path = tuple[Step, ...]


class _Marker:
    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return self._name


ABSENT = _Marker("ABSENT")  # the key or list element doesn't exist on this side
UNKNOWN = _Marker("UNKNOWN")  # known only after apply
HIDDEN = _Marker("HIDDEN")  # sensitive: the value is withheld


@dataclass(frozen=True)
class AttrChange:
    path: Path
    before: Any
    after: Any
    sensitive: bool = False
    forces_replacement: bool = False

    @property
    def name(self) -> str:
        return format_path(self.path)

    @property
    def kind(self) -> str:
        if self.before is ABSENT:
            return "added"
        if self.after is ABSENT:
            return "removed"
        return "changed"


def attribute_changes(change: dict) -> list[AttrChange]:
    """Every leaf that differs between change["before"] and change["after"]."""
    walker = _Walker(change)
    walker.walk(_side(change.get("before")), _side(change.get("after")), ())
    return walker.out


def _side(value: Any) -> Any:
    return ABSENT if value is None else value


class _Walker:
    def __init__(self, change: dict) -> None:
        self.unknown = change.get("after_unknown")
        self.sensitive = [change.get("before_sensitive"), change.get("after_sensitive")]
        self.replace = [tuple(p) for p in change.get("replace_paths") or [] if isinstance(p, list)]
        self.out: list[AttrChange] = []

    def walk(self, before: Any, after: Any, path: Path) -> None:
        if _marked(self.unknown, path):
            self._emit(path, before, UNKNOWN)
            return
        if any(_marked(m, path) for m in self.sensitive):
            if before != after:
                self._emit(path, before, after, sensitive=True)
            return
        if before == after and not _contains_mark(_node(self.unknown, path)):
            return

        container = _container_type(before, after, self.unknown, path)
        if container is dict:
            b = before if isinstance(before, dict) else {}
            a = after if isinstance(after, dict) else {}
            keys = set(b) | set(a) | set(_node(self.unknown, path) or {})
            for key in sorted(keys, key=str):
                self.walk(b.get(key, ABSENT), a.get(key, ABSENT), (*path, key))
            return
        if container is list:
            b = before if isinstance(before, list) else []
            a = after if isinstance(after, list) else []
            unknown_node = _node(self.unknown, path)
            length = max(len(b), len(a), len(unknown_node) if isinstance(unknown_node, list) else 0)
            for i in range(length):
                self.walk(b[i] if i < len(b) else ABSENT, a[i] if i < len(a) else ABSENT, (*path, i))
            return

        # A leaf, or a value whose type changed. If anything at or below this path is
        # sensitive, withhold the whole value rather than risk printing part of it.
        hidden = any(_contains_mark(_node(m, path)) for m in self.sensitive)
        self._emit(path, before, after, sensitive=hidden)

    def _emit(self, path: Path, before: Any, after: Any, sensitive: bool = False) -> None:
        if sensitive:
            before = _hide(before)
            after = _hide(after)
        forces = any(path[: len(r)] == r or r[: len(path)] == path for r in self.replace)
        self.out.append(AttrChange(path, before, after, sensitive, forces))


def _hide(value: Any) -> Any:
    return value if value is ABSENT or value is UNKNOWN else HIDDEN


def _container_type(before: Any, after: Any, unknown: Any, path: Path) -> type | None:
    """dict or list when both sides are that container (or missing or null), else None."""
    sides = [v for v in (before, after) if v is not ABSENT and v is not None]
    if sides and all(isinstance(v, dict) for v in sides):
        return dict
    if sides and all(isinstance(v, list) for v in sides):
        return list
    if not sides:
        node = _node(unknown, path)
        if isinstance(node, (dict, list)):
            return type(node)
    return None


def _node(marks: Any, path: Path) -> Any:
    """The part of a marks structure (after_unknown, *_sensitive) at path."""
    node = marks
    for step in path:
        if node is True:
            return True
        if isinstance(node, dict) and isinstance(step, str):
            node = node.get(step)
        elif isinstance(node, list) and isinstance(step, int) and step < len(node):
            node = node[step]
        else:
            return None
    return node


def _marked(marks: Any, path: Path) -> bool:
    """True if path, or any parent of it, is marked."""
    node = marks
    for step in path:
        if node is True:
            return True
        if isinstance(node, dict) and isinstance(step, str):
            node = node.get(step)
        elif isinstance(node, list) and isinstance(step, int) and step < len(node):
            node = node[step]
        else:
            return False
    return node is True


def _contains_mark(node: Any) -> bool:
    if node is True:
        return True
    if isinstance(node, dict):
        return any(_contains_mark(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_mark(v) for v in node)
    return False


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def format_path(path: Path) -> str:
    """ingress[1].cidr_blocks[0], tags["kubernetes.io/role"]."""
    out = ""
    for step in path:
        if isinstance(step, int):
            out += f"[{step}]"
        elif _IDENT.match(step):
            out += f".{step}" if out else step
        else:
            out += f"[{json.dumps(step, ensure_ascii=False)}]"
    return out or "(whole value)"


def show_value(value: Any, limit: int = 80) -> str:
    """A value as it should appear in a report. Never reveals a HIDDEN value."""
    if value is ABSENT:
        return "(none)"
    if value is UNKNOWN:
        return "(known after apply)"
    if value is HIDDEN:
        return "(sensitive)"
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text
