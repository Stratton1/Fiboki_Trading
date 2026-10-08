"""RUNG 2 must actually fit, then transfer the fit. V1's did neither.

V1's "walk-forward" evaluated one FIXED parameter set on a train window and then
on a test window. No parameter was ever selected, so nothing about selection was
measured and the procedure could not detect overfitting -- it never overfitted
anything.

The evaluator here is built so the two procedures give opposite answers:

* the DEFAULT parameters have a small, genuine, constant edge, so running them
  fixed on every test window is profitable in every fold;
* every other parameterisation belongs to a regime that has ALREADY ENDED by the
  time the test window starts, so a procedure that selects on train and
  evaluates that selection on test loses in every fold.

If the ladder ever passes this candidate, rung 2 has silently reverted to the V1
procedure.
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from fiboki.validation.evaluation import params_key
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder
from fiboki.validation.report import RungOutcome, Verdict
from tests.validation_fixtures import (
    DATA_END,
    DATA_START,
    DATASET_VERSION,
    DEFAULT_PARAMS,
    candidate,
    genuine_edge_evaluator,
    overfit_trap_evaluator,
)

CONFIG = LadderConfig(selection_metric="net_profit", stress_samples=20, spa_bootstraps=200)


@dataclasses.dataclass
class _WithOpeningEquity:
    """Records an equity base, as every EngineEvaluator run does.

    The production gate set (``v2.1.0-calibrated``) reads walk-forward
    efficiency on LOG GROWTH, which needs an equity base and has no
    money-per-day fallback; the synthetic evaluator records none on its own.
    """

    inner: object

    def __call__(self, params, window):
        ev = self.inner(params, window)
        return dataclasses.replace(ev, meta={**ev.meta, "opening_equity": 10_000.0})


def _run(evaluator, content_hash: str, config: LadderConfig = CONFIG):
    registry = HoldoutRegistry.in_memory()
    registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    report = ValidationLadder(config=config).run(
        candidate("moving_regime", content_hash),
        _WithOpeningEquity(evaluator),
        registry=registry,
        dataset_version_id=DATASET_VERSION,
        actor="agent:test",
    )
    return report, registry


@pytest.fixture(scope="module")
def trap():
    evaluator = overfit_trap_evaluator()
    report, _ = _run(evaluator, "c" * 64)
    return report, evaluator


class TestTheV1ProcedureWouldHavePassedThisStrategy:
    """The contrast figures rung 2 records are the V1 answer, computed alongside."""

    def test_fixed_parameters_are_profitable_in_every_test_window(self, trap) -> None:
        report, _ = trap
        folds = report.rung(2).metrics["folds"]
        assert all(f["fixed_param_oos_net_profit"] > 0 for f in folds)

    def test_the_v1_style_hit_rate_would_have_cleared_the_gate(self, trap) -> None:
        report, _ = trap
        metrics = report.rung(2).metrics
        assert metrics["CONTRAST_fixed_parameter_hit_rate"] >= 0.6
        assert metrics["CONTRAST_fixed_parameter_oos_rate"] > 0.0


class TestTrueWalkForwardCatchesIt:
    def test_the_candidate_is_rejected_at_rung_2(self, trap) -> None:
        report, _ = trap
        assert report.verdict is Verdict.REJECT
        assert report.rung(2).outcome is RungOutcome.FAIL
        assert report.binding_constraint.rung_index == 2

    def test_the_binding_constraint_is_walk_forward_efficiency(self, trap) -> None:
        """Under the production set the WFE gate is the log-growth one (E-2)."""
        report, _ = trap
        assert report.binding_constraint.name == "walk_forward_efficiency_log_growth"
        assert report.binding_constraint.observed < 50.0
        assert report.binding_constraint.shortfall > 0.0

    def test_the_selected_parameters_lose_in_every_test_window(self, trap) -> None:
        report, _ = trap
        folds = report.rung(2).metrics["folds"]
        assert all(f["oos_net_profit"] < 0 for f in folds)
        assert report.rung(2).metrics["oos_profitable_fraction"] == 0.0

    def test_every_fold_was_profitable_in_sample_before_it_was_selected(self, trap) -> None:
        """Otherwise the rung would be catching a bad strategy, not overfitting."""
        report, _ = trap
        assert all(f["is_net_profit"] > 0 for f in report.rung(2).metrics["folds"])

    def test_the_two_procedures_disagree_in_every_single_fold(self, trap) -> None:
        report, _ = trap
        for fold in report.rung(2).metrics["folds"]:
            assert fold["oos_net_profit"] < 0 < fold["fixed_param_oos_net_profit"]


class TestTheSelectionIsGenuinelyTransferred:
    def test_each_fold_selects_something_other_than_the_defaults(self, trap) -> None:
        report, _ = trap
        default = params_key(DEFAULT_PARAMS)
        selected = [params_key(f["selected_params"]) for f in report.rung(2).metrics["folds"]]
        assert all(s != default for s in selected)

    def test_the_selected_parameterisation_is_the_one_run_on_the_test_window(
        self, trap
    ) -> None:
        """Proved from the evaluator's own call log, not from the rung's report."""
        report, evaluator = trap
        for fold in report.rung(2).metrics["folds"]:
            key = params_key(fold["selected_params"])
            test_window = fold["test"]["name"]
            assert (key, test_window) in evaluator.calls

    def test_the_folds_disagree_about_the_optimum(self, trap) -> None:
        """A surface whose optimum moves is exactly what this rung exists to find."""
        agreement = report_agreement(trap[0])
        assert agreement["n_distinct_selections"] > 1
        assert agreement["modal_share"] < 1.0

    def test_the_train_window_never_reaches_into_the_test_window(self, trap) -> None:
        report, _ = trap
        for fold in report.rung(2).metrics["folds"]:
            assert fold["train"]["end"] <= fold["test"]["start"]


def report_agreement(report):
    return report.rung(2).metrics["parameter_stability_across_folds"]


class TestAStableSurfacePasses:
    """The positive control: rung 2 must not simply reject everything."""

    @pytest.fixture(scope="class")
    def stable(self):
        report, _ = _run(genuine_edge_evaluator(), "a" * 64)
        return report

    def test_rung_2_passes(self, stable) -> None:
        assert stable.rung(2).outcome is RungOutcome.PASS

    def test_efficiency_and_hit_rate_clear_the_audit_gates(self, stable) -> None:
        metrics = stable.rung(2).metrics
        assert metrics["walk_forward_efficiency"] >= GATE_SET_V2.by_name(
            "walk_forward_efficiency"
        ).threshold
        assert metrics["oos_profitable_fraction"] >= GATE_SET_V2.by_name(
            "oos_window_hit_rate"
        ).threshold


class TestSchemes:
    def test_anchored_train_windows_grow_and_rolling_ones_do_not(self) -> None:
        anchored, _ = _run(
            genuine_edge_evaluator(),
            "d" * 64,
            LadderConfig(
                selection_metric="net_profit",
                walk_forward_scheme="anchored",
                stress_samples=20,
                spa_bootstraps=200,
            ),
        )
        rolling, _ = _run(
            genuine_edge_evaluator(),
            "e" * 64,
            LadderConfig(
                selection_metric="net_profit",
                walk_forward_scheme="rolling",
                stress_samples=20,
                spa_bootstraps=200,
            ),
        )

        def train_days(report):
            return [
                (
                    pd.Timestamp(f["train"]["end"]) - pd.Timestamp(f["train"]["start"])
                ).total_seconds()
                / 86_400.0
                for f in report.rung(2).metrics["folds"]
            ]

        anchored_days = train_days(anchored)
        rolling_days = train_days(rolling)
        assert anchored_days == sorted(anchored_days)
        assert anchored_days[-1] > anchored_days[0]
        assert max(rolling_days) - min(rolling_days) < 2.0

    def test_an_unknown_scheme_is_refused(self) -> None:
        with pytest.raises(ValueError, match="anchored"):
            LadderConfig(walk_forward_scheme="hopeful")
