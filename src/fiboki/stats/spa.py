"""Tests for superior predictive ability across a family of strategies.

The question these answer is the one V1 never asked: "given that I searched
thousands of strategies, is the BEST one better than the benchmark, or is it
just the best of thousands of draws?"

Three related procedures:

* :func:`reality_check` - White (2000).  Recentres EVERY strategy at its sample
  mean, so hopeless strategies still contribute to the null distribution of the
  maximum.  Adding junk to the search therefore inflates the p-value and destroys
  power.  Included because it is the historical baseline and the upper bound.
* :func:`superior_predictive_ability` - Hansen (2005).  Studentises, and drops
  strategies that are far enough below the benchmark that they cannot plausibly
  be the best under the null.  Returns lower / consistent / upper p-values.
* :func:`step_m` - Romano & Wolf (2005).  Identifies WHICH strategies beat the
  benchmark while controlling the family-wise error rate, rather than only
  answering the single "is anything good?" question.

All three share one stationary-bootstrap index draw across strategies, which is
mandatory: resampling each column independently would destroy the cross-sectional
dependence that makes the maximum statistic behave the way it does.

References
----------
White, H. (2000). "A Reality Check for Data Snooping", Econometrica 68(5).
Hansen, P. R. (2005). "A Test for Superior Predictive Ability", JBES 23(4), 365-380.
Romano, J. and Wolf, M. (2005). "Stepwise Multiple Testing as Formalized Data
    Snooping", Econometrica 73(4), 1237-1282.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fiboki.stats.bootstrap import optimal_block_length, stationary_bootstrap_indices

__all__ = [
    "RealityCheckResult",
    "SPAResult",
    "StepMResult",
    "differentials_from_returns",
    "reality_check",
    "spa_p_value",
    "step_m",
    "superior_predictive_ability",
]

_EPS = 1e-12


def _as_matrix(x: np.ndarray | pd.DataFrame) -> np.ndarray:
    arr = np.asarray(x, dtype=float)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2:
        raise ValueError("expected a 2-D (T x L) array of performance differentials")
    if arr.shape[0] < 4:
        raise ValueError("need at least 4 periods")
    if not np.all(np.isfinite(arr)):
        raise ValueError("differentials contain non-finite values")
    return arr


def differentials_from_returns(
    strategy_returns: np.ndarray | pd.DataFrame,
    benchmark_returns: np.ndarray | pd.Series | float = 0.0,
) -> np.ndarray:
    """Build the ``(T, L)`` differential matrix: positive means the strategy won.

    ``benchmark_returns`` may be a scalar (e.g. 0.0 for "beats doing nothing"),
    or a per-period series such as buy-and-hold.
    """
    arr = _as_matrix(strategy_returns)
    if np.isscalar(benchmark_returns):
        return arr - float(benchmark_returns)  # type: ignore[arg-type]
    bench = np.asarray(benchmark_returns, dtype=float).ravel()
    if bench.size != arr.shape[0]:
        raise ValueError("benchmark_returns length must match the number of periods")
    return arr - bench[:, None]


def _shared_block_length(d: np.ndarray, block_length: float | None) -> float:
    if block_length is not None:
        if block_length < 1.0:
            raise ValueError("block_length must be >= 1")
        return float(block_length)
    if d.shape[0] < 8:
        return 1.0
    # Median of the per-strategy estimates: one shared draw needs one shared b,
    # and the median is robust to a single pathological column.
    per_col = []
    for k in range(d.shape[1]):
        col = d[:, k]
        if float(col.std()) == 0.0:
            continue
        per_col.append(optimal_block_length(col).stationary)
    return float(np.median(per_col)) if per_col else 1.0


def _bootstrap_means(
    d: np.ndarray, n_boot: int, block_length: float, rng: np.random.Generator | int | None
) -> np.ndarray:
    """``(n_boot, L)`` resampled column means using ONE shared index draw per replicate."""
    n = d.shape[0]
    idx = stationary_bootstrap_indices(n, block_length, n_boot, rng)
    counts = np.zeros((n_boot, n))
    for b in range(n_boot):
        counts[b] = np.bincount(idx[b], minlength=n)
    return (counts @ d) / float(n)


def _omega(d_star: np.ndarray, d_bar: np.ndarray, n: int) -> np.ndarray:
    """Bootstrap estimate of the std of ``sqrt(n) * d_bar`` for each strategy."""
    omega = np.sqrt(n) * d_star.std(axis=0, ddof=0)
    scale = float(np.max(omega)) if omega.size else 1.0
    floor = max(_EPS, scale * _EPS)
    return np.maximum(omega, floor)


@dataclass(frozen=True, slots=True)
class SPAResult:
    """Hansen SPA outcome.

    ``p_consistent`` is THE p-value to use, and is what :attr:`p_value` returns.
    The three differ only in which underperforming strategies are treated as
    genuinely bad (and so excluded from the null distribution of the maximum):

    * ``p_lower``   - excludes every strategy with a negative sample mean.  Least
      conservative; it is a lower bound because it assumes those strategies are
      truly bad rather than unlucky.
    * ``p_consistent`` - excludes only strategies far enough below zero that the
      data rules them out, using Hansen's threshold
      ``sqrt(n) * d_bar_k / omega_k < -(1/4) * n^(1/4)``.  This is the only one of
      the three that is consistent: its rejection probability converges to the
      right thing whether or not poor strategies are present.
    * ``p_upper``   - excludes nothing (White's Reality Check recentring).  An
      upper bound, and the reason RC loses power the moment you add junk to the
      search.

    Report all three.  A wide ``p_lower``-to-``p_upper`` gap means the answer is
    being driven by how the also-rans are treated, which is itself a finding.
    """

    statistic: float
    p_lower: float
    p_consistent: float
    p_upper: float
    n_obs: int
    n_strategies: int
    n_boot: int
    block_length: float
    best_index: int
    mean_differential: np.ndarray = field(repr=False)
    omega: np.ndarray = field(repr=False)
    t_stats: np.ndarray = field(repr=False)

    @property
    def p_value(self) -> float:
        """The consistent p-value - the default for every decision."""
        return self.p_consistent


def superior_predictive_ability(
    differentials: np.ndarray | pd.DataFrame,
    *,
    n_boot: int = 1000,
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
) -> SPAResult:
    """Hansen's SPA test.

    ``H0``: no strategy in the family beats the benchmark
    (``max_k E[d_k] <= 0``).  A small p-value says at least one genuinely does.

    Parameters
    ----------
    differentials
        ``(T, L)`` matrix, positive where strategy ``k`` beat the benchmark in
        period ``t``.  Build it with :func:`differentials_from_returns`.
    """
    d = _as_matrix(differentials)
    n, n_strat = d.shape
    b = _shared_block_length(d, block_length)
    d_bar = d.mean(axis=0)
    d_star = _bootstrap_means(d, n_boot, b, rng)
    omega = _omega(d_star, d_bar, n)

    t_stats = math.sqrt(n) * d_bar / omega
    statistic = float(max(0.0, float(t_stats.max())))
    best_index = int(np.argmax(t_stats))

    threshold = -0.25 * n**0.25  # Hansen (2005): sqrt(n) d_bar / omega >= -A_n
    keep = {
        "lower": d_bar >= 0.0,
        "consistent": t_stats >= threshold,
        "upper": np.ones(n_strat, dtype=bool),
    }
    centred = math.sqrt(n) * (d_star - d_bar[None, :]) / omega[None, :]
    raw = math.sqrt(n) * d_star / omega[None, :]

    p_values: dict[str, float] = {}
    for name, mask in keep.items():
        # Recentred where kept; left at its (negative) sample location otherwise,
        # so hopeless strategies cannot masquerade as the maximum.
        z = np.where(mask[None, :], centred, raw)
        z_max = np.maximum(0.0, z.max(axis=1))
        p_values[name] = float(np.mean(z_max > statistic))

    return SPAResult(
        statistic=statistic,
        p_lower=p_values["lower"],
        p_consistent=p_values["consistent"],
        p_upper=p_values["upper"],
        n_obs=n,
        n_strategies=n_strat,
        n_boot=n_boot,
        block_length=b,
        best_index=best_index,
        mean_differential=d_bar,
        omega=omega,
        t_stats=t_stats,
    )


def spa_p_value(
    differentials: np.ndarray | pd.DataFrame,
    *,
    n_boot: int = 1000,
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
) -> float:
    """Convenience wrapper returning only the consistent p-value."""
    return superior_predictive_ability(
        differentials, n_boot=n_boot, block_length=block_length, rng=rng
    ).p_consistent


@dataclass(frozen=True, slots=True)
class RealityCheckResult:
    statistic: float
    p_value: float
    n_obs: int
    n_strategies: int
    n_boot: int
    block_length: float
    best_index: int
    studentised: bool


def reality_check(
    differentials: np.ndarray | pd.DataFrame,
    *,
    n_boot: int = 1000,
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
    studentised: bool = False,
) -> RealityCheckResult:
    """White's (2000) Reality Check.

    Equivalent to SPA's ``p_upper`` (every strategy recentred).  Kept separate
    because it is the published baseline and because the divergence between this
    and :func:`superior_predictive_ability` is the diagnostic for "my search space
    is full of junk that is hiding a real result".
    """
    d = _as_matrix(differentials)
    n, n_strat = d.shape
    b = _shared_block_length(d, block_length)
    d_bar = d.mean(axis=0)
    d_star = _bootstrap_means(d, n_boot, b, rng)
    scale = _omega(d_star, d_bar, n) if studentised else np.ones(n_strat)

    v = float(np.max(math.sqrt(n) * d_bar / scale))
    v_star = np.max(math.sqrt(n) * (d_star - d_bar[None, :]) / scale[None, :], axis=1)
    return RealityCheckResult(
        statistic=v,
        p_value=float(np.mean(v_star > v)),
        n_obs=n,
        n_strategies=n_strat,
        n_boot=n_boot,
        block_length=b,
        best_index=int(np.argmax(d_bar / scale)),
        studentised=studentised,
    )


@dataclass(frozen=True, slots=True)
class StepMResult:
    """Which strategies survive, under family-wise error control at ``alpha``."""

    rejected: tuple[int, ...]
    """Indices declared better than the benchmark. FWER <= alpha asymptotically."""
    critical_values: tuple[float, ...]
    """One per step; each step's cut-off is computed on the surviving set only."""
    t_stats: np.ndarray = field(repr=False)
    alpha: float = 0.05
    n_steps: int = 0
    n_obs: int = 0
    n_strategies: int = 0
    block_length: float = 1.0

    @property
    def n_rejected(self) -> int:
        return len(self.rejected)


def step_m(
    differentials: np.ndarray | pd.DataFrame,
    *,
    alpha: float = 0.05,
    n_boot: int = 1000,
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
    studentised: bool = True,
    max_steps: int = 100,
) -> StepMResult:
    """Romano-Wolf StepM: identify every strategy that beats the benchmark.

    SPA answers "is ANY of them real?".  StepM answers "WHICH of them are real?",
    which is what a promotion decision actually needs, while still controlling the
    probability of ANY false promotion at ``alpha``.

    Each step recomputes the critical value from the maximum over the strategies
    NOT yet rejected, so the cut-off falls as obvious winners are removed and
    genuine-but-smaller effects become detectable.
    """
    d = _as_matrix(differentials)
    n, n_strat = d.shape
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    b = _shared_block_length(d, block_length)
    d_bar = d.mean(axis=0)
    d_star = _bootstrap_means(d, n_boot, b, rng)
    scale = _omega(d_star, d_bar, n) if studentised else np.ones(n_strat)

    t_stats = math.sqrt(n) * d_bar / scale
    z_star = math.sqrt(n) * (d_star - d_bar[None, :]) / scale[None, :]

    active = np.ones(n_strat, dtype=bool)
    rejected: list[int] = []
    crit_values: list[float] = []
    for _ in range(max_steps):
        if not active.any():
            break
        c = float(np.quantile(z_star[:, active].max(axis=1), 1.0 - alpha))
        crit_values.append(c)
        newly = np.flatnonzero(active & (t_stats > c))
        if newly.size == 0:
            break
        rejected.extend(int(i) for i in newly)
        active[newly] = False

    order = sorted(rejected, key=lambda i: -t_stats[i])
    return StepMResult(
        rejected=tuple(order),
        critical_values=tuple(crit_values),
        t_stats=t_stats,
        alpha=alpha,
        n_steps=len(crit_values),
        n_obs=n,
        n_strategies=n_strat,
        block_length=b,
    )
