import pytest

from relay.engine.errors import UnknownWorkflow
from relay.engine.submission import canonical_hash
from relay.workflows.registry import (
    Registry,
    StepDefinition,
    WorkflowDefinition,
    default_registry,
)


def _noop(ctx):
    return {}


def test_canonical_hash_ignores_key_order_but_not_values() -> None:
    assert canonical_hash({"a": 1, "b": {"c": 2}}) == canonical_hash({"b": {"c": 2}, "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_registry_resolves_exact_and_latest_versions() -> None:
    registry = Registry()
    v1 = registry.register(WorkflowDefinition("t", 1, (StepDefinition("a", _noop),)))
    v2 = registry.register(WorkflowDefinition("t", 2, (StepDefinition("a", _noop),)))
    assert registry.get("t", 1) is v1
    assert registry.get("t") is v2
    assert registry.keys() == ["t:1", "t:2"]
    with pytest.raises(UnknownWorkflow):
        registry.get("t", 3)


def test_a_published_version_cannot_be_replaced() -> None:
    registry = Registry()
    registry.register(WorkflowDefinition("t", 1, (StepDefinition("a", _noop),)))
    with pytest.raises(ValueError):
        registry.register(WorkflowDefinition("t", 1, (StepDefinition("b", _noop),)))


def test_a_step_cannot_be_defined_with_a_budget_one_crash_would_exhaust() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        StepDefinition("fragile", lambda ctx: {}, max_attempts=1)
    for definition in default_registry().definitions():
        assert all(s.max_attempts >= 2 for s in definition.steps)
