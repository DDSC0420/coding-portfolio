# Implementation status

_Last updated: 2026-11-12 · All five stages complete._

RELAY lives in the `coding-portfolio` repository as `08-relay/`. Stages 0 to 2 were built in
the original `C:\Users\kyran\relay` repository (three commits, kept there); Stages 3 to 5 were
built here on top of them. The README is the front door; this file records what was verified
and where.

## What exists

| Stage | Delivered |
|---|---|
| 0 | `docs/architecture.md`, five decision records |
| 1 | Schema and guard triggers, submission, `SKIP LOCKED` claims, fenced writes, enquiry v1, demo CRM, API, worker |
| 2 | Lease renewal, recovery sweep, retries, error classes, `outcome_unknown`, CRM faults, failpoints, crash tests |
| 3 | Approval gate (enquiry v2), version pinning, operations API |
| 4 | Dashboard (`web/`) |
| 5 | `relay/verify.py`, `scripts/demo.py`, `scripts/benchmark.py`, `scripts/run_local.py`, CI, interview guide |

## Verified

**Development sandbox** (Linux, 2 CPUs, PostgreSQL 16, Python 3.13), 2026-11-12:

```
uv run pytest                       224 passed (unit 43, integration 158, e2e 8, fault injection 15)
cd web && npm test                  5 passed (real stack; one worker killed mid-batch)
uv run python scripts/demo.py --local    exit 0; 65 enquiries, worker killed holding 3 leases, 0 CRM writes applied twice
uv run python scripts/benchmark.py --workflows 1200    see README
uv run ruff check . / format --check     clean
```

**GitHub Actions** (`.github/workflows/relay.yml` in the portfolio repo), every push: the Python
suite on PostgreSQL 17; the browser suite; and the real Docker Compose stack with
`scripts/demo.py` (which uses `docker compose kill worker-a`) and a small benchmark.

**Kyran's PC** (Windows, Docker Desktop): Stages 1 and 2 were run there on 2026-11-04.
Stages 3 to 5 have **not** been run there yet. To do so, from `coding-portfolio/08-relay`:

```powershell
docker compose -f compose.yaml -f compose.demo.yaml up -d --build
# open http://127.0.0.1:8080
uv run python scripts/demo.py
```

The first build downloads the Node and nginx images for the dashboard. If the old `relay`
Compose project from `C:\Users\kyran\relay` is still up, stop it first (`docker compose down`
there): both use ports 8000, 8100, 55432 and 55433, and the old database lacks the `approvals`
table (the `migrate` service adds it if the volume is reused).

## Mutation checks

Each of these was made on purpose, made at least one test fail, and was reverted. Stage 2:
fence without owner / generation / expiry; claim without row lock; worker never renews;
recovery ignores expiry; renewal revives expired leases; ambiguous failures retried without an
idempotency key. Stage 3: pause without the fence; decision without the row lock; opposite
decision not a conflict; approve without scheduling the successor.

## Known issues and limitations

See `guarantees-and-limitations.md`. In short: at-least-once; no authentication; sequential
workflows only; a worker cannot be interrupted mid-step; no operator retry; polling dashboard;
SIGSTOP and SIGTERM tests skip on native Windows.
