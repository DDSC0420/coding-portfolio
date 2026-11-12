# Guarantees and limitations

This describes what RELAY actually does. Each claim below is backed by a test.

## What is durable

- Every workflow run, step, attempt, event and worker heartbeat is a row in the
  engine PostgreSQL database. No in-memory state is needed to know what has
  happened or what is next.
- A step's output is committed in the same transaction that marks it
  `succeeded` and makes the next step eligible. A completed step is never
  executed again.
- A claim is committed before the step runs, so the database always records
  which worker instance owns which step and until when.

All of this assumes the engine database itself is durable and available. If
PostgreSQL is down, workers cannot claim or record anything; they log errors
and keep polling. RELAY does not replicate or back up the database.

## Execution is at least once

A step can run more than once. A worker can finish a step's work and die
before recording it. Steps are therefore written to be safe to re-run:

- `extract`, `validate`, `enrich` and `approval` are pure functions of persisted input.
- `crm_upsert` relies on the CRM's idempotency contract (below).

RELAY does **not** provide exactly-once execution. With the demo CRM, it
provides *at most one applied CRM mutation per logical step* in the scenarios
tested, because the receiver deduplicates.

## What fencing protects

Each claim increments `lease_generation`. Completion, failure and renewal
writes only apply if the step is still `running`, owned by the same
worker instance, at the same generation, with an unexpired lease by database
time. A worker that lost its lease cannot overwrite a newer attempt's state.

Tested: a stale worker (different instance) and a stale slot of the *same*
instance are both refused; an expired-but-not-yet-reclaimed lease is refused;
a renewal never revives an expired lease; and a real worker process frozen
with SIGSTOP past its lease, then resumed, has both its renewal and its
completion refused while the worker that took over keeps its result.

Fencing does **not** stop a stale worker from sending an HTTP request. It
protects engine state only.

## What CRM idempotency protects

The demo CRM applies a request with a given `Idempotency-Key` at most once
and stores the response. Same key and payload returns the stored response;
same key with a different payload returns 409. A unique constraint on
`mutations.idempotency_key` makes a second mutation for one key impossible.

Tested: replay returns the stored result; 10 concurrent requests with one key
(in-process and over HTTP) apply one mutation; a different payload is refused.

**Retention:** idempotency records are kept for the life of the demo database.
Deleting a record re-opens the window for a duplicate. Any real retention
period must be longer than the longest time a retry could still arrive.

**Arbitrary external APIs:** RELAY cannot prevent duplicates against a
receiver that does not honour idempotency keys. It also cannot roll back an
external effect.

## Retry classification

| Outcome | Class | Retried |
|---|---|---|
| Connection refused / connect timeout | transient | yes |
| HTTP 429, 503 (demo CRM: 503 means rolled back) | transient, honours `Retry-After` seconds | yes |
| Read timeout, connection dropped after sending | ambiguous | yes, same key |
| HTTP 500, 502, 504 and other 5xx | ambiguous | yes, same key |
| HTTP 400, 409, 413, 422, other 4xx | permanent | no |
| Validation errors | permanent | no |
| Unexpected exception in a local step | transient | yes |
| Unexpected exception in the CRM step | ambiguous | yes, same key |

Retries wait `base * 2^(attempt-1)` seconds (capped), with equal jitter, and
at least as long as a `Retry-After` header (capped at 120 s). Each step has
its own attempt budget (`crm_upsert`: 6; the others: 3). A worker vanishing
mid-attempt counts as an attempt, so no step may have a budget below 2: with a
budget of 1, a crash at the wrong moment failed a valid workflow (this was a real
bug, found from a dashboard screenshot and now enforced in `StepDefinition`).

## When retries are exhausted

If any attempt of an external-effect step was ambiguous (or its lease expired
mid-attempt), the step and workflow end as `outcome_unknown`, not `failed`.
The CRM may have applied the change; a human must check. Otherwise they end
as `failed`. Remaining steps are `cancelled`. Both paths are tested with real
processes: a CRM refusing every request (503 before commit) ends `failed` with
no mutation; an exhausted run with an earlier ambiguous attempt ends
`outcome_unknown`.

## Recovery after a crash

Every worker runs a recovery sweep. A step whose lease expired (its worker
was killed, frozen or cut off) goes back to `ready` with its open attempt
closed as `lease_expired`, or ends terminally if its attempt budget is spent.
Expect recovery within `lease_seconds + recovery_interval_seconds` of the
last renewal (17 s by default, 9 s with `compose.demo.yaml`).

A worker on a slow step renews its lease every `lease_seconds / 3`, so a
healthy slow step is not taken away. If a worker loses a lease anyway, the
step's code keeps running to the end (Python threads cannot be cancelled), but
its result is refused by the fence.

## The approval gate

An enquiry worth £50,000 or more (`RELAY_APPROVAL_THRESHOLD_GBP`), or one the
submitter flagged, pauses before the CRM step. While paused it holds no lease,
so it survives a restart of every process and is never "recovered". Approve and
the workflow continues with the output stored at the pause; reject and it ends
as `rejected` without the CRM ever being called.

A decision is final. Tested: 16 simultaneous decisions (half approve, half
reject) produce exactly one; repeating a decision returns the original; the
opposite decision is refused with 409; a direct SQL update of a decided row is
refused by a trigger; a stale worker cannot create an approval request.

## Version pinning

A run keeps the workflow version it was submitted with. `enquiry` v1 (no gate)
and v2 (with the gate) are both registered; a v1 run of a £120,000 enquiry goes
straight to the CRM step even after v2 exists, and a worker claims only the
versions it can execute.

## Checking it afterwards

`scripts/verify.py` runs twelve read-only checks against both databases (for
example: every succeeded CRM step has exactly one write in the CRM; no finished
workflow has an unfinished step). Its own tests corrupt the data on purpose,
with the guard triggers switched off, to prove each check fires.

## Known limitations

- Event cursor pagination orders by event id, which is assigned at insert,
  not commit. A live tail across many workflows could briefly skip an event
  committed out of order. A single workflow's timeline is not affected,
  because its transactions are sequential.
- Workers poll; idle workers issue one cheap indexed query per slot per poll
  interval.
- No authentication. All ports bind to `127.0.0.1`. Approval reviewer names
  are labels, not identities: anyone who can reach the API can approve.
- A worker that loses a lease cannot interrupt the step's code; it runs to the
  end and its result is refused.
- `max_attempts` is fixed in the workflow definition; there is no operator
  "retry this again" action for a `failed` or `outcome_unknown` run.
- The dashboard polls once a second; it is not pushed updates.
- Throughput on a 2-CPU machine did not rise beyond two workers
  (see the README's benchmark): everything shares the same CPUs and one database.

## What public production use would need

Authentication and authorisation on every endpoint; TLS; secrets management;
database backups, replication and failover; metrics and alerting on stuck or
`outcome_unknown` workflows; an operator workflow for reconciliation; data
retention policies; load testing beyond a laptop; and a security review.
