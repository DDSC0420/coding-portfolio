import json
import re

import pytest

from plandrift.plan import parse_plan
from plandrift.render import RENDERERS, as_json, headline, markdown, table
from plandrift.report import build_report, parse_rule

from .conftest import EXAMPLES, entry, example, plan_doc


def report_for(name, rules=()):
    return build_report(parse_plan(example(name)), [parse_rule(r) for r in rules])


def _sensitive_strings(doc) -> set[str]:
    """Every string value the plan marks sensitive, found by walking the *_sensitive marks."""
    found = set()

    def collect(value, marks):
        if marks is True:
            stack = [value]
            while stack:
                v = stack.pop()
                if isinstance(v, str) and len(v) > 3:
                    found.add(v)
                elif isinstance(v, dict):
                    stack.extend(v.values())
                elif isinstance(v, list):
                    stack.extend(v)
        elif isinstance(marks, dict) and isinstance(value, dict):
            for k, m in marks.items():
                collect(value.get(k), m)
        elif isinstance(marks, list) and isinstance(value, list):
            for v, m in zip(value, marks, strict=False):
                collect(v, m)

    for key in ("resource_drift", "resource_changes"):
        for rc in doc.get(key, []):
            ch = rc["change"]
            collect(ch.get("before"), ch.get("before_sensitive"))
            collect(ch.get("after"), ch.get("after_sensitive"))
    return found


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.json")), ids=lambda p: p.relative_to(EXAMPLES).as_posix())
@pytest.mark.parametrize("fmt", sorted(RENDERERS))
def test_no_sensitive_value_appears_in_any_format(path, fmt):
    doc = json.loads(path.read_text(encoding="utf-8"))
    secrets = _sensitive_strings(doc)
    report = build_report(parse_plan(doc))
    text = RENDERERS[fmt](report, 2)
    for secret in secrets:
        assert secret not in text


def test_the_secret_check_is_not_vacuous():
    secrets = _sensitive_strings(example("drift-and-changes.json"))
    assert {"hunter2-DO-NOT-PRINT", "sk-old-DO-NOT-PRINT-7f3a", "sk-new-DO-NOT-PRINT-91bc"} <= secrets


@pytest.mark.parametrize(
    ("drift", "changes", "expected"),
    [
        (1, 0, "Drift found: 1 changed outside Terraform."),
        (2, 1, "Drift found: 2 changed outside Terraform, 1 pending change."),
        (0, 3, "No drift outside Terraform; 3 pending changes in the plan."),
        (0, 0, "No drift. Infrastructure matches the state, and the plan has no changes."),
    ],
)
def test_headline(drift, changes, expected):
    plan = parse_plan(
        plan_doc(
            drift=[entry(f"a.d{i}", ["delete"], {"x": 1}, None) for i in range(drift)],
            changes=[entry(f"a.c{i}", ["create"], None, {"x": 1}) for i in range(changes)],
        )
    )
    assert headline(build_report(plan)) == expected


def test_table_lists_drift_with_attribute_diffs():
    text = table(report_for("console-changes.json"), 2)
    assert "Changed outside Terraform (4)" in text
    assert re.search(r"~\s+aws_security_group\.web\s+changed", text)
    assert re.search(r'ingress\[1\]\.from_port\s+\(none\) -> 22', text)
    assert re.search(r"-\s+aws_instance\.batch_worker\s+deleted", text)
    assert "Pending changes: none" in text
    assert text.rstrip().endswith("Exit 2.")


def test_table_sensitive_unknown_and_forced_replacement():
    text = table(report_for("drift-and-changes.json"), 2)
    assert re.search(r"secret_string\s+\(sensitive\) -> \(sensitive\)", text)
    assert re.search(r'public_ip\s+"18\.130\.4\.21" -> \(known after apply\)', text)
    assert re.search(r'ami\s+"ami-0c1a7f89451184c8b" -> "ami-0f9de6e2d2f067fca"\s+forces replacement', text)
    assert "-/+ aws_instance.web" in text
    assert "move (from aws_cloudwatch_log_group.this)" in text
    assert "data.aws_ami" not in text


def test_table_is_plain_ascii_without_colour():
    text = table(report_for("drift-and-changes.json"), 2, color=False)
    assert text.isascii() and "\033[" not in text


def test_table_colour_codes():
    text = table(report_for("console-changes.json"), 2, color=True)
    assert "\033[33m~  \033[0m" in text  # update in yellow
    assert "\033[1;31m" in text  # result line in bold red when something was found


def test_table_truncates_long_attribute_lists():
    before = {f"k{i:02}": i for i in range(30)}
    after = {f"k{i:02}": i + 1 for i in range(30)}
    plan = parse_plan(plan_doc(drift=[entry("a.b", ["update"], before, after)]))
    text = table(build_report(plan), 2)
    assert "... and 18 more (--format json shows all)" in text


def test_table_update_with_no_visible_differences():
    plan = parse_plan(plan_doc(drift=[entry("a.b", ["update"], {"tags": None}, {"tags": {}})]))
    assert "(no attribute-level differences to show)" in table(build_report(plan), 2)


def test_table_reports_ignored_items():
    text = table(report_for("console-changes.json", ["aws_instance.*"]), 2)
    assert "Ignored by rule: aws_instance.batch_worker" in text


def test_table_warns_about_incomplete_plans():
    plan = parse_plan(plan_doc(complete=False))
    assert "incomplete" in table(build_report(plan), 0)


def test_markdown_structure():
    text = markdown(report_for("drift-and-changes.json"), 2)
    assert text.startswith("## Drift found: 4 changed outside Terraform, 8 pending changes.")
    assert "### Changed outside Terraform (4)" in text and "### Pending changes (8)" in text
    assert "| <code>~</code> | <code>aws_security_group.web</code> | changed | 6 |" in text
    assert "<details><summary><code>aws_instance.web</code>: 5 attributes</summary>" in text
    assert "| <code>ami</code> | <code>\"ami-0c1a7f89451184c8b\"</code> |" in text
    assert "forces replacement |" in text
    assert "Sensitive values are never shown" in text


def test_markdown_escapes_values_that_would_break_a_table_or_inject_html():
    nasty = '<img src=x onerror=alert(1)> | `tick`'
    plan = parse_plan(plan_doc(drift=[entry("a.b", ["update"], {"note": "ok"}, {"note": nasty})]))
    text = markdown(build_report(plan), 2)
    row = next(line for line in text.splitlines() if line.startswith("| <code>note</code>"))
    assert "<img" not in text
    assert "&lt;img src=x onerror=alert(1)&gt; &#124; `tick`" in row
    assert row.count("|") == 5  # the pipe inside the value didn't add a column


def test_markdown_clean():
    text = markdown(report_for("no-drift.json"), 0)
    assert text.startswith("## No drift.")
    assert text.count("None.") == 2


def test_json_document():
    doc = json.loads(as_json(report_for("drift-and-changes.json", ["*:tags_all"]), 2))
    assert doc["schema_version"] == 1
    assert doc["summary"] == {
        "drift": 4,
        "changes": 8,
        "ignored": 1,  # tags_all.Owner, ignored in both sections, counted once
        "exit_code": 2,
        "headline": "Drift found: 4 changed outside Terraform, 8 pending changes.",
    }
    sg = doc["drift"][0]
    assert sg["address"] == "aws_security_group.web" and sg["action"] == "update"
    port = next(a for a in sg["attributes"] if a["path"] == "ingress[1].from_port")
    assert port == {
        "path": "ingress[1].from_port",
        "path_parts": ["ingress", 1, "from_port"],
        "kind": "added",
        "before": None,
        "after": 22,
        "sensitive": False,
        "unknown": False,
        "forces_replacement": False,
    }
    secret = next(c for c in doc["changes"] if c["address"].endswith("api_key"))
    s = next(a for a in secret["attributes"] if a["path"] == "secret_string")
    assert (s["before"], s["after"], s["sensitive"]) == (None, None, True)
    version = next(a for a in secret["attributes"] if a["path"] == "version_id")
    assert version["unknown"] is True and version["after"] is None
