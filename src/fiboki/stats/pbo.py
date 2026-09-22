"""Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation.

The question: when I pick the best of ``N`` configurations on one sample, how
often does that pick underperform the median configuration out of sample?  If the
answer is "about half the time", the selection procedure has no skill at all -
the leaderboard is a ranking of noise.  V1's 23,040-cell research matrix was
never subjected to this test.

Method (Bailey, Borwein, Lopez de Prado & Zhu, 2017):

1. Split the ``T x N`` per-period return matrix into ``S`` disjoint contiguous
   row blocks (default ``S = 16``).
2. For each of the ``C(S, S/2) = 12,870`` ways to choose half the blocks as
   in-sample, evaluate every trial in-sample and out-of-sample.
3. Take the in-sample winner ``n*`` and find its out-of-sample relative rank
   ``w = rank / (N + 1)``; the logit is ``lambda = ln(w / (1 - w))``.
4. ``PBO = P(lambda <= 0)`` - the probability the in-sample winner lands in the
   bottom half out of sample.

Interpretation: ``PBO ~ 0.5`` is a coin flip (pure overfitting).  Under about 0.1
is the usual bar for believing that in-sample selection carries information.

Two properties a user must know before acting on a single number:

* PBO has real sampling error.  On pure noise with 800 periods and 100 trials the
  estimate has a standard deviation of roughly 0.13 across realisations, so a
  single PBO of 0.35 is not meaningfully different from 0.5.  Compare against
  :func:`pbo_null_expectation` and read the logit distribution, not the point.
* With an ODD number of trials the null expectation is not 0.5.  Ranks are
  discrete, and ``omega <= 0.5`` includes the exact median rank, giving
  ``(N+1)/(2N)`` - 0.6 at N=5.  Use an even number of trials where you can.

References
----------
Bailey, D., Borwein, J., Lopez de Prado, M. and Zhu, Q. (2017). "The Probability
    of Backtest Overfitting", Journal of Computational Finance 20(4), 39-69.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

__all__ = [
    "PBOResult",
    "combinatorially_symmetric_cv",
    "pbo_null_expectation",
    "probability_of_backtest_overfitting",
    "sharpe_columns",
]


def sharpe_columns(block: np.ndarray) -> np.ndarray:
    """Per-column Sharpe ratio of a ``(rows, N)`` block; 0.0 where dispersion is nil."""
    if block.shape[0] < 2:
        raise ValueError("need at least 2 rows to compute a Sharpe ratio")
    mean = block.mean(axis=0)
    sd = block.std(axis=0, ddof=1)
    out = np.zeros_like(mean)
    ok = sd > 0.0
    out[ok] = mean[ok] / sd[ok]
    return out


@dataclass(frozen=True, slots=True)
class PBOResult:
    """Full CSCV output.  The logit distribution is the object of interest, not
    just its summary: a bimodal ``lambda`` says selection works for some regimes
    and inverts for others, which a single PBO number hides."""

    pbo: float
    """P(in-sample winner ranks below the out-of-sample median)."""
    logits: np.ndarray = field(repr=False)
    """lambda per combination; ``-inf``/``+inf`` are clipped to finite extremes."""
    relative_ranks: np.ndarray = field(repr=False)
    """omega in (0, 1) per combination."""
    is_best_index: np.ndarray = field(repr=False)
    is_performance: np.ndarray = field(repr=False)
    oos_performance: np.ndarray = field(repr=False)
    """Out-of-sample performance OF THE IN-SAMPLE WINNER, per combination."""
    degradation_slope: float = 0.0
    """OLS slope of OOS-of-winner on IS-of-winner.  <= 0 means in-sample
    improvement buys nothing (or costs) out of sample."""
    degradation_intercept: float = 0.0
    degradation_r2: float = 0.0
    probability_of_loss: float = 0.0
    """P(the in-sample winner has negative OOS performance)."""
    n_combinations: int = 0
    n_trials: int = 0
    n_splits: int = 0
    n_obs: int = 0

    @property
    def median_logit(self) -> float:
        return float(np.median(self.logits))

    @property
    def is_overfit(self) -> bool:
        """True at the conventional PBO >= 0.5 coin-flip line."""
        return self.pbo >= 0.5

    def summary(self) -> dict[str, float]:
        return {
            "pbo": self.pbo,
            "median_logit": self.median_logit,
            "degradation_slope": self.degradation_slope,
            "degradation_r2": self.degradation_r2,
            "probability_of_loss": self.probability_of_loss,
            "n_combinations": float(self.n_combinations),
        }


def _block_moments(matrix: np.ndarray, n_splits: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-block count, sum and sum-of-squares, so every combination is a matmul."""
    blocks = np.array_split(np.arange(matrix.shape[0]), n_splits)
    counts = np.array([b.size for b in blocks], dtype=float)
    sums = np.stack([matrix[b].sum(axis=0) for b in blocks])
    sumsq = np.stack([(matrix[b] ** 2).sum(axis=0) for b in blocks])
    return counts, sums, sumsq


def _sharpe_from_moments(
    cnt: np.ndarray, s1: np.ndarray, s2: np.ndarray
) -> np.ndarray:
    """Sharpe per (combination, trial) from aggregated moments. ddof=1."""
    n = cnt[:, None]
    mean = s1 / n
    var = (s2 - n * mean**2) / (n - 1.0)
    out = np.zeros_like(mean)
    ok = var > 0.0
    out[ok] = mean[ok] / np.sqrt(var[ok])
    return out


def combinatorially_symmetric_cv(
    returns: np.ndarray | pd.DataFrame,
    n_splits: int = 16,
    performance: Callable[[np.ndarray], np.ndarray] | None = None,
    *,
    logit_clip: float = 10.0,
) -> PBOResult:
    """Run CSCV on a ``T x N`` matrix of per-period returns (one column per trial).

    Parameters
    ----------
    returns
        ``T x N``.  Rows are time-ordered periods; the row ORDER matters because
        blocks are contiguous, which is what preserves serial structure.
    n_splits
        ``S``, must be even and at least 4.  ``S=16`` gives 12,870 combinations,
        the value used in the source paper.
    performance
        Optional ``(rows, N) -> (N,)`` evaluator, used for every submatrix.  The
        default is an analytic Sharpe computed from per-block moments, which is
        orders of magnitude faster; supplying a callable forces the explicit
        path, so drop ``n_splits`` to 10-12 if it is expensive.
    logit_clip
        ``lambda`` is infinite when the winner ranks first or last out of sample;
        clipped to this magnitude so the distribution stays plottable.  PBO itself
        is computed from ranks and is unaffected.
    """
    matrix = np.asarray(returns, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("returns must be a 2-D T x N matrix (one column per trial)")
    n_obs, n_trials = matrix.shape
    if n_trials < 2:
        raise ValueError("CSCV needs at least 2 trials to rank")
    if n_splits % 2 != 0 or n_splits < 4:
        raise ValueError("n_splits must be even and >= 4")
    if n_obs < 2 * n_splits:
        raise ValueError(
            f"need at least 2 rows per block: {n_obs} rows cannot support S={n_splits}"
        )
    if not np.all(np.isfinite(matrix)):
        raise ValueError("returns contain non-finite values")

    half = n_splits // 2
    combos = np.array(list(combinations(range(n_splits), half)), dtype=int)
    n_combos = combos.shape[0]
    select = np.zeros((n_combos, n_splits))
    select[np.arange(n_combos)[:, None], combos] = 1.0
    complement = 1.0 - select

    if performance is None:
        cnt, s1, s2 = _block_moments(matrix, n_splits)
        perf_is = _sharpe_from_moments(select @ cnt, select @ s1, select @ s2)
        perf_oos = _sharpe_from_moments(complement @ cnt, complement @ s1, complement @ s2)
    else:
        blocks = np.array_split(np.arange(n_obs), n_splits)
        perf_is = np.empty((n_combos, n_trials))
        perf_oos = np.empty((n_combos, n_trials))
        for c in range(n_combos):
            chosen = set(combos[c].tolist())
            rows_is = np.concatenate([blocks[b] for b in range(n_splits) if b in chosen])
            rows_oos = np.concatenate([blocks[b] for b in range(n_splits) if b not in chosen])
            perf_is[c] = np.asarray(performance(matrix[rows_is]), dtype=float)
            perf_oos[c] = np.asarray(performance(matrix[rows_oos]), dtype=float)

    best = np.argmax(perf_is, axis=1)
    rows = np.arange(n_combos)
    best_oos = perf_oos[rows, best]

    # Average ranks, so ties cannot manufacture a spuriously good or bad omega.
    fewer = (perf_oos < best_oos[:, None]).sum(axis=1)
    equal = (perf_oos == best_oos[:, None]).sum(axis=1)
    rank = fewer + (equal + 1.0) / 2.0
    omega = rank / (n_trials + 1.0)

    logits = np.log(omega / (1.0 - omega))
    logits = np.clip(logits, -abs(logit_clip), abs(logit_clip))
    pbo = float(np.mean(logits <= 0.0))

    best_is = perf_is[rows, best]
    slope, intercept, r2 = _ols(best_is, best_oos)

    return PBOResult(
        pbo=pbo,
        logits=logits,
        relative_ranks=omega,
        is_best_index=best,
        is_performance=best_is,
        oos_performance=best_oos,
        degradation_slope=slope,
        degradation_intercept=intercept,
        degradation_r2=r2,
        probability_of_loss=float(np.mean(best_oos < 0.0)),
        n_combinations=n_combos,
        n_trials=n_trials,
        n_splits=n_splits,
        n_obs=n_obs,
    )


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    if sxx <= 0.0:
        return 0.0, float(ym), 0.0
    slope = float(((x - xm) * (y - ym)).sum() / sxx)
    intercept = float(ym - slope * xm)
    resid = y - (intercept + slope * x)
    sst = float(((y - ym) ** 2).sum())
    r2 = 0.0 if sst <= 0.0 else float(1.0 - (resid**2).sum() / sst)
    return slope, intercept, r2


def probability_of_backtest_overfitting(
    returns: np.ndarray | pd.DataFrame, n_splits: int = 16
) -> float:
    """Just the PBO number, for callers that only need the gate."""
    return combinatorially_symmetric_cv(returns, n_splits=n_splits).pbo


def pbo_null_expectation(n_trials: int) -> float:
    """E[PBO] when the out-of-sample rank is uniform, i.e. selection has NO skill.

    ``floor((N+1)/2) / N``.  Exactly 0.5 for even ``N``; above 0.5 for odd ``N``
    because the median rank itself satisfies ``omega <= 0.5``.  Compare a measured
    PBO against this, not against 0.5, when the trial count is small or odd.
    """
    if n_trials < 2:
        raise ValueError("n_trials must be >= 2")
    return math.floor((n_trials + 1) / 2) / n_trials


def n_cscv_combinations(n_splits: int) -> int:
    """``C(S, S/2)``.  S=16 -> 12,870."""
    if n_splits % 2 != 0 or n_splits < 4:
        raise ValueError("n_splits must be even and >= 4")
    return math.comb(n_splits, n_splits // 2)
