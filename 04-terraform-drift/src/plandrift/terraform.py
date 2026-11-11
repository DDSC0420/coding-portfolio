"""Optional: run Terraform to get the plan JSON, instead of being handed it.

`plandrift run` does `terraform plan -refresh-only -out=<tmp>` then `terraform show -json <tmp>`.
Works with any binary that speaks the same CLI (pass --terraform-bin tofu for OpenTofu).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .plan import decode

Runner = Callable[..., subprocess.CompletedProcess]


class TerraformError(RuntimeError):
    pass


def find_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise TerraformError(
            f"couldn't find `{name}` on PATH. Install Terraform, pass --terraform-bin, "
            "or run Terraform yourself and use `plandrift check plan.json`."
        )
    return found


def plan_json(
    chdir: str | os.PathLike,
    binary: str = "terraform",
    full_plan: bool = False,
    extra_args: list[str] | tuple[str, ...] = (),
    runner: Runner = subprocess.run,
) -> Any:
    """Make a plan in `chdir` and return `terraform show -json` of it, decoded."""
    exe = find_binary(binary)
    workdir = Path(chdir)
    if not workdir.is_dir():
        raise TerraformError(f"{workdir} isn't a directory.")

    with tempfile.TemporaryDirectory(prefix="plandrift-") as tmp:
        planfile = str(Path(tmp) / "plan.tfplan")
        mode = [] if full_plan else ["-refresh-only"]
        plan_cmd = [exe, f"-chdir={workdir}", "plan", *mode, "-input=false", "-no-color", f"-out={planfile}"]
        _run(runner, [*plan_cmd, *extra_args], "plan")
        shown = _run(runner, [exe, f"-chdir={workdir}", "show", "-json", planfile], "show -json")
        return decode(shown.stdout, source="terraform show -json")


def _run(runner: Runner, cmd: list[str], what: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    try:
        proc = runner(cmd, capture_output=True, env=env)
    except OSError as exc:
        raise TerraformError(f"couldn't start terraform {what}: {exc}") from exc
    if proc.returncode != 0:
        detail = _tail(proc.stderr) or _tail(proc.stdout)
        raise TerraformError(f"terraform {what} failed (exit {proc.returncode}):\n{detail}")
    return proc


def _tail(output: bytes | str | None, lines: int = 20) -> str:
    if not output:
        return ""
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
    return "\n".join(text.strip().splitlines()[-lines:])
