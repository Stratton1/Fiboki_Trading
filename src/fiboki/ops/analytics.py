"""Advisory portfolio analytics over strategies' trade records (Wave 3O). Display only.

Self-correlation (``self_correlation``)
    The Pearson correlation of a candidate strategy's daily returns with each
    paper bot's, on the days both have a value. A candidate that would mostly
    re-buy the risk the fleet already holds adds less than its own statistics
    suggest; above :data:`SELF_CORRELATION_FLAG` it is flagged, as advice.

Portfolio weights (``portfolio_weights``)
    Equal-risk (equal risk CONTRIBUTION from the covariance), inverse-volatility
    and minimum-correlation weights across the members, with the resulting
    portfolio statistics of each scheme. These are not the sizing rule: the
    paper runtime's portfolio construction (``portfolio/construction.py``)
    decides sizes, once, and nothing here is read by it.

Daily returns, one convention for every member: the closed-trade balance curve
(``initial + cumulative net P&L``, stepped at each exit) at each 17:00 New York
close (:func:`fiboki.backtest.metrics.daily_equity`), carried forward over
every CALENDAR day between the first and last exit, so a day without a closed
trade is a 0 return and FX and crypto members share one calendar (FX weekends
are zeros, which dilutes an FX-crypto correlation slightly towards 0).
Mark-to-market of open positions is NOT included (the stored records do not
all carry it); stated on every view.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from fiboki.backtest.metrics import daily_equity

__all__ = [
    "MIN_COMMON_DAYS",
    "SELF_CORRELATION_FLAG",
    "SchemeResult",
    "daily_returns_from_trades",
    "pairwise",
    "portfolio_weights",
]

SELF_CORRELATION_FLAG = 0.7
MIN_COMMON_DAYS = 20
DAYS_PER_YEAR = 252.0
_ITERATIONS = 2_000


def daily_returns_from_trades(trades: Sequence[Mapping[str, Any]], initial_equity: float | None) -> pd.Series:
    """Daily simple returns of the closed-trade balance curve (17:00 New York days)."""
    if not trades or not initial_equity or initial_equity <= 0:
        return pd.Series([], dtype=float)
    rows = sorted(
        ((pd.Timestamp(t["exit_time"]), float(t["net_pnl"])) for t in trades if t.get("exit_time") is not None),
        key=lambda r: r[0],
    )
    if not rows:
        return pd.Series([], dtype=float)
    index = pd.DatetimeIndex([r[0] for r in rows])
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    equity = pd.Series(float(initial_equity) + np.cumsum([r[1] for r in rows]), index=index)
    equity = equity.groupby(level=0).last()
    closes = daily_equity(equity, initial_equity=float(initial_equity))
    full = closes.reindex(pd.date_range(closes.index[0], closes.index[-1], freq="D"), method="ffill")
    rets = full.pct_change().dropna()
    return rets[np.isfinite(rets.to_numpy())]


@dataclass(frozen=True, slots=True)
class Pair:
    rho: float | None
    n_common: int


def pairwise(a: pd.Series, b: pd.Series, *, min_common: int = MIN_COMMON_DAYS) -> Pair:
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    n = int(len(joined))
    if n < min_common:
        return Pair(None, n)
    x, y = joined.iloc[:, 0].to_numpy(), joined.iloc[:, 1].to_numpy()
    if float(np.std(x)) <= 0 or float(np.std(y)) <= 0:
        return Pair(None, n)
    return Pair(float(np.corrcoef(x, y)[0, 1]), n)


@dataclass(frozen=True, slots=True)
class SchemeResult:
    scheme: str
    weights: dict[str, float]
    annual_return_pct: float | None
    annual_vol_pct: float | None
    sharpe: float | None
    max_drawdown_pct: float | None
    effective_n: float | None
    days: int


def _stats(panel: pd.DataFrame, w: np.ndarray, scheme: str, names: list[str]) -> SchemeResult:
    port = panel.to_numpy() @ w
    days = int(port.size)
    if days < 2:
        return SchemeResult(scheme, dict(zip(names, map(float, w), strict=True)), None, None, None, None, None, days)
    mean, sd = float(np.mean(port)), float(np.std(port, ddof=1))
    curve = np.cumprod(1.0 + port)
    peak = np.maximum.accumulate(np.concatenate([[1.0], curve]))
    full = np.concatenate([[1.0], curve])
    mdd = float(np.max((peak - full) / peak) * 100.0)
    return SchemeResult(
        scheme=scheme,
        weights={n: round(float(x), 6) for n, x in zip(names, w, strict=True)},
        annual_return_pct=round(mean * DAYS_PER_YEAR * 100.0, 4),
        annual_vol_pct=round(sd * math.sqrt(DAYS_PER_YEAR) * 100.0, 4),
        sharpe=round(mean / sd * math.sqrt(DAYS_PER_YEAR), 4) if sd > 0 else None,
        max_drawdown_pct=round(mdd, 4),
        effective_n=round(1.0 / float(np.sum(w**2)), 4) if float(np.sum(w**2)) > 0 else None,
        days=days,
    )


def _project_simplex(v: np.ndarray) -> np.ndarray:
    """Euclidean projection onto {w >= 0, sum w = 1} (Duchi et al. 2008)."""
    u = np.sort(v)[::-1]
    css = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, len(v) + 1) > (css - 1))[0][-1]
    theta = (css[rho] - 1) / (rho + 1.0)
    return np.maximum(v - theta, 0.0)


def _min_quadratic(m: np.ndarray) -> np.ndarray:
    """argmin w'Mw on the simplex by projected gradient: deterministic, fixed iterations."""
    n = m.shape[0]
    w = np.full(n, 1.0 / n)
    step = 1.0 / max(float(np.max(np.abs(np.linalg.eigvalsh(m)))) * 2.0, 1e-12)
    for _ in range(_ITERATIONS):
        w = _project_simplex(w - step * 2.0 * (m @ w))
    return w


def _equal_risk(cov: np.ndarray) -> np.ndarray:
    """Equal risk contribution (Maillard, Roncalli, Teiletche 2010), fixed-point iteration."""
    n = cov.shape[0]
    w = np.full(n, 1.0 / n)
    for _ in range(_ITERATIONS):
        marginal = cov @ w
        rc = w * marginal
        total = float(np.sum(rc))
        if total <= 0:
            break
        target = total / n
        w = w * np.sqrt(target / np.maximum(rc, 1e-18))
        w = w / float(np.sum(w))
    return w


def portfolio_weights(returns: Mapping[str, pd.Series], *, min_common: int = MIN_COMMON_DAYS) -> tuple[list[SchemeResult], list[str], int, str]:
    """``(schemes, members, common_days, reason)``. Deterministic in its inputs."""
    names = sorted(k for k, v in returns.items() if len(v) > 0)
    if len(names) < 2:
        return [], names, 0, "fewer than two members have daily returns"
    panel = pd.concat([returns[n].rename(n) for n in names], axis=1, join="inner").dropna()
    if len(panel) < min_common:
        return [], names, int(len(panel)), (
            f"the members share {len(panel)} day(s); at least {min_common} common days are needed"
        )
    vol = panel.std(ddof=1).to_numpy()
    if np.any(vol <= 0):
        return [], names, int(len(panel)), "a member has no dispersion on the common days"
    cov = panel.cov().to_numpy()
    corr = panel.corr().to_numpy()
    inv = (1.0 / vol) / float(np.sum(1.0 / vol))
    schemes = [
        _stats(panel, np.full(len(names), 1.0 / len(names)), "equal_weight", names),
        _stats(panel, _equal_risk(cov), "equal_risk", names),
        _stats(panel, inv, "inverse_vol", names),
        _stats(panel, _min_quadratic(corr), "min_correlation", names),
    ]
    return schemes, names, int(len(panel)), ""
