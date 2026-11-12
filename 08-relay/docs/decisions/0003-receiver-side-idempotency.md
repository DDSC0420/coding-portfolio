# ADR 0003: Receiver-side idempotency for external effects

**Status:** accepted (Stage 0)

## Context
Execution is at least once. The CRM step can run twice: a worker may die after
the CRM committed, or the response may be lost. The engine cannot know whether
the CRM applied the first request.

## Decision
Each logical CRM step has one stable idempotency key, assigned when the step
row is created and reused by every attempt. The CRM, in one transaction,
claims the key, verifies the request hash, applies the mutation and stores the
response. Same key + same payload returns the stored response; same key +
different payload returns 409. A unique constraint allows at most one mutation
row per key.

## Consequences
- Duplicate CRM effects are prevented in the scenarios tested, because the
  receiver cooperates. This is not a general exactly-once guarantee.
- Retries must send identical payloads, so the payload is built only from
  persisted step input — never from attempt numbers or timestamps.
- Idempotency records must outlive any possible retry. The demo keeps them forever.
- If retries are exhausted after an ambiguous attempt, the engine reports
  `outcome_unknown` rather than `failed`.

## Rejected alternatives
- **Check-then-write from the engine** ("does the contact exist?"): racy, and
  cannot distinguish "I created it" from "someone else did".
- **Distributed transaction / 2PC:** not available across arbitrary HTTP APIs
  and would make the demo unrealistically easy.
