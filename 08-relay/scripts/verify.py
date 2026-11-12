"""Check the engine's invariants against the live databases. Read-only; exit code 1 on a violation.

    uv run python scripts/verify.py                 # the Compose databases (ports 55432 / 55433)
    uv run python scripts/verify.py --local         # the databases scripts/run_local.py uses

URLs come from RELAY_DATABASE_URL and CRM_DATABASE_URL (or .env), like the services.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, make_url  # noqa: E402

from relay.config import crm_settings, engine_settings  # noqa: E402
from relay.verify import render, verify  # noqa: E402


def local_url(name: str) -> str:
    admin = os.environ.get(
        "RELAY_TEST_ADMIN_URL", "postgresql+psycopg://relay:relay@127.0.0.1:55432/postgres"
    )
    return make_url(admin).set(database=name).render_as_string(hide_password=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--local", action="store_true", help="use run_local.py's databases")
    parser.add_argument("--no-crm", action="store_true", help="check the engine database only")
    args = parser.parse_args()
    engine_url = local_url("relay_local_engine") if args.local else engine_settings().database_url
    crm_url = local_url("relay_local_crm") if args.local else crm_settings().database_url
    engine = create_engine(engine_url)
    crm = None if args.no_crm else create_engine(crm_url)
    report = verify(engine, crm)
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
