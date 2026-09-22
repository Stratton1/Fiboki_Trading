"""Multiple-testing corrections and the effective number of independent trials.

V1 ran 23,040 strategy-instrument-timeframe backtests and ranked them by raw
performance.  With 23,040 tests at alpha=0.05, roughly 1,152 cells clear
significance from noise alone.  The leaderboard was not a discovery mechanism, it
was a sampling distribution of the maximum.

Two distinct jobs live here:

* Controlling error across a family of p-values - Bonferroni (FWER, blunt),
  Holm (FWER, uniformly more powerful than Bonferroni, no extra assumptions) and
  Benjamini-Hochberg (FDR, the right tool when you expect several real effects
  and can tolerate a known fraction of false ones).
* Counting how many INDEPENDENT trials the search really represents, via
  :func:`effective_trials_by_clustering`.  That count feeds
  :func:`fiboki.stats.sharpe.deflated_sharpe_ratio`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.cluster import hierarchy as _hier
from scipy.spatial.distance import squareform as _squareform

__all__ = [
    "EffectiveTrials",
    "MultipleTestResult",
    "benjamini_hochberg",
    "bonferroni",
    "effective_trials_by_clustering",
    "holm",
]


def _as_pvalues(pvalues: np.ndarray | pd.Series | list[float]) -> np.ndarray:
    p = np.asarray(pvalues, dtype=float).ravel()
    if p.size == 0:
        raise ValueError("no p-values supplied")
    if not np.all(np.isfinite(p)):
        raise ValueError("p-values contain non-finite values")
    if np.any(p < 0.0) or np.any(p > 1.0):
        raise ValueError("p-values must lie in [0, 1]")
    return p


@dataclass(frozen=True, slots=True)
class MultipleTestResult:
    method: str
    alpha: float
    rejected: np.ndarray = field(repr=False)
    """Boolean mask, in the ORIGINAL order of the input."""
    adjusted_pvalues: np.ndarray = field(repr=False)
    n_tests: int = 0
    threshold: float = 0.0
    """The largest raw p-value that was rejected, or 0.0 if none was."""

    @property
    def n_rejected(self) -> int:
        return int(self.rejected.sum())

    @property
    def rejected_indices(self) -> tuple[int, ...]:
        return tuple(int(i) for i in np.flatnonzero(self.rejected))


def bonferroni(
    pvalues: np.ndarray | pd.Series | list[float], alpha: float = 0.05
) -> MultipleTestResult:
    """Bonferroni FWER control: reject where ``p <= alpha / m``.

    Valid under ANY dependence structure, which is its only virtue.  With V1's
    23,040 trials the per-test threshold is 2.2e-6 - so conservative that nothing
    short of a decade of daily data could clear it.  Use it as a sanity bound,
    not as the working gate.
    """
    p = _as_pvalues(pvalues)
    m = p.size
    adjusted = np.minimum(1.0, p * m)
    rejected = adjusted <= alpha
    return MultipleTestResult(
        method="bonferroni",
        alpha=alpha,
        rejected=rejected,
        adjusted_pvalues=adjusted,
        n_tests=m,
        threshold=float(p[rejected].max()) if rejected.any() else 0.0,
    )


def holm(
    pvalues: np.ndarray | pd.Series | list[float], alpha: float = 0.05
) -> MultipleTestResult:
    """Holm-Bonferroni step-down FWER control.

    Uniformly more powerful than :func:`bonferroni` under the same (nil)
    assumptions, so there is never a reason to prefer plain Bonferroni for
    decisions.  Adjusted p-values are made monotone by a running maximum.
    """
    p = _as_pvalues(pvalues)
    m = p.size
    order = np.argsort(p, kind="stable")
    p_sorted = p[order]
    raw = (m - np.arange(m)) * p_sorted
    adjusted_sorted = np.minimum(1.0, np.maximum.accumulate(raw))
    adjusted = np.empty(m)
    adjusted[order] = adjusted_sorted
    rejected = adjusted <= alpha
    return MultipleTestResult(
        method="holm",
        alpha=alpha,
        rejected=rejected,
        adjusted_pvalues=adjusted,
        n_tests=m,
        threshold=float(p[rejected].max()) if rejected.any() else 0.0,
    )


def benjamini_hochberg(
    pvalues: np.ndarray | pd.Series | list[float], alpha: float = 0.05
) -> MultipleTestResult:
    """Benjamini-Hochberg step-up control of the FALSE DISCOVERY RATE.

    Controls the expected proportion of false positives among the rejections, not
    the probability of any false positive.  This is the right frame for strategy
    research, where the goal is a shortlist that is mostly real rather than a
    single certainty - but it must be stated as such: at ``alpha=0.1``, about one
    in ten promoted strategies is expected to be noise.

    Assumes independence or positive regression dependence (PRDS).  Strategy
    trials on overlapping instruments are positively dependent, which PRDS
    covers; for adversarial dependence, use :func:`holm`.
    """
    p = _as_pvalues(pvalues)
    m = p.size
    order = np.argsort(p, kind="stable")
    p_sorted = p[order]
    ranks = np.arange(1, m + 1)
    raw = p_sorted * m / ranks
    # Step-up: enforce monotonicity from the largest p-value downwards.
    adjusted_sorted = np.minimum(1.0, np.minimum.accumulate(raw[::-1])[::-1])
    adjusted = np.empty(m)
    adjusted[order] = adjusted_sorted
    rejected = adjusted <= alpha
    return MultipleTestResult(
        method="benjamini_hochberg",
        alpha=alpha,
        rejected=rejected,
        adjusted_pvalues=adjusted,
        n_tests=m,
        threshold=float(p[rejected].max()) if rejected.any() else 0.0,
    )


@dataclass(frozen=True, slots=True)
class EffectiveTrials:
    """How many genuinely independent bets a search represents."""

    n_effective: int
    n_trials: int
    labels: np.ndarray = field(repr=False)
    """Cluster id per trial, in input column order."""
    distance_threshold: float = 0.0
    mean_abs_correlation: float = 0.0
    linkage_method: str = "average"

    @property
    def redundancy(self) -> float:
        """``1 - n_effective / n_trials``: the share of the search that was duplication."""
        return 1.0 - self.n_effective / self.n_trials


def effective_trials_by_clustering(
    returns_matrix: np.ndarray | pd.DataFrame,
    *,
    corr_threshold: float = 0.7,
    linkage_method: str = "average",
) -> EffectiveTrials:
    """Effective number of independent trials, by clustering trial return series.

    Trials are clustered on the correlation distance ``d = sqrt(0.5 * (1 - rho))``
    (Lopez de Prado's metric, a proper distance on [0, 1]) with hierarchical
    average linkage, cut so that trials correlated above ``corr_threshold`` land
    in one cluster.  The cluster COUNT is the effective trial count fed to
    :func:`fiboki.stats.sharpe.deflated_sharpe_ratio`.

    Why not the naive adjustment ``N_eff = N / (1 + (M-1) * rho_bar)``
    -----------------------------------------------------------------
    Because it answers a different question and, at Fiboki's scale, answers it
    catastrophically.  That formula is the effective SAMPLE SIZE for the variance
    of a mean of ``M`` equicorrelated variables.  It is not the number of
    independent extremes, which is what a maximum-of-N correction needs.  Plug in
    V1's numbers: ``M = 23,040`` with a modest average correlation ``rho_bar =
    0.3`` gives ``1 + 23,039 * 0.3 = 6,913`` and therefore ``N_eff = 3``.  Even
    ``rho_bar = 0.05`` gives ``N_eff = 20``.  The expected maximum Sharpe at
    ``N = 20`` (sd 0.5) is about 0.94 against 2.03 at ``N = 23,040`` - so the
    naive adjustment would HALVE the false-discovery threshold and wave through
    precisely the results this library exists to stop.  It also assumes a single
    equicorrelation, which a search spanning 60 instruments and 7 timeframes
    violates by construction: EURUSD-H1 and EURUSD-H4 are near-duplicates while
    EURUSD-H1 and BTCUSD-D1 are not, and one average cannot represent both.

    Clustering makes no equicorrelation assumption and degrades gracefully: with
    perfectly independent trials it returns ``N``, with ``g`` duplicated families
    it returns ``g``.

    Parameters
    ----------
    returns_matrix
        ``T x N``: one column per trial, rows aligned in time.
    corr_threshold
        Trials more correlated than this are treated as one trial.
    """
    matrix = np.asarray(returns_matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("returns_matrix must be 2-D (T x N)")
    n_obs, n_trials = matrix.shape
    if n_obs < 3:
        raise ValueError("need at least 3 periods to estimate correlations")
    if not -1.0 < corr_threshold < 1.0:
        raise ValueError("corr_threshold must be in (-1, 1)")
    if n_trials == 1:
        return EffectiveTrials(1, 1, np.zeros(1, dtype=int), 0.0, 0.0, linkage_method)

    sd = matrix.std(axis=0, ddof=1)
    corr = np.eye(n_trials)
    live = sd > 0.0
    if live.sum() >= 2:
        sub = np.corrcoef(matrix[:, live], rowvar=False)
        sub = np.nan_to_num(sub, nan=0.0)
        ix = np.flatnonzero(live)
        corr[np.ix_(ix, ix)] = sub
    np.fill_diagonal(corr, 1.0)
    corr = np.clip(corr, -1.0, 1.0)

    dist = np.sqrt(0.5 * (1.0 - corr))
    np.fill_diagonal(dist, 0.0)
    dist = (dist + dist.T) / 2.0  # enforce exact symmetry for squareform
    link = _hier.linkage(_squareform(dist, checks=False), method=linkage_method)
    threshold = float(np.sqrt(0.5 * (1.0 - corr_threshold)))
    labels = _hier.fcluster(link, t=threshold, criterion="distance")

    off_diag = ~np.eye(n_trials, dtype=bool)
    return EffectiveTrials(
        n_effective=int(np.unique(labels).size),
        n_trials=int(n_trials),
        labels=labels.astype(int),
        distance_threshold=threshold,
        mean_abs_correlation=float(np.abs(corr[off_diag]).mean()),
        linkage_method=linkage_method,
    )
