"""A whole multi-agent research cycle, offline, with a full audit trail.

Hypothesis -> strategy mutation -> pre-registered experiment -> queued backtest
-> queued validation -> adversarial critique -> librarian filing. Six agents,
one deterministic worker, no network.
"""
from __future__ import annotations

import json

import pytest

from fiboki.agents.audit import ActionKind, Outcome
from fiboki.agents.providers import EchoProvider, ModelRouter
from fiboki.agents.roles import AgentRole
from fiboki.agents.session import open_session
from fiboki.agents.tools import ToolExecutionError
from fiboki.agents.workflows import (
    WorkflowDeps,
    offline_research_script,
    run_failure_investigation,
    run_research_cycle,
)
from fiboki.research.artefacts import Critique, ResearchNote
from tests.agents_fixtures import Harness

TRAIN = ("2024-01-01", "2024-02-01")
TEST = ("2024-02-01", "2024-03-03")


def _deps(harness: Harness, script: dict[str, str]) -> WorkflowDeps:
    return WorkflowDeps(
        context=harness.context,
        resolver=harness.resolver,
        ledger=harness.ledger,
        router=ModelRouter([EchoProvider(script)]),
        orchestrator=harness.orchestrator,
        queue="research",
    )


def _script(**overrides: object) -> dict[str, str]:
    base = offline_research_script(
        instrument="EURUSD",
        timeframe="H1",
        new_strategy_id="ema_cross_wide_stop",
        train_start=TRAIN[0],
        train_end=TRAIN[1],
        test_start=TEST[0],
        test_end=TEST[1],
    )
    base.update({k: json.dumps(v) if not isinstance(v, str) else v
                 for k, v in overrides.items()})
    return base


@pytest.fixture(scope="module")
def cycle() -> tuple[Harness, object]:
    harness = Harness()
    result = run_research_cycle(
        _deps(harness, _script()),
        seed_strategy_id="ema_cross_fixture",
        instrument="EURUSD",
        timeframe="H1",
        question="Does stop width carry the result, or does the entry?",
        workflow_id="wf_test_cycle",
    )
    return harness, result


# ------------------------------------------------------- the whole loop


def test_the_cycle_runs_end_to_end_offline(cycle) -> None:
    _harness, result = cycle
    assert result.ok, result.failed_steps
    assert [s.step for s in result.steps] == [
        "research_brief",
        "hypothesis",
        "mutation",
        "experiment_design",
        "queue_backtest",
        "worker_backtest",
        "queue_validation",
        "worker_validation",
        "critique",
        "filing",
    ]


def test_every_artefact_was_produced_and_linked(cycle) -> None:
    harness, result = cycle
    assert result.hypothesis_id and result.strategy_id and result.experiment_id
    assert result.backtest_id and result.validation_report_id
    assert result.critique_id and result.note_id

    note = harness.research.get("notes", result.note_id)
    assert isinstance(note, ResearchNote)
    for key in (
        "hypothesis_id", "strategy_id", "experiment_id", "backtest_id",
        "validation_report_id", "critique_id", "workflow_id",
    ):
        assert key in note.links, key
    assert note.links["workflow_id"] == "wf_test_cycle"


def test_the_mutation_produced_a_real_derived_strategy(cycle) -> None:
    harness, result = cycle
    parent = harness.strategies.get("ema_cross_fixture")
    child = harness.strategies.get(result.strategy_id)
    assert child.content_hash() != parent.content_hash()
    assert child.parent_strategy_ids == ("ema_cross_fixture",)
    assert child.mutation is not None
    assert child.mutation.parent_hash == parent.content_hash()
    assert child.mutation.operator == "scale_stop"
    assert child.stop.value == pytest.approx(parent.stop.value * 1.5)


def test_the_worker_not_the_agent_produced_the_result(cycle) -> None:
    harness, result = cycle
    record = harness.research.get_backtest(result.backtest_id)
    assert record.created_by == "worker"
    assert record.role == "deterministic_worker"
    assert record.ledger_sha256
    report = harness.research.get_validation_report(result.validation_report_id)
    assert report.created_by == "worker"
    # The verdict came from the gates, and no agent touched it.
    assert report.verdict in ("pass", "fail", "inconclusive")


def test_the_critic_filed_specific_falsifiable_objections(cycle) -> None:
    harness, result = cycle
    critique = harness.research.get("critiques", result.critique_id)
    assert isinstance(critique, Critique)
    assert len(critique.objections) >= 3
    for objection in critique.objections:
        assert len(objection.claim) > 40
        assert len(objection.decisive_test) > 40
        assert objection.severity in ("fatal", "major", "minor")
        assert objection.decisive_test.strip() != objection.claim.strip()
    assert critique.verdict in ("reject", "revise", "survives_this_attack")


# ------------------------------------------------------ the audit trail


def test_the_whole_workflow_is_in_the_ledger(cycle) -> None:
    harness, _result = cycle
    records = harness.ledger.by_workflow("wf_test_cycle")
    assert len(records) >= 12
    assert harness.ledger.verify_chain()

    model_calls = [r for r in records if r.kind is ActionKind.MODEL_CALL]
    tool_calls = [r for r in records if r.kind is ActionKind.TOOL_CALL]
    assert len(model_calls) == 6  # one per thinking agent
    assert {"create_hypothesis", "mutate_strategy", "design_experiment",
            "run_backtest", "run_validation", "record_critique",
            "file_research_note"} <= {r.tool for r in tool_calls}
    assert all(r.outcome is Outcome.OK for r in records)


def test_every_record_names_its_agent_role_model_and_cost(cycle) -> None:
    harness, _result = cycle
    for record in harness.ledger.by_workflow("wf_test_cycle"):
        assert record.agent_id
        assert record.role
        assert record.reason
        assert record.wall_ms >= 0.0
        assert record.cost_usd >= 0.0
        if record.kind is ActionKind.MODEL_CALL:
            assert record.model == "echo-1"
            assert record.model_version == "echo-1.0.0"
            assert record.provider == "echo"
            assert record.prompt
            assert record.prompt_tokens > 0


def test_each_tool_call_points_back_at_the_thought_that_caused_it(cycle) -> None:
    harness, _result = cycle
    records = {r.action_id: r for r in harness.ledger.by_workflow("wf_test_cycle")}
    writes = [
        r for r in records.values()
        if r.kind is ActionKind.TOOL_CALL and r.tool == "create_hypothesis"
    ]
    assert writes
    parent = records[writes[0].parent_action_id]
    assert parent.kind is ActionKind.MODEL_CALL
    assert "falsifiable hypothesis" in parent.prompt or "hypothesis" in parent.prompt


def test_the_full_inputs_and_outputs_are_recorded(cycle) -> None:
    harness, result = cycle
    record = next(
        r for r in harness.ledger.by_workflow("wf_test_cycle") if r.tool == "mutate_strategy"
    )
    assert record.inputs["operator"] == "scale_stop"
    assert record.inputs["arguments"] == {"factor": 1.5}
    assert record.outputs["strategy_id"] == result.strategy_id
    assert record.outputs["content_hash"]


# -------------------------------------------------- failure is recorded


def test_a_step_whose_model_output_does_not_parse_is_recorded_as_failed() -> None:
    harness = Harness()
    script = _script()
    script["hypothesis"] = "I am afraid I cannot express that as JSON."
    result = run_research_cycle(
        _deps(harness, script),
        seed_strategy_id="ema_cross_fixture",
        instrument="EURUSD",
        timeframe="H1",
        workflow_id="wf_bad_json",
    )
    assert not result.ok
    assert "hypothesis" in result.failed_steps
    failed = next(s for s in result.steps if s.step == "hypothesis")
    assert "not valid JSON" in failed.error
    # The cycle carries on rather than hiding the gap.
    assert result.strategy_id is not None
    assert harness.ledger.verify_chain()


def test_a_no_op_mutation_is_refused_and_recorded() -> None:
    """A mutation that changes nothing must not enter the population."""
    harness = Harness()
    script = _script()
    script["mutation"] = json.dumps(
        {
            "new_strategy_id": "ema_cross_noop",
            "operator": "scale_stop",
            "arguments": {"factor": 1.0},
            "rationale": "no change at all",
            "name_suffix": "noop",
        }
    )
    result = run_research_cycle(
        _deps(harness, script),
        seed_strategy_id="ema_cross_fixture",
        instrument="EURUSD",
        timeframe="H1",
        workflow_id="wf_noop",
    )
    assert "mutation" in result.failed_steps
    assert "semantically unchanged" in next(
        s for s in result.steps if s.step == "mutation"
    ).error
    assert "ema_cross_noop" not in harness.strategies


def test_a_critique_of_pure_caution_is_refused() -> None:
    """Generic caution is a refusal to do the critic's job, and is rejected."""
    harness = Harness()
    session = open_session(
        agent_id="critic",
        role=AgentRole.ADVERSARIAL_QUANT_CRITIC,
        context=harness.context,
        resolver=harness.resolver,
        ledger=harness.ledger,
    )
    with pytest.raises(ToolExecutionError, match="generic caution"):
        session.call(
            "record_critique",
            {
                "target_kind": "strategy",
                "target_id": "ema_cross_fixture",
                "verdict": "revise",
                "objections": [
                    {
                        "claim": "This strategy might not work in the future markets.",
                        "decisive_test": "past performance is no guarantee",
                        "severity": "major",
                    }
                ],
            },
            reason="hedge instead of attacking",
        )
    assert harness.research.counts()["critiques"] == 0


def test_an_experiment_with_leaking_windows_is_refused() -> None:
    harness = Harness()
    script = _script()
    script["experiment_design"] = json.dumps(
        {
            "train_start": "2024-01-01",
            "train_end": "2024-03-01",
            "test_start": "2024-02-01",  # overlaps the training window
            "test_end": "2024-03-03",
            "embargo_bars": 0,
            "n_folds": 1,
            "seed": 1,
            "n_trials_in_search": 1,
            "success_criteria": [
                {"metric": "n_trades", "comparator": ">=", "threshold": 80.0,
                 "rationale": "house floor"}
            ],
            "notes": "",
        }
    )
    result = run_research_cycle(
        _deps(harness, script),
        seed_strategy_id="ema_cross_fixture",
        instrument="EURUSD",
        timeframe="H1",
        workflow_id="wf_leak",
    )
    assert "experiment_design" in result.failed_steps
    assert "leak" in next(
        s for s in result.steps if s.step == "experiment_design"
    ).error


# ---------------------------------------------- the second workflow


def test_the_failure_investigation_is_read_only() -> None:
    harness = Harness()
    cycle_result = run_research_cycle(
        _deps(harness, _script()),
        seed_strategy_id="ema_cross_fixture",
        instrument="EURUSD",
        timeframe="H1",
        workflow_id="wf_for_investigation",
    )
    before = harness.research.counts()

    result = run_failure_investigation(
        _deps(harness, _script()),
        backtest_id=cycle_result.backtest_id,
        workflow_id="wf_investigation",
    )
    assert result.ok, result.failed_steps
    assert [s.step for s in result.steps] == ["drawdown", "worst_trades", "diagnosis"]
    diagnosis = result.steps[-1].output
    assert diagnosis["most_likely_cause"] in (
        "always_this_bad", "market_changed", "execution_changed", "data_defect"
    )
    # An investigator holds no write capability, so nothing changed.
    assert harness.research.counts() == before


def test_the_investigation_actually_read_the_damage() -> None:
    harness = Harness()
    cycle_result = run_research_cycle(
        _deps(harness, _script()),
        seed_strategy_id="ema_cross_fixture",
        instrument="EURUSD",
        timeframe="H1",
        workflow_id="wf_for_reading",
    )
    result = run_failure_investigation(
        _deps(harness, _script()),
        backtest_id=cycle_result.backtest_id,
        workflow_id="wf_reading",
    )
    drawdown = result.steps[0].output
    assert drawdown["episodes"]
    assert drawdown["max_drawdown_pct"] >= 0.0
    worst = result.steps[1].output
    assert worst["selection"] == "worst_5_by_net_pnl"
    pnls = [t["net_pnl"] for t in worst["trades"]]
    assert pnls == sorted(pnls)


# ----------------------------------------------------- reproducibility


def test_running_the_same_cycle_twice_produces_the_same_decisions() -> None:
    """Same script, same data: the same mutation, the same verdict."""
    results = []
    for i in range(2):
        harness = Harness()
        results.append(
            (
                harness,
                run_research_cycle(
                    _deps(harness, _script()),
                    seed_strategy_id="ema_cross_fixture",
                    instrument="EURUSD",
                    timeframe="H1",
                    workflow_id=f"wf_repeat_{i}",
                ),
            )
        )
    (h1, r1), (h2, r2) = results
    assert r1.strategy_id == r2.strategy_id
    assert h1.strategies.get(r1.strategy_id).content_hash() == (
        h2.strategies.get(r2.strategy_id).content_hash()
    )
    assert (
        h1.research.get_backtest(r1.backtest_id).ledger_sha256
        == h2.research.get_backtest(r2.backtest_id).ledger_sha256
    )
    assert (
        h1.research.get_validation_report(r1.validation_report_id).verdict
        == h2.research.get_validation_report(r2.validation_report_id).verdict
    )
