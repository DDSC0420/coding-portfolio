"""Check the engine's invariants against what is actually in the two databases.

Read-only. Used by scripts/verify.py after a demo or a crash test, and by the
test suite. Each check is one SQL statement that returns the rows breaking it,
so a failure names the exact workflow or step.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    violations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Report:
    checks: list[Check]
    facts: dict[str, Any]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


_ENGINE_CHECKS: list[tuple[str, str]] = [
    (
        "A finished workflow has no unfinished steps (I11)",
        """
        SELECT w.id AS workflow, w.status, s.name AS step, s.status AS step_status
        FROM workflow_runs w JOIN step_runs s ON s.workflow_run_id = w.id
        WHERE w.status IN ('succeeded','failed','outcome_unknown','rejected')
          AND s.status IN ('pending','ready','running','awaiting_approval')
        """,
    ),
    (
        "An unfinished workflow has exactly one active step (I1)",
        """
        SELECT w.id AS workflow, w.status, count(s.id) AS active_steps
        FROM workflow_runs w LEFT JOIN step_runs s ON s.workflow_run_id = w.id
             AND s.status IN ('ready','running','awaiting_approval')
        WHERE w.status IN ('running','awaiting_approval')
        GROUP BY w.id, w.status HAVING count(s.id) <> 1
        """,
    ),
    (
        "A succeeded workflow has every step succeeded",
        """
        SELECT w.id AS workflow, s.name AS step, s.status AS step_status
        FROM workflow_runs w JOIN step_runs s ON s.workflow_run_id = w.id
        WHERE w.status = 'succeeded' AND s.status <> 'succeeded'
        """,
    ),
    (
        "No step ran before its predecessor succeeded",
        """
        SELECT s.workflow_run_id AS workflow, s.name AS step, s.status, p.name AS predecessor,
               p.status AS predecessor_status
        FROM step_runs s JOIN step_runs p ON p.workflow_run_id = s.workflow_run_id
             AND p.position = s.position - 1
        WHERE s.status NOT IN ('pending','cancelled') AND p.status <> 'succeeded'
        """,
    ),
    (
        "Every claim is one attempt: attempts = attempt count = lease generation (I3)",
        """
        SELECT s.id AS step, s.name, s.attempt_count, s.lease_generation, count(a.id) AS attempts
        FROM step_runs s LEFT JOIN step_attempts a ON a.step_run_id = s.id
        GROUP BY s.id HAVING count(a.id) <> s.attempt_count OR s.lease_generation <> s.attempt_count
        """,
    ),
    (
        "A succeeded step was completed exactly once (I5)",
        """
        SELECT s.id AS step, s.name,
               count(*) FILTER (WHERE a.outcome = 'succeeded') AS succeeded_attempts,
               count(*) FILTER (WHERE a.outcome = 'paused_for_approval') AS paused_attempts
        FROM step_runs s LEFT JOIN step_attempts a ON a.step_run_id = s.id
        WHERE s.status = 'succeeded'
        GROUP BY s.id
        HAVING count(*) FILTER (WHERE a.outcome IN ('succeeded','paused_for_approval')) <> 1
        """,
    ),
    (
        "Only the step that is running has an open attempt",
        """
        SELECT s.id AS step, s.name, s.status, count(a.id) AS open_attempts
        FROM step_runs s LEFT JOIN step_attempts a ON a.step_run_id = s.id
             AND a.outcome = 'in_progress'
        GROUP BY s.id HAVING count(a.id) <> (s.status = 'running')::int
        """,
    ),
    (
        "A workflow waits for approval exactly when it has an undecided request",
        """
        SELECT w.id AS workflow, w.status,
               count(a.id) FILTER (WHERE a.status = 'pending') AS pending_requests
        FROM workflow_runs w LEFT JOIN approvals a ON a.workflow_run_id = w.id
        GROUP BY w.id
        HAVING (w.status = 'awaiting_approval')::int
               <> count(a.id) FILTER (WHERE a.status = 'pending')
        """,
    ),
    (
        "A rejected workflow never had its CRM step claimed",
        """
        SELECT w.id AS workflow, s.attempt_count
        FROM workflow_runs w JOIN step_runs s ON s.workflow_run_id = w.id
        WHERE w.status = 'rejected' AND s.idempotency_key IS NOT NULL AND s.attempt_count > 0
        """,
    ),
]


def _rows(engine: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def verify(engine: Engine, crm: Engine | None = None, sample: int = 5) -> Report:
    checks = []
    for name, sql in _ENGINE_CHECKS:
        bad = _rows(engine, sql)
        checks.append(Check(name, not bad, f"{len(bad)} violations", bad[:sample]))

    facts: dict[str, Any] = {
        "workflows": {
            r["status"]: r["n"]
            for r in _rows(engine, "SELECT status, count(*) AS n FROM workflow_runs GROUP BY 1")
        },
        "attempts": _rows(engine, "SELECT count(*) AS n FROM step_attempts")[0]["n"],
        "retries": _rows(
            engine, "SELECT count(*) AS n FROM step_attempts WHERE attempt_number > 1"
        )[0]["n"],
        "leases_expired": _rows(
            engine, "SELECT count(*) AS n FROM step_attempts WHERE outcome = 'lease_expired'"
        )[0]["n"],
        "stale_writes_refused": _rows(
            engine,
            "SELECT count(*) AS n FROM workflow_events WHERE type = 'step.stale_write_rejected'",
        )[0]["n"],
    }

    if crm is not None:
        # The two databases cannot be joined, so compare key sets in Python.
        steps = _rows(
            engine,
            """
            SELECT s.idempotency_key AS key, s.status, s.attempt_count, w.status AS workflow_status,
                   w.id AS workflow
            FROM step_runs s JOIN workflow_runs w ON w.id = s.workflow_run_id
            WHERE s.idempotency_key IS NOT NULL
            """,
        )
        applied = {
            r["idempotency_key"]: r["n"]
            for r in _rows(
                crm, "SELECT idempotency_key, count(*) AS n FROM mutations GROUP BY idempotency_key"
            )
        }
        twice = [{"key": k, "mutations": n} for k, n in applied.items() if n > 1]
        checks.append(
            Check(
                "No CRM write was applied twice (I7)",
                not twice,
                f"{len(applied)} keys in the CRM, {len(twice)} applied more than once",
                twice[:sample],
            )
        )
        missing = [s for s in steps if s["status"] == "succeeded" and applied.get(s["key"]) != 1]
        checks.append(
            Check(
                "Every succeeded CRM step has exactly one write in the CRM",
                not missing,
                f"{sum(s['status'] == 'succeeded' for s in steps)} succeeded CRM steps, "
                f"{len(missing)} without exactly one write",
                missing[:sample],
            )
        )
        never_ran = [s for s in steps if s["attempt_count"] == 0 and s["key"] in applied]
        checks.append(
            Check(
                "No CRM write exists for a step that was never claimed",
                not never_ran,
                f"{len(never_ran)} violations",
                never_ran[:sample],
            )
        )
        known = {s["key"] for s in steps}
        unknown = [s for s in steps if s["status"] == "outcome_unknown"]
        facts["crm_writes"] = sum(applied.values())
        facts["crm_writes_from_other_data"] = sum(1 for k in applied if k not in known)
        facts["outcome_unknown_actually_applied"] = sum(1 for s in unknown if s["key"] in applied)
        facts["outcome_unknown_not_applied"] = sum(1 for s in unknown if s["key"] not in applied)
    return Report(checks, facts)


def render(report: Report) -> str:
    lines = []
    for c in report.checks:
        lines.append(f"{'PASS' if c.ok else 'FAIL'}  {c.name}  ({c.detail})")
        for v in c.violations:
            lines.append(f"        {v}")
    lines.append("")
    for k, v in report.facts.items():
        lines.append(f"{k.replace('_', ' ')}: {v}")
    lines.append("")
    lines.append("All invariants hold." if report.ok else "INVARIANT VIOLATIONS FOUND.")
    return "\n".join(lines)
