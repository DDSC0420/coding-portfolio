"""Test-only failpoints: crash a worker at an exact point in the step lifecycle.

Disabled unless RELAY_FAILPOINTS is set. Nothing in compose.yaml sets it.

    RELAY_FAILPOINTS="after_claim@extract=exit,before_complete@crm_upsert=exit"

Each entry is `<point>[@<step name>]=<action>`. Points:

    after_claim      the claim has committed; the step has not started
    before_complete  the step's code returned (for crm_upsert: the CRM has
                     committed and answered); the engine has not recorded it

Actions:

    exit        os._exit(137): no cleanup, no lease release, like SIGKILL
    sleep:<s>   block this slot for <s> seconds (other threads keep running)

Tests use these instead of arbitrary sleeps to hit the failure windows in
docs/architecture.md §8 precisely.
"""

import logging
import os
import time
from functools import lru_cache

log = logging.getLogger("relay.failpoints")

POINTS = frozenset({"after_claim", "before_complete"})


@lru_cache
def _parse(spec: str) -> dict[tuple[str, str | None], str]:
    table: dict[tuple[str, str | None], str] = {}
    for entry in filter(None, (e.strip() for e in spec.split(","))):
        target, _, action = entry.partition("=")
        point, _, step = target.partition("@")
        if point not in POINTS or not (action == "exit" or action.startswith("sleep:")):
            raise ValueError(f"invalid RELAY_FAILPOINTS entry {entry!r}")
        table[(point, step or None)] = action
    return table


def active() -> dict[tuple[str, str | None], str]:
    return _parse(os.environ.get("RELAY_FAILPOINTS", ""))


def hit(point: str, step: str) -> None:
    table = active()
    if not table:
        return
    action = table.get((point, step)) or table.get((point, None))
    if action is None:
        return
    log.warning("FAILPOINT %s@%s -> %s", point, step, action)
    if action == "exit":
        os._exit(137)
    time.sleep(float(action.removeprefix("sleep:")))
