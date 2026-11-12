"""Measure throughput with 1, 2 and 4 worker processes. Numbers are for this machine only.

    uv run python scripts/benchmark.py                 # 300 workflows per run
    uv run python scripts/benchmark.py --workflows 1000 --workers 1 2 4 8

Uses throwaway databases on RELAY_TEST_ADMIN_URL (like the test suite), a real demo CRM
process and real worker processes; each workflow is the four-step enquiry v1, so one workflow
is four claims, four fenced completions and one HTTP call to the CRM. Writes
results/benchmark.json and prints a Markdown table. It measures this engine on one laptop-class
machine against a local database: a sanity check, not a capacity claim.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, make_url, text  # noqa: E402

from relay.engine.submission import submit_workflow  # noqa: E402
from relay.verify import verify  # noqa: E402
from relay.workflows.registry import default_registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "RELAY_TEST_ADMIN_URL", "postgresql+psycopg://relay:relay@127.0.0.1:55432/postgres"
)
DBS = {"engine": "relay_bench_engine", "crm": "relay_bench_crm"}
CRM_PORT = 8199


def url(name: str) -> str:
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def fresh_databases() -> dict[str, str]:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        for name in DBS.values():
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    urls = {k: url(v) for k, v in DBS.items()}
    for section, u in urls.items():
        cfg = Config(str(ROOT / "alembic.ini"), ini_section=section)
        cfg.set_main_option("sqlalchemy.url", u)
        command.upgrade(cfg, "head")
    return urls


def run(workers: int, workflows: int, concurrency: int) -> dict:
    urls = fresh_databases()
    env = {
        **os.environ,
        "RELAY_DATABASE_URL": urls["engine"],
        "CRM_DATABASE_URL": urls["crm"],
        "RELAY_CRM_BASE_URL": f"http://127.0.0.1:{CRM_PORT}",
        "RELAY_POLL_INTERVAL_SECONDS": "0.05",
        "RELAY_WORKER_CONCURRENCY": str(concurrency),
    }
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    crm = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "services.demo_crm.app:app", "--port", str(CRM_PORT),
         "--log-level", "error"],  # fmt: skip
        cwd=ROOT, env=env, **quiet,
    )  # fmt: skip
    procs = [crm]
    engine, crm_engine = create_engine(urls["engine"]), create_engine(urls["crm"])
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{CRM_PORT}/health/ready", timeout=1).is_success:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        registry = default_registry()
        golden = json.loads((ROOT / "fixtures/enquiries/golden.json").read_text("utf-8"))
        for i in range(workflows):
            e = {**golden[i % len(golden)], "enquiry_ref": f"BENCH-{i}"}
            submit_workflow(engine, registry, workflow_type="enquiry", workflow_version=1, input=e)
        # Start the clock when the workers start: submission is not what is being measured.
        start = time.monotonic()
        for n in range(workers):
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-m", "services.worker"],
                    cwd=ROOT,
                    env={**env, "RELAY_WORKER_NAME": f"bench-{n}"},
                    **quiet,
                )  # fmt: skip
            )
        while True:
            with engine.connect() as conn:
                left = conn.execute(
                    text("SELECT count(*) FROM workflow_runs WHERE status = 'running'")
                ).scalar_one()
            if left == 0:
                break
            if time.monotonic() - start > 600:
                raise SystemExit("benchmark run did not finish in 10 minutes")
            time.sleep(0.05)
        seconds = time.monotonic() - start
        with engine.connect() as conn:
            succeeded = conn.execute(
                text("SELECT count(*) FROM workflow_runs WHERE status = 'succeeded'")
            ).scalar_one()
            p50, p95 = conn.execute(
                text(
                    """
                    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY d),
                           percentile_cont(0.95) WITHIN GROUP (ORDER BY d)
                    FROM (SELECT EXTRACT(EPOCH FROM a.finished_at - a.started_at) * 1000 AS d
                          FROM step_attempts a) x
                    """
                )
            ).one()
        report = verify(engine, crm_engine)
        return {
            "workers": workers,
            "slots": workers * concurrency,
            "workflows": workflows,
            "succeeded": succeeded,
            "seconds": round(seconds, 2),
            "workflows_per_second": round(workflows / seconds, 1),
            "steps_per_second": round(workflows * 4 / seconds, 1),
            "step_p50_ms": round(p50, 1),
            "step_p95_ms": round(p95, 1),
            "invariants_hold": report.ok,
            "crm_writes": report.facts["crm_writes"],
        }
    finally:
        for p in procs[::-1]:
            p.terminate()
        for p in procs:
            p.wait(20)
        engine.dispose()
        crm_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workflows", type=int, default=300)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--concurrency", type=int, default=4, help="slots per worker")
    args = parser.parse_args()
    results = []
    for n in args.workers:
        r = run(n, args.workflows, args.concurrency)
        print(
            f"{n} worker(s): {r['seconds']}s, {r['workflows_per_second']} workflows/s", flush=True
        )
        results.append(r)
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    machine = f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs, Python {platform.python_version()}"  # noqa: E501
    (out / "benchmark.json").write_text(
        json.dumps({"machine": machine, "runs": results}, indent=2) + "\n"
    )
    print(f"\n{machine}\n")
    print("| Workers | Slots | Workflows | Time | Workflows/s | Steps/s | Step p50 | Step p95 |")
    print("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in results:
        print(
            f"| {r['workers']} | {r['slots']} | {r['workflows']} | {r['seconds']} s | "
            f"{r['workflows_per_second']} | {r['steps_per_second']} | {r['step_p50_ms']} ms | "
            f"{r['step_p95_ms']} ms |"
        )
    ok = all(
        r["invariants_hold"] and r["succeeded"] == r["crm_writes"] == r["workflows"]
        for r in results
    )  # noqa: E501
    print("\nEvery run: all workflows succeeded, one CRM write each, invariants hold." if ok
          else "\nPROBLEM: a run lost or doubled work; see results/benchmark.json")  # fmt: skip
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
