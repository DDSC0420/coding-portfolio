"""`plandrift run`, with Terraform faked. The real thing runs in CI (the e2e job)."""

import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from plandrift import terraform
from plandrift.terraform import TerraformError, plan_json

from .conftest import EXAMPLES


class FakeRunner:
    def __init__(self, plan_rc=0, show_rc=0, show_out=None, stderr=b""):
        self.calls = []
        self.plan_rc, self.show_rc, self.stderr = plan_rc, show_rc, stderr
        self.show_out = show_out if show_out is not None else (EXAMPLES / "console-changes.json").read_bytes()

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if "plan" in cmd:
            return subprocess.CompletedProcess(cmd, self.plan_rc, b"", self.stderr)
        return subprocess.CompletedProcess(cmd, self.show_rc, self.show_out, self.stderr)


@pytest.fixture
def on_path(monkeypatch):
    monkeypatch.setattr(terraform.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_runs_refresh_only_plan_then_show_json(tmp_path, on_path):
    runner = FakeRunner()
    data = plan_json(tmp_path, runner=runner)
    assert data["resource_drift"][0]["address"] == "aws_security_group.web"

    (plan_cmd, plan_kwargs), (show_cmd, _) = runner.calls
    assert plan_cmd[:3] == ["/usr/bin/terraform", f"-chdir={tmp_path}", "plan"]
    assert "-refresh-only" in plan_cmd and "-input=false" in plan_cmd
    out = next(a for a in plan_cmd if a.startswith("-out="))[5:]
    assert show_cmd == ["/usr/bin/terraform", f"-chdir={tmp_path}", "show", "-json", out]
    assert plan_kwargs["env"]["TF_IN_AUTOMATION"] == "1"
    assert not os.path.exists(out)  # the temporary plan file is cleaned up


def test_full_plan_and_extra_args(tmp_path, on_path):
    runner = FakeRunner()
    plan_json(tmp_path, binary="tofu", full_plan=True, extra_args=["-var-file=prod.tfvars"], runner=runner)
    plan_cmd = runner.calls[0][0]
    assert plan_cmd[0] == "/usr/bin/tofu"
    assert "-refresh-only" not in plan_cmd and plan_cmd[-1] == "-var-file=prod.tfvars"


def test_plan_failure_shows_terraform_error(tmp_path, on_path):
    runner = FakeRunner(plan_rc=1, stderr=b"Error: No valid credential sources found")
    with pytest.raises(TerraformError, match=r"terraform plan failed \(exit 1\):\nError: No valid credential"):
        plan_json(tmp_path, runner=runner)
    assert len(runner.calls) == 1


def test_show_failure(tmp_path, on_path):
    with pytest.raises(TerraformError, match="show -json failed"):
        plan_json(tmp_path, runner=FakeRunner(show_rc=1, stderr=b"boom"))


def test_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(terraform.shutil, "which", lambda name: None)
    with pytest.raises(TerraformError, match="couldn't find `terraform` on PATH"):
        plan_json(tmp_path, runner=FakeRunner())


def test_missing_directory(tmp_path, on_path):
    with pytest.raises(TerraformError, match="isn't a directory"):
        plan_json(tmp_path / "nope", runner=FakeRunner())


def test_cli_run_error_exits_1(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(terraform.shutil, "which", lambda name: None)
    code, _, err = cli("run", "--chdir", str(tmp_path))
    assert code == 1 and "couldn't find `terraform`" in err


@pytest.mark.skipif(sys.platform == "win32", reason="fake executable is a POSIX script")
def test_cli_run_against_a_fake_terraform_binary(cli, tmp_path, monkeypatch):
    """End to end through a real subprocess: a stand-in `terraform` that records its arguments."""
    log = tmp_path / "calls.jsonl"
    fake = tmp_path / "bin" / "terraform"
    fake.parent.mkdir()
    fake.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json, sys
            with open({str(log)!r}, "a") as f:
                f.write(json.dumps(sys.argv[1:]) + "\\n")
            if "show" in sys.argv:
                sys.stdout.write(open({str(EXAMPLES / "drift-and-changes.json")!r}).read())
            """
        )
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    workdir = tmp_path / "infra"
    workdir.mkdir()

    code, out, _ = cli("run", "--chdir", str(workdir), "-f", "json", "--", "-var", "env=prod")
    assert code == 2 and json.loads(out)["summary"]["changes"] == 8
    plan_args, show_args = [json.loads(line) for line in log.read_text().splitlines()]
    assert plan_args[:2] == [f"-chdir={workdir}", "plan"] and plan_args[-2:] == ["-var", "env=prod"]
    assert show_args[1:3] == ["show", "-json"]
