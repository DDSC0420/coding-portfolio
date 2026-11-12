"""Run the whole stack as plain processes, without Docker (development and the UI tests).

    uv run python scripts/run_local.py            # needs a PostgreSQL server, see below
    uv run python scripts/run_local.py --fresh    # drop and recreate the two databases first

Creates databases `relay_local_engine` and `relay_local_crm` on the server named by
RELAY_TEST_ADMIN_URL (default: the Compose engine database on 127.0.0.1:55432), migrates them,
then starts the demo CRM (fault switches on), the control API on :8000 and two workers with
short leases. Ctrl-C stops everything; the data stays.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "RELAY_TEST_ADMIN_URL", "postgresql+psycopg://relay:relay@127.0.0.1:55432/postgres"
)
DATABASES = {"engine": "relay_local_engine", "crm": "relay_local_crm"}


def db_url(name: str) -> str:
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def prepare(fresh: bool) -> dict[str, str]:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        for name in DATABASES.values():
            if fresh:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).first()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    urls = {section: db_url(name) for section, name in DATABASES.items()}
    for section, url in urls.items():
        cfg = Config(str(ROOT / "alembic.ini"), ini_section=section)
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fresh", action="store_true", help="drop and recreate the databases")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--crm-port", type=int, default=8100)
    parser.add_argument("--lease-seconds", default="8")
    args = parser.parse_args()

    urls = prepare(args.fresh)
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "RELAY_DATABASE_URL": urls["engine"],
        "CRM_DATABASE_URL": urls["crm"],
        "CRM_DEMO_MODE": "true",
        "RELAY_CRM_BASE_URL": f"http://127.0.0.1:{args.crm_port}",
        "RELAY_LEASE_SECONDS": args.lease_seconds,
        "RELAY_RECOVERY_INTERVAL_SECONDS": "1",
        "RELAY_WORKER_OFFLINE_AFTER_SECONDS": "5",
    }

    def uvicorn(app: str, port: int) -> list[str]:
        return [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port),
                "--log-level", "warning"]  # fmt: skip

    commands = {
        "crm": (uvicorn("services.demo_crm.app:app", args.crm_port), {}),
        "api": (uvicorn("services.api.app:app", args.api_port), {}),
        "worker-a": ([sys.executable, "-m", "services.worker"], {"RELAY_WORKER_NAME": "worker-a"}),
        "worker-b": ([sys.executable, "-m", "services.worker"], {"RELAY_WORKER_NAME": "worker-b"}),
    }
    procs = {
        name: subprocess.Popen(cmd, cwd=ROOT, env={**env, **extra})
        for name, (cmd, extra) in commands.items()
    }
    print(f"RELAY running: API http://127.0.0.1:{args.api_port}  (pids: "
          + ", ".join(f"{n} {p.pid}" for n, p in procs.items()) + ")", flush=True)  # fmt: skip

    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        time.sleep(0.5)
        # A worker killed on purpose (the demo) is fine; the API or CRM dying is not.
        for name in ("api", "crm"):
            if procs[name].poll() is not None:
                print(f"{name} exited with {procs[name].returncode}; stopping", flush=True)
                stopping = True
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    for p in procs.values():
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()


if __name__ == "__main__":
    main()
