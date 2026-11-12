"""RELAY worker process.

    python -m services.worker

Threads:

- `slot-N` (RELAY_WORKER_CONCURRENCY of them): claim one step in a short
  committed transaction, execute it with no transaction open, then write the
  fenced outcome.
- `leases`: renews every lease this process holds, every lease_seconds / 3.
  A renewal that fails means the lease was lost; the step's eventual
  completion will be refused by the fence too.
- `recovery`: sweeps expired leases (from any worker, including dead ones)
  back to ready, every RELAY_RECOVERY_INTERVAL_SECONDS.
- `heartbeat`: records process liveness in `workers` for the dashboard.
  Liveness is informational; correctness depends only on leases.

Shutdown: SIGTERM/SIGINT stops new claims, lets in-flight steps finish (their
leases keep being renewed), then exits. SIGKILL (`docker kill`) is the crash
case: nothing is cleaned up, and recovery takes over once the leases expire.
"""

import logging
import os
import random
import secrets
import signal
import socket
import sys
import threading
from collections.abc import Callable
from uuid import UUID

import httpx
from sqlalchemy import Engine, text

from relay.config import EngineSettings, engine_settings
from relay.db.session import make_engine
from relay.engine import failpoints
from relay.engine.claims import ClaimedStep, claim_next_step
from relay.engine.errors import LeaseLost
from relay.engine.executor import execute_claimed
from relay.engine.leases import renew_lease
from relay.engine.recovery import recover_expired_leases
from relay.workflows.registry import Registry, default_registry

log = logging.getLogger("relay.worker")


class LeaseKeeper:
    """The leases this process holds right now, renewed in the background."""

    def __init__(self, engine: Engine, lease_seconds: float) -> None:
        self.engine = engine
        self.lease_seconds = lease_seconds
        self._held: dict[UUID, ClaimedStep] = {}
        self._lock = threading.Lock()

    def hold(self, step: ClaimedStep) -> None:
        with self._lock:
            self._held[step.step_id] = step

    def release(self, step: ClaimedStep) -> None:
        with self._lock:
            self._held.pop(step.step_id, None)

    def renew_all(self) -> None:
        with self._lock:
            held = list(self._held.values())
        for step in held:
            try:
                renew_lease(self.engine, step, self.lease_seconds)
            except LeaseLost as exc:
                # Expired or taken over. Stop renewing; the slot's final write
                # will be refused by the same fence.
                log.warning("lease lost: %s", exc)
                self.release(step)
            except Exception:
                log.exception("lease renewal failed for step %s", step.step_id)


class Worker:
    def __init__(self, settings: EngineSettings, engine: Engine, registry: Registry) -> None:
        self.settings = settings
        self.engine = engine
        self.registry = registry
        self.supported = registry.keys()
        # Instance id: a restarted worker-a is a different owner from its
        # previous incarnation, so it can never touch that incarnation's leases.
        self.instance_id = f"{settings.worker_name}/{secrets.token_hex(4)}"
        self.stopping = threading.Event()  # stop claiming new work
        self.halted = threading.Event()  # stop background threads (after slots finish)
        self.leases = LeaseKeeper(engine, settings.lease_seconds)
        self.http = httpx.Client(
            timeout=httpx.Timeout(settings.crm_timeout_seconds, connect=2.0),
            limits=httpx.Limits(max_connections=settings.worker_concurrency),
        )

    # --- liveness -----------------------------------------------------------

    def register(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO workers (id, name, hostname, pid) VALUES (:id, :name, :host, :pid)"
                ),
                {
                    "id": self.instance_id,
                    "name": self.settings.worker_name,
                    "host": socket.gethostname(),
                    "pid": os.getpid(),
                },
            )

    def _heartbeat(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE workers SET last_heartbeat_at = now() WHERE id = :id"),
                {"id": self.instance_id},
            )

    def _mark_stopped(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE workers SET stopped_at = now(), last_heartbeat_at = now() "
                    "WHERE id = :id"
                ),
                {"id": self.instance_id},
            )

    # --- background loops ---------------------------------------------------

    def _every(self, seconds: float, name: str, action: Callable[[], None]) -> None:
        while not self.halted.wait(seconds):
            try:
                action()
            except Exception:
                log.exception("%s failed", name)

    def _recover(self) -> None:
        for r in recover_expired_leases(
            self.engine, swept_by=self.instance_id, limit=self.settings.recovery_batch_size
        ):
            log.warning(
                "recovered %s/%s from %s (gen %d) -> %s",
                r.workflow_run_id,
                r.name,
                r.previous_owner,
                r.lease_generation,
                r.new_status,
            )

    # --- work ---------------------------------------------------------------

    def _slot_loop(self, slot: int) -> None:
        idle = self.settings.poll_interval_seconds
        while not self.stopping.is_set():
            try:
                claimed = claim_next_step(
                    self.engine,
                    worker_id=self.instance_id,
                    lease_seconds=self.settings.lease_seconds,
                    supported=self.supported,
                )
            except Exception:
                log.exception("claim failed (slot %d)", slot)
                self.stopping.wait(idle)
                continue
            if claimed is None:
                # Jitter keeps idle slots from polling in lockstep.
                self.stopping.wait(idle * random.uniform(0.5, 1.5))
                continue
            log.info(
                "claimed %s/%s attempt %d gen %d",
                claimed.workflow_run_id,
                claimed.name,
                claimed.attempt,
                claimed.lease_generation,
            )
            self.leases.hold(claimed)
            try:
                failpoints.hit("after_claim", claimed.name)
                outcome = execute_claimed(
                    self.engine, self.registry, self.settings, claimed, self.http
                )
                log.info("%s/%s -> %s", claimed.workflow_run_id, claimed.name, outcome)
            except Exception:
                # e.g. the database went away mid-write. The lease will expire
                # and recovery makes the step eligible again.
                log.exception(
                    "recording outcome failed for %s/%s", claimed.workflow_run_id, claimed.name
                )
            finally:
                self.leases.release(claimed)

    def run(self) -> None:
        self.register()
        if failpoints.active():
            log.warning("TEST FAILPOINTS ENABLED: %s", os.environ["RELAY_FAILPOINTS"])
        log.info(
            "worker %s started; concurrency=%d; lease=%.1fs; executes %s",
            self.instance_id,
            self.settings.worker_concurrency,
            self.settings.lease_seconds,
            ", ".join(self.supported),
        )
        s = self.settings
        background = [
            threading.Thread(
                target=self._every,
                name="heartbeat",
                daemon=True,
                args=(s.worker_heartbeat_seconds, "heartbeat", self._heartbeat),
            ),
            threading.Thread(
                target=self._every,
                name="leases",
                daemon=True,
                args=(s.lease_seconds / 3, "lease renewal", self.leases.renew_all),
            ),
            threading.Thread(
                target=self._every,
                name="recovery",
                daemon=True,
                args=(s.recovery_interval_seconds, "recovery", self._recover),
            ),
        ]
        slots = [
            threading.Thread(target=self._slot_loop, args=(i,), name=f"slot-{i}")
            for i in range(s.worker_concurrency)
        ]
        for t in background + slots:
            t.start()
        self.stopping.wait()
        log.info("stopping: finishing in-flight steps")
        for t in slots:
            t.join()
        self.halted.set()
        self._mark_stopped()
        self.http.close()
        log.info("worker %s stopped", self.instance_id)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = engine_settings()
    engine = make_engine(
        settings.database_url,
        pool_size=settings.worker_concurrency + 4,
        application_name=f"relay-{settings.worker_name}",
    )
    worker = Worker(settings, engine, default_registry())

    def _stop(signum: int, _frame: object) -> None:
        log.info("received %s", signal.Signals(signum).name)
        worker.stopping.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker.run()


if __name__ == "__main__":
    main()
