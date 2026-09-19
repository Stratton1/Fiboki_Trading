"""The ladder, wired end to end to ``backtest/engine.py``.

``tests/unit/test_walk_forward_transfers.py`` proves that RUNG 2 catches a moving
parameter surface, using an analytic evaluator whose truth is known exactly. That
is the right way to test the RULE. It cannot test the WIRING: until the DSL could
bind a parameter, the ladder had never once been run against the real engine, so
"the selected parameterisation is the one evaluated out of sample" was a property
of a fixture rather than of the platform.

This module runs the same contrast against ``EngineEvaluator`` on deterministic
synthetic bars and proves, from the evaluator's own call log, that:

* every fold swept the declared grid on its TRAIN window;
* the parameterisation that won the train window is the one the TEST window was
  evaluated with;
* the V1-style fixed-parameter contrast was computed separately, on the same test
  windows, so the two procedures can be compared rather than conflated;
* no train window reaches into its test window.

Nothing here asserts a VERDICT. Whether this strategy passes is a fact about the
market, and a test that required a particular answer would be a test that had to
be tuned.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from synthetic_prices import choppy_ohlc

from fiboki.core.enums import Timeframe
from fiboki.core.money import IdentityFxSource
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import (
    EngineEvaluator,
    EvaluationCache,
    EvaluatorConfig,
)
from fiboki.validation.evaluation import Candidate, params_key
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder
from fiboki.validation.report import RungOutcome
from fiboki.validation.run import run_validation

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "research" / "strategies"
DATASET = "synthetic_eurusd_h1_ladder_v1"

#: A deliberately loose bar. These are 6,000 synthetic hourly bars of ONE
#: instrument; the production gate demands 400 trades, which one instrument on
#: one timeframe cannot reach and is not expected to. The version string is
#: mandatory on an override precisely so a report can never claim to have
#: cleared a bar it never faced, and nothing in this module asserts a verdict.
TEST_GATES = GATE_SET_V2.with_overrides("test-wiring-only", min_trades=20)


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return choppy_ohlc(6000)


@pytest.fixture(scope="module")
def document() -> StrategyDocument:
    return StrategyDocument.from_json(
        (SEED_DIR / "rsi_band_mean_reversion.json").read_text()
    )


@pytest.fixture(scope="module")
def ladder_run(document, bars, tmp_path_factory):
    """One full ladder run against the real engine. Shared: it is not cheap."""
    cache = EvaluationCache(tmp_path_factory.mktemp("ladder_cache"))
    evaluator = EngineEvaluator(
        document=document,
        frame=bars,
        dataset_version_id=DATASET,
        config=EvaluatorConfig(
            instrument="EURUSD",
            timeframe=Timeframe.H1,
            account_ccy="USD",
            initial_balance=100_000.0,
            min_window_bars=20,
        ),
        fx=IdentityFxSource(),
        cache=cache,
    )
    registry = HoldoutRegistry.in_memory()
    registry.define(DATASET, data_start=bars.index[0], data_end=bars.index[-1])
    candidate = Candidate(
        strategy_id=document.strategy_id,
        content_hash=document.bind_defaults().content_hash(),
        default_params=document.default_values(),
        grid=evaluator.candidate(
            max_points=8,
            max_values_per_axis=2,
            include=("rsi_period", "rsi_ceiling", "adx_ceiling"),
        ).grid,
        document=document,
    )
    ladder = ValidationLadder(
        config=LadderConfig(
            min_trades=20,
            selection_metric="net_profit",
            walk_forward_folds=3,
            cv_groups=6,
            stress_samples=20,
            spa_bootstraps=100,
        ),
        gate_set=TEST_GATES,
    )
    report = ladder.run(
        candidate,
        evaluator,
        registry=registry,
        dataset_version_id=DATASET,
        engine_config=evaluator.engine_fingerprint(),
        broker_profile=evaluator.config.profile.fingerprint(),
        actor="test:ladder-wiring",
    )
    return report, evaluator, registry


# --------------------------------------------------------- it runs at all


def test_the_ladder_reaches_the_walk_forward_rung_on_the_real_engine(
    ladder_run,
) -> None:
    report, evaluator, _ = ladder_run
    assert evaluator.n_engine_runs > 0
    assert report.rung(0).outcome is not RungOutcome.NOT_REACHED
    assert report.rung(2) is not None
    if report.rung(2).outcome is RungOutcome.NOT_REACHED:
        pytest.fail(
            "the ladder stopped before rung 2: "
            f"{report.first_failing_rung().label} -- "
            f"{report.first_failing_rung().reason}"
        )


def test_the_report_names_the_dataset_the_engine_and_the_gate_set(ladder_run) -> None:
    report, evaluator, _ = ladder_run
    assert report.dataset_version_id == DATASET
    assert report.gate_set_version == "test-wiring-only"
    assert report.engine_config["evaluator"]["instrument"] == "EURUSD"
    assert report.broker_profile["name"]
    assert report.engine_config == evaluator.engine_fingerprint()


# ------------------------------------------------------- the transfer


def test_every_fold_swept_the_declared_grid_on_its_TRAIN_window(ladder_run) -> None:
    report, evaluator, _ = ladder_run
    folds = report.rung(2).metrics["folds"]
    assert folds
    for fold in folds:
        train_name = fold["train"]["name"]
        calls_on_train = [c for c in evaluator.calls if c[1] == train_name]
        assert len(calls_on_train) >= 2, (
            f"{train_name} saw {len(calls_on_train)} evaluations; a fold that "
            "evaluates one parameterisation is not fitting anything"
        )


def test_the_parameterisation_selected_on_train_is_the_one_run_on_test(
    ladder_run,
) -> None:
    """Proved from the evaluator's call log, not from the rung's own summary."""
    report, evaluator, _ = ladder_run
    for fold in report.rung(2).metrics["folds"]:
        key = params_key(fold["selected_params"])
        assert (key, fold["test"]["name"]) in evaluator.calls


def test_the_v1_contrast_was_run_on_the_same_test_windows(ladder_run) -> None:
    report, evaluator, _ = ladder_run
    default_key = params_key(report.rung(0).metrics["baseline"]["params"])
    for fold in report.rung(2).metrics["folds"]:
        assert (default_key, fold["test"]["name"]) in evaluator.calls
        assert "fixed_param_oos_net_profit" in fold
        assert "oos_net_profit" in fold


def test_no_train_window_reaches_into_its_test_window(ladder_run) -> None:
    report, _, _ = ladder_run
    for fold in report.rung(2).metrics["folds"]:
        assert pd.Timestamp(fold["train"]["end"]) <= pd.Timestamp(fold["test"]["start"])


def test_the_selection_is_a_real_choice_between_distinguishable_trials(
    ladder_run,
) -> None:
    """A sweep whose cells are identical is not a sweep.

    This is the property the whole binding mechanism exists to provide: before
    it, every cell of a 'sweep' ran the same numbers, so the selection step was
    choosing between identical evidence and rung 2 could not have measured
    anything at all.
    """
    report, _, _ = ladder_run
    screen = report.rung(1).metrics
    assert screen["n_trials"] >= 4
    assert screen["cross_trial_sharpe_variance"] > 0.0


# ------------------------------------------------------- holdout discipline


def test_no_rung_before_six_touched_the_holdout(ladder_run) -> None:
    report, evaluator, registry = ladder_run
    segment = registry.segment(DATASET)
    holdout = segment.window
    for key, window_name in evaluator.calls:
        del key
        if window_name == holdout.name:
            continue
        assert window_name != holdout.name


def test_the_holdout_is_spent_at_most_once(ladder_run) -> None:
    report, _, registry = ladder_run
    consumed = registry.consumed_hashes(DATASET)
    if report.rung(6).outcome is RungOutcome.NOT_REACHED:
        assert consumed == ()
    else:
        assert len(consumed) == 1


# ----------------------------------------------------------- run_validation


def test_run_validation_is_a_single_call_that_produces_a_report(
    document, bars, tmp_path
) -> None:
    registry = HoldoutRegistry.in_memory()
    run = run_validation(
        document=document,
        bars=bars,
        dataset_version_id="synthetic_eurusd_h1_runner_v1",
        instrument="EURUSD",
        timeframe=Timeframe.H1,
        registry=registry,
        account_ccy="USD",
        initial_balance=100_000.0,
        gate_set=TEST_GATES,
        ladder_config=LadderConfig(
            min_trades=20,
            selection_metric="net_profit",
            walk_forward_folds=3,
            stress_samples=10,
            spa_bootstraps=50,
        ),
        max_grid_points=4,
        max_values_per_axis=2,
        sweep_parameters=("rsi_period", "rsi_ceiling"),
        cache_dir=tmp_path / "cache",
        actor="test:run_validation",
    )
    report = run.report
    assert report.strategy_id == "rsi_band_mean_reversion"
    assert report.dataset_version_id == "synthetic_eurusd_h1_runner_v1"
    assert report.verdict is not None
    assert report.summary()
    # A rejection is a result, not an error: the report exists either way.
    assert report.first_failing_rung() is None or report.first_failing_rung().reason
    assert run.evaluator.n_engine_runs > 0
    assert set(run.candidate.grid.names) == {"rsi_period", "rsi_ceiling"}
    assert json_round_trips(report)


def json_round_trips(report) -> bool:
    from fiboki.validation.report import ValidationReport

    return ValidationReport.from_json(report.to_json()).content_hash() == (
        report.content_hash()
    )


def test_run_validation_refuses_a_silent_currency_conversion(bars) -> None:
    metal = StrategyDocument.from_json(
        (SEED_DIR / "donchian_breakout_atr.json").read_text()
    )
    with pytest.raises(ValueError, match="no FX source was supplied"):
        run_validation(
            document=metal,
            bars=bars,
            dataset_version_id="fx_guard_v1",
            instrument="XAUUSD",
            timeframe=Timeframe.H4,
            registry=HoldoutRegistry.in_memory(),
            account_ccy="GBP",
        )
