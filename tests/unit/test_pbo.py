"""CSCV / PBO: the noise-versus-signal discrimination the whole library rests on."""
from __future__ import annotations

import numpy as np
import pytest

from fiboki.stats.pbo import (
    combinatorially_symmetric_cv,
    n_cscv_combinations,
    pbo_null_expectation,
    probability_of_backtest_overfitting,
    sharpe_columns,
)


def _noise(n_obs: int = 800, n_trials: int = 25, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal((n_obs, n_trials)) * 0.01


class TestCombinationCount:
    def test_default_splits_give_the_published_count(self) -> None:
        """S=16 -> C(16,8) = 12,870 combinations, as in Bailey et al. (2017)."""
        assert n_cscv_combinations(16) == 12_870
        assert combinatorially_symmetric_cv(_noise()).n_combinations == 12_870

    @pytest.mark.parametrize(("s", "expected"), [(4, 6), (6, 20), (8, 70), (10, 252), (12, 924)])
    def test_smaller_split_counts(self, s: int, expected: int) -> None:
        assert n_cscv_combinations(s) == expected

    @pytest.mark.parametrize("bad", [3, 5, 2, 0])
    def test_rejects_odd_or_tiny_splits(self, bad: int) -> None:
        with pytest.raises(ValueError):
            n_cscv_combinations(bad)


class TestPBODiscrimination:
    """Pure noise must land on the null; a real edge must not."""

    def test_pure_noise_averages_to_a_coin_flip(self) -> None:
        pbos = [combinatorially_symmetric_cv(_noise(n_trials=100, seed=s)).pbo for s in range(12)]
        assert float(np.mean(pbos)) == pytest.approx(0.5, abs=0.08)

    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5])
    def test_single_noise_realisation_stays_near_the_null(self, seed: int) -> None:
        """Wide band on purpose: a SINGLE PBO estimate has sd ~0.13 at these
        dimensions, which is itself a reason never to gate on one run."""
        pbo = combinatorially_symmetric_cv(_noise(n_trials=100, seed=seed)).pbo
        assert 0.15 < pbo < 0.85

    def test_null_expectation_is_exact_arithmetic(self) -> None:
        assert pbo_null_expectation(100) == 0.5
        assert pbo_null_expectation(5) == pytest.approx(0.6)
        assert pbo_null_expectation(2) == 0.5

    def test_one_genuinely_superior_column_gives_low_pbo(self) -> None:
        matrix = _noise(seed=1)
        matrix[:, 7] += 0.01 * 0.25  # a real 0.25-sigma per-period edge
        result = combinatorially_symmetric_cv(matrix)
        assert result.pbo < 0.05
        assert not result.is_overfit
        assert result.probability_of_loss < 0.05

    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
    def test_superior_column_gives_low_pbo_across_seeds(self, seed: int) -> None:
        matrix = _noise(seed=seed)
        matrix[:, 7] += 0.01 * 0.3
        assert combinatorially_symmetric_cv(matrix).pbo < 0.1

    def test_superior_column_is_the_one_selected(self) -> None:
        matrix = _noise(seed=2)
        matrix[:, 3] += 0.01 * 0.3
        result = combinatorially_symmetric_cv(matrix)
        chosen, counts = np.unique(result.is_best_index, return_counts=True)
        assert chosen[int(np.argmax(counts))] == 3

    def test_signal_beats_noise_on_pbo(self) -> None:
        """The discriminating comparison, on the SAME underlying noise."""
        base = _noise(seed=11)
        noisy = combinatorially_symmetric_cv(base).pbo
        signal = base.copy()
        signal[:, 0] += 0.01 * 0.25
        assert combinatorially_symmetric_cv(signal).pbo < noisy


class TestPBOStructure:
    def test_logit_and_rank_consistency(self) -> None:
        r = combinatorially_symmetric_cv(_noise(seed=3))
        assert r.relative_ranks.shape == (r.n_combinations,)
        assert np.all((r.relative_ranks > 0.0) & (r.relative_ranks < 1.0))
        assert r.pbo == pytest.approx(float(np.mean(r.relative_ranks <= 0.5)))
        assert np.all(np.isfinite(r.logits))
        assert r.pbo == pytest.approx(float(np.mean(r.logits <= 0.0)))

    def test_degradation_slope_reported(self) -> None:
        """Under pure noise, in-sample improvement buys nothing out of sample, so
        the slope of OOS-on-IS for the winner is at or below zero."""
        r = combinatorially_symmetric_cv(_noise(seed=5))
        assert r.degradation_slope <= 0.0
        assert 0.0 <= r.degradation_r2 <= 1.0

    def test_column_permutation_invariance(self) -> None:
        matrix = _noise(seed=6)
        perm = np.random.default_rng(0).permutation(matrix.shape[1])
        a = combinatorially_symmetric_cv(matrix)
        b = combinatorially_symmetric_cv(matrix[:, perm])
        assert a.pbo == b.pbo
        assert np.allclose(np.sort(a.relative_ranks), np.sort(b.relative_ranks))

    def test_convenience_wrapper_matches_the_full_result(self) -> None:
        matrix = _noise(n_obs=200, n_trials=8, seed=13)
        assert probability_of_backtest_overfitting(matrix, n_splits=6) == (
            combinatorially_symmetric_cv(matrix, n_splits=6).pbo
        )

    def test_summary_keys(self) -> None:
        s = combinatorially_symmetric_cv(_noise(seed=7)).summary()
        assert set(s) == {
            "pbo",
            "median_logit",
            "degradation_slope",
            "degradation_r2",
            "probability_of_loss",
            "n_combinations",
        }


class TestCustomPerformance:
    def test_custom_metric_matches_analytic_sharpe_path(self) -> None:
        """The fast moment-based path and the explicit callable path must agree."""
        matrix = _noise(n_obs=200, n_trials=8, seed=8)
        fast = combinatorially_symmetric_cv(matrix, n_splits=6)
        slow = combinatorially_symmetric_cv(matrix, n_splits=6, performance=sharpe_columns)
        assert fast.pbo == pytest.approx(slow.pbo)
        assert np.allclose(fast.is_performance, slow.is_performance)
        assert np.allclose(fast.oos_performance, slow.oos_performance)

    def test_custom_metric_can_be_total_return(self) -> None:
        matrix = _noise(n_obs=200, n_trials=6, seed=10)
        matrix[:, 2] += 0.01 * 0.4
        r = combinatorially_symmetric_cv(
            matrix, n_splits=6, performance=lambda block: block.sum(axis=0)
        )
        assert r.pbo < 0.2


class TestValidation:
    def test_needs_two_trials(self) -> None:
        with pytest.raises(ValueError, match="at least 2 trials"):
            combinatorially_symmetric_cv(np.random.default_rng(0).standard_normal((100, 1)))

    def test_needs_enough_rows_per_block(self) -> None:
        with pytest.raises(ValueError, match="2 rows per block"):
            combinatorially_symmetric_cv(np.zeros((10, 4)) + np.arange(4), n_splits=16)

    def test_rejects_nan(self) -> None:
        matrix = _noise(n_obs=100, n_trials=4)
        matrix[3, 2] = np.nan
        with pytest.raises(ValueError, match="non-finite"):
            combinatorially_symmetric_cv(matrix, n_splits=6)

    def test_rejects_1d(self) -> None:
        with pytest.raises(ValueError, match="2-D"):
            combinatorially_symmetric_cv(np.zeros(100))
