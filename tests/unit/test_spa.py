"""Hansen's SPA, White's Reality Check and Romano-Wolf StepM."""
from __future__ import annotations

import numpy as np
import pytest

from fiboki.stats.spa import (
    differentials_from_returns,
    reality_check,
    spa_p_value,
    step_m,
    superior_predictive_ability,
)


def noise(n_obs: int = 400, n_strat: int = 20, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal((n_obs, n_strat)) * 0.01


class TestSPANull:
    def test_no_strategy_beats_the_benchmark(self) -> None:
        r = superior_predictive_ability(noise(seed=11), n_boot=500, rng=7)
        assert r.p_consistent > 0.10
        assert r.p_value == r.p_consistent

    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5])
    def test_p_value_ordering_always_holds(self, seed: int) -> None:
        """p_lower <= p_consistent <= p_upper by construction: each version keeps
        strictly more strategies in the null distribution of the maximum."""
        r = superior_predictive_ability(noise(seed=seed), n_boot=400, rng=seed)
        assert r.p_lower <= r.p_consistent <= r.p_upper

    def test_all_p_values_in_unit_interval(self) -> None:
        r = superior_predictive_ability(noise(seed=2), n_boot=300, rng=1)
        for p in (r.p_lower, r.p_consistent, r.p_upper):
            assert 0.0 <= p <= 1.0

    def test_uniformity_of_the_null(self) -> None:
        """Under the null the p-value should not concentrate near zero: across
        independent noise panels, few of them should 'find' anything."""
        ps = [spa_p_value(noise(seed=100 + s), n_boot=300, rng=s) for s in range(20)]
        assert float(np.mean([p < 0.05 for p in ps])) <= 0.20


class TestSPAPower:
    def test_detects_a_genuinely_superior_strategy(self) -> None:
        d = noise(seed=3)
        d[:, 5] += 0.01 * 0.30
        r = superior_predictive_ability(d, n_boot=500, rng=1)
        assert r.p_consistent < 0.01
        assert r.best_index == 5

    def test_hansen_keeps_power_where_whites_reality_check_loses_it(self) -> None:
        """The published motivation for SPA: pad the search with 400 clearly bad
        strategies and the Reality Check no longer sees the real one, because it
        recentres every one of them at its own sample mean."""
        rng = np.random.default_rng(3)
        good = rng.standard_normal((400, 1)) * 0.01 + 0.01 * 0.12
        junk = rng.standard_normal((400, 400)) * 0.01 - 0.01 * 0.7
        d = np.hstack([good, junk])

        spa = superior_predictive_ability(d, n_boot=1000, rng=5)
        rc = reality_check(d, n_boot=1000, rng=5)

        assert spa.p_consistent < 0.05  # SPA still finds it
        assert spa.p_upper > 0.05  # the conservative bound does not
        assert rc.p_value > 0.05  # nor does White's Reality Check
        assert rc.p_value == pytest.approx(spa.p_upper, abs=0.1)

    def test_reality_check_alone_detects_a_strong_effect(self) -> None:
        d = noise(seed=4, n_strat=10)
        d[:, 2] += 0.01 * 0.5
        assert reality_check(d, n_boot=500, rng=2).p_value < 0.01


class TestStepM:
    def test_identifies_exactly_the_real_strategies(self) -> None:
        d = noise(n_obs=600, n_strat=30, seed=6)
        for k in (2, 11, 23):
            d[:, k] += 0.01 * 0.35
        result = step_m(d, alpha=0.05, n_boot=800, rng=3)
        assert set(result.rejected) == {2, 11, 23}

    @pytest.mark.slow
    def test_empirical_family_wise_error_rate_matches_alpha(self) -> None:
        """The calibration test that matters: over 200 independent noise panels of
        20 strategies each, the share of panels where StepM flags ANYTHING must sit
        near the nominal 5%.  A liberal test here would re-create exactly the V1
        failure this library exists to prevent.

        Measured: 0.045 (9/200).  The band allows Monte Carlo error at n=200
        (binomial sd ~0.015) without tolerating a genuinely liberal test.
        """
        panels = 200
        flagged = sum(
            step_m(noise(seed=1000 + s), alpha=0.05, n_boot=300, rng=s).n_rejected > 0
            for s in range(panels)
        )
        assert flagged / panels <= 0.10

    @pytest.mark.slow
    def test_spa_p_values_are_correctly_sized_under_the_null(self) -> None:
        """Measured: P(p < 0.05) = 0.050 and mean p = 0.48 over 200 noise panels."""
        ps = np.array(
            [spa_p_value(noise(seed=1000 + s), n_boot=300, rng=s) for s in range(200)]
        )
        assert float((ps < 0.05).mean()) <= 0.10
        assert 0.35 < float(ps.mean()) < 0.65

    def test_rarely_flags_noise(self) -> None:
        """Cheap smoke version of the size test above."""
        flagged = [
            step_m(noise(seed=300 + s), alpha=0.05, n_boot=400, rng=s).n_rejected
            for s in range(10)
        ]
        assert sum(1 for f in flagged if f > 0) <= 2

    def test_is_stepwise(self) -> None:
        """A second step must exist when the first rejects something - that is the
        power gain over a single-step maximum test."""
        d = noise(n_obs=600, n_strat=30, seed=8)
        d[:, 0] += 0.01 * 0.8
        d[:, 1] += 0.01 * 0.3
        result = step_m(d, n_boot=800, rng=1)
        assert result.n_steps >= 2
        assert 0 in result.rejected

    def test_critical_values_fall_as_winners_are_removed(self) -> None:
        d = noise(n_obs=600, n_strat=40, seed=9)
        d[:, 3] += 0.01 * 0.9
        result = step_m(d, n_boot=600, rng=4)
        assert list(result.critical_values) == sorted(result.critical_values, reverse=True)

    def test_stricter_alpha_rejects_no_more(self) -> None:
        d = noise(n_obs=600, n_strat=30, seed=10)
        d[:, 4] += 0.01 * 0.25
        loose = step_m(d, alpha=0.10, n_boot=600, rng=2)
        tight = step_m(d, alpha=0.01, n_boot=600, rng=2)
        assert set(tight.rejected).issubset(set(loose.rejected))

    def test_rejects_bad_alpha(self) -> None:
        with pytest.raises(ValueError):
            step_m(noise(), alpha=0.0, rng=0)


class TestPlumbing:
    def test_differentials_from_scalar_benchmark(self) -> None:
        r = np.arange(12, dtype=float).reshape(6, 2)
        assert np.allclose(differentials_from_returns(r, 1.0), r - 1.0)

    def test_differentials_from_series_benchmark(self) -> None:
        r = np.ones((6, 2))
        bench = np.arange(6, dtype=float)
        assert np.allclose(differentials_from_returns(r, bench), 1.0 - bench[:, None])

    def test_benchmark_length_checked(self) -> None:
        with pytest.raises(ValueError, match="length"):
            differentials_from_returns(np.ones((6, 2)), np.ones(5))

    def test_block_length_is_estimated_and_shared(self) -> None:
        r = superior_predictive_ability(noise(seed=1), n_boot=200, rng=0)
        assert r.block_length >= 1.0

    def test_explicit_block_length_is_respected(self) -> None:
        r = superior_predictive_ability(noise(seed=1), n_boot=200, block_length=12.0, rng=0)
        assert r.block_length == 12.0

    def test_serial_dependence_lifts_the_estimated_block_length(self) -> None:
        rng = np.random.default_rng(0)
        e = rng.standard_normal((800, 5)) * 0.01
        persistent = np.cumsum(e, axis=0) * 0.05 + e  # strongly autocorrelated
        assert (
            superior_predictive_ability(persistent, n_boot=200, rng=0).block_length
            > superior_predictive_ability(noise(800, 5, seed=0), n_boot=200, rng=0).block_length
        )

    def test_deterministic_for_a_fixed_seed(self) -> None:
        d = noise(seed=12)
        assert (
            superior_predictive_ability(d, n_boot=300, rng=77).p_consistent
            == superior_predictive_ability(d, n_boot=300, rng=77).p_consistent
        )

    def test_rejects_short_samples_and_nan(self) -> None:
        with pytest.raises(ValueError, match="at least 4 periods"):
            superior_predictive_ability(np.ones((3, 2)), rng=0)
        bad = noise(n_obs=50, n_strat=3)
        bad[2, 1] = np.nan
        with pytest.raises(ValueError, match="non-finite"):
            superior_predictive_ability(bad, rng=0)

    def test_single_strategy_is_accepted(self) -> None:
        d = np.random.default_rng(0).standard_normal(300) * 0.01 + 0.004
        r = superior_predictive_ability(d, n_boot=300, rng=0)
        assert r.n_strategies == 1
        assert r.p_consistent < 0.10
