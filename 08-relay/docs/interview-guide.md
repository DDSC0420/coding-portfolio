# Interview guide

Questions this project should let you answer, with the short answer and where to point in the
code. Read `architecture.md` first; this is the revision sheet. If you can explain each answer
without looking, you can defend the project.

## The thirty-second version

"RELAY is a small workflow engine I built to understand what Celery and Temporal do for you.
Workers claim steps from a PostgreSQL table using `SKIP LOCKED`, each claim is a lease with a
fencing token, and a worker that has lost its lease can't write. The external call uses an
idempotency key, so a retry after a lost reply doesn't apply the change twice. The tests kill
and freeze real worker processes and check both databases afterwards. It's at-least-once, not
exactly-once, and it isn't production software. I built it with heavy AI assistance, from a
design I wrote down first."

## Queue and claiming

**Why PostgreSQL as the queue, not RabbitMQ or Redis?**
The hard problems here (who owns a step, is that still true, did the side effect happen) need
transactions with the workflow state. A broker would be a second source of truth that has to
agree with the database, and you'd still solve ownership in the database. At this scale a
polled, indexed table is cheap. Cost: throughput is capped by one database's writes.
(`docs/decisions/0001-postgres-queue.md`)

**What does `FOR UPDATE SKIP LOCKED` do, exactly?**
`FOR UPDATE` locks the selected row. `SKIP LOCKED` makes other transactions pass over locked
rows instead of waiting. So two workers polling at the same instant lock different rows: no
double claim, and no queueing behind each other. (`relay/engine/claims.py`)

**Why does the claim commit before the step runs?**
So no transaction or row lock is held while slow or remote work happens, and so the lease is
visible to everyone. Holding a transaction open across an HTTP call ties up a connection and
blocks vacuum; and if the process dies, the lock vanishes with no record of who had it.

**What stops two workers running the same step?**
The claim only takes `ready` rows and flips them to `running` under the row lock. A partial
unique index also allows only one active step per workflow.
Test: `test_competing_workers_never_claim_the_same_step`.

## Leases and fencing

**What is a lease?**
Ownership with an expiry. The owner must keep renewing; if it stops, for any reason, ownership
ends by itself. That turns "is that worker dead?" (unknowable) into "has its lease expired?"
(one comparison, on the database clock).

**Why the database clock?**
Workers' clocks can disagree. `now()` in PostgreSQL is one clock everyone shares.

**A worker pauses for 30 seconds (GC, a frozen VM) and wakes up. It still thinks it owns the
step. What happens?**
Its lease expired; another worker claimed the step, which raised the lease generation. The
stale worker's write is `UPDATE … WHERE status='running' AND lease_owner=me AND
lease_generation=mine AND lease_expires_at > now()`. It matches nothing, so nothing is written.
That generation number is the fencing token. (`relay/engine/leases.py`;
`test_frozen_worker_cannot_overwrite_the_worker_that_took_over` does this with SIGSTOP.)

**Why is the owner a per-process id rather than "worker-a"?**
A restarted worker-a is a different process. If the id were the name, the new one could renew
or complete a lease its dead predecessor held.

**What can fencing not do?**
Stop the stale worker's HTTP request from leaving. Fencing protects the engine's own state.
The CRM needs its own protection: the idempotency key.

**Why renew at a third of the lease?**
A healthy worker can miss two renewals (a slow query, a short stall) before the lease is at
risk. Shorter leases recover faster but tolerate less jitter.

## Side effects and idempotency

**The CRM saved the contact, then the worker died before recording it. Now what?**
The lease expires, the step is retried with the *same* idempotency key and a byte-identical
payload. The CRM sees the key, returns the stored response, applies nothing. One write, two
attempts. (`test_worker_killed_after_crm_commit_is_retried_and_crm_replays`)

**Where does the key come from?**
`relay:<workflow id>:crm_upsert`, stored on the step row when the workflow is submitted. It
never depends on the attempt, the worker or the time.

**Is that exactly-once?**
No. Delivery is at-least-once; the *effect* is once because the receiver deduplicates. Against
an API without idempotency keys RELAY cannot prevent a duplicate, and it cannot undo an
external effect.

**What is `outcome_unknown`?**
Retries ran out after an attempt whose result never came back (timeout, dropped connection,
worker death). The CRM may have applied it. Saying "failed" would be a guess, and someone might
resubmit and create a duplicate; so it says it doesn't know, and a person checks.

**How are errors classified?**
Permanent (a validation error, HTTP 400): never retried. Transient (connection refused, 503):
definitely not applied, retried with exponential backoff and jitter. Ambiguous (read timeout,
500): may have been applied, retried only because the key makes it safe.

**Why jitter?**
Without it, everything that failed together retries together and knocks the recovering service
over again.

## Recovery

**Who notices a dead worker?**
Every worker runs a small sweep for expired leases, itself using `SKIP LOCKED`, so several
sweeps at once each recover a step exactly once. There is no coordinator to fail.

**How long does recovery take?**
At most lease length plus the sweep interval after the last renewal: 17 s by default, 9 s in
the demo.

**What if every process dies at once?**
Nothing is in memory that matters. On restart, in-flight steps are recovered by lease expiry.
(`test_full_restart_with_work_in_flight_loses_nothing`)

**Why are "step done" and "next step ready" one transaction?**
If they were two, a crash between them would leave a finished step and nothing to run next: a
workflow stuck forever with no error.

## Approvals and versions

**How does a workflow wait days for a person without tying up a worker?**
The paused step holds no lease. Nothing to renew, nothing to expire, and the recovery sweep only
looks at `running` steps. The decision is an API call that completes the step and readies the
next one in one transaction. (`relay/engine/approvals.py`)

**Two people click Approve and Reject at the same moment?**
The decision locks the approval row. One wins; the same decision repeated gets the original
back; the opposite gets a 409. A trigger refuses any later change.
(`test_sixteen_decisions_at_once_produce_exactly_one`)

**You change the workflow while runs are in flight. What happens to them?**
Nothing. A run's steps are created at submission from the version it was submitted with, and
that version can't be changed. You publish v2 and leave v1 registered until its runs finish.
Workers only claim versions they can execute.

## Things that went wrong (good stories)

**A crash failed a valid workflow.** `validate` had a retry budget of 1 ("validation errors are
permanent, why retry?"). But a worker dying mid-step also uses an attempt, so a crash at the
wrong moment marked a good enquiry failed. No test caught it; a dashboard screenshot did. The
fix separates the two ideas: "don't retry this error" is the error's class, and the budget must
be at least 2. Lesson: one number was doing two jobs.

**A test passed for the wrong reason.** The first stale-worker test used two different workers,
so the owner check alone caught it and the generation check was never exercised. Deliberately
removing the generation check and seeing no test fail is how that was found.

**A metric that cried wolf.** "Stale writes refused" showed 5 on a healthy system: the renewal
thread racing its own worker's completion. Harmless, but a signal that fires when nothing is
wrong gets ignored when something is.

## What I'd do next, and what I'd never claim

Next: authentication; an operator "retry" for failed runs; `LISTEN/NOTIFY` instead of polling;
parallel steps; partitioning or archiving old runs; metrics and alerts on `outcome_unknown`.

Never: that it is exactly-once, that it is production-ready, or that the benchmark (one 2-CPU
machine, where four workers were no faster than two) says anything about scale.
