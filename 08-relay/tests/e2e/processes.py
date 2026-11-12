"""Run RELAY services as real OS processes for end-to-end and crash tests."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Service:
    def __init__(self, name: str, args: list[str], env: dict[str, str], log_dir: Path) -> None:
        self.name, self.args, self.log_path = name, args, log_dir / f"{name}.log"
        self.env = {**os.environ, **env, "PYTHONUNBUFFERED": "1"}
        self.proc: subprocess.Popen | None = None

    def start(self) -> "Service":
        self._log = self.log_path.open("ab")
        self.proc = subprocess.Popen(
            self.args, cwd=ROOT, env=self.env, stdout=self._log, stderr=subprocess.STDOUT
        )
        return self

    def stop(self, timeout: float = 15) -> int | None:
        """Graceful: SIGTERM, then SIGKILL if it does not exit in time."""
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._log.close()
        return self.proc.returncode if self.proc else None

    def kill(self) -> None:
        """Crash: SIGKILL, no cleanup, exactly like `docker kill`."""
        assert self.proc is not None
        self.proc.kill()
        self.proc.wait()

    def pause(self) -> None:
        """Freeze every thread (SIGSTOP), like a long GC pause or a hung VM. POSIX only."""
        assert self.proc is not None
        self.proc.send_signal(signal.SIGSTOP)

    def resume(self) -> None:
        assert self.proc is not None
        self.proc.send_signal(signal.SIGCONT)

    def wait_exit(self, timeout: float = 20) -> int:
        assert self.proc is not None
        return self.proc.wait(timeout)

    def log(self) -> str:
        return self.log_path.read_text(errors="replace") if self.log_path.exists() else ""


def uvicorn(name: str, app: str, port: int, env: dict[str, str], log_dir: Path) -> Service:
    return Service(
        name,
        [
            sys.executable,
            "-m",
            "uvicorn",
            app,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env,
        log_dir,
    )


def worker(name: str, env: dict[str, str], log_dir: Path) -> Service:
    return Service(
        name, [sys.executable, "-m", "services.worker"], {**env, "RELAY_WORKER_NAME": name}, log_dir
    )


def wait_ready(url: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError(f"{url} not ready after {timeout}s")


def wait_until(predicate, timeout: float, what: str, interval: float = 0.05):
    """Poll real system state until `predicate()` is truthy. Not a sleep: it returns
    as soon as the condition holds, and fails loudly with `what` if it never does."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for: {what}")
