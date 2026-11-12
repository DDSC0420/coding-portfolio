"""Versioned workflow definitions.

A run is pinned to `(workflow_type, workflow_version)` at submission. Its step
rows are materialised from the definition then, and workers resolve handlers
by `(type, version, step name)`. Editing a published definition is therefore
never needed: publish a new version instead and leave the old one registered
until no runs use it.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from relay.config import EngineSettings
from relay.engine.errors import UnknownWorkflow


@dataclass(frozen=True)
class StepContext:
    """Everything a step handler may use. Handlers must not touch the engine database."""

    workflow_run_id: str
    step_name: str
    attempt: int
    input: dict[str, Any]
    idempotency_key: str | None
    settings: EngineSettings
    http: httpx.Client | None = None


StepHandler = Callable[[StepContext], dict[str, Any]]


@dataclass(frozen=True)
class StepDefinition:
    name: str
    handler: StepHandler
    max_attempts: int = 3
    # True if the step changes something outside the engine database. Such
    # steps get a stable idempotency key, and an unexplained failure is
    # classified as ambiguous rather than transient.
    external_effect: bool = False

    def __post_init__(self) -> None:
        # Every claim uses one attempt, including a claim whose worker then dies. With a
        # budget of 1, a crash at the wrong moment would fail a perfectly good workflow.
        # "Do not retry this error" is said by raising PermanentStepError, not by the budget.
        if self.max_attempts < 2:
            raise ValueError(
                f"step {self.name!r}: max_attempts must be at least 2 so one worker crash "
                "cannot fail the workflow"
            )


@dataclass(frozen=True)
class WorkflowDefinition:
    type: str
    version: int
    steps: tuple[StepDefinition, ...]
    description: str = ""
    _by_name: dict[str, StepDefinition] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        names = [s.name for s in self.steps]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate step names in {self.type} v{self.version}")
        object.__setattr__(self, "_by_name", {s.name: s for s in self.steps})

    def step(self, name: str) -> StepDefinition:
        try:
            return self._by_name[name]
        except KeyError:
            raise UnknownWorkflow(f"{self.type} v{self.version} has no step {name!r}") from None

    @property
    def key(self) -> str:
        return f"{self.type}:{self.version}"


class Registry:
    def __init__(self) -> None:
        self._defs: dict[tuple[str, int], WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        k = (definition.type, definition.version)
        if k in self._defs and self._defs[k] is not definition:
            raise ValueError(f"{definition.type} v{definition.version} is already registered")
        self._defs[k] = definition
        return definition

    def get(self, workflow_type: str, version: int | None = None) -> WorkflowDefinition:
        if version is None:
            versions = [v for (t, v) in self._defs if t == workflow_type]
            if not versions:
                raise UnknownWorkflow(f"unknown workflow type {workflow_type!r}")
            version = max(versions)
        try:
            return self._defs[(workflow_type, version)]
        except KeyError:
            raise UnknownWorkflow(f"unknown workflow {workflow_type!r} v{version}") from None

    def definitions(self) -> list[WorkflowDefinition]:
        return [self._defs[k] for k in sorted(self._defs)]

    def keys(self) -> list[str]:
        """`type:version` strings this process can execute (workers claim only these)."""
        return sorted(d.key for d in self._defs.values())


def default_registry() -> Registry:
    from relay.workflows import enquiry_v1, enquiry_v2

    registry = Registry()
    registry.register(enquiry_v1.DEFINITION)
    registry.register(enquiry_v2.DEFINITION)
    return registry
