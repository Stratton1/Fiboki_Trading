"""PSR / DSR / MinTRL, checked against the published worked figures."""
from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
import pytest

from fiboki.stats.sharpe import (
    EULER_MASCHERONI,
    deflated_sharpe_from_trials,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_moments,
)


class TestExpectedMaxSharpe:
    """PUBLISHED FIGURES: Bailey & Lopez de Prado (2014), sd(SR) = 0.5."""

    @pytest.mark.golden
    @pytest.mark.parametrize(
        ("n_trials", "expected"),
        [(100, 1.27), (1000, 1.63), (23_040, 2.03)],
    )
    def test_matches_published_values(self, n_trials: int, expected: float) -> None:
        got = expected_max_sharpe(n_trials, sr_variance=0.5**2)
        assert got == pytest.approx(expected, abs=0.005)

    @pytest.mark.golden
    def test_v1_search_space_threshold(self) -> None:
        """V1's 23,040 cells: anything under 2.03 Sharpe was inside the noise."""
        assert expected_max_sharpe(23_040, 0.25) == pytest.approx(2.03005, abs=1e-4)

    def test_increases_with_trials(self) -> None:
        values = [expected_max_sharpe(n, 0.25) for n in (10, 100, 1_000, 23_040, 10**6)]
        assert all(a < b for a, b in pairwise(values))

    def test_scales_with_sd_not_variance(self) -> None:
        base = expected_max_sharpe(1000, 1.0)
        assert expected_max_sharpe(1000, 4.0) == pytest.approx(2.0 * base)

    def test_zero_variance_means_no_dispersion_to_exploit(self) -> None:
        assert expected_max_sharpe(23_040, 0.0) == 0.0

    def test_euler_constant(self) -> None:
        assert pytest.approx(0.5772156649, abs=1e-10) == EULER_MASCHERONI

    @pytest.mark.parametrize("bad", [1, 0, -5])
    def test_rejects_degenerate_trial_counts(self, bad: int) -> None:
        with pytest.raises(ValueError):
            expected_max_sharpe(bad, 0.25)

    def test_rejects_negative_variance(self) -> None:
        with pytest.raises(ValueError):
            expected_max_sharpe(100, -0.1)


class TestProbabilisticSharpe:
    @pytest.mark.golden
    def test_gaussian_closed_form(self) -> None:
        """With skew=0 and kurtosis=3 the variance term is exactly 1 + SR^2/2."""
        sr, n = 0.10, 250
        expected_z = sr * math.sqrt(n - 1) / math.sqrt(1.0 + 0.5 * sr**2)
        from scipy.stats import norm

        assert probabilistic_sharpe_ratio(sr, n) == pytest.approx(float(norm.cdf(expected_z)))

    def test_zero_sharpe_against_zero_benchmark_is_a_coin_flip(self) -> None:
        assert probabilistic_sharpe_ratio(0.0, 500) == pytest.approx(0.5)

    def test_negative_skew_and_fat_tails_reduce_confidence(self) -> None:
        """The whole point of PSR: the same Sharpe is worth less with bad moments."""
        clean = probabilistic_sharpe_ratio(0.12, 500, skew=0.0, kurtosis=3.0)
        nasty = probabilistic_sharpe_ratio(0.12, 500, skew=-1.5, kurtosis=9.0)
        assert nasty < clean

    def test_more_observations_increase_confidence(self) -> None:
        few = probabilistic_sharpe_ratio(0.08, 60)
        many = probabilistic_sharpe_ratio(0.08, 600)
        assert many > few

    def test_impossible_moments_rejected(self) -> None:
        """kurtosis < 1 + skew^2 is mathematically impossible - and is what you get
        by passing EXCESS kurtosis by mistake."""
        with pytest.raises(ValueError, match="NON-EXCESS"):
            probabilistic_sharpe_ratio(0.1, 500, skew=0.0, kurtosis=0.0)

    def test_too_few_observations(self) -> None:
        with pytest.raises(ValueError):
            probabilistic_sharpe_ratio(0.1, 1)


class TestSharpeMoments:
    def test_round_trip_on_known_series(self) -> None:
        rng = np.random.default_rng(4)
        r = rng.normal(0.001, 0.01, 4000)
        m = sharpe_moments(r)
        assert m.n_obs == 4000
        assert m.sr_hat == pytest.approx(r.mean() / r.std(ddof=1))
        assert m.kurtosis == pytest.approx(3.0, abs=0.3)
        assert m.skew == pytest.approx(0.0, abs=0.15)

    def test_annualisation_helper(self) -> None:
        m = sharpe_moments(np.random.default_rng(1).normal(0.001, 0.01, 500))
        assert m.annualised(252) == pytest.approx(m.sr_hat * math.sqrt(252))

    def test_constant_series_rejected(self) -> None:
        with pytest.raises(ValueError, match="zero dispersion"):
            sharpe_moments(np.ones(50))


class TestDeflatedSharpe:
    @pytest.mark.golden
    def test_equals_psr_at_expected_max(self) -> None:
        sr0 = expected_max_sharpe(1000, 0.25)
        assert deflated_sharpe_ratio(2.0, 500, 1000, 0.25) == pytest.approx(
            probabilistic_sharpe_ratio(2.0, 500, sr_benchmark=sr0)
        )

    def test_v1_leaderboard_would_have_been_rejected(self) -> None:
        """A 'top' V1 cell: daily Sharpe 0.12 over 4 years, best of 23,040 trials."""
        dsr = deflated_sharpe_ratio(
            sr_hat=0.12, n_obs=1000, n_trials=23_040, sr_variance=0.25, skew=-0.4, kurtosis=6.0
        )
        assert dsr < 0.05

    def test_more_trials_lower_dsr(self) -> None:
        kwargs = {"sr_hat": 2.2, "n_obs": 500, "sr_variance": 0.25}
        assert deflated_sharpe_ratio(n_trials=100, **kwargs) > deflated_sharpe_ratio(
            n_trials=23_040, **kwargs
        )

    def test_from_trials_uses_observed_dispersion(self) -> None:
        trials = np.random.default_rng(0).normal(0.0, 0.5, 400)
        direct = deflated_sharpe_ratio(2.0, 500, 400, float(trials.var(ddof=1)))
        assert deflated_sharpe_from_trials(2.0, 500, trials) == pytest.approx(direct)

    def test_effective_trials_override(self) -> None:
        trials = np.random.default_rng(0).normal(0.0, 0.5, 400)
        clustered = deflated_sharpe_from_trials(2.0, 500, trials, n_effective_trials=12)
        raw = deflated_sharpe_from_trials(2.0, 500, trials)
        assert clustered > raw


class TestMinimumTrackRecordLength:
    @pytest.mark.golden
    def test_closed_form(self) -> None:
        from scipy.stats import norm

        sr, skew, kurt, conf = 0.15, -0.5, 5.0, 0.95
        var = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr**2
        expected = 1.0 + var * (float(norm.ppf(conf)) / sr) ** 2
        assert minimum_track_record_length(sr, 0.0, skew, kurt, conf) == pytest.approx(expected)

    def test_no_edge_means_never(self) -> None:
        assert minimum_track_record_length(0.05, 0.05) == math.inf
        assert minimum_track_record_length(0.01, 0.20) == math.inf

    def test_bigger_edge_needs_less_data(self) -> None:
        assert minimum_track_record_length(0.20) < minimum_track_record_length(0.05)

    def test_higher_confidence_needs_more_data(self) -> None:
        assert minimum_track_record_length(0.1, confidence=0.99) > minimum_track_record_length(
            0.1, confidence=0.90
        )

    def test_negative_skew_needs_more_data(self) -> None:
        assert minimum_track_record_length(0.1, skew=-1.0, kurtosis=6.0) > (
            minimum_track_record_length(0.1, skew=0.0, kurtosis=3.0)
        )

    @pytest.mark.parametrize("conf", [0.0, 1.0, -0.1, 1.2])
    def test_rejects_bad_confidence(self, conf: float) -> None:
        with pytest.raises(ValueError):
            minimum_track_record_length(0.1, confidence=conf)


class TestMinTRLAsTheE2CandidateReadsIt:
    """The audit's MinTRL figures (F_backend_audit section 3.1), gamma3 = 0, gamma4 = 3, 95%.

    MinTRL = 1 + [1 - 0*SR + (3-1)/4 * SR^2] * (Z/SR)^2, Z = Phi^-1(0.95) = 1.6448536:
      SR 0.20: 1 + 1.0200 * (8.224268)^2   =   69.99
      SR 0.10: 1 + 1.0050 * (16.448536)^2  =  272.91
      SR 0.05: 1 + 1.00125 * (32.897073)^2 = 1084.58
    """

    Z95 = 1.6448536269514722

    @pytest.mark.parametrize(
        ("sr", "expected"), [(0.20, 69.99), (0.10, 272.91), (0.05, 1084.58)]
    )
    def test_the_audits_worked_figures(self, sr: float, expected: float) -> None:
        by_hand = 1.0 + (1.0 + 0.5 * sr**2) * (self.Z95 / sr) ** 2
        assert by_hand == pytest.approx(expected, abs=0.01)
        assert minimum_track_record_length(sr, 0.0, 0.0, 3.0, 0.95) == pytest.approx(by_hand)

    def test_fat_tails_and_negative_skew_raise_it(self) -> None:
        """SR 0.1, skew -1, kurtosis 9: 1 + (1 + 0.1 + 2 * 0.01) * (16.4485)^2 = 304.02."""
        by_hand = 1.0 + (1.0 + 0.1 + 0.02) * (self.Z95 / 0.1) ** 2
        assert by_hand == pytest.approx(304.02, abs=0.01)
        assert minimum_track_record_length(0.1, 0.0, -1.0, 9.0, 0.95) == pytest.approx(by_hand)
