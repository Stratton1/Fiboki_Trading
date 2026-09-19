"""The deterministic worker: an agent queues a payload, the platform runs it.

These exercise the real engine, the real stats library and the real integrity
checker. The strategy fixture is a plain EMA crossover, which on synthetic data
loses money after costs -- that is the intended outcome, and the validation
gates are expected to say so.
"""
from __future__ import annotations

import pytest

from fiboki.agents.audit import AuditLedger
from fiboki.agents.capabilities import Capability
from fiboki.agents.jobs import CompiledStrategyRunner, register_research_handlers
from fiboki.agents.orchestrator import JobSpec, JobStatus, JobType, Orchestrator
from fiboki.agents.roles import AgentRole
from fiboki.agents.session import open_session
from fiboki.core.enums import Timeframe
from fiboki.research.artefacts import BacktestRecord
from fiboki.strategy.compiler import compile_strategy
from fiboki.validation.gates import GATE_SET_V2
from tests.agents_fixtures import Harness


@pytest.fixture(scope="module")
def harness() -> Harness:
    return Harness()


@pytest.fixture(scope="module")
def backtest_id(harness: Harness) -> str:
    harness.orchestrator.submit(
        JobSpec(
            job_type=JobType.BACKTEST,
            queue="research",
            payload={
                "strategy_id": "ema_cross_fixture",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "account_ccy": "USD",
            },
            idempotency_key="module_backtest",
        )
    )
    record = harness.orchestrator.drain("research")[0]
    assert record.status is JobStatus.SUCCEEDED, record.error
    assert record.result is not None
    return str(record.result["backtest_id"])


def _run(harness: Harness, job_type: JobType, payload: dict, key: str) -> dict:
    harness.orchestrator.submit(
        JobSpec(job_type=job_type, queue="research", payload=payload, idempotency_key=key)
    )
    record = harness.orchestrator.drain("research")[0]
    assert record.status is JobStatus.SUCCEEDED, f"{job_type.value}: {record.error}"
    assert record.result is not None
    return dict(record.result)


# ------------------------------------------------------------- backtest


def test_a_queued_backtest_runs_the_real_engine(harness: Harness, backtest_id: str) -> None:
    record = harness.research.get_backtest(backtest_id)
    assert record.strategy_id == "ema_cross_fixture"
    assert record.n_trades > 0
    assert record.ledger_sha256
    assert record.equity_curve
    assert record.dataset_version_ids
    assert record.content_hash == harness.document.content_hash()
    assert record.created_by == "worker"


def test_the_recorded_result_is_deterministic(harness: Harness, backtest_id: str) -> None:
    """Same payload, same bytes, same ledger. Re-run it and compare hashes."""
    first = harness.research.get_backtest(backtest_id)
    result = _run(
        harness,
        JobType.BACKTEST,
        {
            "strategy_id": "ema_cross_fixture",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "account_ccy": "USD",
        },
        key="determinism_rerun",
    )
    assert result["ledger_sha256"] == first.ledger_sha256
    assert result["metrics"]["total_return_pct"] == first.metrics["total_return_pct"]


def test_a_currency_mismatch_is_refused_unless_acknowledged(harness: Harness) -> None:
    """V1 silently reported USD-quoted results as GBP. V2 will not."""
    harness.orchestrator.submit(
        JobSpec(
            job_type=JobType.BACKTEST,
            queue="research",
            payload={
                "strategy_id": "ema_cross_fixture",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "account_ccy": "GBP",
            },
            idempotency_key="fx_refused",
            max_attempts=1,
        )
    )
    record = harness.orchestrator.drain("research")[0]
    assert record.status is JobStatus.DEAD_LETTER
    assert "fx_approximation_acknowledged" in record.error


def test_an_acknowledged_mismatch_records_the_caveat(harness: Harness) -> None:
    result = _run(
        harness,
        JobType.BACKTEST,
        {
            "strategy_id": "ema_cross_fixture",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "account_ccy": "GBP",
            "fx_approximation_acknowledged": True,
        },
        key="fx_acknowledged",
    )
    assert any("FX APPROXIMATION" in c for c in result["caveats"])


def test_a_strategy_run_outside_its_universe_is_refused(harness: Harness) -> None:
    harness.orchestrator.submit(
        JobSpec(
            job_type=JobType.BACKTEST,
            queue="research",
            payload={
                "strategy_id": "ema_cross_fixture",
                "instrument": "GBPUSD",
                "timeframe": "H1",
            },
            idempotency_key="outside_universe",
            max_attempts=1,
        )
    )
    record = harness.orchestrator.drain("research")[0]
    assert record.status is JobStatus.DEAD_LETTER
    assert "not in the document's universe" in record.error


# ----------------------------------------------------------- validation


def test_the_validation_gates_run_and_decide(harness: Harness, backtest_id: str) -> None:
    result = _run(
        harness,
        JobType.VALIDATION,
        {"backtest_id": backtest_id, "n_trials_in_search": 4, "bootstrap_samples": 200},
        key="validation_main",
    )
    assert result["verdict"] in ("pass", "fail", "inconclusive")
    report = harness.research.get_validation_report(result["report_id"])
    check_names = {c["name"] for c in report.checks}
    # The gates are the PLATFORM's, not the agent layer's.
    assert check_names == {g.name for g in GATE_SET_V2.gates}
    assert report.metrics["gate_set_version"] == GATE_SET_V2.version
    assert set(result["failed_checks"]) <= check_names
    assert report.created_by == "worker"


def test_gates_this_job_cannot_compute_block_rather_than_vanish(
    harness: Harness, backtest_id: str
) -> None:
    """A gate nobody ran is not a gate that passed."""
    record = harness.orchestrator.by_key("validation_main")
    assert record is not None and record.result is not None
    report = harness.research.get_validation_report(str(record.result["report_id"]))
    by_name = {c["name"]: c for c in report.checks}
    for ladder_only in ("walk_forward_efficiency", "pbo", "spa_consistent_p", "stepm_survivor"):
        assert by_name[ladder_only]["status"] == "not_evaluated"
        assert by_name[ladder_only]["passed"] is False
    assert any("NOT_EVALUATED" in c for c in report.caveats)


def test_an_honestly_bad_strategy_fails_the_gates(harness: Harness, backtest_id: str) -> None:
    """Honest underperformance beats a flattering pass.

    Reads the job recorded by the previous test through its idempotency key --
    a re-submission would be deduplicated, which is itself the guarantee.
    """
    record = harness.orchestrator.by_key("validation_main")
    assert record is not None and record.result is not None
    assert record.result["verdict"] == "fail"
    assert "min_trades" in record.result["failed_checks"]
    assert record.result["binding_constraint"] == "min_trades"
    report = harness.research.get_validation_report(str(record.result["report_id"]))
    assert report.metrics["deflated_sharpe_ratio"] < 0.95


def test_the_report_states_its_own_caveats(harness: Harness, backtest_id: str) -> None:
    result = _run(
        harness,
        JobType.VALIDATION,
        {"backtest_id": backtest_id, "n_trials_in_search": 1, "bootstrap_samples": 200},
        key="validation_single_trial",
    )
    report = harness.research.get_validation_report(result["report_id"])
    joined = " ".join(report.caveats)
    assert "PER TRADE" in joined
    assert "n_trials_in_search=1" in joined


def test_the_verdict_is_computed_not_supplied(harness: Harness, backtest_id: str) -> None:
    """There is no payload field that sets the verdict; only the checks do."""
    result = _run(
        harness,
        JobType.VALIDATION,
        {"backtest_id": backtest_id, "bootstrap_samples": 200, "seed": 11},
        key="validation_loosened",
    )
    report = harness.research.get_validation_report(result["report_id"])
    expected = "pass" if all(c["passed"] for c in report.checks) else "fail"
    assert report.verdict == expected
    # There is no payload key that could have set it.
    assert "verdict" not in result["metrics"]


# ---------------------------------------------------- the other studies


def test_walkforward_produces_one_recorded_run_per_fold(harness: Harness) -> None:
    result = _run(
        harness,
        JobType.WALKFORWARD,
        {
            "strategy_id": "ema_cross_fixture",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "n_folds": 3,
            "embargo_bars": 5,
        },
        key="walkforward_main",
    )
    assert result["n_folds"] == 3
    assert len(result["folds"]) == 3
    assert len(result["backtest_ids"]) == 3
    for backtest_id in result["backtest_ids"]:
        assert harness.research.get_backtest(backtest_id).label.startswith("walkforward_fold")
    assert result["consistency"] is not None
    assert any("not independent samples" in c for c in result["caveats"])


def test_ablation_measures_each_component_against_the_baseline(harness: Harness) -> None:
    result = _run(
        harness,
        JobType.ABLATION,
        {
            "strategy_id": "ema_cross_fixture",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "components": ["take_profits", "regime", "trailing"],
        },
        key="ablation_main",
    )
    components = {row["component"] for row in result["ablations"]}
    assert "take_profits" in components
    # The fixture has no regime rules or trailing model, so those are SKIPPED
    # rather than silently reported as a no-change ablation.
    assert set(result["skipped"]) == {"regime", "trailing"}
    row = next(r for r in result["ablations"] if r["component"] == "take_profits")
    assert row["delta_return_pct"] is not None
    assert any("fitted" in c for c in result["caveats"])


def test_sensitivity_sweeps_execution_assumptions_and_says_so(
    harness: Harness, backtest_id: str
) -> None:
    result = _run(
        harness,
        JobType.SENSITIVITY,
        {"backtest_id": backtest_id, "n_samples": 40, "seed": 3},
        key="sensitivity_main",
    )
    names = {curve["stress"] for curve in result["curves"]}
    assert {"spread_multiplier", "execution_delay_adverse"} <= names
    assert any("not strategy parameters" in c for c in result["caveats"])
    for curve in result["curves"]:
        assert len(curve["levels"]) == len(curve["median"])


def test_sensitivity_on_a_tradeless_backtest_is_refused(harness: Harness) -> None:
    empty = harness.research.add_backtest(
        BacktestRecord(strategy_id="empty", trades=(), equity_curve=())
    )
    harness.orchestrator.submit(
        JobSpec(
            job_type=JobType.SENSITIVITY,
            queue="research",
            payload={"backtest_id": empty.backtest_id},
            idempotency_key="sensitivity_empty",
            max_attempts=1,
        )
    )
    record = harness.orchestrator.drain("research")[0]
    assert record.status is JobStatus.DEAD_LETTER
    assert "nothing to stress" in record.error


def test_data_quality_scan_detects_but_never_repairs(harness: Harness) -> None:
    result = _run(
        harness,
        JobType.DATA_QUALITY_SCAN,
        {"instrument": "EURUSD", "timeframe": "H1"},
        key="dq_main",
    )
    assert result["row_count"] == len(harness.frame)
    assert result["quality"] in ("raw", "validated", "repaired", "suspect", "rejected")
    assert "is_clean" in result
    # Detection never mutates: the source frame is byte-identical afterwards.
    frame, _version = harness.bars.load("EURUSD", Timeframe.H1)
    assert frame.equals(harness.frame)


# ------------------------------------------------- the agent's boundary


def test_an_agent_only_ever_hands_over_a_payload() -> None:
    """The submitting agent gets a ticket, not a result."""
    harness = Harness()
    session = open_session(
        agent_id="auditor",
        role=AgentRole.STATISTICAL_AUDITOR,
        context=harness.context,
        resolver=harness.resolver,
        ledger=harness.ledger,
    )
    ticket = session.call(
        "run_backtest",
        {"strategy_id": "ema_cross_fixture", "instrument": "EURUSD", "timeframe": "H1"},
        reason="queue it",
    )
    assert ticket.status == "pending"
    assert "does not run the engine" in ticket.note
    assert harness.research.counts()["backtests"] == 0  # nothing has run yet

    records = harness.orchestrator.drain("research")
    assert records[0].status is JobStatus.SUCCEEDED
    assert harness.research.counts()["backtests"] == 1
    # The job records who asked, and under which capability.
    assert records[0].spec.submitted_by == "auditor"
    assert records[0].spec.required_capabilities == frozenset({Capability.SUBMIT_JOB})


def test_a_resubmitted_identical_job_does_not_run_twice() -> None:
    harness = Harness()
    session = open_session(
        agent_id="auditor",
        role=AgentRole.STATISTICAL_AUDITOR,
        context=harness.context,
        resolver=harness.resolver,
        ledger=harness.ledger,
    )
    inputs = {"strategy_id": "ema_cross_fixture", "instrument": "EURUSD", "timeframe": "H1"}
    first = session.call("run_backtest", inputs, reason="queue it")
    second = session.call("run_backtest", inputs, reason="queue it again by mistake")
    assert second.job_id == first.job_id
    assert second.deduplicated is True
    harness.orchestrator.drain("research")
    assert harness.research.counts()["backtests"] == 1


def test_only_registered_job_types_can_be_queued() -> None:
    """The set of things the fleet can cause is fixed at wiring time."""
    harness = Harness(register_handlers=False)
    orchestrator = Orchestrator()
    register_research_handlers(
        orchestrator,
        research=harness.research,
        strategies=harness.strategies,
        bars=harness.bars,
        job_types=[JobType.BACKTEST],
    )
    assert orchestrator.handled_types() == (JobType.BACKTEST,)
    with pytest.raises(Exception, match="no handler registered"):
        orchestrator.submit(
            JobSpec(job_type=JobType.VALIDATION, queue="research", payload={})
        )


# ------------------------------------------------------- engine adapter


def test_the_runner_emits_nothing_during_warmup(harness: Harness) -> None:
    """A signal produced from an unwarmed indicator is a number with no meaning."""
    compiled = compile_strategy(harness.document)
    runner = CompiledStrategyRunner(
        compiled, {"EURUSD": harness.frame}, harness.document.timeframes[0]
    )
    prepared = compiled.prepare(harness.frame)
    for idx in range(compiled.warmup_period):
        assert compiled.generate_signal(prepared, idx, "EURUSD", Timeframe.H1) is None
    assert runner.signals_emitted == 0


def test_a_fresh_ledger_and_harness_are_independent() -> None:
    a, b = Harness(), Harness()
    assert a.research is not b.research
    assert isinstance(a.ledger, AuditLedger)
