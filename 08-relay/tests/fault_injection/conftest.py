"""A small RELAY cluster of real processes with short timings, for crash tests.

Workers and the demo CRM run as separate OS processes against the test
databases. Failures are real: SIGKILL, SIGSTOP, failpoints that call
os._exit(137), and CRM faults that commit and then drop the connection.

Timings are shortened so recovery takes seconds:
lease 2 s (renewed every ~0.7 s), recovery sweep every 0.3 s, retry backoff
from 50 ms, CRM client timeout 1 s.
"""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import Engine

from relay.engine.submission import submit_workflow
from relay.workflows.registry import Registry
from tests.conftest import VALID_ENQUIRY
from tests.e2e.processes import Service, free_port, uvicorn, wait_ready, wait_until, worker
from tests.helpers import fetch_all, fetch_one

LEASE_SECONDS = 2.0
TERMINAL = ("succeeded", "failed", "outcome_unknown", "rejected")


class Cluster:
    def __init__(
        self,
        database_urls: dict[str, str],
        engine: Engine,
        crm_engine: Engine,
        registry: Registry,
        log_dir: Path,
    ) -> None:
        self.engine, self.crm_engine, self.registry, self.log_dir = (
            engine,
            crm_engine,
            registry,
            log_dir,
        )
        self.crm_url = f"http://127.0.0.1:{free_port()}"
        self.crm_env = {"CRM_DATABASE_URL": database_urls["crm"], "CRM_DEMO_MODE": "true"}
        self.worker_env = {
            "RELAY_DATABASE_URL": database_urls["engine"],
            "RELAY_CRM_BASE_URL": self.crm_url,
            "RELAY_LEASE_SECONDS": str(LEASE_SECONDS),
            "RELAY_RECOVERY_INTERVAL_SECONDS": "0.3",
            "RELAY_POLL_INTERVAL_SECONDS": "0.05",
            "RELAY_WORKER_HEARTBEAT_SECONDS": "0.3",
            "RELAY_RETRY_BASE_SECONDS": "0.05",
            "RELAY_RETRY_MAX_DELAY_SECONDS": "0.2",
            "RELAY_CRM_TIMEOUT_SECONDS": "1.0",
            "RELAY_WORKER_CONCURRENCY": "2",
        }
        self.services: list[Service] = []
        self._incarnation = 0

    # --- processes ----------------------------------------------------------

    def start_crm(self) -> Service:
        self._incarnation += 1
        port = self.crm_url.rsplit(":", 1)[1]
        crm = uvicorn(
            f"crm-{self._incarnation}",
            "services.demo_crm.app:app",
            int(port),
            self.crm_env,
            self.log_dir,
        ).start()
        self.services.append(crm)
        wait_ready(f"{self.crm_url}/health/ready")
        return crm

    def start_worker(self, name: str, failpoints: str | None = None) -> Service:
        env = dict(self.worker_env)
        if failpoints:
            env["RELAY_FAILPOINTS"] = failpoints
        self._incarnation += 1
        w = worker(name, env, self.log_dir)
        w.log_path = self.log_dir / f"{name}-{self._incarnation}.log"
        w.start()
        self.services.append(w)
        return w

    def faults(self, **settings: Any) -> None:
        httpx.put(f"{self.crm_url}/demo/faults", json=settings, timeout=5).raise_for_status()

    # --- workflows ------------------------------------------------------------

    def submit(self, n: int = 1, prefix: str = "CRASH") -> list[UUID]:
        return [
            submit_workflow(
                self.engine,
                self.registry,
                workflow_type="enquiry",
                workflow_version=1,
                input={
                    **VALID_ENQUIRY,
                    "enquiry_ref": f"{prefix}-{i}",
                    "email": f"crash.{prefix.lower()}.{i}@northwind-logistics.example",
                },
            ).workflow_run_id
            for i in range(n)
        ]

    def status(self, run_id: UUID) -> str:
        return fetch_one(self.engine, "SELECT status FROM workflow_runs WHERE id = :id", id=run_id)[
            "status"
        ]

    def wait_terminal(self, run_ids: list[UUID], timeout: float = 30) -> dict[str, int]:
        def done() -> dict[str, int] | None:
            rows = fetch_all(
                self.engine,
                "SELECT status, count(*) AS n FROM workflow_runs "
                "WHERE id = ANY(:ids) GROUP BY status",
                ids=run_ids,
            )
            counts = {r["status"]: r["n"] for r in rows}
            terminal = sum(n for s, n in counts.items() if s in TERMINAL)
            return counts if terminal == len(run_ids) else None

        return wait_until(done, timeout, f"{len(run_ids)} workflows to reach a terminal status")

    def step(self, run_id: UUID, name: str) -> dict[str, Any]:
        return fetch_one(
            self.engine,
            "SELECT * FROM step_runs WHERE workflow_run_id = :id AND name = :name",
            id=run_id,
            name=name,
        )

    def attempts(self, step_id: UUID) -> list[dict[str, Any]]:
        return fetch_all(
            self.engine,
            "SELECT a.attempt_number, a.outcome, a.error_class, w.name AS worker "
            "FROM step_attempts a JOIN workers w ON w.id = a.worker_id "
            "WHERE a.step_run_id = :id ORDER BY a.attempt_number",
            id=step_id,
        )

    def events(self, run_id: UUID, event_type: str) -> list[dict[str, Any]]:
        return [
            r["details"]
            for r in fetch_all(
                self.engine,
                "SELECT details FROM workflow_events WHERE workflow_run_id = :id "
                "AND type = :t ORDER BY id",
                id=run_id,
                t=event_type,
            )
        ]

    def crm_mutations(self) -> dict[str, Any]:
        total = fetch_one(self.crm_engine, "SELECT count(*) AS n FROM mutations")["n"]
        dupes = fetch_all(
            self.crm_engine,
            "SELECT idempotency_key FROM mutations GROUP BY idempotency_key HAVING count(*) > 1",
        )
        return {"total": total, "duplicate_keys": len(dupes)}


@pytest.fixture
def cluster(database_urls, engine, crm_engine, registry, tmp_path) -> Iterator[Cluster]:
    c = Cluster(database_urls, engine, crm_engine, registry, tmp_path)
    try:
        yield c
    finally:
        for s in c.services:
            if s.proc and s.proc.poll() is None:
                if sys.platform != "win32":
                    s.resume()  # in case a test left it frozen
                s.stop(timeout=10)
        for s in c.services:
            print(f"----- {s.log_path.name} -----\n{s.log()[-2500:]}")
