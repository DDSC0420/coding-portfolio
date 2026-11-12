"""Command line: ``python -m intake <command>``.

    python -m intake serve [--port 8000] [--open]    the local drop-a-CV page
    python -m intake extract CV_FILE [--text]        print the record as JSON
    python -m intake eval                            score against the hand-labelled samples
"""

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

from . import evaluate, llm
from .pipeline import extract
from .readers import ReadError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m intake", description="CV in, validated candidate record out.")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the local web page")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--open", action="store_true", help="open it in your browser")

    one = commands.add_parser("extract", help="extract one CV and print JSON")
    one.add_argument("file", type=Path)
    one.add_argument("--text", action="store_true", help="include the cleaned CV text in the output")

    commands.add_parser("eval", help="score the extractor on the bundled samples")
    args = parser.parse_args(argv)

    try:
        extractor = llm.from_env()
    except llm.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "extract":
        try:
            result = extract(args.file.read_bytes(), args.file.name, llm=extractor)
        except (OSError, ReadError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        exclude = None if args.text else {"text"}
        if hasattr(sys.stdout, "reconfigure"):
            # JSON is UTF-8. Without this, Windows writes a redirected file in its legacy code page
            # and fails on any name outside it.
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(result.model_dump(mode="json", exclude=exclude), indent=2, ensure_ascii=False))
        return 0

    if args.command == "eval":
        report = evaluate.run(llm=extractor)
        print(evaluate.render(report, extractor.label if extractor else "rule-based"))
        return 0 if all(c.ok for checks in report.values() for c in checks) else 1

    import uvicorn

    from .web import create_app

    url = f"http://{args.host}:{args.port}"
    print(f"Intake is running at {url}  (extractor: {extractor.label if extractor else 'rule-based'})")
    print("Nothing you upload is stored. Press Ctrl+C to stop.")
    if args.open:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(create_app(llm=extractor), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
