"""Block bootstrap, automatic block length, and the compounding ruin simulation."""
from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from fiboki.stats.bootstrap import (
    _block_length_from_moments,
    bootstrap_confidence_interval,
    compare_sizing_modes,
    iid_bootstrap_indices,
    moving_block_bootstrap,
    moving_block_bootstrap_indices,
    optimal_block_length,
    resample_fixed_size,
    resample_with_compounding,
    stationary_bootstrap,
    stationary_bootstrap_indices,
)


def ar1(n: int, rho: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n)
    x = np.empty(n)
    x[0] = e[0]
    for i in range(1, n):
        x[i] = rho * x[i - 1] + e[i]
    return x


class TestIndexDraws:
    @pytest.mark.parametrize("n_obs", [10, 137, 500])
    def test_stationary_shape_and_range(self, n_obs: int) -> None:
        idx = stationary_bootstrap_indices(n_obs, 5.0, 20, rng=0)
        assert idx.shape == (20, n_obs)
        assert idx.min() >= 0 and idx.max() < n_obs

    def test_moving_block_shape_and_range(self) -> None:
        idx = moving_block_bootstrap_indices(137, 10, 20, rng=0)
        assert idx.shape == (20, 137)
        assert idx.min() >= 0 and idx.max() < 137

    def test_block_length_one_is_iid(self) -> None:
        """With mean block length 1 every step starts a new block."""
        idx = stationary_bootstrap_indices(50, 1.0, 200, rng=3)
        consecutive = (np.diff(idx, axis=1) == 1).mean()
        assert consecutive < 0.1

    def test_long_blocks_preserve_order(self) -> None:
        idx = stationary_bootstrap_indices(200, 50.0, 100, rng=3)
        consecutive = (np.diff(idx, axis=1) == 1).mean()
        assert consecutive > 0.9

    def test_moving_block_is_contiguous_within_blocks(self) -> None:
        idx = moving_block_bootstrap_indices(60, 6, 5, rng=1)
        first = idx[0, :6]
        assert np.all(np.diff(first) == 1) or first[-1] < first[0]  # wraps at the end

    def test_stationary_draws_every_observation_uniformly(self) -> None:
        """The stationary bootstrap is stationary: no observation is under-drawn,
        unlike the non-circular moving block, whose edges are."""
        idx = stationary_bootstrap_indices(20, 4.0, 4000, rng=5)
        counts = np.bincount(idx.ravel(), minlength=20) / idx.size
        assert counts.max() / counts.min() < 1.25

    def test_iid_indices(self) -> None:
        idx = iid_bootstrap_indices(30, 10, rng=1)
        assert idx.shape == (10, 30)

    @pytest.mark.parametrize("bad", [0.0, 0.5, -1.0])
    def test_rejects_block_length_below_one(self, bad: float) -> None:
        with pytest.raises(ValueError):
            stationary_bootstrap_indices(10, bad, 5, rng=0)


class TestResampling:
    def test_stationary_preserves_length_and_values(self) -> None:
        x = ar1(300, 0.5)
        out = stationary_bootstrap(x, n_boot=50, block_length=8.0, rng=0)
        assert out.shape == (50, 300)
        assert set(np.unique(out)).issubset(set(np.unique(x)))

    def test_moving_block_preserves_length(self) -> None:
        x = ar1(300, 0.5)
        assert moving_block_bootstrap(x, n_boot=25, block_length=10, rng=0).shape == (25, 300)

    def test_block_bootstrap_retains_serial_correlation(self) -> None:
        """The reason blocks exist: an iid bootstrap destroys autocorrelation,
        which is what understates risk on trading series."""
        x = ar1(2000, 0.7, seed=2)

        def lag1(v: np.ndarray) -> float:
            return float(np.corrcoef(v[:-1], v[1:])[0, 1])

        blocked = np.mean([lag1(s) for s in stationary_bootstrap(x, 40, 40.0, rng=1)])
        shuffled = np.mean([lag1(s) for s in stationary_bootstrap(x, 40, 1.0, rng=1)])
        assert blocked > 0.5
        assert abs(shuffled) < 0.1


class TestOptimalBlockLength:
    def test_scaling_in_n_is_exactly_one_third(self) -> None:
        """b* = (2g^2/D)^(1/3) * n^(1/3): with the moments held fixed the block
        length must scale as n^(1/3) exactly."""
        b_small = _block_length_from_moments(3.0, 1.5, 1_000)
        b_large = _block_length_from_moments(3.0, 1.5, 8_000)
        assert b_large / b_small == pytest.approx(2.0)
        assert _block_length_from_moments(3.0, 1.5, 27_000) / b_small == pytest.approx(3.0)

    @pytest.mark.slow
    def test_empirical_scaling_is_near_one_third(self) -> None:
        x = ar1(200_000, 0.6, seed=7)
        ns = [500, 2_000, 8_000, 32_000, 128_000]
        bs = [optimal_block_length(x[:n]).stationary for n in ns]
        slope = float(np.polyfit(np.log(ns), np.log(bs), 1)[0])
        assert 0.25 < slope < 0.45
        assert all(a < b for a, b in pairwise(bs))

    def test_independent_series_gets_block_length_one(self) -> None:
        x = np.random.default_rng(1).standard_normal(2_000)
        est = optimal_block_length(x)
        assert est.stationary == 1.0
        assert est.m_hat == 0

    def test_more_persistence_means_longer_blocks(self) -> None:
        weak = optimal_block_length(ar1(4_000, 0.2, seed=3)).stationary
        strong = optimal_block_length(ar1(4_000, 0.8, seed=3)).stationary
        assert strong > weak

    def test_circular_and_stationary_differ_by_the_published_constant(self) -> None:
        """D_SB = 2*g(0)^2 and D_CB = (4/3)*g(0)^2 (Patton et al. 2009), so the
        circular block length is (3/2)^(1/3) times the stationary one."""
        est = optimal_block_length(ar1(4_000, 0.6, seed=4))
        assert est.circular / est.stationary == pytest.approx(1.5 ** (1 / 3), rel=1e-9)

    def test_capped_at_n_over_three(self) -> None:
        est = optimal_block_length(ar1(400, 0.98, seed=5))
        assert est.stationary <= np.ceil(min(3 * np.sqrt(400), 400 / 3))

    def test_constant_series(self) -> None:
        assert optimal_block_length(np.ones(100)).stationary == 1.0

    def test_too_short(self) -> None:
        with pytest.raises(ValueError, match="at least 8"):
            optimal_block_length(np.arange(5.0))


class TestConfidenceIntervals:
    def test_brackets_the_true_mean(self) -> None:
        x = np.random.default_rng(0).normal(0.5, 1.0, 1_000)
        ci = bootstrap_confidence_interval(x, np.mean, n_boot=500, rng=0)
        assert ci.lower < 0.5 < ci.upper
        assert ci.point == pytest.approx(float(x.mean()))
        assert ci.width > 0

    def test_percentile_and_basic_agree_for_a_symmetric_statistic(self) -> None:
        x = np.random.default_rng(1).normal(0.0, 1.0, 800)
        pct = bootstrap_confidence_interval(x, np.mean, n_boot=800, method="percentile", rng=2)
        basic = bootstrap_confidence_interval(x, np.mean, n_boot=800, method="basic", rng=2)
        assert pct.width == pytest.approx(basic.width)
        assert pct.lower == pytest.approx(basic.lower, abs=0.05)

    def test_excludes_zero_flag(self) -> None:
        x = np.random.default_rng(2).normal(2.0, 0.5, 500)
        assert bootstrap_confidence_interval(x, np.mean, n_boot=400, rng=1).excludes_zero
        y = np.random.default_rng(3).normal(0.0, 1.0, 500)
        assert not bootstrap_confidence_interval(y, np.mean, n_boot=400, rng=1).excludes_zero

    def test_dependence_widens_the_interval(self) -> None:
        """An iid bootstrap on a dependent series reports a narrower interval than
        it should - the exact direction of error V1 kept making."""
        x = ar1(1_500, 0.8, seed=9)
        naive = bootstrap_confidence_interval(x, np.mean, n_boot=500, scheme="iid", rng=4)
        blocked = bootstrap_confidence_interval(x, np.mean, n_boot=500, scheme="stationary", rng=4)
        assert blocked.width > naive.width

    @pytest.mark.parametrize("scheme", ["stationary", "moving", "iid"])
    def test_all_schemes_run(self, scheme: str) -> None:
        x = ar1(500, 0.4, seed=1)
        ci = bootstrap_confidence_interval(x, np.median, n_boot=200, scheme=scheme, rng=0)
        assert ci.distribution.size == 200

    def test_rejects_unknown_method_and_scheme(self) -> None:
        x = ar1(100, 0.2)
        with pytest.raises(ValueError, match="unknown method"):
            bootstrap_confidence_interval(x, np.mean, n_boot=10, method="bca", rng=0)
        with pytest.raises(ValueError, match="unknown scheme"):
            bootstrap_confidence_interval(x, np.mean, n_boot=10, scheme="wild", rng=0)


class TestRuinSimulation:
    """The V1 defect: a Monte Carlo that freezes position size.

    The claims here are deliberately scoped.  Against a PEAK-RELATIVE barrier at a
    realistic risk fraction, frozen sizing understates ruin every time.  It is NOT
    a universal ordering, and the last test in this class pins the exception so
    nobody later mistakes the frozen number for a conservative bound.
    """

    @staticmethod
    def r_sample(seed: int = 0, win_rate: float = 0.40, payoff: float = 3.0) -> np.ndarray:
        """R-multiples: losers lose exactly 1R, winners are uniform on [1, payoff]."""
        rng = np.random.default_rng(seed)
        wins = rng.random(200) < win_rate
        return np.where(wins, rng.uniform(1.0, payoff, 200), -1.0)

    @pytest.mark.parametrize("seed", [0, 1, 2, 3])
    @pytest.mark.parametrize(("win_rate", "payoff"), [(0.30, 4.0), (0.40, 3.0), (0.50, 2.0)])
    @pytest.mark.parametrize("risk", [0.05, 0.10])
    def test_frozen_sizing_understates_peak_relative_ruin(
        self, seed: int, win_rate: float, payoff: float, risk: float
    ) -> None:
        r = self.r_sample(seed=seed, win_rate=win_rate, payoff=payoff)
        comp, fixed = compare_sizing_modes(
            r, n_paths=1_500, risk_fraction=risk, ruin_basis="peak", rng=seed + 200
        )
        assert comp.ruin_probability >= fixed.ruin_probability

    @pytest.mark.parametrize("seed", [0, 1, 2, 3])
    @pytest.mark.parametrize("risk", [0.01, 0.02, 0.05, 0.10])
    def test_frozen_sizing_understates_the_median_drawdown(self, seed: int, risk: float) -> None:
        """For a genuinely positive-expectancy sample, the equity path grows, and a
        frozen stake then risks a shrinking percentage of it."""
        r = self.r_sample(seed=seed, win_rate=0.45, payoff=2.5)
        comp, fixed = compare_sizing_modes(r, n_paths=1_500, risk_fraction=risk, rng=seed + 300)
        assert float(np.median(comp.max_drawdown)) > float(np.median(fixed.max_drawdown))

    def test_the_gap_is_material_at_a_realistic_risk_setting(self) -> None:
        """Not a rounding error: at 5% risk per trade the two answers are far
        apart, and the frozen one is what V1 gated promotions on."""
        r = self.r_sample(seed=1, win_rate=0.40)
        comp, fixed = compare_sizing_modes(r, n_paths=4_000, risk_fraction=0.05, rng=11)
        assert comp.ruin_probability - fixed.ruin_probability > 0.05

    def test_frozen_is_not_a_bound_in_either_direction(self) -> None:
        """At 1% risk the ordering reverses: a frozen stake never de-risks inside a
        drawdown, so early-drawdown paths ruin it FIRST.  Asserted, not hidden."""
        r = self.r_sample(seed=0, win_rate=0.35, payoff=3.0)
        comp, fixed = compare_sizing_modes(
            r, n_paths=4_000, risk_fraction=0.01, ruin_basis="initial", rng=5
        )
        assert comp.ruin_probability < fixed.ruin_probability

    def test_all_losing_sequence_ruins_every_path(self) -> None:
        sim = resample_with_compounding(
            np.full(80, -1.0), n_paths=50, risk_fraction=0.2, ruin_fraction=0.5, rng=0
        )
        assert sim.ruin_probability == 1.0

    def test_ruin_is_absorbing(self) -> None:
        sim = resample_with_compounding(
            np.full(80, -1.0),
            n_paths=20,
            risk_fraction=0.2,
            ruin_fraction=0.5,
            rng=0,
            keep_paths=True,
        )
        assert sim.equity_paths is not None
        for path in sim.equity_paths:
            hit = np.flatnonzero(path <= 50_000.0)
            if hit.size:
                assert np.allclose(path[hit[0] :], path[hit[0]])

    def test_no_ruin_for_a_strong_edge_at_small_risk(self) -> None:
        r = np.where(np.arange(200) % 2 == 0, 2.0, -1.0)
        sim = resample_with_compounding(r, n_paths=500, risk_fraction=0.005, rng=0)
        assert sim.ruin_probability == 0.0
        assert sim.quantile(0.5) > 100_000.0

    def test_drawdown_distribution_is_reported(self) -> None:
        sim = resample_with_compounding(self.r_sample(), n_paths=500, risk_fraction=0.02, rng=3)
        assert sim.max_drawdown.shape == (500,)
        assert np.all((sim.max_drawdown >= 0.0) & (sim.max_drawdown <= 1.0))
        assert sim.drawdown_quantile(0.95) >= sim.drawdown_quantile(0.5)

    def test_modes_and_basis_are_labelled(self) -> None:
        r = self.r_sample()
        assert resample_with_compounding(r, n_paths=10, rng=0).mode == "compounding"
        assert resample_fixed_size(r, n_paths=10, rng=0).mode == "fixed"
        assert resample_with_compounding(r, n_paths=10, rng=0).ruin_basis == "peak"

    def test_rejects_unknown_basis(self) -> None:
        with pytest.raises(ValueError, match="ruin_basis"):
            resample_with_compounding(self.r_sample(), n_paths=5, ruin_basis="wishful", rng=0)

    def test_deterministic_for_a_fixed_seed(self) -> None:
        r = self.r_sample()
        a = resample_with_compounding(r, n_paths=200, risk_fraction=0.05, rng=99)
        b = resample_with_compounding(r, n_paths=200, risk_fraction=0.05, rng=99)
        assert np.array_equal(a.final_equity, b.final_equity)
