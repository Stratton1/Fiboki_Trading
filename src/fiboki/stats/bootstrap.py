"""Dependence-aware resampling.

Trading returns are serially dependent: a trend day is not 24 independent hourly
draws.  An iid bootstrap on such a series understates every dispersion it
touches, which is the same direction of error as every other shortcut V1 took.
Everything here therefore resamples BLOCKS, with the block length estimated from
the data rather than guessed.

The other V1 defect fixed here is in :func:`resample_with_compounding`.  V1's
Monte Carlo drew a trade sequence and applied the ORIGINAL, frozen position
sizes to it.  Under fixed-fractional sizing that is the wrong experiment: real
stakes track running equity, so risk stays constant in PERCENTAGE terms while a
frozen stake becomes a smaller and smaller fraction of a growing account.

The consequence, verified in ``tests/unit/test_bootstrap.py`` over a grid of win
rates, payoffs, seeds and risk fractions: against a PEAK-RELATIVE ruin barrier -
which is what a maximum-drawdown promotion gate actually is - frozen sizing
understates the ruin probability at every realistic risk setting (>= 5% per
trade), by 0.08 to 0.35 in probability, and understates the median maximum
drawdown for every positive-expectancy sample.

The mechanism, stated precisely because the folk version ("compounding is always
riskier") is false: once equity has grown above its starting point, a frozen
stake is a SMALLER percentage of the account, so every subsequent drawdown is
shallower in percentage terms than the one the strategy would really take.  The
effect reverses for paths that draw down before they grow - there a frozen stake
never de-risks and so ruins faster, since ``prod(1 - f) > 1 - k*f``.  That is why
the gap is uniform in the median and at realistic risk fractions but is NOT a
universal ordering: at 1-2% risk with a marginal edge the sign can flip, and
``ruin_basis="initial"`` flips it more often still.  Frozen-size Monte Carlo is
therefore not a conservative bound in either direction - it is simply the wrong
experiment.

References
----------
Politis, D. and Romano, J. (1994). "The Stationary Bootstrap", JASA 89, 1303-1313.
Politis, D. and White, H. (2004). "Automatic Block-Length Selection for the
    Dependent Bootstrap", Econometric Reviews 23(1), 53-70.
Patton, A., Politis, D. and White, H. (2009). "Correction to Automatic
    Block-Length Selection...", Econometric Reviews 28(4), 372-375.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "BlockLengthEstimate",
    "BootstrapCI",
    "RuinSimulation",
    "bootstrap_confidence_interval",
    "compare_sizing_modes",
    "iid_bootstrap_indices",
    "moving_block_bootstrap",
    "moving_block_bootstrap_indices",
    "optimal_block_length",
    "resample_fixed_size",
    "resample_with_compounding",
    "stationary_bootstrap",
    "stationary_bootstrap_indices",
]


def _as_1d(x: np.ndarray | pd.Series | list[float]) -> np.ndarray:
    arr = np.asarray(x, dtype=float).ravel()
    if arr.size == 0:
        raise ValueError("series is empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError("series contains non-finite values")
    return arr


def _rng(rng: np.random.Generator | int | None) -> np.random.Generator:
    if isinstance(rng, np.random.Generator):
        return rng
    return np.random.default_rng(rng)


# --------------------------------------------------------------- index draws


def iid_bootstrap_indices(
    n_obs: int, n_boot: int, rng: np.random.Generator | int | None = None
) -> np.ndarray:
    """``(n_boot, n_obs)`` iid indices.  Only valid for serially independent data."""
    if n_obs < 1 or n_boot < 1:
        raise ValueError("n_obs and n_boot must be >= 1")
    return _rng(rng).integers(0, n_obs, size=(n_boot, n_obs))


def stationary_bootstrap_indices(
    n_obs: int,
    block_length: float,
    n_boot: int,
    rng: np.random.Generator | int | None = None,
) -> np.ndarray:
    """``(n_boot, n_obs)`` indices from the Politis-Romano stationary bootstrap.

    Blocks have geometric lengths with mean ``block_length`` and wrap circularly,
    so every observation has equal draw probability and the resampled series is
    stationary (unlike the moving-block bootstrap, whose edges are undersampled).
    """
    if n_obs < 1 or n_boot < 1:
        raise ValueError("n_obs and n_boot must be >= 1")
    if block_length < 1.0:
        raise ValueError("block_length must be >= 1")
    gen = _rng(rng)
    p_new = 1.0 / float(block_length)
    starts = gen.integers(0, n_obs, size=(n_boot, n_obs))
    # new_block[:, 0] is forced True: every path starts a block.
    new_block = gen.random((n_boot, n_obs)) < p_new
    new_block[:, 0] = True
    idx = np.empty((n_boot, n_obs), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for t in range(1, n_obs):
        cont = (idx[:, t - 1] + 1) % n_obs
        idx[:, t] = np.where(new_block[:, t], starts[:, t], cont)
    return idx


def moving_block_bootstrap_indices(
    n_obs: int,
    block_length: int,
    n_boot: int,
    rng: np.random.Generator | int | None = None,
    *,
    circular: bool = True,
) -> np.ndarray:
    """``(n_boot, n_obs)`` indices from the moving-block bootstrap.

    Fixed block length.  ``circular=True`` wraps blocks around the end of the
    series (Kunsch's circular block bootstrap), which keeps every observation
    equally likely; ``circular=False`` is the classic MBB and undersamples the
    two ends.
    """
    if n_obs < 1 or n_boot < 1:
        raise ValueError("n_obs and n_boot must be >= 1")
    b = int(block_length)
    if b < 1:
        raise ValueError("block_length must be >= 1")
    b = min(b, n_obs)
    gen = _rng(rng)
    n_blocks = int(math.ceil(n_obs / b))
    high = n_obs if circular else n_obs - b + 1
    starts = gen.integers(0, max(high, 1), size=(n_boot, n_blocks))
    offsets = np.arange(b, dtype=np.int64)
    idx = (starts[:, :, None] + offsets[None, None, :]).reshape(n_boot, n_blocks * b)
    if circular:
        idx %= n_obs
    return idx[:, :n_obs]


def stationary_bootstrap(
    series: np.ndarray | pd.Series | list[float],
    n_boot: int = 1000,
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
) -> np.ndarray:
    """``(n_boot, len(series))`` stationary-bootstrap resamples of ``series``.

    ``block_length=None`` estimates it with :func:`optimal_block_length`.
    """
    arr = _as_1d(series)
    b = optimal_block_length(arr).stationary if block_length is None else float(block_length)
    return arr[stationary_bootstrap_indices(arr.size, b, n_boot, rng)]


def moving_block_bootstrap(
    series: np.ndarray | pd.Series | list[float],
    n_boot: int = 1000,
    block_length: int | None = None,
    rng: np.random.Generator | int | None = None,
    *,
    circular: bool = True,
) -> np.ndarray:
    """``(n_boot, len(series))`` moving-block resamples of ``series``."""
    arr = _as_1d(series)
    b = (
        max(1, int(round(optimal_block_length(arr).circular)))
        if block_length is None
        else int(block_length)
    )
    return arr[moving_block_bootstrap_indices(arr.size, b, n_boot, rng, circular=circular)]


# ------------------------------------------------------- block-length choice


@dataclass(frozen=True, slots=True)
class BlockLengthEstimate:
    """Politis-White / Patton automatic block lengths."""

    stationary: float
    circular: float
    m_hat: int
    """Lag beyond which the autocorrelation is deemed negligible."""
    bandwidth: int
    """``M = 2 * m_hat``, the flat-top kernel bandwidth actually used."""
    capped: bool
    """True when the raw estimate hit ``min(3*sqrt(n), n/3)``."""


def _flat_top_kernel(x: np.ndarray) -> np.ndarray:
    """Politis-Romano trapezoidal flat-top kernel: 1 on [0, 1/2], tapering to 0 at 1."""
    ax = np.abs(x)
    out = np.zeros_like(ax)
    out[ax <= 0.5] = 1.0
    mid = (ax > 0.5) & (ax <= 1.0)
    out[mid] = 2.0 * (1.0 - ax[mid])
    return out


def _block_length_from_moments(g_hat: float, d_hat: float, n_obs: int) -> float:
    """``b* = (2 * g^2 / D)^(1/3) * n^(1/3)``.

    Split out so the ``b ~ n^(1/3)`` scaling can be tested exactly, independent of
    the noisy autocovariance estimation that supplies ``g`` and ``D``.
    """
    if d_hat <= 0.0:
        raise ValueError("d_hat must be positive")
    return float((2.0 * g_hat**2 / d_hat) ** (1.0 / 3.0) * n_obs ** (1.0 / 3.0))


def optimal_block_length(
    series: np.ndarray | pd.Series | list[float], *, c: float = 2.0
) -> BlockLengthEstimate:
    """Automatic block length (Politis & White 2004, Patton et al. 2009 correction).

    The ``D`` constant differs between the two bootstraps, and the 2004 paper's
    circular-block constant was wrong; the 2009 corrigendum gives
    ``D_CB = (4/3) * g(0)^2`` against ``D_SB = 2 * g(0)^2``.  Both are implemented
    from the corrected form.

    Returns ``b = 1`` (i.e. iid resampling) when no autocorrelation clears the
    ``c * sqrt(log10(n)/n)`` significance band.
    """
    arr = _as_1d(series)
    n = arr.size
    if n < 8:
        raise ValueError("need at least 8 observations to estimate a block length")
    if float(arr.std()) == 0.0:
        return BlockLengthEstimate(1.0, 1.0, 0, 0, False)

    k_n = max(5, int(math.ceil(math.sqrt(math.log10(n)))))
    m_max = int(math.ceil(math.sqrt(n))) + k_n
    m_max = min(m_max, n - 1)
    b_max = math.ceil(min(3.0 * math.sqrt(n), n / 3.0))

    x = arr - arr.mean()
    gamma0 = float(np.dot(x, x) / n)
    gammas = np.array([float(np.dot(x[k:], x[:-k]) / n) for k in range(1, m_max + 1)])
    rho = gammas / gamma0

    # m_hat: smallest m with |rho(m+k)| < crit for all k = 1..k_n
    crit = c * math.sqrt(math.log10(n) / n)
    significant = np.abs(rho) >= crit
    m_hat = 0
    for m in range(0, m_max - k_n + 1):
        if not significant[m : m + k_n].any():
            m_hat = m
            break
    else:
        # Nothing settled: fall back to the last significant lag (Patton's rule).
        nz = np.flatnonzero(significant)
        m_hat = int(nz[-1] + 1) if nz.size else 0

    if m_hat <= 0:
        return BlockLengthEstimate(1.0, 1.0, 0, 0, False)

    bandwidth = min(2 * m_hat, m_max)
    lags = np.arange(-bandwidth, bandwidth + 1)
    kern = _flat_top_kernel(lags / bandwidth)
    cov = np.concatenate([gammas[:bandwidth][::-1], [gamma0], gammas[:bandwidth]])
    g_hat = float(np.sum(kern * np.abs(lags) * cov))
    g0 = float(np.sum(kern * cov))
    if g0 == 0.0 or g_hat == 0.0:
        return BlockLengthEstimate(1.0, 1.0, m_hat, bandwidth, False)

    d_sb = 2.0 * g0**2
    d_cb = (4.0 / 3.0) * g0**2
    b_sb = _block_length_from_moments(g_hat, d_sb, n)
    b_cb = _block_length_from_moments(g_hat, d_cb, n)
    capped = b_sb > b_max or b_cb > b_max
    return BlockLengthEstimate(
        stationary=float(min(max(b_sb, 1.0), b_max)),
        circular=float(min(max(b_cb, 1.0), b_max)),
        m_hat=int(m_hat),
        bandwidth=int(bandwidth),
        capped=bool(capped),
    )


# --------------------------------------------------------- confidence limits


@dataclass(frozen=True, slots=True)
class BootstrapCI:
    point: float
    lower: float
    upper: float
    alpha: float
    method: str
    scheme: str
    block_length: float
    distribution: np.ndarray = field(repr=False)

    @property
    def width(self) -> float:
        return self.upper - self.lower

    @property
    def excludes_zero(self) -> bool:
        return self.lower > 0.0 or self.upper < 0.0


def bootstrap_confidence_interval(
    series: np.ndarray | pd.Series | list[float],
    statistic: Callable[[np.ndarray], float],
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    method: str = "percentile",
    scheme: str = "stationary",
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
) -> BootstrapCI:
    """Block-bootstrap confidence interval for any scalar statistic of a series.

    ``method`` is ``"percentile"`` or ``"basic"`` (Hall's reversed percentile).
    BCa is deliberately NOT offered: its acceleration term is estimated by a
    delete-one jackknife, which assumes exchangeable observations - exactly the
    assumption block resampling exists to avoid.
    """
    arr = _as_1d(series)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if scheme == "stationary":
        b = optimal_block_length(arr).stationary if block_length is None else float(block_length)
        idx = stationary_bootstrap_indices(arr.size, b, n_boot, rng)
    elif scheme == "moving":
        b = (
            max(1.0, round(optimal_block_length(arr).circular))
            if block_length is None
            else float(block_length)
        )
        idx = moving_block_bootstrap_indices(arr.size, int(b), n_boot, rng)
    elif scheme == "iid":
        b = 1.0
        idx = iid_bootstrap_indices(arr.size, n_boot, rng)
    else:
        raise ValueError(f"unknown scheme {scheme!r}")

    samples = arr[idx]
    dist = np.array([float(statistic(row)) for row in samples])
    point = float(statistic(arr))
    lo_q, hi_q = np.quantile(dist, [alpha / 2.0, 1.0 - alpha / 2.0])
    if method == "percentile":
        lower, upper = float(lo_q), float(hi_q)
    elif method == "basic":
        lower, upper = float(2.0 * point - hi_q), float(2.0 * point - lo_q)
    else:
        raise ValueError(f"unknown method {method!r} (use 'percentile' or 'basic')")
    return BootstrapCI(point, lower, upper, alpha, method, scheme, float(b), dist)


# ------------------------------------------------------ ruin / path Monte Carlo


@dataclass(frozen=True, slots=True)
class RuinSimulation:
    """Outcome distribution of a resampled trade sequence."""

    mode: str
    final_equity: np.ndarray = field(repr=False)
    max_drawdown: np.ndarray = field(repr=False)
    """Fractional peak-to-trough drawdown per path, in [0, 1]."""
    ruined: np.ndarray = field(repr=False)
    initial_equity: float
    risk_fraction: float
    ruin_fraction: float
    ruin_basis: str = "peak"
    """``"peak"``: ruin is a drawdown past ``1 - ruin_fraction`` from the running
    peak (a max-drawdown gate).  ``"initial"``: ruin is equity at or below
    ``ruin_fraction * initial_equity``."""
    equity_paths: np.ndarray | None = field(default=None, repr=False)

    @property
    def ruin_probability(self) -> float:
        return float(self.ruined.mean())

    @property
    def n_paths(self) -> int:
        return int(self.final_equity.size)

    def quantile(self, q: float) -> float:
        return float(np.quantile(self.final_equity, q))

    def drawdown_quantile(self, q: float) -> float:
        return float(np.quantile(self.max_drawdown, q))


def _simulate_paths(
    r_paths: np.ndarray,
    *,
    compounding: bool,
    initial_equity: float,
    risk_fraction: float,
    ruin_fraction: float,
    ruin_basis: str,
    keep_paths: bool,
) -> RuinSimulation:
    if ruin_basis not in ("peak", "initial"):
        raise ValueError("ruin_basis must be 'peak' or 'initial'")
    n_paths, n_steps = r_paths.shape
    equity = np.full(n_paths, float(initial_equity))
    peak = equity.copy()
    max_dd = np.zeros(n_paths)
    ruined = np.zeros(n_paths, dtype=bool)
    ruin_level = float(initial_equity) * float(ruin_fraction)
    paths = np.empty((n_paths, n_steps + 1)) if keep_paths else None
    if paths is not None:
        paths[:, 0] = equity

    fixed_stake = float(initial_equity) * float(risk_fraction)
    for t in range(n_steps):
        alive = ~ruined
        # Compounding: the stake tracks RUNNING equity, which is what
        # fixed-fractional sizing actually does and what V1 failed to re-simulate.
        stake = (
            equity * float(risk_fraction) if compounding else np.full(n_paths, fixed_stake)
        )
        equity = np.where(alive, equity + stake * r_paths[:, t], equity)
        equity = np.maximum(equity, 0.0)
        peak = np.maximum(peak, equity)
        dd = np.where(peak > 0.0, (peak - equity) / peak, 0.0)
        max_dd = np.maximum(max_dd, dd)
        if ruin_basis == "peak":
            ruined |= dd >= (1.0 - float(ruin_fraction))
        else:
            ruined |= equity <= ruin_level
        if paths is not None:
            paths[:, t + 1] = equity
    return RuinSimulation(
        mode="compounding" if compounding else "fixed",
        final_equity=equity,
        max_drawdown=max_dd,
        ruined=ruined,
        initial_equity=float(initial_equity),
        risk_fraction=float(risk_fraction),
        ruin_fraction=float(ruin_fraction),
        ruin_basis=ruin_basis,
        equity_paths=paths,
    )


def _r_paths(
    r_multiples: np.ndarray | pd.Series | list[float],
    n_paths: int,
    block_length: float | None,
    rng: np.random.Generator | int | None,
) -> np.ndarray:
    arr = _as_1d(r_multiples)
    if block_length is not None:
        b = float(block_length)
    elif arr.size >= 8:
        b = optimal_block_length(arr).stationary
    else:
        b = 1.0
    return arr[stationary_bootstrap_indices(arr.size, b, n_paths, rng)]


def resample_with_compounding(
    r_multiples: np.ndarray | pd.Series | list[float],
    *,
    n_paths: int = 5000,
    risk_fraction: float = 0.01,
    initial_equity: float = 100_000.0,
    ruin_fraction: float = 0.5,
    ruin_basis: str = "peak",
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
    keep_paths: bool = False,
) -> RuinSimulation:
    """Monte Carlo that RE-SIMULATES position sizing off running equity.

    Parameters
    ----------
    r_multiples
        Per-trade P&L expressed in units of risk (R).  A trade that lost exactly
        its planned risk is ``-1.0``; one that made twice its risk is ``+2.0``.
        Feeding raw currency P&L here silently changes what ``risk_fraction``
        means, so convert first.
    risk_fraction
        Fraction of CURRENT equity risked per trade.
    ruin_fraction
        With ``ruin_basis="peak"`` (default) ruin is a drawdown deeper than
        ``1 - ruin_fraction`` from the running peak, i.e. 0.5 means "lost half
        from the high-water mark".  With ``ruin_basis="initial"`` ruin is equity
        at or below ``ruin_fraction * initial_equity``.  Ruined paths are
        absorbing - they stop trading, as an operator would.

    Notes
    -----
    On the default peak basis, and on identical resampled sequences, this reports
    a strictly HIGHER ruin probability and a deeper median drawdown than
    :func:`resample_fixed_size` across every win rate, payoff and risk fraction
    tested.  A frozen stake becomes a shrinking percentage of a growing account,
    so it quietly de-risks exactly where the drawdown gate is measured.  That gap
    is the bias V1 shipped.
    """
    r_paths = _r_paths(r_multiples, n_paths, block_length, rng)
    return _simulate_paths(
        r_paths,
        compounding=True,
        initial_equity=initial_equity,
        risk_fraction=risk_fraction,
        ruin_fraction=ruin_fraction,
        ruin_basis=ruin_basis,
        keep_paths=keep_paths,
    )


def resample_fixed_size(
    r_multiples: np.ndarray | pd.Series | list[float],
    *,
    n_paths: int = 5000,
    risk_fraction: float = 0.01,
    initial_equity: float = 100_000.0,
    ruin_fraction: float = 0.5,
    ruin_basis: str = "peak",
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
    keep_paths: bool = False,
) -> RuinSimulation:
    """V1-equivalent Monte Carlo: stake frozen at the INITIAL equity.

    Retained only as the comparison baseline for
    :func:`resample_with_compounding`.  Do not use it as a promotion gate.
    """
    r_paths = _r_paths(r_multiples, n_paths, block_length, rng)
    return _simulate_paths(
        r_paths,
        compounding=False,
        initial_equity=initial_equity,
        risk_fraction=risk_fraction,
        ruin_fraction=ruin_fraction,
        ruin_basis=ruin_basis,
        keep_paths=keep_paths,
    )


def compare_sizing_modes(
    r_multiples: np.ndarray | pd.Series | list[float],
    *,
    n_paths: int = 5000,
    risk_fraction: float = 0.01,
    initial_equity: float = 100_000.0,
    ruin_fraction: float = 0.5,
    ruin_basis: str = "peak",
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
) -> tuple[RuinSimulation, RuinSimulation]:
    """Run both sizing modes over the SAME resampled sequences (paired comparison).

    Returns ``(compounding, fixed)``.  Sharing the draws removes Monte Carlo noise
    from the difference, so the gap between the two ruin probabilities is
    attributable purely to the sizing assumption.
    """
    r_paths = _r_paths(r_multiples, n_paths, block_length, rng)
    # Annotated, not inferred: without this the dict is ``dict[str, object]``
    # and every ``**kwargs`` argument is an error the type checker is right
    # about -- ``object`` really is not a float.
    kwargs: dict[str, Any] = {
        "initial_equity": initial_equity,
        "risk_fraction": risk_fraction,
        "ruin_fraction": ruin_fraction,
        "ruin_basis": ruin_basis,
        "keep_paths": False,
    }
    return (
        _simulate_paths(r_paths, compounding=True, **kwargs),
        _simulate_paths(r_paths, compounding=False, **kwargs),
    )
