# ADR 0002: Time-bounded leases with fencing tokens

**Status:** accepted (Stage 0); implemented in Stage 2

## Context
A worker can die, hang or be partitioned at any moment. Its claimed work must
become available again, and a worker that comes back late must not overwrite
the state written by whoever took over.

## Decision
- A claim grants a lease: `lease_owner`, `lease_expires_at` (database time)
  and `lease_generation`, incremented on every claim.
- Workers renew only their own unexpired lease. An expired lease is never revived.
- Every completion, failure and renewal is a conditional `UPDATE` on
  `(status='running', owner, generation, lease_expires_at > now())`. Zero rows
  updated means ownership was lost; the worker writes nothing.
- A bounded recovery sweep returns expired leases to `ready` using `SKIP LOCKED`,
  so several sweeps running at once cannot double-schedule.

## Consequences
- Recovery latency is roughly the lease duration plus the sweep interval.
  Shorter leases recover faster but need more frequent renewals.
- A slow-but-alive worker whose lease expires loses the step and its result is
  discarded. Acceptable: steps are designed to be safely re-executed.
- Fencing protects engine state only. It cannot recall an HTTP request already
  sent (see ADR 0003).

## Rejected alternatives
- **Advisory locks held for the step's duration:** require an open session for
  the whole execution and vanish silently with the connection.
- **Row lock held during execution:** violates the "no transaction during
  external calls" rule and ties work duration to connection lifetime.
