# ADR 0005: Sequential, versioned workflows for v1

**Status:** accepted (Stage 0)

## Decision
v1 supports a predefined, sequential step list per `(workflow_type, version)`,
with a conditional approval gate (Stage 3). All step rows are materialised at
submission, and each run is pinned to its version.

## Consequences
- At most one active step per workflow, enforced with a partial unique index.
  This removes intra-workflow contention and makes locking simple.
- Version pinning is concrete: an existing run's steps are rows in the
  database, and handlers are resolved by `(type, version, step name)`.
- No fan-out, joins or DAGs. Adding them would need a dependency table and a
  different "make successors eligible" rule.
