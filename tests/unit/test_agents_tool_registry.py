"""The registry is the complete surface an LLM can reach. These tests fence it."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from fiboki.agents.capabilities import Capability
from fiboki.agents.orchestrator import JobType
from fiboki.agents.tools import (
    REGISTRY,
    ToolBudget,
    ToolRegistrationError,
    ToolRegistry,
    ToolSpec,
    WriteDomain,
    build_registry,
)

#: Every tool the brief asks for, so a silent deletion is caught.
REQUIRED_TOOLS = (
    "query_market_data",
    "query_regime",
    "query_experiment_history",
    "query_research_memory",
    "query_strategy",
    "compare_candidates",
    "inspect_trade",
    "inspect_drawdown",
    "inspect_validation_report",
    "query_portfolio",
    "query_execution_telemetry",
    "query_data_quality",
    "create_hypothesis",
    "create_strategy",
    "mutate_strategy",
    "design_experiment",
    "run_backtest",
    "run_validation",
    "run_walkforward",
    "run_ablation",
    "run_sensitivity",
    "search_web",
    "fetch_research",
)

#: Words that would betray an execution tool hiding in the registry.
EXECUTION_WORDS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "modify_order",
    "close_position",
    "open_position",
    "size_position",
    "set_risk",
    "risk_limit",
    "kill_switch",
    "enable_live",
    "go_live",
    "broker",
    "route_order",
    "execute_trade",
    "write_market_data",
    "deactivate",
)


# ---------------------------------------------------- THE CARDINAL RULE


def test_no_registered_tool_carries_an_execution_capability() -> None:
    """Enumerate the whole registry and prove the absence."""
    for tool in REGISTRY.all():
        assert isinstance(tool.capability, Capability), tool.name
        assert tool.capability in set(Capability), tool.name


def test_no_tool_name_reads_like_an_order_path() -> None:
    for tool in REGISTRY.all():
        lowered = tool.name.lower()
        for word in EXECUTION_WORDS:
            assert word not in lowered, f"{tool.name} looks like an execution tool"


def test_every_mutating_tool_writes_only_to_research_or_the_queue() -> None:
    allowed = {
        WriteDomain.RESEARCH_HYPOTHESIS,
        WriteDomain.RESEARCH_STRATEGY_PROPOSAL,
        WriteDomain.RESEARCH_EXPERIMENT,
        WriteDomain.RESEARCH_CRITIQUE,
        WriteDomain.RESEARCH_NOTE,
        WriteDomain.JOB_QUEUE,
    }
    for tool in REGISTRY.all():
        if tool.mutates:
            assert tool.write_domain in allowed, tool.name
        else:
            assert tool.write_domain is WriteDomain.NONE, tool.name


def test_write_domain_enum_has_no_execution_destination() -> None:
    for member in WriteDomain:
        assert member.value.split(":")[0] in ("none", "research", "queue"), member


def test_no_job_type_is_an_execution_job() -> None:
    """Even the queue cannot express an order."""
    for job in JobType:
        for word in EXECUTION_WORDS:
            assert word not in job.value.lower(), job


def test_every_required_tool_exists() -> None:
    missing = [name for name in REQUIRED_TOOLS if name not in REGISTRY]
    assert not missing, f"registry is missing {missing}"


# --------------------------------------------------- registration policy


class _Dummy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _spec(**overrides: object) -> ToolSpec:
    base: dict[str, object] = {
        "name": "dummy",
        "description": "d",
        "input_model": _Dummy,
        "output_model": _Dummy,
        "capability": Capability.READ_STRATEGY,
        "write_domain": WriteDomain.NONE,
        "mutates": False,
        "budget": ToolBudget(),
        "handler": lambda ctx, inputs: _Dummy(),
    }
    base.update(overrides)
    return ToolSpec(**base)  # type: ignore[arg-type]


def test_registry_refuses_a_tool_that_lies_about_mutating() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolRegistrationError, match="honest about whether"):
        registry.register(_spec(mutates=True))


def test_registry_refuses_a_write_tool_with_a_read_capability() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolRegistrationError, match="write: or submit:"):
        registry.register(
            _spec(
                mutates=True,
                write_domain=WriteDomain.RESEARCH_NOTE,
                capability=Capability.READ_STRATEGY,
            )
        )


def test_registry_refuses_a_non_capability() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolRegistrationError, match="must be a Capability"):
        registry.register(_spec(capability="place:order"))


def test_registry_refuses_a_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(_spec())
    with pytest.raises(ToolRegistrationError, match="already registered"):
        registry.register(_spec())


# ------------------------------------------------------------ hygiene


def test_every_tool_declares_schemas_and_a_budget() -> None:
    for tool in REGISTRY.all():
        assert issubclass(tool.input_model, BaseModel), tool.name
        assert issubclass(tool.output_model, BaseModel), tool.name
        assert tool.budget.max_calls_per_job >= 1, tool.name
        assert tool.budget.max_calls_per_minute >= 1, tool.name
        assert len(tool.description) > 30, f"{tool.name} needs a real description"


def test_every_tool_schema_forbids_unknown_fields() -> None:
    """A model that ignores extras would let an agent smuggle a field through."""
    for tool in REGISTRY.all():
        assert tool.input_model.model_config.get("extra") == "forbid", tool.name
        assert tool.output_model.model_config.get("extra") == "forbid", tool.name


def test_describe_all_is_serialisable() -> None:
    described = REGISTRY.describe_all()
    assert len(described) == len(REGISTRY)
    for entry in described:
        assert set(entry) >= {"name", "capability", "mutates", "input_schema"}


def test_build_registry_is_deterministic() -> None:
    a, b = build_registry(), build_registry()
    assert a.names() == b.names() == REGISTRY.names()


def test_for_capabilities_filters_to_the_grant() -> None:
    granted = frozenset({Capability.READ_STRATEGY})
    tools = REGISTRY.for_capabilities(granted)
    assert tools
    assert all(t.capability is Capability.READ_STRATEGY for t in tools)
