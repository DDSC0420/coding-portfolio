"""Command line: python -m gridwatch run [--sample] [--days N]"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from .extract import ExtractError
from .load import LoadError
from .pipeline import DEFAULT_DAYS, QualityError, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gridwatch", description="GB electricity grid data pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("run", help="extract, load, transform, check and publish the report")
    cmd.add_argument("--sample", action="store_true", help="use the bundled sample data instead of the live API")
    cmd.add_argument(
        "--days",
        type=int,
        help=f"fetch this many days back (default: {DEFAULT_DAYS} on first run, then only what's new)",
    )
    cmd.add_argument("--db", default="grid.duckdb", help="DuckDB file (default: grid.duckdb)")
    cmd.add_argument("--out", default="site", help="report folder (default: site)")
    cmd.add_argument("--save-raw", metavar="DIR", help="also save the raw API records as JSON in DIR")
    cmd.add_argument("--open", action="store_true", help="open the report in your browser when done")
    args = parser.parse_args(argv)

    if args.days is not None and not 1 <= args.days <= 365:
        parser.error("--days must be between 1 and 365")
    try:
        result = run(db_path=args.db, out_dir=args.out, days=args.days, sample=args.sample, save_raw=args.save_raw)
    except (ExtractError, LoadError, QualityError) as err:
        print(f"gridwatch: {err}", file=sys.stderr)
        return 1
    if args.open:
        webbrowser.open(result.report.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
