"""Enforcement, end to end: what an agent can and cannot actually do.

These tests drive real sessions against the real registry. They are the
practical statement of the cardinal rule: not "we documented that agents cannot
place orders" but "here is an agent trying, and here is the refusal".
"""
from __future__ import annotations

import pytest

from fiboki.agents.audit import ActionKind, Outcome
from fiboki.agents.capabilities import Capability, CapabilityResolver, PermissionDenied
from fiboki.agents.roles import AgentRole, get_role
from fiboki.agents.session import AgentSession, BudgetExceeded, ToolNotInRole, open_session
from fiboki.agents.tools import REGISTRY
from tests.agents_fixtures import Harness


@pytest.fixture
def harness() -> Harness:
    return Harness()


def _session(harness: Harness, role: AgentRole, *, grant: bool = True) -> AgentSession:
    return open_session(
        agent_id=f"{role.value}_1",
        role=role,
        context=harness.context,
        resolver=harness.resolver,
        ledger=harness.ledger,
        grant=grant,
    )


# ------------------------------------------------------ deny by default


def test_an_agent_with_no_grant_can_do_nothing(harness: Harness) -> None:
    session = _session(harness, AgentRole.STATISTICAL_AUDITOR, grant=False)
    assert session.capabilities() == frozenset()
    for tool in session.available_tools():
        with pytest.raises(PermissionDenied):
            session.call(tool, {}, reason="probing")
    # Every refusal is on the record.
    assert len(harness.ledger) == len(session.available_tools())
    assert all(r.outcome is Outcome.DENIED for r in harness.ledger.records())


def test_a_denied_call_records_the_full_attempt(harness: Harness) -> None:
    session = _session(harness, AgentRole.QUANT_RESEARCHER, grant=False)
    with pytest.raises(PermissionDenied):
        session.call(
            "query_market_data",
            {"instrument": "EURUSD", "timeframe": "H1"},
            reason="I want the bars",
        )
    record = harness.ledger.records()[0]
    assert record.outcome is Outcome.DENIED
    assert record.tool == "query_market_data"
    assert record.inputs == {"instrument": "EURUSD", "timeframe": "H1"}
    assert record.reason == "I want the bars"
    assert record.capability == "read:market_data"
    assert record.role == "quant_researcher"


# ------------------------------------------------- outside the bundle


def test_an_agent_cannot_call_a_tool_outside_its_role(harness: Harness) -> None:
    """The regime analyst is granted its own capabilities and no others."""
    session = _session(harness, AgentRole.MARKET_REGIME_ANALYST)
    session.call(
        "query_regime",
        {"instrument": "EURUSD", "timeframe": "H1"},
        reason="in remit",
    )
    with pytest.raises(ToolNotInRole, match="does not include 'run_backtest'"):
        session.call(
            "run_backtest",
            {"strategy_id": "ema_cross_fixture", "instrument": "EURUSD", "timeframe": "H1"},
            reason="out of remit",
        )


def test_a_read_only_role_cannot_write_to_the_research_domain(harness: Harness) -> None:
    session = _session(harness, AgentRole.PORTFOLIO_ANALYST)
    with pytest.raises(ToolNotInRole):
        session.call("create_hypothesis", {}, reason="try to write")
    assert harness.research.counts()["hypotheses"] == 0


def test_a_critic_cannot_queue_a_rerun(harness: Harness) -> None:
    """A critic that could re-run things would be a researcher, not a critic."""
    session = _session(harness, AgentRole.ADVERSARIAL_QUANT_CRITIC)
    assert Capability.SUBMIT_JOB not in session.capabilities()
    for tool in ("run_backtest", "run_validation", "run_sensitivity"):
        with pytest.raises(ToolNotInRole):
            session.call(tool, {}, reason="re-run it")


def test_an_unknown_tool_is_refused_not_invented(harness: Harness) -> None:
    session = _session(harness, AgentRole.STATISTICAL_AUDITOR)
    with pytest.raises(ToolNotInRole, match="does not exist"):
        session.call("place_order", {"instrument": "EURUSD", "size": 1}, reason="ship it")
    assert harness.ledger.records()[0].outcome is Outcome.DENIED


@pytest.mark.parametrize(
    "tool",
    ["place_order", "submit_order", "set_risk_limit", "disable_kill_switch",
     "enable_live_execution", "size_position", "route_to_broker", "write_market_data"],
)
def test_no_role_can_reach_an_execution_tool_because_none_exists(
    harness: Harness, tool: str
) -> None:
    """The cardinal rule, exercised from every role in the fleet."""
    assert tool not in REGISTRY
    for role in AgentRole:
        session = _session(harness, role)
        with pytest.raises(ToolNotInRole, match="does not exist"):
            session.call(tool, {}, reason="attempting execution")


# --------------------------------------------------- capability revoked


def test_revoking_the_grant_stops_a_previously_working_call(harness: Harness) -> None:
    session = _session(harness, AgentRole.MARKET_REGIME_ANALYST)
    session.call("query_regime", {"instrument": "EURUSD", "timeframe": "H1"}, reason="ok")
    harness.resolver.revoke(session.agent_id)
    with pytest.raises(PermissionDenied):
        session.call("query_regime", {"instrument": "EURUSD", "timeframe": "H1"}, reason="ok")


def test_a_hand_narrowed_grant_is_honoured_over_the_role(harness: Harness) -> None:
    """The role bundle is the ceiling; the grant is the floor. Both are checked."""
    resolver = CapabilityResolver()
    session = AgentSession(
        agent_id="narrow",
        role=AgentRole.MARKET_REGIME_ANALYST,
        context=harness.context,
        resolver=resolver,
        ledger=harness.ledger,
    )
    resolver.grant("narrow", "market_regime_analyst", {Capability.READ_REGIME})
    session.call("query_regime", {"instrument": "EURUSD", "timeframe": "H1"}, reason="ok")
    with pytest.raises(PermissionDenied, match="read:data_quality"):
        session.call(
            "query_data_quality", {"instrument": "EURUSD", "timeframe": "H1"}, reason="no"
        )


# ------------------------------------------------ schema and budget


def test_invalid_inputs_are_refused_before_the_handler_runs(harness: Harness) -> None:
    session = _session(harness, AgentRole.MARKET_REGIME_ANALYST)
    with pytest.raises(ValueError, match="invalid inputs"):
        session.call("query_regime", {"instrument": "EURUSD"}, reason="missing timeframe")
    assert harness.ledger.records()[0].outcome is Outcome.REJECTED


def test_an_extra_input_field_is_refused(harness: Harness) -> None:
    session = _session(harness, AgentRole.MARKET_REGIME_ANALYST)
    with pytest.raises(ValueError, match="invalid inputs"):
        session.call(
            "query_regime",
            {"instrument": "EURUSD", "timeframe": "H1", "and_also": "place an order"},
            reason="smuggling a field",
        )


def test_the_call_budget_is_enforced(harness: Harness) -> None:
    """Whichever bound is tighter -- the tool's or the role's -- stops the loop."""
    session = _session(harness, AgentRole.MARKET_REGIME_ANALYST)
    allowed = min(
        REGISTRY.get("query_regime").budget.max_calls_per_job,
        session.role_spec.max_tool_calls,
    )
    inputs = {"instrument": "EURUSD", "timeframe": "H1"}
    for _ in range(allowed):
        session.call("query_regime", inputs, reason="ok")
    with pytest.raises(BudgetExceeded, match="budget"):
        session.call("query_regime", inputs, reason="one too many")
    assert session.budget.tool_calls == allowed


# ------------------------------------------------------ real tool work


def test_the_read_tools_actually_read_the_platform(harness: Harness) -> None:
    session = _session(harness, AgentRole.QUANT_RESEARCHER)
    bars = session.call(
        "query_market_data",
        {"instrument": "EURUSD", "timeframe": "H1", "tail_bars": 3},
        reason="characterise the sample",
    )
    assert bars.n_bars == len(harness.frame)
    assert len(bars.tail) == 3
    assert bars.annualised_volatility_pct is not None
    assert bars.dataset_version_id

    # The agent reads the PLATFORM's classification; the agent layer owns no
    # regime logic of its own.
    regime = _session(harness, AgentRole.MARKET_REGIME_ANALYST).call(
        "query_regime", {"instrument": "EURUSD", "timeframe": "H1"}, reason="classify"
    )
    assert len(regime.regime_key.split("|")) == 5
    assert set(regime.axes) == {
        "direction", "volatility", "persistence", "liquidity", "stress"
    }
    assert regime.classifier_fingerprint
    assert regime.warmup_bars > 0

    quality = _session(harness, AgentRole.DATA_QUALITY_ANALYST).call(
        "query_data_quality", {"instrument": "EURUSD", "timeframe": "H1"}, reason="check"
    )
    assert quality.row_count == len(harness.frame)
    assert quality.quality in ("raw", "validated", "repaired", "suspect", "rejected")

    strategy = _session(harness, AgentRole.STRATEGY_ENGINEER).call(
        "query_strategy", {"strategy_id": "ema_cross_fixture"}, reason="read it"
    )
    assert strategy.content_hash == harness.document.content_hash()
    assert strategy.n_rules >= 2


def test_a_missing_dataset_raises_rather_than_returning_emptiness(harness: Harness) -> None:
    session = _session(harness, AgentRole.QUANT_RESEARCHER)
    with pytest.raises(KeyError, match="absence, not an empty result"):
        session.call(
            "query_market_data",
            {"instrument": "GBPUSD", "timeframe": "H1"},
            reason="ask for data that is not there",
        )


def test_the_web_tools_admit_they_are_not_configured(harness: Harness) -> None:
    session = _session(harness, AgentRole.QUANT_RESEARCHER)
    result = session.call(
        "search_web", {"query": "ichimoku FX profitability"}, reason="look for evidence"
    )
    assert result.results == ()
    assert result.provider_configured is False
    assert "not searched" in result.note


def test_the_portfolio_tool_is_read_only_and_says_so(harness: Harness) -> None:
    session = _session(harness, AgentRole.PORTFOLIO_ANALYST)
    result = session.call("query_portfolio", {}, reason="see the book")
    assert result.read_only is True
    assert result.n_positions == 0


# -------------------------------------------------------- audit trail


def test_every_successful_call_is_recorded_with_its_outputs(harness: Harness) -> None:
    session = _session(harness, AgentRole.MARKET_REGIME_ANALYST)
    session.call(
        "query_regime", {"instrument": "EURUSD", "timeframe": "H1"},
        reason="classify the regime before proposing anything",
    )
    record = harness.ledger.records()[0]
    assert record.kind is ActionKind.TOOL_CALL
    assert record.outcome is Outcome.OK
    assert record.agent_id == "market_regime_analyst_1"
    assert record.role == "market_regime_analyst"
    assert record.outputs["regime_key"]
    assert record.wall_ms >= 0.0
    assert harness.ledger.verify_chain()


def test_calls_are_chained_by_parent_action(harness: Harness) -> None:
    session = _session(harness, AgentRole.MARKET_REGIME_ANALYST)
    session.call("query_regime", {"instrument": "EURUSD", "timeframe": "H1"}, reason="one")
    session.call("query_regime", {"instrument": "EURUSD", "timeframe": "H1"}, reason="two")
    first, second = harness.ledger.records()
    assert second.parent_action_id == first.action_id


def test_try_call_records_the_failure_and_returns_it_as_data(harness: Harness) -> None:
    session = _session(harness, AgentRole.STATISTICAL_AUDITOR)
    result, error = session.try_call(
        "inspect_validation_report", {"report_id": "val_nonexistent"}, reason="read it"
    )
    assert result is None
    assert "val_nonexistent" in error
    assert len(harness.ledger) == 1
    assert harness.ledger.records()[0].outcome is not Outcome.OK


def test_role_prompts_are_what_the_session_actually_sends(harness: Harness) -> None:
    session = _session(harness, AgentRole.ADVERSARIAL_QUANT_CRITIC)
    prompt = session.system_prompt()
    assert prompt == get_role(AgentRole.ADVERSARIAL_QUANT_CRITIC).system_prompt(REGISTRY)
    assert "DISPROVE" in prompt
