# Implementation status

_Last updated: 2026-11-04 · Stages 0, 1 and 2 complete · Next: Stage 3_

## Completed

**Stage 0 — design**
- [architecture.md](architecture.md): components, state machines, transaction
  boundaries T1–T8, invariants I1–I11, failure scenarios with the tests that cover them.
- Five decision records in [decisions/](decisions/).

**Stage 1 — vertical slice**
- `uv.lock`, Dockerfile, `compose.yaml` (2 PostgreSQL 17 databases, one-shot
  migrate, API, CRM, worker-a, worker-b; ports on 127.0.0.1 only).
- Hand-written Alembic migrations; database triggers enforce the state machine,
  monotonic lease generations and immutable pinned versions.
- Submission (T1) with idempotency keys; claim (T2) with `FOR UPDATE SKIP
  LOCKED`; fenced completion (T4) with atomic successor scheduling.
- Enquiry workflow v1 (fixture-based extraction, validation, fixture
  enrichment, CRM upsert with a stable idempotency key); demo CRM with the
  full idempotency contract (T8); control API; worker process; CI.

**Stage 2 — reliability**
- `relay/engine/leases.py`: the fence, and fenced lease renewal (T3). An
  expired lease is never revived.
- `relay/engine/recovery.py`: bounded `SKIP LOCKED` recovery sweep (T6), run
  by every worker; expired attempts closed as `lease_expired`; step back to
  `ready`, or `failed` / `outcome_unknown` when the budget is spent.
- `relay/engine/retries.py`: exponential backoff with equal jitter, delay cap,
  `Retry-After` (capped), retryability by error class.
- `fail_step` (T5) schedules retries; ambiguous failures retried only for
  steps whose receiver deduplicates.
- Worker: lease-renewal thread (every lease/3), recovery thread, heartbeat
  thread; graceful shutdown keeps renewing until in-flight steps finish.
- Demo CRM fault controls (`/demo/faults`, only with `CRM_DEMO_MODE=true`):
  delay; 503 before commit (+`Retry-After`); 500, dropped connection or stall
  after commit. Counts for tests, rates for the live demo.
- Test-only failpoints (`RELAY_FAILPOINTS`): `after_claim`, `before_complete`,
  with `exit` (os._exit(137)) or `sleep:<s>`.
- `compose.demo.yaml`: enables CRM faults, lease 8 s, sweep every 1 s.

## Architecture decisions so far

- PostgreSQL is the queue; no broker (ADR 0001).
- Leases + fencing tokens; owner = per-process instance id (ADR 0002).
- Receiver-side idempotency for the CRM (ADR 0003).
- Persisted step outputs, no replay; events are an audit log (ADR 0004).
- Sequential workflows; partial unique index allows one active step per run (ADR 0005).
- Recovery treats a step as having an external effect exactly when it has an
  idempotency key (both fixed at submission from the pinned definition).
- A crashed attempt is retried immediately; a failed one waits for backoff.
- CRM fault state is in memory and resets when the CRM restarts (safe default).

## Actual test results

**Development sandbox** (Linux, PostgreSQL 16.15, Python 3.13), 2026-11-04:

```
uv run pytest                      191 passed in 45.2s
uv run pytest tests/fault_injection  4 consecutive runs, 10/10 each (11th test added after)
uv run ruff check .                All checks passed
docker compose [-f compose.demo.yaml] config --quiet   valid
```

| Suite | Count | What it proves |
|---|---|---|
| unit | 39 | parsing, validation, enrichment, CRM error classification, backoff/jitter/Retry-After, retryability, failpoint parsing |
| integration: triggers | 105 | DB triggers accept exactly the transitions in `states.py`, immutability, I1 |
| integration: submission | 4 | materialised steps, idempotent submission, concurrency |
| integration: claims/fencing | 10 | no double claims (12 × 40), atomic hand-off, stale worker refused (other and same instance), expired lease refused |
| integration: retries/recovery | 13 | backoff scheduling, Retry-After, permanent not retried, exhaustion → failed / outcome_unknown, renewal fencing (spec 7), recovery, 8 concurrent sweeps recover 30 steps exactly once |
| integration: CRM | 5 | replay, conflict, 10 concurrent same-key requests → 1 mutation |
| e2e (real processes) | 4 | 25-enquiry batch over 2 workers, HTTP replay, HTTP concurrency, worker health |
| fault injection (real processes) | 11 | spec tests 3, 4, 5 (×3 fault kinds), 6 (SIGSTOP), 8, 10, 17, renewal keeps a slow step, failpoints off by default |

Mutation checks (each made at least one test fail, then reverted): fence
without owner / generation / expiry; claim without row lock; worker never
renews; recovery ignores expiry; renewal revives expired leases; ambiguous
failures retried without an idempotency key.

**Your PC** (Windows, Docker Desktop):
- Stage 1: `docker compose up -d --build` built and started all 7 services.
  105 enquiries submitted through the API: 100 succeeded, 5 failed validation,
  steps spread over both workers; submission replay 200, conflict 409; CRM 100
  mutations, 0 duplicate keys. The 157 Stage 1 tests also passed on the repo
  files on your PC (run from the Linux shell attached to that folder).
- Stage 2: see the section below (updated after it runs).

Spec test checklist: done 1–10, 13–18; partly 19 (immutability +
version-filtered claims). Remaining: 11, 12, 19 (Stage 3).

## Known issues and limitations

- A worker that loses a lease cannot interrupt the step's code; it runs to
  the end and its result is refused.
- Event cursor pagination is by insert id (see guarantees doc).
- `max_attempts` per step is fixed in the workflow definition; there is no
  operator "retry again" action yet.
- The SIGSTOP and SIGTERM tests are skipped on native Windows.

## Next commands to run (on your machine)

```powershell
docker compose -f compose.yaml -f compose.demo.yaml up -d --build
docker compose kill worker-a     # while a batch is running
docker compose start worker-a
docker compose stop              # keeps data
```

## Next concrete step: Stage 3

1. `enquiry` v2 with the approval gate (project value above
   `RELAY_APPROVAL_THRESHOLD_GBP`), keeping v1 registered; prove a v1 run stays v1 (spec 19).
2. `approvals` table + migration; step `awaiting_approval` holds no lease.
3. `POST /workflows/{id}/approve|reject` (T7): decision and successor
   eligibility in one transaction; duplicate decision → same result;
   conflicting decision → 409 (spec 12).
4. Approval survives a full restart (spec 11).
5. Metrics endpoint and batch creation endpoint for the dashboard.

## Handover summary for a fresh Claude conversation

RELAY is a Python/FastAPI/PostgreSQL workflow engine portfolio project
(spec: the original prompt). Stages 0–2 are done and tested (191 tests on
real PostgreSQL, including crash tests that SIGKILL/SIGSTOP real worker
processes). Engine core: `relay/engine/` (`claims.py` T2, `leases.py` fence +
T3, `transitions.py` T4/T5, `recovery.py` T6, `retries.py`). Invariants and
transaction boundaries: `docs/architecture.md`. The repo lives on the user's
Windows PC at `C:\Users\kyran\relay`; Docker Desktop runs the stack there.
Next: Stage 3 (approval gate as enquiry v2, approvals API, metrics).
