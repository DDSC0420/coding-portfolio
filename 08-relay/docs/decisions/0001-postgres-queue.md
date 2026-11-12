# ADR 0001: PostgreSQL as the queue, not a message broker

**Status:** accepted (Stage 0)

## Context
Workers need to find eligible work, claim it without colliding, and record
progress durably. The state that decides "is this step done?" must live
somewhere durable regardless of how work is distributed.

## Decision
Use the engine PostgreSQL database as the queue. Eligible steps are rows with
`status = 'ready' AND next_eligible_at <= now()`, claimed with
`SELECT … FOR UPDATE SKIP LOCKED`. Workers poll.

## Consequences
- One source of truth: claim, completion and successor scheduling happen in
  the same database, so they can share transactions (invariant I6).
- No dual-write problem between a broker message and a database row.
- Polling costs a cheap indexed query every poll interval per worker slot.
  Fine at demo scale; at much higher scale, `LISTEN/NOTIFY` wake-ups or a
  broker would be worth revisiting.
- Throughput is bounded by database write capacity. Measured by `scripts/benchmark.py`; see the README.

## Rejected alternatives
- **Redis/RabbitMQ:** a second system whose acknowledgements must be
  reconciled with database state; ownership and fencing would still need the database.
- **Celery/Temporal/Prefect:** would delegate exactly the mechanisms this
  project exists to implement.
