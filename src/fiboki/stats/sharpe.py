"""Sharpe-ratio inference under non-normality and selection bias.

Everything here exists because a Sharpe ratio computed from a finite, skewed,
fat-tailed sample and then *selected as the best of many* is not an estimate of
anything. V1 ranked 23,040 strategy-instrument-timeframe combinations by raw
Sharpe with no correction at all; with sd(SR) across trials of only 0.5, the
expected maximum Sharpe from pure noise at that trial count is 2.03 (see
:func:`expected_max_sharpe`). V1's top-of-leaderboard Sharpes were inside the
noise envelope and were promoted anyway.

References
----------
Bailey, D. and Lopez de Prado, M. (2012). "The Sharpe Ratio Efficient Frontier",
    Journal of Risk 15(2), 3-44.  [PSR, MinTRL]
Bailey, D. and Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting
    for Selection Bias, Backtest Overfitting and Non-Normality", Journal of
    Portfolio Management 40(5), 94-107.  [DSR, expected max Sharpe]

Conventions
-----------
* ``sr_hat`` is the NON-annualised Sharpe measured at the same frequency as the
  ``T`` observations.  Passing an annualised Sharpe with a per-bar ``T`` inflates
  PSR massively.  Use :func:`sharpe_moments` to derive a consistent set.
* ``kurtosis`` is NON-EXCESS (3.0 for a Gaussian).  Passing excess kurtosis is
  caught by the moment-inequality check ``kurtosis >= 1 + skew**2``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as _sps

__all__ = [
    "EULER_MASCHERONI",
    "SharpeMoments",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "minimum_track_record_length",
    "probabilistic_sharpe_ratio",
    "sharpe_moments",
]

EULER_MASCHERONI = 0.5772156649015329
"""Euler-Mascheroni constant, gamma. Used by the Gumbel max-order approximation."""


def _as_returns(returns: np.ndarray | pd.Series | list[float]) -> np.ndarray:
    arr = np.asarray(returns, dtype=float).ravel()
    if arr.size == 0:
        raise ValueError("returns is empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError("returns contains non-finite values; clean or drop them first")
    return arr


@dataclass(frozen=True, slots=True)
class SharpeMoments:
    """A self-consistent moment set: all four fields describe the SAME sample."""

    sr_hat: float
    n_obs: int
    skew: float
    kurtosis: float
    """Non-excess kurtosis (3.0 for a Gaussian)."""

    def annualised(self, periods_per_year: float) -> float:
        return self.sr_hat * math.sqrt(periods_per_year)


def sharpe_moments(
    returns: np.ndarray | pd.Series | list[float],
    *,
    risk_free_per_period: float = 0.0,
) -> SharpeMoments:
    """Derive ``(sr_hat, T, skew, kurtosis)`` from one return series.

    The Sharpe is NOT annualised: it is measured per observation, matching ``T``.
    Skew and kurtosis use the population (biased) estimators, which is what the
    PSR derivation assumes.
    """
    arr = _as_returns(returns) - risk_free_per_period
    n = arr.size
    if n < 2:
        raise ValueError("need at least 2 observations to estimate a Sharpe ratio")
    sd = float(arr.std(ddof=1))
    if sd <= 0.0:
        raise ValueError("returns have zero dispersion; Sharpe ratio undefined")
    return SharpeMoments(
        sr_hat=float(arr.mean() / sd),
        n_obs=int(n),
        skew=float(_sps.skew(arr, bias=True)),
        kurtosis=float(_sps.kurtosis(arr, fisher=False, bias=True)),
    )


def _psr_variance_term(sr_hat: float, skew: float, kurtosis: float) -> float:
    """The estimated variance of SR-hat, ``1 - g3*SR + (g4-1)/4 * SR^2``.

    Raises when the moment set is impossible or drives the variance non-positive.
    """
    if kurtosis < 1.0 + skew**2 - 1e-9:
        raise ValueError(
            f"impossible moments: kurtosis ({kurtosis}) must be >= 1 + skew^2 "
            f"({1.0 + skew**2:.6f}). NOTE: this function wants NON-EXCESS kurtosis "
            "(3.0 for a Gaussian) - passing excess kurtosis lands here."
        )
    var = 1.0 - skew * sr_hat + ((kurtosis - 1.0) / 4.0) * sr_hat**2
    if var <= 0.0:
        raise ValueError(
            "PSR variance term is non-positive for "
            f"sr_hat={sr_hat}, skew={skew}, kurtosis={kurtosis}; "
            "the asymptotic expansion does not hold here."
        )
    return var


def probabilistic_sharpe_ratio(
    sr_hat: float,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    sr_benchmark: float = 0.0,
) -> float:
    """Probability that the TRUE Sharpe exceeds ``sr_benchmark`` (Bailey & LdP 2012).

    ``PSR(SR*) = Z[ (SR_hat - SR*) * sqrt(T - 1) / sqrt(1 - g3*SR_hat + (g4-1)/4 * SR_hat^2) ]``

    Parameters
    ----------
    sr_hat, sr_benchmark
        Sharpe ratios at the OBSERVATION frequency (not annualised).
    n_obs
        Number of observations ``T``.
    skew
        Sample skewness ``g3`` of the returns.
    kurtosis
        Sample NON-EXCESS kurtosis ``g4`` (3.0 for a Gaussian).

    Returns
    -------
    float in [0, 1].  Below ~0.95 the track record does not establish that the
    strategy beats the benchmark Sharpe, however pretty the point estimate.
    """
    if n_obs < 2:
        raise ValueError("n_obs must be >= 2")
    var = _psr_variance_term(sr_hat, skew, kurtosis)
    z = (sr_hat - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(var)
    return float(_sps.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe across ``N`` INDEPENDENT trials of zero true skill.

    ``SR_0 = sqrt(V) * [ (1 - gamma) * Z^-1(1 - 1/N) + gamma * Z^-1(1 - 1/(N*e)) ]``

    This is the false-discovery threshold: with ``N`` trials whose Sharpes have
    cross-sectional variance ``V``, a strategy must beat ``SR_0`` before its
    Sharpe is evidence of anything.

    Published worked values (Bailey & Lopez de Prado 2014), sd(SR) = 0.5:
    ``N=100 -> 1.27``, ``N=1000 -> 1.63``.  At V1's ``N=23,040 -> 2.03``.

    Parameters
    ----------
    n_trials
        Number of trials ``N``.  Use the EFFECTIVE number of independent trials
        when the trials are correlated - see
        :func:`fiboki.stats.multiple_testing.effective_trials_by_clustering`.
    sr_variance
        VARIANCE (not standard deviation) of the trial Sharpe ratios.
    """
    if n_trials < 2:
        raise ValueError("n_trials must be >= 2 for an extreme-value approximation")
    if sr_variance < 0.0:
        raise ValueError("sr_variance must be non-negative (it is a variance, not an sd)")
    n = float(n_trials)
    q1 = _sps.norm.ppf(1.0 - 1.0 / n)
    q2 = _sps.norm.ppf(1.0 - 1.0 / (n * math.e))
    return float(
        math.sqrt(sr_variance)
        * ((1.0 - EULER_MASCHERONI) * q1 + EULER_MASCHERONI * q2)
    )


def deflated_sharpe_ratio(
    sr_hat: float,
    n_obs: int,
    n_trials: int,
    sr_variance: float,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio: PSR evaluated against the expected max of the search.

    ``DSR = PSR(SR_0)`` where ``SR_0 = expected_max_sharpe(n_trials, sr_variance)``.

    This is the single number V1 never computed.  A DSR below 0.95 means the
    result is not distinguishable from the best of ``n_trials`` coin flips.
    """
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(
        sr_hat, n_obs, skew=skew, kurtosis=kurtosis, sr_benchmark=sr0
    )


def deflated_sharpe_from_trials(
    sr_hat: float,
    n_obs: int,
    trial_sharpes: np.ndarray | pd.Series | list[float],
    skew: float = 0.0,
    kurtosis: float = 3.0,
    *,
    n_effective_trials: int | None = None,
) -> float:
    """DSR taking ``sr_variance`` from the observed cross-section of trial Sharpes.

    ``n_effective_trials`` overrides the raw trial count; pass the clustering-based
    effective N when trials are correlated (they always are - the same strategy on
    EURUSD H1 and EURUSD H4 is not two independent bets).
    """
    arr = _as_returns(trial_sharpes)
    n_trials = int(n_effective_trials if n_effective_trials is not None else arr.size)
    return deflated_sharpe_ratio(
        sr_hat,
        n_obs,
        n_trials=n_trials,
        sr_variance=float(arr.var(ddof=1)) if arr.size > 1 else 0.0,
        skew=skew,
        kurtosis=kurtosis,
    )


def minimum_track_record_length(
    sr_hat: float,
    sr_benchmark: float = 0.0,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    confidence: float = 0.95,
) -> float:
    """Observations needed before ``SR_hat > sr_benchmark`` is significant.

    ``MinTRL = 1 + [1 - g3*SR + (g4-1)/4 * SR^2] * (Z_alpha / (SR - SR*))^2``

    Returned in OBSERVATIONS at the frequency of ``sr_hat``.  Divide by
    ``Timeframe.bars_per_year`` for years.  Returns ``inf`` when ``sr_hat`` does
    not exceed the benchmark at all - no amount of data makes that significant.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if sr_hat <= sr_benchmark:
        return math.inf
    var = _psr_variance_term(sr_hat, skew, kurtosis)
    z = float(_sps.norm.ppf(confidence))
    return 1.0 + var * (z / (sr_hat - sr_benchmark)) ** 2
