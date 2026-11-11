"""python -m tally serve [--open] [--port 8000] [--reset] [--no-dev]"""

from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from pathlib import Path

DEFAULT_DB = Path(".data") / "tally.db"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m tally")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the app locally")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--db", default=os.environ.get("TALLY_DB", str(DEFAULT_DB)))
    serve.add_argument("--open", action="store_true", help="open a browser tab")
    serve.add_argument("--reset", action="store_true", help="delete the local database first")
    serve.add_argument("--no-dev", action="store_true", help="hide the dev panel and outbox")
    args = parser.parse_args()

    import uvicorn

    from .web import Config, create_app

    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)
    app = create_app(Config(db_path=db, dev=not args.no_dev))
    url = f"http://{args.host}:{args.port}"
    print(f"Tally on {url}  (data: {db}; dev panel {'off' if args.no_dev else 'on'})")
    if args.open:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
