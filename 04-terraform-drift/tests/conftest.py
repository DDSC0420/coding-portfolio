import copy
import io
import json
import sys
from pathlib import Path

import pytest

from plandrift.cli import main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def plan_doc(drift=(), changes=(), **extra) -> dict:
    """A minimal valid plan document."""
    doc = {"format_version": "1.2", "terraform_version": "1.9.8", "planned_values": {"root_module": {}}}
    if drift:
        doc["resource_drift"] = list(drift)
    if changes:
        doc["resource_changes"] = list(changes)
    doc.update(extra)
    return doc


def entry(address, actions, before=None, after=None, mode="managed", **change_extra) -> dict:
    type_, _, name = address.rpartition(".")
    change = {"actions": list(actions), "before": before, "after": after, **change_extra}
    return {"address": address, "mode": mode, "type": type_.split(".")[-1], "name": name, "change": change}


@pytest.fixture
def cli(capsys, monkeypatch):
    """Run the CLI in-process. Returns (exit code, stdout, stderr)."""

    def run(*args, stdin: bytes | None = None):
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin)))
        code = main(list(args))
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


@pytest.fixture
def write_plan(tmp_path):
    def write(doc, name="plan.json") -> str:
        path = tmp_path / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        return str(path)

    return write


@pytest.fixture
def deepcopy():
    return copy.deepcopy
