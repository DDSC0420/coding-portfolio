# ADR 0004: Persisted step execution, not deterministic replay

**Status:** accepted (Stage 0)

## Context
Durable execution engines either replay workflow code against an event history
(Temporal) or persist each step's output and resume from the last completed
step.

## Decision
Persist each step's output when it completes. The next step's input is the
previous step's output. Recovery resumes at the first non-completed step; a
completed step is never re-executed.

## Consequences
- Simple to reason about and to inspect: every intermediate value is in a row.
- Workflow code needs no determinism rules, because it is never replayed.
- Workflows are limited to predefined step lists; no dynamic control flow
  expressed in ordinary code.
- The event table is a transactional audit log, not the source of state.
  This is not event sourcing.

## Rejected alternatives
- **Deterministic replay:** much more machinery (history, determinism checks,
  versioning of in-flight code paths) for no benefit to a sequential v1.
