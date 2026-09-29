"""Workflows are bracketed, manifest-stamped and filed.  Offline, EchoProvider."""
from __future__ import annotations

import dataclasses

import pytest

from fiboki.agents.audit import NO_MODEL, ActionKind, Outcome
from fiboki.agents.manifest import build_run_manifest
from fiboki.agents.providers import EchoProvider, ModelRouter
from fiboki.agents.workflows import (
    MANIFEST_NOTE_TAG,
    WORKFLOW_END,
    WORKFLOW_START,
    WorkflowDeps,
    offline_research_script,
    run_failure_investigation,
    run_research_cycle,
)
from tests.agents_fixtures import Harness


def _deps(harness: Harness, **overrides: object) -> WorkflowDeps:
    script = offline_research_script(
        instrument="EURUSD", timeframe="H1", new_strategy_id="ema_cross_wide_stop",
        train_start="2024-01-01", train_end="2024-02-01",
        test_start="2024-02-01", test_end="2024-03-03",
    )
    deps = WorkflowDeps(
        context=harness.context, resolver=harness.resolver, ledger=harness.ledger,
        router=ModelRouter([EchoProvider(script)]), orchestrator=harness.orchestrator,
    )
    return dataclasses.replace(deps, **overrides)  # type: ignore[arg-type]


def _cycle(harness: Harness, wid: str, **overrides: object):
    return run_research_cycle(
        _deps(harness, **overrides), seed_strategy_id="ema_cross_fixture",
        instrument="EURUSD", timeframe="H1", workflow_id=wid,
    )


@pytest.fixture(scope="module")
def cycled() -> tuple[Harness, object, object]:
    harness = Harness()
    cycle = _cycle(harness, "wf_manifest")
    investigation = run_failure_investigation(
        _deps(harness), backtest_id=cycle.backtest_id, workflow_id="wf_manifest_inv"
    )
    return harness, cycle, investigation


def test_the_manifest_hash_is_on_the_workflow_start_record(cycled) -> None:
    harness, cycle, _ = cycled
    records = harness.ledger.by_workflow("wf_manifest")
    start = records[0]
    assert start.tool == WORKFLOW_START and start.kind is ActionKind.WORKFLOW_STEP
    expected = build_run_manifest()
    assert start.manifest_hash == expected.hash == cycle.manifest_hash
    assert start.outputs["manifest"]["components"] == dict(sorted(expected.components.items()))
    assert start.model_id == NO_MODEL
    assert start.outputs["session_budgets"]["adversarial_quant_critic"]["max_cost_usd"] > 0


def test_every_record_of_both_workflows_carries_model_id_and_manifest(cycled) -> None:
    harness, cycle, investigation = cycled
    for wid in ("wf_manifest", "wf_manifest_inv"):
        records = harness.ledger.by_workflow(wid)
        assert records, wid
        for record in records:
            assert record.manifest_hash == cycle.manifest_hash, (wid, record.tool)
            assert record.model_id, (wid, record.tool)
            if record.kind is ActionKind.MODEL_CALL:
                assert record.model_id == "echo-1"
                assert record.model_digest and record.model_digest.startswith("sha256:")
    assert investigation.manifest_hash == cycle.manifest_hash


def test_each_workflow_ends_with_exactly_one_terminal_record(cycled) -> None:
    harness, _, _ = cycled
    for wid in ("wf_manifest", "wf_manifest_inv"):
        records = harness.ledger.by_workflow(wid)
        assert [r.tool for r in records].count(WORKFLOW_END) == 1
        assert records[-1].tool == WORKFLOW_END
        assert records[-1].outcome is Outcome.OK
        assert records[-1].outputs["failed_steps"] == []


def test_the_manifest_is_filed_once_per_distinct_hash(cycled) -> None:
    harness, cycle, _ = cycled
    notes = harness.research.search_notes(tags=(MANIFEST_NOTE_TAG,), limit=100)
    assert [n.links["manifest_hash"] for n in notes] == [cycle.manifest_hash]
    # A second run under the same manifest files nothing new ...
    _cycle(harness, "wf_manifest_again")
    assert len(harness.research.search_notes(tags=(MANIFEST_NOTE_TAG,), limit=100)) == 1
    # ... and a different manifest files exactly one more.
    other = dataclasses.replace(
        build_run_manifest(),
        hash="f" * 64,
    )
    run_failure_investigation(
        _deps(harness, manifest=other), backtest_id=cycle.backtest_id, workflow_id="wf_other"
    )
    hashes = {
        n.links["manifest_hash"]
        for n in harness.research.search_notes(tags=(MANIFEST_NOTE_TAG,), limit=100)
    }
    assert hashes == {cycle.manifest_hash, "f" * 64}
    assert harness.ledger.by_workflow("wf_other")[0].manifest_hash == "f" * 64


def test_a_failed_step_ends_the_run_with_an_error_terminal_record() -> None:
    harness = Harness()
    script = offline_research_script(
        instrument="EURUSD", timeframe="H1", new_strategy_id="x_variant",
        train_start="2024-01-01", train_end="2024-02-01",
        test_start="2024-02-01", test_end="2024-03-03",
    )
    script["research_brief"] = "not json"
    deps = _deps(harness, router=ModelRouter([EchoProvider(script)]))
    result = run_research_cycle(
        deps, seed_strategy_id="ema_cross_fixture", instrument="EURUSD", timeframe="H1",
        workflow_id="wf_partial",
    )
    end = harness.ledger.by_workflow("wf_partial")[-1]
    assert end.tool == WORKFLOW_END
    assert end.outcome is Outcome.ERROR
    assert "research_brief" in end.error
    assert end.outputs["failed_steps"] == list(result.failed_steps)


class _ExplodingOrchestrator:
    """Raises from outside any step's own try block, as a crashed worker would."""

    def drain(self, queue: str):
        raise RuntimeError("worker pool vanished")


def test_a_run_that_raises_still_gets_a_terminal_record() -> None:
    harness = Harness()
    deps = _deps(harness, orchestrator=_ExplodingOrchestrator())
    with pytest.raises(RuntimeError, match="worker pool vanished"):
        run_research_cycle(
            deps, seed_strategy_id="ema_cross_fixture", instrument="EURUSD",
            timeframe="H1", workflow_id="wf_crash",
        )
    records = harness.ledger.by_workflow("wf_crash")
    assert records[0].tool == WORKFLOW_START
    assert records[-1].tool == WORKFLOW_END
    assert records[-1].outcome is Outcome.ERROR
    assert "worker pool vanished" in records[-1].error
    assert harness.ledger.verify_chain()


def test_schema_constrained_output_passes_the_tool_schema_and_can_be_switched_off() -> None:
    harness = Harness()
    provider = EchoProvider(
        offline_research_script(
            instrument="EURUSD", timeframe="H1", new_strategy_id="ema_cross_wide_stop",
            train_start="2024-01-01", train_end="2024-02-01",
            test_start="2024-02-01", test_end="2024-03-03",
        )
    )
    run_research_cycle(
        _deps(harness, router=ModelRouter([provider])), seed_strategy_id="ema_cross_fixture",
        instrument="EURUSD", timeframe="H1", workflow_id="wf_schema",
    )
    by_step = {c.metadata["step"]: c for c in provider.calls}
    assert by_step["research_brief"].json_schema is None
    assert by_step["hypothesis"].json_schema["title"] == "CreateHypothesisIn"
    assert by_step["critique"].json_schema["title"] == "RecordCritiqueIn"

    provider.calls.clear()
    run_research_cycle(
        _deps(Harness(), router=ModelRouter([provider]), schema_constrained_output=False),
        seed_strategy_id="ema_cross_fixture", instrument="EURUSD", timeframe="H1",
        workflow_id="wf_no_schema",
    )
    assert all(c.json_schema is None for c in provider.calls)
