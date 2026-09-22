"""Roles are tight bundles with prompts that state their limits."""
from __future__ import annotations

import pytest

from fiboki.agents.capabilities import Capability
from fiboki.agents.roles import (
    CARDINAL_RULE,
    ROLES,
    AgentRole,
    RoleSpec,
    all_roles,
    get_role,
    validate_role_bundles,
)
from fiboki.agents.tools import REGISTRY

EXPECTED_ROLES = {
    "research_director",
    "quant_researcher",
    "strategy_engineer",
    "strategy_mutation_agent",
    "statistical_auditor",
    "adversarial_quant_critic",
    "market_regime_analyst",
    "execution_analyst",
    "portfolio_analyst",
    "data_quality_analyst",
    "failure_investigator",
    "research_librarian",
}


def test_every_role_from_the_brief_exists() -> None:
    assert {r.value for r in AgentRole} == EXPECTED_ROLES
    assert set(ROLES) == set(AgentRole)


def test_role_bundles_are_coherent_with_the_registry() -> None:
    validate_role_bundles()


def test_no_role_holds_a_capability_none_of_its_tools_needs() -> None:
    """Least privilege: a bundle is derived from tools, never hand-widened."""
    for spec in all_roles():
        needed = {REGISTRY.get(name).capability for name in spec.tools}
        assert spec.capabilities == frozenset(needed), spec.role


def test_no_role_can_submit_a_job_unless_it_is_the_auditor() -> None:
    """Only one role may cause the engine to run at all."""
    submitters = [
        s.role.value for s in all_roles() if Capability.SUBMIT_JOB in s.capabilities
    ]
    assert submitters == ["statistical_auditor"]


def test_read_only_roles_hold_no_write_capability() -> None:
    read_only = {
        AgentRole.MARKET_REGIME_ANALYST,
        AgentRole.EXECUTION_ANALYST,
        AgentRole.PORTFOLIO_ANALYST,
        AgentRole.DATA_QUALITY_ANALYST,
        AgentRole.FAILURE_INVESTIGATOR,
        AgentRole.RESEARCH_DIRECTOR,
    }
    for role in read_only:
        spec = get_role(role)
        assert not any(c.is_write for c in spec.capabilities), role
        assert Capability.SUBMIT_JOB not in spec.capabilities, role


def test_the_portfolio_analyst_cannot_read_market_data_or_write_anything() -> None:
    """The role that sees the book is the most narrowly scoped of all."""
    spec = get_role(AgentRole.PORTFOLIO_ANALYST)
    assert Capability.READ_PORTFOLIO in spec.capabilities
    assert Capability.READ_MARKET_DATA not in spec.capabilities
    assert not any(c.is_write for c in spec.capabilities)


def test_the_data_quality_analyst_has_no_repair_tool() -> None:
    spec = get_role(AgentRole.DATA_QUALITY_ANALYST)
    assert all("repair" not in t and "fix" not in t for t in spec.tools)
    assert "DETECT, NEVER REPAIR" in spec.system_prompt()


def test_every_prompt_carries_the_cardinal_rule() -> None:
    for spec in all_roles():
        prompt = spec.system_prompt()
        assert CARDINAL_RULE.strip() in prompt, spec.role
        assert "no authority over execution" in prompt


def test_every_prompt_states_a_remit_and_limits_and_lists_its_tools() -> None:
    for spec in all_roles():
        prompt = spec.system_prompt()
        assert "REMIT." in prompt and "LIMITS." in prompt, spec.role
        for tool in spec.tools:
            assert tool in prompt, f"{spec.role}: {tool} missing from its prompt"
        assert len(spec.remit.strip()) > 80, spec.role
        assert len(spec.limits.strip()) > 80, spec.role


# ------------------------------------------- the critic is the sharp one


def test_the_critic_is_told_to_disprove_not_to_hedge() -> None:
    prompt = get_role(AgentRole.ADVERSARIAL_QUANT_CRITIC).system_prompt()
    assert "DISPROVE" in prompt
    assert "GENERIC CAUTION IS NOT AN OBJECTION" in prompt
    assert "DECISIVE TEST" in prompt
    assert "at least three" in prompt


def test_the_critic_is_given_concrete_attack_surfaces() -> None:
    prompt = get_role(AgentRole.ADVERSARIAL_QUANT_CRITIC).system_prompt()
    for surface in (
        "trade count",
        "survivorship",
        "slippage",
        "look-ahead",
        "trials",
        "currency conversion",
    ):
        assert surface in prompt, surface


def test_the_critic_is_told_what_to_do_when_it_cannot_break_it() -> None:
    """A critic with no escape hatch invents objections; this one has one."""
    prompt = get_role(AgentRole.ADVERSARIAL_QUANT_CRITIC).system_prompt()
    assert "survives_this_attack" in prompt
    assert "real finding" in prompt


def test_the_critic_can_read_the_evidence_it_needs_to_attack() -> None:
    spec = get_role(AgentRole.ADVERSARIAL_QUANT_CRITIC)
    for tool in ("inspect_drawdown", "inspect_trade", "query_data_quality"):
        assert tool in spec.tools


def test_the_critic_cannot_change_or_rerun_anything() -> None:
    spec = get_role(AgentRole.ADVERSARIAL_QUANT_CRITIC)
    assert Capability.SUBMIT_JOB not in spec.capabilities
    assert Capability.WRITE_STRATEGY_MUTATION not in spec.capabilities
    assert spec.capabilities & {Capability.WRITE_CRITIQUE}


# ----------------------------------------------------------- plumbing


def test_get_role_accepts_a_string() -> None:
    assert get_role("research_librarian").role is AgentRole.RESEARCH_LIBRARIAN


def test_an_unknown_role_raises() -> None:
    with pytest.raises(ValueError):
        get_role("chief_trading_officer")


def test_describe_is_serialisable() -> None:
    described = get_role(AgentRole.QUANT_RESEARCHER).describe()
    assert described["role"] == "quant_researcher"
    assert isinstance(described["capabilities"], list)


def test_a_role_naming_an_unregistered_tool_is_rejected() -> None:
    rogue = RoleSpec(
        role=AgentRole.RESEARCH_DIRECTOR,
        title="Rogue",
        remit="r",
        limits="l",
        method="m",
        tools=("place_order",),
        task_class=get_role(AgentRole.RESEARCH_DIRECTOR).task_class,
    )
    with pytest.raises(RuntimeError, match="unregistered tool"):
        validate_role_bundles([rogue])


def test_a_role_with_no_tools_is_rejected() -> None:
    rogue = RoleSpec(
        role=AgentRole.RESEARCH_DIRECTOR,
        title="Idle",
        remit="r",
        limits="l",
        method="m",
        tools=(),
        task_class=get_role(AgentRole.RESEARCH_DIRECTOR).task_class,
    )
    with pytest.raises(RuntimeError, match="has no tools"):
        validate_role_bundles([rogue])
