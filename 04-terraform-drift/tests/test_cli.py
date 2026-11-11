import json
import os
import subprocess
import sys

import pytest

from plandrift import __version__

from .conftest import EXAMPLES


def test_drift_exits_2(cli):
    code, out, err = cli("check", str(EXAMPLES / "console-changes.json"))
    assert code == 2 and "Drift found: 4 changed outside Terraform." in out and err == ""


def test_clean_exits_0(cli):
    code, out, _ = cli("check", str(EXAMPLES / "no-drift.json"))
    assert code == 0 and out.rstrip().endswith("Exit 0.")


@pytest.mark.parametrize(
    ("fail_on", "example", "code"),
    [
        ("drift", "console-changes.json", 2),
        ("never", "console-changes.json", 0),
        ("drift", "no-drift.json", 0),
    ],
)
def test_fail_on(cli, fail_on, example, code):
    assert cli("check", str(EXAMPLES / example), "--fail-on", fail_on)[0] == code


def test_pending_changes_only_respects_fail_on(cli, write_plan):
    from .conftest import entry, plan_doc

    path = write_plan(plan_doc(changes=[entry("aws_s3_bucket.a", ["create"], None, {"x": 1})]))
    assert cli("check", path)[0] == 2
    assert cli("check", path, "--fail-on", "drift")[0] == 0


def test_stdin(cli):
    raw = (EXAMPLES / "console-changes.json").read_bytes()
    code, out, _ = cli("check", "-", "--format", "json", stdin=raw)
    assert code == 2 and json.loads(out)["summary"]["drift"] == 4


def test_output_file(cli, tmp_path):
    target = tmp_path / "drift.md"
    code, out, _ = cli("check", str(EXAMPLES / "console-changes.json"), "-f", "markdown", "-o", str(target))
    assert code == 2 and out == ""
    assert target.read_text(encoding="utf-8").startswith("## Drift found")


def test_ignore_flag(cli):
    code, out, _ = cli(
        "check",
        str(EXAMPLES / "console-changes.json"),
        "-f",
        "json",
        "--ignore",
        "aws_instance.*",
        "--ignore",
        "*:tags*",
    )
    doc = json.loads(out)
    assert code == 2
    assert [c["address"] for c in doc["drift"]] == ["aws_security_group.web", "aws_db_instance.main"]


def test_ignoring_everything_exits_0(cli):
    assert cli("check", str(EXAMPLES / "console-changes.json"), "--ignore", "*")[0] == 0


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["check", "missing.json"], "doesn't exist"),
        (["check", "{examples}"], "doesn't exist or isn't a file"),
        (["check", "{examples}/no-drift.json", "--ignore", ":x"], "ADDRESS"),
    ],
)
def test_errors_exit_1(cli, args, message):
    args = [a.replace("{examples}", str(EXAMPLES)) for a in args]
    code, out, err = cli(*args)
    assert code == 1 and message in err and out == ""


def test_binary_plan_file_exits_1_with_advice(cli, tmp_path):
    plan = tmp_path / "plan.out"
    plan.write_bytes(b"PK\x03\x04binary")
    code, _, err = cli("check", str(plan))
    assert code == 1 and "terraform show -json" in err


def test_bad_json_exits_1(cli, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    code, _, err = cli("check", str(bad))
    assert code == 1 and "isn't valid JSON" in err


def test_unwritable_output_exits_1(cli, tmp_path):
    code, _, err = cli("check", str(EXAMPLES / "no-drift.json"), "-o", str(tmp_path / "nope" / "out.md"))
    assert code == 1 and "couldn't write" in err


@pytest.mark.parametrize(
    "args",
    [["check", "--format", "xml", "x.json"], ["check"], ["frobnicate"], ["check", "x.json", "--", "-var=a"]],
)
def test_usage_errors_exit_1_not_2(cli, args):
    # argparse's own exit code for usage errors is 2, which here would mean "drift found".
    with pytest.raises(SystemExit) as exc:
        cli(*args)
    assert exc.value.code == 1


def test_no_command_prints_help_and_exits_1(cli):
    code, _, err = cli()
    assert code == 1 and "check" in err and "run" in err


def test_version(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli("--version")
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"plandrift {__version__}"


@pytest.mark.parametrize(("flag", "has_colour"), [("always", True), ("never", False), ("auto", False)])
def test_colour_flag(cli, flag, has_colour):
    # auto: stdout is captured, not a terminal, so no colour.
    _, out, _ = cli("check", str(EXAMPLES / "console-changes.json"), "--color", flag)
    assert ("\033[" in out) is has_colour


def test_python_dash_m_entry_point():
    proc = subprocess.run(
        [sys.executable, "-m", "plandrift", "check", str(EXAMPLES / "no-drift.json"), "-f", "json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0 and json.loads(proc.stdout)["summary"]["exit_code"] == 0


def test_non_ascii_values_do_not_crash_a_narrow_console(tmp_path):
    from .conftest import entry, plan_doc

    plan = tmp_path / "plan.json"
    rc = entry("aws_s3_bucket.a", ["update"], {"tags": {"Team": "Ops"}}, {"tags": {"Team": "Ops ✓ 🚀"}})
    doc = plan_doc(drift=[rc])
    plan.write_text(json.dumps(doc), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "plandrift", "check", str(plan)],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert proc.returncode == 2, proc.stderr
    assert b'"Ops \\u2713 \\U0001f680"' in proc.stdout
