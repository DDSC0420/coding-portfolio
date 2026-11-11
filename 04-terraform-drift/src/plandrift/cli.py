"""Command line. Exit codes: 0 nothing found, 2 drift or changes found, 1 error (always 1, never 2)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .plan import PlanError, decode, parse_plan
from .render import RENDERERS
from .report import EXIT_ERROR, FAIL_ON, build_report, exit_code, parse_rule
from .terraform import TerraformError, plan_json


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse exits 2 by default, which here means "drift found"
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="plandrift",
        description="Report Terraform drift (changes made outside Terraform) and pending changes from plan JSON.",
        epilog="Exit codes: 0 nothing found, 2 drift or pending changes found, 1 error.",
    )
    parser.add_argument("--version", action="version", version=f"plandrift {__version__}")

    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--format", "-f", choices=sorted(RENDERERS), default="table", help="default: table")
    output.add_argument("--output", "-o", metavar="FILE", help="write the report to FILE instead of stdout")
    output.add_argument(
        "--fail-on",
        choices=FAIL_ON,
        default="any",
        help="what makes the exit code 2: any (drift or pending changes, default), drift only, or never",
    )
    output.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="RULE",
        help="ADDRESS or ADDRESS:ATTRIBUTE to leave out; * and ? are wildcards. Repeatable.",
    )
    output.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="table colours")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)
    check = sub.add_parser(
        "check", parents=[output], help="read plan JSON from a file or stdin", description="Report on plan JSON."
    )
    check.add_argument("plan", metavar="PLAN_JSON", help="output of `terraform show -json plan.out`, or - for stdin")

    run = sub.add_parser(
        "run",
        parents=[output],
        help="run terraform plan -refresh-only here and report",
        description="Run `terraform plan -refresh-only` (or a full plan) and report on it.",
    )
    run.add_argument("--chdir", default=".", metavar="DIR", help="Terraform working directory (default: .)")
    run.add_argument("--terraform-bin", default=os.environ.get("PLANDRIFT_TERRAFORM", "terraform"), metavar="BIN")
    run.add_argument("--full-plan", action="store_true", help="a normal plan: drift and pending changes")
    run.epilog = "Anything after -- goes to terraform plan, e.g. plandrift run -- -var-file=prod.tfvars"
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")  # never crash on a non-UTF-8 console

    argv = list(sys.argv[1:] if argv is None else argv)
    plan_args: list[str] = []
    if "--" in argv:  # split by hand: argparse's handling of -- differs between Python versions
        cut = argv.index("--")
        argv, plan_args = argv[:cut], argv[cut + 1 :]

    parser = _parser()
    args = parser.parse_args(argv)
    if plan_args and args.command != "run":
        parser.error("arguments after -- are only for `plandrift run`")
    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_ERROR

    try:
        rules = [parse_rule(r) for r in args.ignore]
        if args.command == "check":
            data = decode(_read(args.plan), source="stdin" if args.plan == "-" else args.plan)
        else:
            data = plan_json(args.chdir, args.terraform_bin, args.full_plan, plan_args)
        report = build_report(parse_plan(data), rules)
    except (PlanError, TerraformError, ValueError, OSError) as exc:
        print(f"plandrift: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    code = exit_code(report, args.fail_on)
    if args.format == "table":
        text = RENDERERS["table"](report, code, color=_use_color(args.color, args.output))
    else:
        text = RENDERERS[args.format](report, code)

    if args.output:
        try:
            Path(args.output).write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"plandrift: error: couldn't write {args.output}: {exc}", file=sys.stderr)
            return EXIT_ERROR
    else:
        sys.stdout.write(text)
        sys.stdout.flush()
    return code


def _read(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    file = Path(path)
    if not file.is_file():
        raise PlanError(f"{path} doesn't exist or isn't a file.")
    return file.read_bytes()


def _use_color(choice: str, output: str | None) -> bool:
    if choice != "auto":
        return choice == "always"
    if output or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        return _enable_windows_ansi()
    return True


def _enable_windows_ansi() -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
