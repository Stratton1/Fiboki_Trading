"""Research-domain writes: proposals, mutations, experiments, filings.

These are the only writes an agent can make. Each one is tested for what it
accepts and, more importantly, for what it refuses.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fiboki.agents.research_store import (
    BacktestRecord,
    Hypothesis,
    ResearchNote,
    ResearchStore,
)
from fiboki.agents.roles import AgentRole
from fiboki.agents.session import open_session
from fiboki.agents.tools import MutationOperator, ToolExecutionError
from tests.agents_fixtures import Harness, ema_crossover_document

AGENTS_ROOT = Path("src/fiboki/agents")


@pytest.fixture
def harness() -> Harness:
    return Harness()


def _session(harness: Harness, role: AgentRole):
    return open_session(
        agent_id=f"{role.value}_w",
        role=role,
        context=harness.context,
        resolver=harness.resolver,
        ledger=harness.ledger,
    )


def _document_payload(strategy_id: str = "proposed_one", **kwargs: object) -> dict:
    """A payload that differs semantically from the harness seed by default."""
    kwargs.setdefault("fast", 8)
    kwargs.setdefault("slow", 34)
    data = ema_crossover_document(strategy_id, **kwargs).model_dump(mode="json")  # type: ignore[arg-type]
    data.pop("complexity_score", None)
    return data


# ------------------------------------------------------ create_strategy


def test_create_strategy_registers_a_valid_document(harness: Harness) -> None:
    session = _session(harness, AgentRole.STRATEGY_ENGINEER)
    result = session.call(
        "create_strategy",
        {"document": _document_payload(), "rationale": "expresses the hypothesis"},
        reason="propose it",
    )
    assert result.strategy_id == "proposed_one"
    assert result.warmup_period > 0
    assert "proposed_one" in harness.strategies
    assert harness.research.counts()["proposals"] == 1


@pytest.mark.parametrize(
    "document",
    [
        {"not": "a document"},
        {"strategy_id": "x"},
        [],
        "just a sentence",
        42,
    ],
)
def test_create_strategy_rejects_anything_that_is_not_a_document(
    harness: Harness, document: object
) -> None:
    session = _session(harness, AgentRole.STRATEGY_ENGINEER)
    with pytest.raises(Exception):  # noqa: B017 - schema or sandbox, both are refusals
        session.call("create_strategy", {"document": document}, reason="nonsense")
    assert harness.research.counts()["proposals"] == 0


def test_create_strategy_rejects_a_code_bearing_document(harness: Harness) -> None:
    session = _session(harness, AgentRole.STRATEGY_ENGINEER)
    payload = _document_payload()
    payload["notes"] = "__import__('os').system('id')"
    with pytest.raises(ToolExecutionError, match="sandbox boundary"):
        session.call("create_strategy", {"document": payload}, reason="smuggle")
    assert harness.research.counts()["proposals"] == 0


def test_create_strategy_rejects_a_duplicate_by_content_hash(harness: Harness) -> None:
    """A search process proposes the same thing twice; it must count once.

    The payload here is the SEED's rules under a new name, which is exactly the
    case a renaming search process produces.
    """
    session = _session(harness, AgentRole.STRATEGY_ENGINEER)
    with pytest.raises(ToolExecutionError, match="semantically identical"):
        session.call(
            "create_strategy",
            {"document": _document_payload("renamed_but_identical", fast=5, slow=20)},
            reason="propose a rename of the seed",
        )


# ------------------------------------------------------ mutate_strategy


@pytest.mark.parametrize(
    ("operator", "arguments"),
    [
        (MutationOperator.SCALE_STOP, {"factor": 1.5}),
        (MutationOperator.SET_STOP_VALUE, {"value": 3.0}),
        (MutationOperator.REMOVE_TAKE_PROFIT_LEG, {"index": 0}),
        (MutationOperator.SET_BREAKEVEN_TRAILING, {"activate_after_r": 1.0}),
        (MutationOperator.SET_MAX_BARS_IN_TRADE, {"bars": 48}),
        (MutationOperator.SET_COOLDOWN_BARS, {"bars": 3}),
        (MutationOperator.SET_MAX_CONCURRENT_POSITIONS, {"value": 2}),
    ],
)
def test_each_operator_produces_a_valid_distinct_child(
    harness: Harness, operator: MutationOperator, arguments: dict
) -> None:
    session = _session(harness, AgentRole.STRATEGY_MUTATION_AGENT)
    child_id = f"child_{operator.value}"[:64]
    result = session.call(
        "mutate_strategy",
        {
            "parent_strategy_id": "ema_cross_fixture",
            "new_strategy_id": child_id,
            "operator": operator.value,
            "arguments": arguments,
            "rationale": "testing the operator",
        },
        reason="mutate",
    )
    child = harness.strategies.get(result.strategy_id)
    assert child.content_hash() != harness.document.content_hash()
    assert child.mutation is not None and child.mutation.operator == operator.value
    assert child.mutation.generation == 1
    assert child.parent_strategy_ids == ("ema_cross_fixture",)


def test_adding_a_leg_that_over_allocates_is_refused(harness: Harness) -> None:
    """The fixture already closes 100%; a strategy cannot close more than all."""
    session = _session(harness, AgentRole.STRATEGY_MUTATION_AGENT)
    with pytest.raises(ToolExecutionError, match="invalid document"):
        session.call(
            "mutate_strategy",
            {
                "parent_strategy_id": "ema_cross_fixture",
                "new_strategy_id": "over_allocated",
                "operator": "add_take_profit_leg",
                "arguments": {"r_multiple": 4.0, "allocation": 0.5},
            },
            reason="close 150% of the position",
        )


def test_adding_a_leg_inside_the_remaining_allocation_is_accepted(
    harness: Harness,
) -> None:
    partial = ema_crossover_document("partial_allocation", fast=9, slow=21)
    data = partial.model_dump(mode="json")
    data.pop("complexity_score", None)
    data["take_profits"][0]["allocation"] = 0.5
    harness.strategies.register(type(partial).model_validate(data))
    session = _session(harness, AgentRole.STRATEGY_MUTATION_AGENT)
    result = session.call(
        "mutate_strategy",
        {
            "parent_strategy_id": "partial_allocation",
            "new_strategy_id": "partial_plus_runner",
            "operator": "add_take_profit_leg",
            "arguments": {"r_multiple": 4.0, "allocation": 0.5},
        },
        reason="scale out in two legs",
    )
    child = harness.strategies.get(result.strategy_id)
    assert len(child.take_profits) == 2
    assert sum(leg.allocation for leg in child.take_profits) == pytest.approx(1.0)


def test_a_mutation_cannot_widen_the_universe(harness: Harness) -> None:
    session = _session(harness, AgentRole.STRATEGY_MUTATION_AGENT)
    with pytest.raises(ToolExecutionError, match="only narrow"):
        session.call(
            "mutate_strategy",
            {
                "parent_strategy_id": "ema_cross_fixture",
                "new_strategy_id": "wider",
                "operator": "restrict_universe",
                "arguments": {"instruments": ["EURUSD", "GBPUSD"]},
            },
            reason="sneak an instrument in",
        )


def test_a_mutation_cannot_widen_the_timeframes(harness: Harness) -> None:
    session = _session(harness, AgentRole.STRATEGY_MUTATION_AGENT)
    with pytest.raises(ToolExecutionError, match="only narrow"):
        session.call(
            "mutate_strategy",
            {
                "parent_strategy_id": "ema_cross_fixture",
                "new_strategy_id": "wider_tf",
                "operator": "restrict_timeframes",
                "arguments": {"timeframes": ["H1", "D1"]},
            },
            reason="sneak a timeframe in",
        )


def test_a_mutation_cannot_overwrite_its_parent(harness: Harness) -> None:
    session = _session(harness, AgentRole.STRATEGY_MUTATION_AGENT)
    with pytest.raises(ToolExecutionError, match="destroy the lineage"):
        session.call(
            "mutate_strategy",
            {
                "parent_strategy_id": "ema_cross_fixture",
                "new_strategy_id": "ema_cross_fixture",
                "operator": "scale_stop",
                "arguments": {"factor": 2.0},
            },
            reason="overwrite it",
        )


def test_an_operator_outside_the_enumeration_is_refused(harness: Harness) -> None:
    session = _session(harness, AgentRole.STRATEGY_MUTATION_AGENT)
    with pytest.raises(ValueError, match="invalid inputs"):
        session.call(
            "mutate_strategy",
            {
                "parent_strategy_id": "ema_cross_fixture",
                "new_strategy_id": "arbitrary",
                "operator": "set_field",
                "arguments": {"path": "stop.kind", "value": "none"},
            },
            reason="write an arbitrary field",
        )


def test_there_is_no_operator_that_removes_the_stop() -> None:
    """The schema makes a stopless strategy unrepresentable; so must mutation."""
    for operator in MutationOperator:
        assert "remove_stop" not in operator.value
        assert "clear_stop" not in operator.value


def test_an_out_of_range_scaling_factor_is_refused(harness: Harness) -> None:
    session = _session(harness, AgentRole.STRATEGY_MUTATION_AGENT)
    with pytest.raises(ToolExecutionError, match=r"\[0.1, 10.0\]"):
        session.call(
            "mutate_strategy",
            {
                "parent_strategy_id": "ema_cross_fixture",
                "new_strategy_id": "absurd",
                "operator": "scale_stop",
                "arguments": {"factor": 1000.0},
            },
            reason="absurd stop",
        )


# ---------------------------------------------------- design_experiment


def _experiment_inputs(**overrides: object) -> dict:
    base: dict[str, object] = {
        "strategy_ids": ["ema_cross_fixture"],
        "instruments": ["EURUSD"],
        "timeframes": ["H1"],
        "train_start": "2024-01-01",
        "train_end": "2024-02-01",
        "test_start": "2024-02-01",
        "test_end": "2024-03-01",
        "success_criteria": [
            {"metric": "n_trades", "comparator": ">=", "threshold": 80.0,
             "rationale": "house floor"}
        ],
    }
    base.update(overrides)
    return base


def test_design_experiment_pre_registers_criteria(harness: Harness) -> None:
    session = _session(harness, AgentRole.STATISTICAL_AUDITOR)
    hypothesis = harness.research.add_hypothesis(
        Hypothesis(
            title="A testable claim about EURUSD",
            statement="x" * 45,
            rationale="y" * 45,
            testable_prediction="z" * 25,
            falsifier="w" * 25,
        )
    )
    result = session.call(
        "design_experiment",
        _experiment_inputs(hypothesis_id=hypothesis.hypothesis_id),
        reason="pre-register",
    )
    assert result.n_criteria == 1
    stored = harness.research.get_experiment(result.experiment_id)
    assert stored.success_criteria[0].metric == "n_trades"


def test_an_experiment_for_an_unknown_hypothesis_is_refused(harness: Harness) -> None:
    session = _session(harness, AgentRole.STATISTICAL_AUDITOR)
    with pytest.raises(KeyError):
        session.call(
            "design_experiment",
            _experiment_inputs(hypothesis_id="hyp_nonexistent"),
            reason="skip pre-registration",
        )


def test_an_experiment_for_an_unregistered_strategy_is_refused(harness: Harness) -> None:
    session = _session(harness, AgentRole.STATISTICAL_AUDITOR)
    hypothesis = harness.research.add_hypothesis(
        Hypothesis(
            title="Another testable claim", statement="x" * 45, rationale="y" * 45,
            testable_prediction="z" * 25, falsifier="w" * 25,
        )
    )
    with pytest.raises(ToolExecutionError, match="unregistered strategy"):
        session.call(
            "design_experiment",
            _experiment_inputs(
                hypothesis_id=hypothesis.hypothesis_id, strategy_ids=["nonexistent"]
            ),
            reason="test a strategy nobody wrote",
        )


# --------------------------------------------------- comparison honesty


def test_compare_candidates_will_not_rank_below_the_trade_floor(harness: Harness) -> None:
    harness.research.add_backtest(
        BacktestRecord(strategy_id="a", metrics={"sharpe": 3.0}, trades=({"net_pnl": 1.0},))
    )
    harness.research.add_backtest(
        BacktestRecord(strategy_id="b", metrics={"sharpe": 1.0}, trades=({"net_pnl": 1.0},))
    )
    session = _session(harness, AgentRole.STATISTICAL_AUDITOR)
    result = session.call(
        "compare_candidates",
        {"strategy_ids": ["a", "b"], "rank_by": "sharpe"},
        reason="rank them",
    )
    assert result.ranking == ()
    assert set(result.excluded) == {"a", "b"}
    assert any("nothing is rankable" in c for c in result.caveats)
    assert any("multiple-testing" in c for c in result.caveats)


def test_compare_candidates_reports_a_missing_backtest_as_absence(harness: Harness) -> None:
    session = _session(harness, AgentRole.STATISTICAL_AUDITOR)
    result = session.call(
        "compare_candidates",
        {"strategy_ids": ["ema_cross_fixture", "never_run"], "min_trades": 0},
        reason="compare",
    )
    row = next(r for r in result.rows if r.strategy_id == "never_run")
    assert row.backtest_id is None
    assert "absence is not a zero score" in row.note


# ------------------------------------------- the store is append-only


def test_the_research_store_has_no_update_or_delete() -> None:
    public = {n for n in dir(ResearchStore) if not n.startswith("_")}
    assert not (public & {"update", "delete", "remove", "clear", "overwrite"})


def test_the_store_refuses_to_overwrite_a_record() -> None:
    store = ResearchStore()
    note = store.add_note(ResearchNote(title="A filed note", body="b" * 25))
    with pytest.raises(ValueError, match="append-only"):
        store.add_note(note)


def test_records_are_frozen() -> None:
    store = ResearchStore()
    note = store.add_note(ResearchNote(title="Another note", body="b" * 25))
    with pytest.raises(ValueError):
        note.title = "changed"  # type: ignore[misc]


def test_the_store_persists_and_reloads(tmp_path: Path) -> None:
    directory = tmp_path / "research"
    store = ResearchStore(directory)
    note = store.add_note(ResearchNote(title="Persisted note", body="b" * 25,
                                       tags=("negative_result",)))
    reloaded = ResearchStore(directory)
    assert reloaded.counts()["notes"] == 1
    assert reloaded.get("notes", note.note_id).title == "Persisted note"


def test_note_search_is_deterministic() -> None:
    store = ResearchStore()
    store.add_note(ResearchNote(title="Ichimoku on FX", body="kumo " * 10,
                                tags=("ichimoku",)))
    store.add_note(ResearchNote(title="Donchian breakouts", body="channel " * 10,
                                tags=("breakout",)))
    first = store.search_notes("ichimoku")
    second = store.search_notes("ichimoku")
    assert [n.note_id for n in first] == [n.note_id for n in second]
    assert len(first) == 1
    assert store.search_notes(tags=["breakout"])[0].title == "Donchian breakouts"


# ------------------------------------- the package cannot touch execution


def test_the_agents_package_never_imports_an_execution_construct() -> None:
    """No Order, no Fill, no broker adapter, anywhere under agents/."""
    forbidden_names = {"Order", "Fill", "OrderType", "ExecutionMode"}
    forbidden_modules = ("fiboki.broker", "fiboki.risk", "fiboki.portfolio")
    offences: list[str] = []
    for source in sorted(AGENTS_ROOT.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(forbidden_modules):
                    offences.append(f"{source.name}:{node.lineno}: from {module}")
                for alias in node.names:
                    if alias.name in forbidden_names:
                        offences.append(
                            f"{source.name}:{node.lineno}: imports {alias.name} from {module}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_modules):
                        offences.append(f"{source.name}:{node.lineno}: import {alias.name}")
    assert not offences, "agents may not reach the execution layer:\n" + "\n".join(offences)
