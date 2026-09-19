"""Bonferroni / Holm / BH, and the effective trial count."""
from __future__ import annotations

import numpy as np
import pytest

from fiboki.stats.multiple_testing import (
    benjamini_hochberg,
    bonferroni,
    effective_trials_by_clustering,
    holm,
)
from fiboki.stats.sharpe import expected_max_sharpe

# Benjamini & Hochberg (1995) worked example, Table 1: 15 hypotheses.
BH_1995 = np.array(
    [
        0.0001,
        0.0004,
        0.0019,
        0.0095,
        0.0201,
        0.0278,
        0.0298,
        0.0344,
        0.0459,
        0.3240,
        0.4262,
        0.5719,
        0.6528,
        0.7590,
        1.0000,
    ]
)


class TestKnownAnswers:
    @pytest.mark.golden
    def test_benjamini_hochberg_1995_worked_example(self) -> None:
        """The paper's own example: at q=0.05 BH rejects the first FOUR hypotheses
        (p <= 0.0095), where Bonferroni, at 0.05/15 = 0.0033, rejects only three."""
        bh = benjamini_hochberg(BH_1995, alpha=0.05)
        assert bh.rejected_indices == (0, 1, 2, 3)
        assert bh.threshold == pytest.approx(0.0095)
        assert bonferroni(BH_1995, alpha=0.05).rejected_indices == (0, 1, 2)
        assert holm(BH_1995, alpha=0.05).rejected_indices == (0, 1, 2)

    @pytest.mark.golden
    def test_hand_computed_adjustments(self) -> None:
        p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.5])
        m = 7
        assert bonferroni(p).adjusted_pvalues[0] == pytest.approx(0.001 * m)
        # Holm: (m - i) * p_(i), made monotone.
        assert holm(p).adjusted_pvalues[1] == pytest.approx(0.008 * 6)
        # BH: p_(i) * m / i, made monotone from the top down.
        assert benjamini_hochberg(p).adjusted_pvalues[1] == pytest.approx(0.008 * 7 / 2)

    def test_single_hypothesis_is_unchanged(self) -> None:
        for fn in (bonferroni, holm, benjamini_hochberg):
            assert fn(np.array([0.03])).adjusted_pvalues[0] == pytest.approx(0.03)

    def test_v1_scale_bonferroni_threshold(self) -> None:
        """23,040 tests at alpha=0.05 needs p <= 2.2e-6 per test."""
        p = np.full(23_040, 0.04)
        p[0] = 1e-7
        result = bonferroni(p, alpha=0.05)
        assert result.n_rejected == 1
        assert result.rejected_indices == (0,)


class TestOrdering:
    @pytest.mark.parametrize("seed", range(10))
    def test_power_ordering_bonferroni_holm_bh(self, seed: int) -> None:
        """Bonferroni's rejections are a subset of Holm's, which are a subset of
        BH's. Anything else is a bug in one of the three."""
        p = np.random.default_rng(seed).beta(0.3, 3.0, 200)
        b = set(bonferroni(p).rejected_indices)
        h = set(holm(p).rejected_indices)
        f = set(benjamini_hochberg(p).rejected_indices)
        assert b <= h <= f

    @pytest.mark.parametrize("seed", range(5))
    def test_adjusted_pvalues_never_shrink(self, seed: int) -> None:
        p = np.random.default_rng(seed).uniform(0, 1, 100)
        for fn in (bonferroni, holm, benjamini_hochberg):
            assert np.all(fn(p).adjusted_pvalues >= p - 1e-12)

    @pytest.mark.parametrize("fn", [bonferroni, holm, benjamini_hochberg])
    def test_adjusted_pvalues_are_monotone_in_the_raw_ordering(self, fn) -> None:
        p = np.random.default_rng(1).uniform(0, 1, 60)
        adj = fn(p).adjusted_pvalues
        order = np.argsort(p)
        assert np.all(np.diff(adj[order]) >= -1e-12)

    def test_false_discovery_rate_is_controlled_on_pure_noise(self) -> None:
        """With all nulls true, FDR control collapses to FWER control, so BH should
        flag something in about 5% of panels - not in most of them."""
        flagged = [
            benjamini_hochberg(np.random.default_rng(s).uniform(0, 1, 200)).n_rejected > 0
            for s in range(100)
        ]
        assert sum(flagged) / len(flagged) <= 0.12

    def test_bh_finds_real_effects_that_bonferroni_misses(self) -> None:
        rng = np.random.default_rng(0)
        p = np.concatenate([rng.uniform(0, 0.001, 20), rng.uniform(0, 1, 480)])
        assert benjamini_hochberg(p).n_rejected > bonferroni(p).n_rejected


class TestValidation:
    @pytest.mark.parametrize("fn", [bonferroni, holm, benjamini_hochberg])
    def test_rejects_out_of_range(self, fn) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            fn(np.array([0.1, 1.4]))

    @pytest.mark.parametrize("fn", [bonferroni, holm, benjamini_hochberg])
    def test_rejects_empty_and_nan(self, fn) -> None:
        with pytest.raises(ValueError):
            fn(np.array([]))
        with pytest.raises(ValueError, match="non-finite"):
            fn(np.array([0.1, np.nan]))


class TestEffectiveTrials:
    @staticmethod
    def factor_matrix(n_factors: int, per_factor: int, seed: int = 0, noise: float = 0.15):
        rng = np.random.default_rng(seed)
        factors = rng.standard_normal((600, n_factors))
        cols = [
            factors[:, f] + noise * rng.standard_normal(600)
            for f in range(n_factors)
            for _ in range(per_factor)
        ]
        return np.column_stack(cols)

    @pytest.mark.parametrize(("n_factors", "per_factor"), [(4, 5), (3, 10), (6, 4), (1, 12)])
    def test_recovers_the_number_of_independent_families(
        self, n_factors: int, per_factor: int
    ) -> None:
        e = effective_trials_by_clustering(self.factor_matrix(n_factors, per_factor))
        assert e.n_effective == n_factors
        assert e.n_trials == n_factors * per_factor

    def test_independent_trials_are_all_effective(self) -> None:
        m = np.random.default_rng(3).standard_normal((600, 25))
        assert effective_trials_by_clustering(m).n_effective == 25

    def test_identical_columns_collapse_to_one(self) -> None:
        col = np.random.default_rng(4).standard_normal(300)
        m = np.column_stack([col] * 8)
        assert effective_trials_by_clustering(m).n_effective == 1

    def test_redundancy_is_reported(self) -> None:
        e = effective_trials_by_clustering(self.factor_matrix(4, 5))
        assert e.redundancy == pytest.approx(1.0 - 4 / 20)

    def test_threshold_controls_granularity(self) -> None:
        m = self.factor_matrix(4, 5, noise=0.6)
        loose = effective_trials_by_clustering(m, corr_threshold=0.2).n_effective
        strict = effective_trials_by_clustering(m, corr_threshold=0.95).n_effective
        assert loose <= strict

    def test_constant_columns_do_not_crash(self) -> None:
        m = np.random.default_rng(5).standard_normal((200, 6))
        m[:, 2] = 1.0
        e = effective_trials_by_clustering(m)
        assert 1 <= e.n_effective <= 6

    def test_bounds(self) -> None:
        m = self.factor_matrix(3, 6)
        e = effective_trials_by_clustering(m)
        assert 1 <= e.n_effective <= e.n_trials
        assert e.labels.size == e.n_trials

    @pytest.mark.golden
    def test_naive_correlation_adjustment_would_be_catastrophic(self) -> None:
        """The arithmetic quoted in the docstring, asserted so it stays true.

        N/(1+(M-1)*rho) at M=23,040 collapses the trial count to single digits,
        which would cut the false-discovery Sharpe threshold from 2.03 to about
        0.7 and wave through exactly what this library exists to stop.
        """
        m, rho_bar = 23_040, 0.30
        naive = m / (1.0 + (m - 1) * rho_bar)
        assert naive < 4.0
        assert expected_max_sharpe(max(2, int(naive)), 0.25) < 0.8
        assert expected_max_sharpe(m, 0.25) == pytest.approx(2.03, abs=0.01)

    def test_rejects_bad_input(self) -> None:
        with pytest.raises(ValueError, match="2-D"):
            effective_trials_by_clustering(np.zeros(10))
        with pytest.raises(ValueError, match="at least 3 periods"):
            effective_trials_by_clustering(np.zeros((2, 4)))
        with pytest.raises(ValueError, match="corr_threshold"):
            effective_trials_by_clustering(np.random.default_rng(0).standard_normal((50, 3)), corr_threshold=1.0)
