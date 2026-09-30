"""The ladder's E-2 candidate metrics: computed beside the audited ones, never instead.

Every metric here is an input a CANDIDATE gate set (``GATE_SET_V2_1_CANDIDATES``)
may read. The audited ``GATE_SET_V2`` reads none of them, so its verdicts cannot
move; what these tests pin is that each new number is the number its docstring
says it is, and that "cannot be computed" arrives as ``None`` (NOT_EVALUATED,
which blocks) rather than as a value.
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np
import pandas as pd
import pytest

from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    ParameterGrid,
    WindowEvaluation,
)
from fiboki.validation.gates import GATE_SET_V2, GATE_SET_V2_1_CANDIDATES, GateStatus
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import (
    MIN_TRL_FLOOR_TRADES,
    PLATEAU_GATE_NAMES,
    DeflationRung,
    LadderConfig,
    LadderContext,
    RobustnessRung,
    SanityRung,
    ValidationLadder,
    WalkForwardRung,
    sanity_trade_floor,
)
from tests.validation_fixtures import (
    DATA_END,
    DATA_START,
    DATASET_VERSION,
    candidate,
    genuine_edge_evaluator,
)

Z95 = 1.6448536269514722
CONFIG = LadderConfig(selection_metric="net_profit", stress_samples=20, spa_bootstraps=200)
OPENING = 10_000.0


def _ctx(evaluator, cand: Candidate | None = None, config: LadderConfig = CONFIG) -> LadderContext:
    registry = HoldoutRegistry.in_memory()
    segment = registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    return LadderContext(
        candidate=cand or candidate("unit", "u" * 64),
        evaluator=evaluator,
        segment=segment,
        config=config,
        registry=registry,
    )


def _trade_basis(pnls: list[float]):
    """An evaluator returning the same per-trade P&L series for any call."""

    def evaluate(params, window: DateWindow) -> WindowEvaluation:
        arr = np.asarray(pnls, dtype=float)
        return WindowEvaluation(
            window=window, params=dict(params), n_trades=int(arr.size),
            net_profit=float(arr.sum()) if arr.size else 0.0, returns=arr,
            returns_basis="trade",
        )

    return evaluate


@dataclasses.dataclass
class WithOpeningEquity:
    """Records an equity base, as every EngineEvaluator run does."""

    inner: object

    def __call__(self, params, window):
        ev = self.inner(params, window)
        return dataclasses.replace(ev, meta={**ev.meta, "opening_equity": OPENING})


# --------------------------------------------------------------------------
# a. min_trl_95 and n_trades_over_min_trl (rung 0)
# --------------------------------------------------------------------------


class TestMinTRL:
    def test_hand_computed_below_the_floor(self) -> None:
        """400 trades alternating +1.1 / -1.0: a symmetric two-point distribution.

        mean 0.05; population sd 1.05, so sample sd 1.05 * sqrt(400/399) =
        1.0513150; SR = 0.05 / 1.0513150 = 0.0475595. Skew 0, kurtosis 1, so the
        variance term is 1 + 0 * SR^2 = 1 and
        MinTRL = 1 + (1.6448536 / 0.0475595)^2 = 1 + 1196.13 = 1197.13;
        400 / max(1197.13, 150) = 0.33413.
        """
        ctx = _ctx(_trade_basis([1.1, -1.0] * 200))
        SanityRung().run(ctx)
        sr = 0.05 / (1.05 * math.sqrt(400 / 399))
        expected = 1.0 + (Z95 / sr) ** 2
        assert expected == pytest.approx(1197.13, abs=0.01)
        assert ctx.gate_values["min_trl_95"] == pytest.approx(expected, rel=1e-9)
        assert ctx.gate_values["n_trades_over_min_trl"] == pytest.approx(400 / expected, rel=1e-9)

    def test_the_150_trade_floor_binds_for_a_strong_edge(self) -> None:
        """200 trades alternating +2 / -1: SR = 0.5 / (1.5 * sqrt(200/199)) = 0.332501,
        MinTRL = 1 + (1.6448536 / 0.332501)^2 = 25.47 < 150, so the ratio is 200 / 150."""
        ctx = _ctx(_trade_basis([2.0, -1.0] * 100))
        result = SanityRung().run(ctx)
        sr = 0.5 / (1.5 * math.sqrt(200 / 199))
        assert ctx.gate_values["min_trl_95"] == pytest.approx(1.0 + (Z95 / sr) ** 2, rel=1e-9)
        assert ctx.gate_values["min_trl_95"] < MIN_TRL_FLOOR_TRADES
        assert ctx.gate_values["n_trades_over_min_trl"] == pytest.approx(200 / 150)
        assert result.metrics["min_trl_basis"]["floor_trades"] == 150
        assert result.metrics["n_trades_over_min_trl"] == ctx.gate_values["n_trades_over_min_trl"]

    @pytest.mark.parametrize(
        ("pnls", "why"),
        [
            ([1.0, -1.1] * 200, "per-trade Sharpe <= 0"),
            ([0.5], "1 trades"),
            ([1.0, 1.0, 1.0], "zero dispersion"),
        ],
    )
    def test_undefined_is_none_and_blocks(self, pnls, why) -> None:
        ctx = _ctx(_trade_basis(pnls))
        result = SanityRung().run(ctx)
        assert ctx.gate_values["min_trl_95"] is None
        assert ctx.gate_values["n_trades_over_min_trl"] is None
        assert why in result.metrics["min_trl_basis"]["undefined"]
        gate = GATE_SET_V2_1_CANDIDATES["c_min_trl"].by_name("min_track_record")
        assert gate.evaluate(ctx.gate_values["n_trades_over_min_trl"]).status is (
            GateStatus.NOT_EVALUATED
        )

    def test_period_returns_without_trades_have_no_per_trade_series(self) -> None:
        def evaluate(params, window):
            return WindowEvaluation(
                window=window, params=dict(params), n_trades=500, net_profit=10.0,
                returns=np.linspace(-1, 2, 50), returns_basis="period",
            )

        ctx = _ctx(evaluate)
        SanityRung().run(ctx)
        assert ctx.gate_values["n_trades_over_min_trl"] is None


class TestTheTradeFloorFollowsTheGateSet:
    def test_the_audited_set_keeps_400(self) -> None:
        assert sanity_trade_floor(GATE_SET_V2) == 400

    @pytest.mark.parametrize("name", ["c_min_trl", "c_all"])
    def test_a_min_trl_set_uses_the_metric_floor(self, name) -> None:
        gates = GATE_SET_V2_1_CANDIDATES[name]
        assert sanity_trade_floor(gates) == MIN_TRL_FLOOR_TRADES == 150
        ValidationLadder(config=LadderConfig(min_trades=150), gate_set=gates)
        with pytest.raises(ValueError, match="must equal"):
            ValidationLadder(config=LadderConfig(min_trades=400), gate_set=gates)

    @pytest.mark.parametrize("name", ["c_wfe_log", "c_hit_wilson", "c_plateau_median", "c_dsr_family"])
    def test_the_other_candidates_keep_the_audited_trade_gate(self, name) -> None:
        gates = GATE_SET_V2_1_CANDIDATES[name]
        assert sanity_trade_floor(gates) == 400
        with pytest.raises(ValueError, match="disagrees"):
            ValidationLadder(config=LadderConfig(min_trades=150), gate_set=gates)


# --------------------------------------------------------------------------
# b, c. walk-forward log-growth WFE, min OOS trades, Wilson hit rate (rung 2)
# --------------------------------------------------------------------------


def _days(window: dict) -> float:
    return (pd.Timestamp(window["end"]) - pd.Timestamp(window["start"])).total_seconds() / 86_400


def _wilson(k: int, n: int) -> float:
    p, z2 = k / n, Z95**2
    return (p + z2 / (2 * n) - Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n)


class TestWalkForwardCandidates:
    def test_hand_computed_from_the_recorded_folds(self) -> None:
        """WFE_log = 100 * mean_f[ln(1 + oos_f / E0) / oos_days_f] / mean_f[ln(1 + is_f / E0) / is_days_f]."""
        ctx = _ctx(WithOpeningEquity(genuine_edge_evaluator()), config=dataclasses.replace(
            CONFIG, walk_forward_folds=3))
        metrics = WalkForwardRung().run(ctx).metrics
        folds = metrics["folds"]
        is_r = [math.log1p(f["is_net_profit"] / OPENING) / _days(f["train"]) for f in folds]
        oos_r = [math.log1p(f["oos_net_profit"] / OPENING) / _days(f["test"]) for f in folds]
        expected = 100.0 * np.mean(oos_r) / np.mean(is_r)
        assert ctx.gate_values["walk_forward_efficiency_log_growth"] == pytest.approx(expected, rel=1e-9)
        # Every fold here had an equity base, so the audited WFE is on the same basis.
        assert metrics["walk_forward_efficiency_basis"] == ["log_growth_per_day"]
        assert ctx.gate_values["walk_forward_efficiency"] == pytest.approx(expected, rel=1e-9)

        assert ctx.gate_values["walk_forward_min_oos_trades"] == min(f["oos_trades"] for f in folds)
        k = sum(f["oos_net_profit"] > 0 for f in folds)
        assert metrics["oos_profitable_windows"] == k
        assert ctx.gate_values["oos_profitable_fraction_wilson_lower"] == pytest.approx(
            _wilson(k, 3), abs=1e-12
        )
        # The audited metric is untouched.
        assert ctx.gate_values["oos_profitable_fraction"] == pytest.approx(k / 3)

    def test_no_equity_base_means_no_log_growth_wfe(self) -> None:
        """Strictly log growth: no money-per-day fallback, so NOT_EVALUATED, which blocks.
        The audited WFE still falls back and is still computed."""
        ctx = _ctx(genuine_edge_evaluator(), config=dataclasses.replace(CONFIG, walk_forward_folds=2))
        metrics = WalkForwardRung().run(ctx).metrics
        assert metrics["walk_forward_efficiency_basis"] == ["profit_per_day"]
        assert math.isfinite(ctx.gate_values["walk_forward_efficiency"])
        assert ctx.gate_values["walk_forward_efficiency_log_growth"] is None
        results = GATE_SET_V2_1_CANDIDATES["c_wfe_log"].evaluate(ctx.gate_values)
        status = {r.gate.name: r.status for r in results}
        assert status["walk_forward_efficiency_log_growth"] is GateStatus.NOT_EVALUATED
        assert status["walk_forward_min_oos_trades"] is GateStatus.PASS

    def test_the_min_oos_trade_gate_binds_before_the_wfe_gate(self) -> None:
        """Declared first, so when both block the binding constraint names the trade floor."""
        gates = GATE_SET_V2_1_CANDIDATES["c_wfe_log"]
        values = {
            "n_trades": 900.0, "walk_forward_min_oos_trades": 12.0,
            "walk_forward_efficiency_log_growth": 10.0,
        }
        binding = gates.binding_constraint(gates.evaluate(values))
        assert binding.gate.name == "walk_forward_min_oos_trades"
        assert binding.status is GateStatus.FAIL


# --------------------------------------------------------------------------
# d. plateau neighbourhood median ratio and minimum (rung 4)
# --------------------------------------------------------------------------


class TestPlateauCandidates:
    def test_every_plateau_reading_gate_goes_not_applicable_together(self) -> None:
        plateau_metrics = {
            "point_plateau_ratio", "plateau_neighbourhood_median_ratio", "plateau_neighbourhood_min",
        }
        for gates in (GATE_SET_V2, *GATE_SET_V2_1_CANDIDATES.values()):
            for gate in gates.gates:
                if gate.metric in plateau_metrics:
                    assert gate.name in PLATEAU_GATE_NAMES, (gates.version, gate.name)

    def test_a_two_point_grid_has_no_plateau(self) -> None:
        grid = ParameterGrid.from_axes({"fast": (5, 10), "slow": (40,)})
        cand = Candidate("unit", "p" * 64, default_params={"fast": 5, "slow": 40}, grid=grid)
        ctx = _ctx(genuine_edge_evaluator(), cand)
        ctx.scratch["sweep"] = ctx.sweep(ctx.research_window)
        ctx.scratch["selected_full"] = ctx.evaluate(cand.default_params, ctx.research_window)
        RobustnessRung().run(ctx)
        assert set(PLATEAU_GATE_NAMES) <= ctx.not_applicable
        assert ctx.gate_values["plateau_neighbourhood_median_ratio"] is None
        results = GATE_SET_V2_1_CANDIDATES["c_plateau_median"].evaluate(
            ctx.gate_values, not_applicable=frozenset(ctx.not_applicable)
        )
        status = {r.gate.name: r.status for r in results}
        assert status["plateau_neighbourhood_median"] is GateStatus.NOT_APPLICABLE
        assert status["plateau_neighbourhood_min"] is GateStatus.NOT_APPLICABLE

    def test_nan_becomes_none_and_blocks(self) -> None:
        gates = GATE_SET_V2_1_CANDIDATES["c_plateau_median"]
        for name in ("plateau_neighbourhood_median", "plateau_neighbourhood_min"):
            assert gates.by_name(name).evaluate(None).status is GateStatus.NOT_EVALUATED


# --------------------------------------------------------------------------
# Full ladder: plateau consistency, e. family-N DSR, serialisation
# --------------------------------------------------------------------------


def _run(external: int = 0, gate_set=GATE_SET_V2):
    registry = HoldoutRegistry.in_memory()
    registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    config = dataclasses.replace(CONFIG, external_trial_count=external)
    return ValidationLadder(config=config, gate_set=gate_set).run(
        candidate("synthetic", f"{external:064d}"), WithOpeningEquity(genuine_edge_evaluator()),
        registry=registry, dataset_version_id=DATASET_VERSION, actor="agent:test-harness",
    )


@pytest.fixture(scope="module")
def edge_report():
    return _run()


class TestOnTheFullLadder:
    def test_the_audited_gate_set_reads_none_of_the_new_metrics(self, edge_report) -> None:
        assert [g.gate.name for g in edge_report.gate_results] == [g.name for g in GATE_SET_V2.gates]
        assert edge_report.gate_set_fingerprint == GATE_SET_V2.fingerprint()

    def test_the_plateau_candidates_match_their_definition(self, edge_report) -> None:
        plateau = edge_report.rung(4).metrics["plateau"]
        assert plateau["plateau_neighbourhood_median_ratio"] == pytest.approx(
            plateau["plateau_median_excluding"] / plateau["score"]
        )
        assert plateau["plateau_neighbourhood_min"] <= plateau["plateau_median_excluding"]
        # A genuine plateau: both candidate conditions hold.
        assert plateau["plateau_neighbourhood_median_ratio"] >= 0.6
        assert plateau["plateau_neighbourhood_min"] > 0

    def test_the_family_n_is_what_this_ladder_performed(self, edge_report) -> None:
        """25 grid points + 5 walk-forward fits + C(6, 2) = 15 purged-CV re-selections = 45."""
        family = edge_report.rung(5).metrics["n_trials_family"]
        assert (family["grid_points"], family["walk_forward_fits"], family["purged_cv_fits"]) == (
            25, 5, 15
        )
        assert family["n_used"] == 45

    def test_equal_n_gives_the_audited_dsr_exactly(self, edge_report) -> None:
        """The two DSRs share every input but N. Charging the audited DSR exactly the
        family's N (clustered N_eff + external = 45) must reproduce the family DSR."""
        n_eff = edge_report.rung(5).metrics["n_effective_trials"]
        matched = _run(external=45 - n_eff)
        m = matched.rung(5).metrics
        assert m["n_trials_used_for_deflation"] == 45
        assert m["deflated_sharpe_ratio_family_n"] == pytest.approx(
            m["deflated_sharpe_ratio"], abs=1e-15
        )
        assert m["deflated_sharpe_ratio_family_n"] == pytest.approx(
            edge_report.rung(5).metrics["deflated_sharpe_ratio_family_n"], abs=1e-15
        )

    def test_a_campaign_sized_n_deflates_harder_than_the_family(self) -> None:
        """At E-1's 20,896 external trials the family-N DSR is the LARGER number:
        charging only the candidate's own family understates N."""
        m = _run(external=20_896).rung(5).metrics
        assert m["deflated_sharpe_ratio_family_n"] > m["deflated_sharpe_ratio"]

    def test_new_metrics_are_serialised_like_the_existing_ones(self, edge_report) -> None:
        from fiboki.validation.report import ValidationReport

        new = {
            "min_trl_95": 0, "n_trades_over_min_trl": 0,
            "walk_forward_efficiency_log_growth": 2, "walk_forward_min_oos_trades": 2,
            "oos_profitable_fraction_wilson_lower": 2,
            "deflated_sharpe_ratio_family_n": 5,
        }
        for name, rung in new.items():
            assert name in edge_report.provenance_labels
            assert name in edge_report.rung(rung).metrics
        for name in ("plateau_neighbourhood_median_ratio", "plateau_neighbourhood_min"):
            assert name in edge_report.provenance_labels
            assert name in edge_report.rung(4).metrics["plateau"]
        restored = ValidationReport.from_json(edge_report.to_json())
        assert restored.to_json() == edge_report.to_json()

    def test_c_all_runs_end_to_end(self) -> None:
        registry = HoldoutRegistry.in_memory()
        registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
        gates = GATE_SET_V2_1_CANDIDATES["c_all"]
        report = ValidationLadder(
            config=dataclasses.replace(CONFIG, min_trades=sanity_trade_floor(gates)), gate_set=gates
        ).run(
            candidate("synthetic", "c" * 64), WithOpeningEquity(genuine_edge_evaluator()),
            registry=registry, dataset_version_id=DATASET_VERSION, actor="agent:test-harness",
        )
        assert report.gate_set_version == "v2.1.0-candidate:c_all"
        assert [g.gate.name for g in report.gate_results] == [g.name for g in gates.gates]
        assert all(g.status is not GateStatus.NOT_EVALUATED for g in report.gate_results), [
            g.describe() for g in report.gate_results
        ]


def test_deflation_without_a_trials_matrix_fails_closed_for_the_family_dsr_too() -> None:
    ctx = _ctx(genuine_edge_evaluator())
    DeflationRung().run(ctx)
    assert ctx.gate_values["deflated_sharpe_ratio"] is None
    assert ctx.gate_values["deflated_sharpe_ratio_family_n"] is None
