# RELAY

A fault-tolerant workflow engine with a live operations dashboard. Portfolio
project, built in stages; **currently at Stage 2 of 5** — see
[docs/implementation-status.md](docs/implementation-status.md).

RELAY runs business workflows across several worker processes. It records
every step durably in PostgreSQL, hands work out with time-bounded leases,
refuses writes from workers that lost their lease, and calls an external CRM
with idempotency keys so a retried call cannot apply the same change twice.

It is a small custom engine written to understand these mechanisms. It is
**not** production-ready and not a replacement for Temporal, Celery or similar.

## What it does

The demo workflow processes synthetic business enquiries:

```
extract → validate → enrich → crm_upsert
```

Each step's output is persisted and becomes the next step's input. The CRM is
a separate HTTP service with its own database, so "the CRM committed but the
engine never heard back" is a real failure mode, not a simulation.

## What is technically interesting

- **PostgreSQL as the queue.** Workers claim with `FOR UPDATE SKIP LOCKED` in a
  short transaction that commits before the step runs
  ([claims.py](relay/engine/claims.py)).
- **Fencing tokens.** Every claim increments `lease_generation`; every write
  back is conditional on owner + generation + unexpired lease by database time
  ([transitions.py](relay/engine/transitions.py)).
- **Atomic hand-off.** Completing a step and making the next one eligible
  happen in one transaction, so no crash can strand a workflow between them.
- **Receiver-side idempotency.** The CRM claims the key, checks a payload hash,
  applies the change and stores the response in one transaction
  ([service.py](services/demo_crm/service.py)).
- **State machine in the database.** Triggers reject illegal status
  transitions, decreasing lease generations and edits to a run's pinned
  version; a test checks every status pair against the Python table.
- **Crash recovery.** Workers renew their leases while they work; every worker
  also sweeps for expired leases and takes abandoned steps back
  ([recovery.py](relay/engine/recovery.py)). Tested by killing and freezing
  real worker processes.
- **Classified retries.** Exponential backoff with jitter and `Retry-After`;
  ambiguous failures are retried only because the receiver deduplicates
  ([retries.py](relay/engine/retries.py)).
- **Honest outcomes.** A CRM call that may have been applied but never
  confirmed ends as `outcome_unknown`, not `failed`.

Design: [docs/architecture.md](docs/architecture.md) ·
[guarantees and limitations](docs/guarantees-and-limitations.md) ·
[decision records](docs/decisions/).

## Running it

Requirements: Docker Desktop (WSL2 backend on Windows) and
[uv](https://docs.astral.sh/uv/) for running tests and scripts on the host.

```bash
cp .env.example .env            # PowerShell: Copy-Item .env.example .env
docker compose up -d --build    # databases, migrations, API, CRM, two workers
docker compose ps
```

| Service | URL (localhost only) |
|---|---|
| Control API + OpenAPI docs | http://127.0.0.1:8000/docs |
| Demo CRM | http://127.0.0.1:8100/docs |
| Engine PostgreSQL | 127.0.0.1:55432 |
| CRM PostgreSQL | 127.0.0.1:55433 |

Submit an enquiry (PowerShell users: use `curl.exe`, or the `/docs` page):

```bash
curl -X POST http://127.0.0.1:8000/workflows \
  -H "Content-Type: application/json" -H "Idempotency-Key: try-it-0001" \
  -d '{"input": {"name": "Ada Lovelace", "email": "ada@saltmarsh.example",
       "company": "Saltmarsh Analytics",
       "message": "Please automate our monthly analytics pack. Budget £12k."}}'

curl http://127.0.0.1:8000/workflows/<workflow_run_id>
curl http://127.0.0.1:8000/workers
```

### Crash demo (optional)

`compose.demo.yaml` turns on the demo CRM's fault controls and shortens leases:

```bash
docker compose -f compose.yaml -f compose.demo.yaml up -d
docker compose kill worker-a        # crash a worker mid-batch (SIGKILL)
docker compose start worker-a       # bring it back
```

Its steps are taken over by `worker-b` within about 10 seconds. The full
scripted demo arrives in Stage 5.

Stop without losing data: `docker compose stop`.
**Destructive reset** (deletes both databases): `docker compose down -v`.

## Tests

The suite needs a real PostgreSQL server; SQLite cannot test row locking. It
creates and migrates its own throwaway databases (`relay_test_*`).

```bash
docker compose up -d engine-db
uv sync
uv run pytest                    # everything (~45 s)
uv run pytest tests/unit         # no database needed
uv run pytest -m fault_injection # crash tests: kill/freeze real worker processes
uv run ruff check .
```

The end-to-end and fault-injection tests start the API, CRM and workers as
real processes, then kill them (SIGKILL), freeze them (SIGSTOP) or crash them
at exact points with test-only failpoints (`RELAY_FAILPOINTS`, off unless set).

## Windows notes

- Use Docker Desktop's WSL2 backend.
- Clone with Git's default settings; `.gitattributes` keeps LF line endings.
- Running the test suite from inside WSL2 (Ubuntu) is recommended. On native
  Windows everything runs except two tests that need POSIX signals (graceful
  `SIGTERM` shutdown and the `SIGSTOP` frozen-worker test); they are skipped.
- Install uv on Windows: see the [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/).

## Repository layout

```
relay/            engine library: config, schema, claims, transitions, steps, workflows
services/api/     control API (FastAPI); never executes steps
services/worker/  worker process: claim -> execute -> fenced write
services/demo_crm/ demo CRM with its own database and idempotency contract
migrations/       Alembic environments: engine/ and crm/
fixtures/         synthetic enquiries and companies (.example domains)
tests/            unit/, integration/ (real PostgreSQL), e2e/ (real processes)
docs/             architecture, guarantees, decisions, status
```

## Known limitations

No approval gate or dashboard yet (Stages 3 and 4). Delivery is at least
once; duplicate CRM effects are prevented only because the demo CRM honours
idempotency keys. The full list is in
[docs/guarantees-and-limitations.md](docs/guarantees-and-limitations.md).

## How this was built

Built with heavy AI assistance (Claude). The design is written down in
`docs/` so it can be reviewed and defended, and every guarantee claimed here
is backed by a test in `tests/`.
