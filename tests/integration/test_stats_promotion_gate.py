"""End-to-end: the statistical gate a candidate must survive.

Two candidates go through the whole sequence from ``fiboki.stats``:

* a V1-style "winner" - the best of a large search over pure noise, which is
  exactly what a 23,040-cell leaderboard with no correction produces;
* a strategy with a small but genuine edge.

The gate must reject the first and pass the second.  If this test ever inverts,
the library is no longer doing its job.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason
from fiboki.stats import (
    CombinatorialPurgedCV,
    LabelSpans,
    analyse_parameter_stability,
    combinatorially_symmetric_cv,
    compare_sizing_modes,
    deflated_sharpe_from_trials,
    effective_trials_by_clustering,
    net_profit,
    run_stress_suite,
    sharpe_moments,
    superior_predictive_ability,
)
from fiboki.stats.stability import full_grid

N_PERIODS = 1_000
N_TRIALS = 60


def _edge_trades(n: int = 200, seed: int = 4) -> list[Trade]:
    """Closed trades with a modest genuine edge: 45% winners at +200, rest -120."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2022-01-03 08:00", tz="UTC")
    trades = []
    for i, pnl in enumerate(np.where(rng.random(n) < 0.45, 200.0, -120.0)):
        entry = start + pd.Timedelta(hours=i)
        trades.append(
            Trade(
                instrument="EURUSD",
                direction=Direction.LONG,
                size=1.0,
                entry_price=1.1,
                exit_price=1.1 + pnl / 100_000.0,
                entry_time=entry,
                exit_time=entry + pd.Timedelta(hours=4),
                exit_reason=ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS,
                gross_pnl=float(pnl) + 2.0,
                spread_cost=2.0,
                commission=0.0,
                slippage_cost=0.0,
                financing_cost=0.0,
                net_pnl=float(pnl),
                account_ccy="GBP",
            )
        )
    return trades


def search_over_noise(seed: int = 0) -> np.ndarray:
    """A correlated search: 12 strategy families x 5 near-duplicate variants."""
    rng = np.random.default_rng(seed)
    families = rng.standard_normal((N_PERIODS, 12)) * 0.01
    return np.column_stack(
        [families[:, f] + 0.002 * rng.standard_normal(N_PERIODS) for f in range(12) for _ in range(5)]
    )


class TestFalsePositiveIsRejected:
    """The best cell of a noise search must fail the gate at every stage."""

    @pytest.fixture(scope="class")
    def search(self) -> np.ndarray:
        return search_over_noise(seed=3)

    def test_effective_trials_are_far_below_the_raw_count(self, search) -> None:
        eff = effective_trials_by_clustering(search)
        assert eff.n_trials == N_TRIALS
        assert eff.n_effective == 12  # the true number of families
        assert eff.redundancy == pytest.approx(0.8)

    def test_the_winner_fails_the_deflated_sharpe(self, search) -> None:
        trial_sharpes = np.array([sharpe_moments(search[:, k]).sr_hat for k in range(N_TRIALS)])
        best = int(np.argmax(trial_sharpes))
        moments = sharpe_moments(search[:, best])
        dsr = deflated_sharpe_from_trials(
            moments.sr_hat,
            moments.n_obs,
            trial_sharpes,
            skew=moments.skew,
            kurtosis=moments.kurtosis,
            n_effective_trials=effective_trials_by_clustering(search).n_effective,
        )
        assert dsr < 0.95

    def test_selection_does_not_generalise(self, search) -> None:
        assert combinatorially_symmetric_cv(search).pbo > 0.15

    def test_no_strategy_beats_the_benchmark(self, search) -> None:
        assert superior_predictive_ability(search, n_boot=500, rng=1).p_consistent > 0.05


class TestGenuineEdgeSurvives:
    @pytest.fixture(scope="class")
    def search(self) -> np.ndarray:
        matrix = search_over_noise(seed=5)
        matrix[:, 17] += 0.01 * 0.25
        return matrix

    def test_deflated_sharpe_clears(self, search) -> None:
        trial_sharpes = np.array([sharpe_moments(search[:, k]).sr_hat for k in range(N_TRIALS)])
        moments = sharpe_moments(search[:, int(np.argmax(trial_sharpes))])
        dsr = deflated_sharpe_from_trials(
            moments.sr_hat,
            moments.n_obs,
            trial_sharpes,
            skew=moments.skew,
            kurtosis=moments.kurtosis,
            n_effective_trials=effective_trials_by_clustering(search).n_effective,
        )
        assert dsr > 0.95

    def test_selection_generalises(self, search) -> None:
        assert combinatorially_symmetric_cv(search).pbo < 0.10

    def test_spa_finds_it(self, search) -> None:
        result = superior_predictive_ability(search, n_boot=500, rng=2)
        assert result.p_consistent < 0.05
        assert result.best_index == 17


class TestPipelineComposes:
    """The remaining stages run on real objects and agree with each other."""

    def test_purged_paths_and_stress_and_stability_and_ruin(self) -> None:
        rng = np.random.default_rng(9)
        returns = rng.standard_normal(600) * 0.01 + 0.0015

        spans = LabelSpans(starts=np.arange(600.0), ends=np.arange(600.0) + 3.0)
        cv = CombinatorialPurgedCV(6, 2, spans=spans, embargo_pct=0.01)
        path_returns = []
        for path in cv.path_map():
            idx = np.concatenate([cv.group_indices(g) for _, g in path])
            path_returns.append(float(returns[np.sort(idx)].sum()))
        assert len(path_returns) == 5
        assert min(path_returns) <= np.mean(path_returns) <= max(path_returns)

        trades = _edge_trades(n=200, seed=4)
        curves = run_stress_suite(trades, n_samples=50, rng=0)
        assert curves["spread_multiplier"].median[0] == pytest.approx(net_profit(trades))
        assert curves["spread_multiplier"].median[-1] < curves["spread_multiplier"].median[0]

        grid = full_grid({"a": [1, 2, 3, 4, 5], "b": [1, 2, 3, 4, 5]})
        scores = [1.0 if 2 <= g["a"] <= 4 and 2 <= g["b"] <= 4 else 0.0 for g in grid]
        scores[grid.index({"a": 5, "b": 5})] = 2.0
        report = analyse_parameter_stability(grid, scores)
        assert report.best_by_score.params != report.best_by_plateau.params

        r_multiples = np.array([t.net_pnl / 120.0 for t in trades])
        comp, fixed = compare_sizing_modes(r_multiples, n_paths=2_000, risk_fraction=0.05, rng=6)
        assert comp.ruin_probability >= fixed.ruin_probability
