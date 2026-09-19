"""Property-based tests for the statistical validation library.

These assert invariants that must hold for EVERY admissible input, not just the
hand-picked ones.  Where a property is only conditionally true (PSR monotonicity
is the important case) the condition is encoded in the strategy and explained,
rather than being asserted globally and silently violated.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from fiboki.stats.bootstrap import (
    moving_block_bootstrap_indices,
    optimal_block_length,
    stationary_bootstrap,
    stationary_bootstrap_indices,
)
from fiboki.stats.cv import CombinatorialPurgedCV, LabelSpans, PurgedKFold, n_backtest_paths
from fiboki.stats.multiple_testing import (
    benjamini_hochberg,
    bonferroni,
    effective_trials_by_clustering,
    holm,
)
from fiboki.stats.pbo import combinatorially_symmetric_cv, pbo_null_expectation
from fiboki.stats.sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
)
from fiboki.stats.stability import analyse_parameter_stability, full_grid

SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

finite_floats = st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False)
pvalues = st.lists(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=1, max_size=120
)


# ------------------------------------------------------------------ sharpe


class TestSharpeProperties:
    @SETTINGS
    @given(
        sr_a=st.floats(min_value=0.0, max_value=3.0),
        gap=st.floats(min_value=1e-4, max_value=3.0),
        n_obs=st.integers(min_value=5, max_value=5000),
        benchmark=st.floats(min_value=0.0, max_value=1.0),
        kurtosis=st.floats(min_value=1.0, max_value=20.0),
    )
    def test_psr_is_monotone_in_the_observed_sharpe(
        self, sr_a: float, gap: float, n_obs: int, benchmark: float, kurtosis: float
    ) -> None:
        """Strictly increasing in SR-hat, for zero skew and a non-negative
        benchmark.

        The restriction is real, not a convenience: d/dSR of the PSR z-score is
        proportional to ``1 + SR* * (g4-1)/4 * SR``, so with a NEGATIVE benchmark
        or strong negative skew the mapping genuinely turns over at large SR.  The
        property is stated where it holds.
        """
        assume(kurtosis >= 1.0)
        low = probabilistic_sharpe_ratio(sr_a, n_obs, 0.0, kurtosis, benchmark)
        high = probabilistic_sharpe_ratio(sr_a + gap, n_obs, 0.0, kurtosis, benchmark)
        assert high >= low - 1e-12

    @SETTINGS
    @given(
        sr=st.floats(min_value=-3.0, max_value=3.0),
        n_obs=st.integers(min_value=2, max_value=10_000),
        skew=st.floats(min_value=-2.0, max_value=2.0),
        extra_kurt=st.floats(min_value=0.0, max_value=15.0),
        benchmark=finite_floats,
    )
    def test_psr_is_a_probability(
        self, sr: float, n_obs: int, skew: float, extra_kurt: float, benchmark: float
    ) -> None:
        kurtosis = 1.0 + skew**2 + extra_kurt  # always an admissible moment set
        try:
            p = probabilistic_sharpe_ratio(sr, n_obs, skew, kurtosis, benchmark)
        except ValueError:
            return  # variance term non-positive: refused rather than fudged
        assert 0.0 <= p <= 1.0

    @SETTINGS
    @given(
        sr=st.floats(min_value=-2.0, max_value=2.0),
        n_obs=st.integers(min_value=2, max_value=5000),
        b_low=st.floats(min_value=-1.0, max_value=1.0),
        gap=st.floats(min_value=1e-3, max_value=2.0),
    )
    def test_psr_decreases_in_the_benchmark(
        self, sr: float, n_obs: int, b_low: float, gap: float
    ) -> None:
        """Always true, for any admissible moments: a higher bar is harder."""
        assert probabilistic_sharpe_ratio(sr, n_obs, 0.0, 3.0, b_low) >= (
            probabilistic_sharpe_ratio(sr, n_obs, 0.0, 3.0, b_low + gap) - 1e-12
        )

    @SETTINGS
    @given(
        n_low=st.integers(min_value=2, max_value=10_000),
        factor=st.integers(min_value=2, max_value=100),
        variance=st.floats(min_value=0.0, max_value=4.0),
    )
    def test_expected_max_sharpe_increases_with_trials(
        self, n_low: int, factor: int, variance: float
    ) -> None:
        assert expected_max_sharpe(n_low * factor, variance) >= expected_max_sharpe(
            n_low, variance
        ) - 1e-12

    @SETTINGS
    @given(
        n_trials=st.integers(min_value=2, max_value=100_000),
        variance=st.floats(min_value=1e-6, max_value=4.0),
        scale=st.floats(min_value=0.1, max_value=10.0),
    )
    def test_expected_max_sharpe_scales_with_the_standard_deviation(
        self, n_trials: int, variance: float, scale: float
    ) -> None:
        base = expected_max_sharpe(n_trials, variance)
        scaled = expected_max_sharpe(n_trials, variance * scale**2)
        assert scaled == pytest.approx(scale * base, rel=1e-9)

    @SETTINGS
    @given(
        sr=st.floats(min_value=0.05, max_value=3.0),
        n_obs=st.integers(min_value=10, max_value=5000),
        n_trials=st.integers(min_value=2, max_value=50_000),
        variance=st.floats(min_value=1e-6, max_value=2.0),
    )
    def test_deflated_never_exceeds_undeflated(
        self, sr: float, n_obs: int, n_trials: int, variance: float
    ) -> None:
        """Deflation can only ever cost confidence - the threshold it tests against
        is non-negative for any N >= 2."""
        assert deflated_sharpe_ratio(sr, n_obs, n_trials, variance) <= (
            probabilistic_sharpe_ratio(sr, n_obs) + 1e-12
        )

    @SETTINGS
    @given(
        sr=st.floats(min_value=0.01, max_value=2.0),
        benchmark=st.floats(min_value=0.0, max_value=1.0),
        confidence=st.floats(min_value=0.5, max_value=0.999),
    )
    def test_min_track_record_length_is_at_least_one(
        self, sr: float, benchmark: float, confidence: float
    ) -> None:
        n = minimum_track_record_length(sr, benchmark, 0.0, 3.0, confidence)
        assert n >= 1.0
        assert (n == math.inf) == (sr <= benchmark)


# --------------------------------------------------------------- bootstrap


class TestBootstrapProperties:
    @SETTINGS
    @given(
        n_obs=st.integers(min_value=2, max_value=200),
        block=st.floats(min_value=1.0, max_value=50.0),
        n_boot=st.integers(min_value=1, max_value=20),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
    )
    def test_stationary_bootstrap_preserves_length_and_support(
        self, n_obs: int, block: float, n_boot: int, seed: int
    ) -> None:
        idx = stationary_bootstrap_indices(n_obs, block, n_boot, rng=seed)
        assert idx.shape == (n_boot, n_obs)
        assert idx.min() >= 0
        assert idx.max() < n_obs

    @SETTINGS
    @given(
        n_obs=st.integers(min_value=2, max_value=200),
        block=st.integers(min_value=1, max_value=60),
        n_boot=st.integers(min_value=1, max_value=20),
        seed=st.integers(min_value=0, max_value=2**32 - 1),
    )
    def test_moving_block_preserves_length_and_support(
        self, n_obs: int, block: int, n_boot: int, seed: int
    ) -> None:
        idx = moving_block_bootstrap_indices(n_obs, block, n_boot, rng=seed)
        assert idx.shape == (n_boot, n_obs)
        assert idx.min() >= 0
        assert idx.max() < n_obs

    @SETTINGS
    @given(
        data=st.lists(finite_floats, min_size=2, max_size=120),
        seed=st.integers(min_value=0, max_value=10_000),
    )
    def test_resamples_only_ever_contain_original_values(
        self, data: list[float], seed: int
    ) -> None:
        arr = np.array(data)
        out = stationary_bootstrap(arr, n_boot=5, block_length=3.0, rng=seed)
        assert out.shape == (5, arr.size)
        assert np.isin(out, arr).all()

    @SETTINGS
    @given(data=st.lists(finite_floats, min_size=8, max_size=300))
    def test_block_length_is_within_its_documented_bounds(self, data: list[float]) -> None:
        arr = np.array(data)
        assume(float(arr.std()) > 1e-9)
        est = optimal_block_length(arr)
        upper = math.ceil(min(3.0 * math.sqrt(arr.size), arr.size / 3.0))
        assert 1.0 <= est.stationary <= upper
        assert 1.0 <= est.circular <= upper


# --------------------------------------------------------------------- pbo


class TestPBOProperties:
    @SETTINGS
    @given(
        seed=st.integers(min_value=0, max_value=5_000),
        n_trials=st.integers(min_value=2, max_value=12),
        n_splits=st.sampled_from([4, 6, 8]),
    )
    def test_pbo_is_invariant_to_column_permutation(
        self, seed: int, n_trials: int, n_splits: int
    ) -> None:
        """Trials have no natural order; relabelling them cannot change the answer."""
        rng = np.random.default_rng(seed)
        matrix = rng.standard_normal((120, n_trials))
        perm = rng.permutation(n_trials)
        a = combinatorially_symmetric_cv(matrix, n_splits=n_splits)
        b = combinatorially_symmetric_cv(matrix[:, perm], n_splits=n_splits)
        assert a.pbo == b.pbo
        assert np.allclose(np.sort(a.relative_ranks), np.sort(b.relative_ranks))

    @SETTINGS
    @given(
        seed=st.integers(min_value=0, max_value=5_000),
        n_trials=st.integers(min_value=2, max_value=10),
        scale=st.floats(min_value=0.01, max_value=100.0),
    )
    def test_pbo_is_invariant_to_a_common_positive_rescaling(
        self, seed: int, n_trials: int, scale: float
    ) -> None:
        """Sharpe is scale-free, so expressing returns in pips or percent cannot
        change the overfitting verdict."""
        matrix = np.random.default_rng(seed).standard_normal((120, n_trials))
        assert combinatorially_symmetric_cv(matrix, n_splits=6).pbo == (
            combinatorially_symmetric_cv(matrix * scale, n_splits=6).pbo
        )

    @SETTINGS
    @given(
        seed=st.integers(min_value=0, max_value=5_000),
        n_trials=st.integers(min_value=2, max_value=12),
    )
    def test_pbo_and_its_by_products_are_in_range(self, seed: int, n_trials: int) -> None:
        matrix = np.random.default_rng(seed).standard_normal((120, n_trials))
        r = combinatorially_symmetric_cv(matrix, n_splits=6)
        assert 0.0 <= r.pbo <= 1.0
        assert 0.0 <= r.probability_of_loss <= 1.0
        assert 0.0 <= r.degradation_r2 <= 1.0
        assert r.n_combinations == 20

    @SETTINGS
    @given(n_trials=st.integers(min_value=2, max_value=500))
    def test_null_expectation_brackets_one_half(self, n_trials: int) -> None:
        null = pbo_null_expectation(n_trials)
        assert 0.5 <= null <= 0.5 + 1.0 / (2 * n_trials)
        if n_trials % 2 == 0:
            assert null == 0.5


# ------------------------------------------------------------ multiple tests


class TestMultipleTestingProperties:
    @SETTINGS
    @given(p=pvalues, alpha=st.floats(min_value=0.001, max_value=0.5))
    def test_rejection_sets_are_nested(self, p: list[float], alpha: float) -> None:
        arr = np.array(p)
        b = set(bonferroni(arr, alpha).rejected_indices)
        h = set(holm(arr, alpha).rejected_indices)
        f = set(benjamini_hochberg(arr, alpha).rejected_indices)
        assert b <= h <= f

    @SETTINGS
    @given(p=pvalues)
    def test_adjustment_never_reduces_a_p_value(self, p: list[float]) -> None:
        arr = np.array(p)
        for fn in (bonferroni, holm, benjamini_hochberg):
            adj = fn(arr).adjusted_pvalues
            assert np.all(adj >= arr - 1e-12)
            assert np.all(adj <= 1.0 + 1e-12)

    @SETTINGS
    @given(p=pvalues, seed=st.integers(min_value=0, max_value=10_000))
    def test_corrections_are_permutation_equivariant(self, p: list[float], seed: int) -> None:
        arr = np.array(p)
        perm = np.random.default_rng(seed).permutation(arr.size)
        for fn in (bonferroni, holm, benjamini_hochberg):
            assert np.allclose(fn(arr).adjusted_pvalues[perm], fn(arr[perm]).adjusted_pvalues)

    @SETTINGS
    @given(
        seed=st.integers(min_value=0, max_value=5_000),
        n_trials=st.integers(min_value=1, max_value=30),
    )
    def test_effective_trials_are_bounded_by_the_trial_count(
        self, seed: int, n_trials: int
    ) -> None:
        matrix = np.random.default_rng(seed).standard_normal((80, n_trials))
        e = effective_trials_by_clustering(matrix)
        assert 1 <= e.n_effective <= n_trials
        assert 0.0 <= e.redundancy < 1.0


# ---------------------------------------------------------------------- cv


class TestCrossValidationProperties:
    @SETTINGS
    @given(
        n_samples=st.integers(min_value=20, max_value=200),
        n_splits=st.integers(min_value=2, max_value=6),
        hold=st.integers(min_value=1, max_value=15),
        embargo=st.floats(min_value=0.0, max_value=0.1),
    )
    def test_purged_kfold_never_leaks_an_overlapping_label(
        self, n_samples: int, n_splits: int, hold: int, embargo: float
    ) -> None:
        """THE invariant: no training label may still be open during the test
        window, for any sample size, fold count, holding period or embargo."""
        assume(n_samples >= n_splits)
        starts = np.arange(float(n_samples))
        spans = LabelSpans(starts=starts, ends=starts + hold)
        for split in PurgedKFold(n_splits, spans=spans, embargo_pct=embargo).split():
            assert not set(split.train.tolist()) & set(split.test.tolist())
            if split.train.size == 0:
                continue
            t0 = spans.starts[split.test].min()
            t1 = spans.ends[split.test].max()
            assert not np.any((spans.starts[split.train] <= t1) & (spans.ends[split.train] >= t0))

    @SETTINGS
    @given(
        n_groups=st.integers(min_value=3, max_value=8),
        k=st.integers(min_value=1, max_value=4),
        hold=st.integers(min_value=1, max_value=8),
    )
    def test_combinatorial_paths_cover_the_sample_exactly_once(
        self, n_groups: int, k: int, hold: int
    ) -> None:
        assume(k < n_groups)
        starts = np.arange(120.0)
        spans = LabelSpans(starts=starts, ends=starts + hold)
        cv = CombinatorialPurgedCV(n_groups, k, spans=spans)
        assert cv.n_paths == n_backtest_paths(n_groups, k)
        for path in cv.path_map():
            covered = np.concatenate([cv.group_indices(g) for _, g in path])
            assert sorted(covered.tolist()) == list(range(120))

    @SETTINGS
    @given(
        n_groups=st.integers(min_value=2, max_value=25),
        k=st.integers(min_value=1, max_value=24),
    )
    def test_path_count_matches_the_binomial_identity(self, n_groups: int, k: int) -> None:
        assume(k < n_groups)
        assert n_backtest_paths(n_groups, k) == math.comb(n_groups - 1, k - 1)


# --------------------------------------------------------------- stability


class TestStabilityProperties:
    @SETTINGS
    @given(
        value=st.floats(min_value=-100.0, max_value=100.0),
        size=st.integers(min_value=2, max_value=6),
    )
    def test_a_flat_surface_has_no_peaks_and_unit_ratios(
        self, value: float, size: int
    ) -> None:
        grid = full_grid({"a": list(range(size)), "b": list(range(size))})
        report = analyse_parameter_stability(grid, [value] * len(grid))
        assert not report.isolated_peaks
        for p in report.points:
            assert p.plateau_mean == pytest.approx(value)
            assert p.plateau_std == pytest.approx(0.0)

    @SETTINGS
    @given(
        seed=st.integers(min_value=0, max_value=5_000),
        size=st.integers(min_value=3, max_value=7),
        shift=st.floats(min_value=-50.0, max_value=50.0),
    )
    def test_plateau_quality_is_translation_equivariant(
        self, seed: int, size: int, shift: float
    ) -> None:
        """Adding a constant to every score shifts every plateau level by the same
        constant and cannot reorder the ranking."""
        grid = full_grid({"a": list(range(size)), "b": list(range(size))})
        scores = np.random.default_rng(seed).normal(size=len(grid))
        base = analyse_parameter_stability(grid, scores)
        moved = analyse_parameter_stability(grid, scores + shift)
        for a, b in zip(base.points, moved.points, strict=True):
            assert b.plateau_quality == pytest.approx(a.plateau_quality + shift, abs=1e-9)
        assert base.best_by_plateau.params == moved.best_by_plateau.params
