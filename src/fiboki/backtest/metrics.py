"""Performance metrics that refuse to flatter.

Two V1 behaviours this module exists to prevent
-------------------------------------------------
1. **Hardcoded annualisation.** V1 multiplied by ``sqrt(252)`` regardless of
   timeframe, so an H1 strategy's Sharpe was understated by ~2.4x and a weekly
   one overstated. V2 derives the annualisation factor from the ACTUAL elapsed
   wall-clock span of the equity curve, so a run that covers 3.7 years of H4
   bars is annualised by the number of H4 bars that really occurred in 3.7
   years, not by a constant someone typed once.
2. **NaN mapped to a flattering value.** V1's ranking code did
   ``profit_factor = min(pf if not isnan(pf) else PF_CAP, PF_CAP)``. A strategy
   with zero losing trades — usually one with two trades total — scored the
   maximum and floated to the top of every ranking. In V2 a degenerate metric
   is either an exception (``strict=True``, the default) or ``None``. It is
   never a number, and never a good number.

Which Sharpe is the default, and why
--------------------------------------
``Metrics.sharpe`` is the **returns-based** estimator computed on the
mark-to-market equity curve. It is the default because:

* it prices the risk of OPEN positions, which is where drawdown actually lives;
* it does not change when a strategy merges two adjacent trades into one;
* it is comparable across strategies with wildly different trade counts.

``Metrics.sharpe_trade_based`` is also reported. It uses per-trade returns and
annualises by the realised trade frequency. It is the more familiar number and
is usually HIGHER, because it ignores the equity path between exits and
entries. When the two disagree materially, believe the returns-based one and
treat the gap as a measure of how much intra-trade risk the trade-based view
is hiding.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from fiboki.core.contracts import Trade

__all__ = [
    "DegenerateMetricError",
    "Metrics",
    "annualisation_factor",
    "compute_metrics",
    "max_drawdown",
    "ulcer_index",
]

_SECONDS_PER_YEAR = 365.25 * 24 * 3600.0


class DegenerateMetricError(ValueError):
    """Raised when a metric cannot be computed from the data it was given.

    Deliberately an error and not a sentinel. A sentinel gets compared, sorted,
    averaged and eventually published.
    """


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        raise DegenerateMetricError(
            "Cannot measure an elapsed span from fewer than two timestamps."
        )
    seconds = (index[-1] - index[0]).total_seconds()
    if seconds <= 0:
        raise DegenerateMetricError(f"Non-positive elapsed span: {seconds}s")
    return seconds / _SECONDS_PER_YEAR


def annualisation_factor(n_observations: int, years: float) -> float:
    """Observations per year, measured — never assumed.

    For an equity curve this is bars per year; for a trade series it is trades
    per year. Both are derived from the same real elapsed span, which is the
    whole point: an H4 FX strategy gets ~1560 and a daily one gets ~260 without
    anyone hardcoding either number.
    """
    if n_observations <= 0:
        raise DegenerateMetricError("Cannot annualise zero observations")
    if years <= 0:
        raise DegenerateMetricError("Cannot annualise over a non-positive span")
    return n_observations / years


def max_drawdown(equity: np.ndarray) -> tuple[float, float]:
    """(absolute, percent) peak-to-trough drawdown of a MARK-TO-MARKET curve.

    Percent is relative to the running peak. A run that goes bankrupt reports
    -100%; a run whose peak is non-positive raises rather than dividing.
    """
    if equity.size == 0:
        raise DegenerateMetricError("Empty equity curve")
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    abs_dd = float(dd.min())
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(peak > 0, dd / peak * 100.0, np.nan)
    if np.all(np.isnan(pct)):
        raise DegenerateMetricError(
            "Equity curve never had a positive peak; percentage drawdown is undefined."
        )
    return abs_dd, float(np.nanmin(pct))


def ulcer_index(equity: np.ndarray) -> float:
    """RMS of percentage drawdown. Penalises depth AND duration, unlike max DD."""
    if equity.size == 0:
        raise DegenerateMetricError("Empty equity curve")
    peak = np.maximum.accumulate(equity)
    if not np.any(peak > 0):
        raise DegenerateMetricError("Ulcer index needs a positive running peak")
    with np.errstate(divide="ignore", invalid="ignore"):
        dd_pct = np.where(peak > 0, (equity - peak) / peak * 100.0, 0.0)
    return float(np.sqrt(np.mean(np.square(dd_pct))))


def _sharpe(returns: np.ndarray, periods_per_year: float, rf_annual: float = 0.0) -> float:
    if returns.size < 2:
        raise DegenerateMetricError(
            f"Sharpe needs at least 2 observations, got {returns.size}."
        )
    sd = float(np.std(returns, ddof=1))
    if sd <= 0 or not np.isfinite(sd):
        raise DegenerateMetricError(
            "Zero or non-finite return dispersion: Sharpe is undefined. This is "
            "almost always a run with one trade or a flat equity curve — do not "
            "substitute a large number for it."
        )
    excess = float(np.mean(returns)) - rf_annual / periods_per_year
    return excess / sd * float(np.sqrt(periods_per_year))


def _sortino(returns: np.ndarray, periods_per_year: float, mar_annual: float = 0.0) -> float:
    if returns.size < 2:
        raise DegenerateMetricError("Sortino needs at least 2 observations")
    mar = mar_annual / periods_per_year
    downside = returns[returns < mar] - mar
    if downside.size == 0:
        raise DegenerateMetricError(
            "No returns below the minimum acceptable return: downside deviation is "
            "zero and Sortino is undefined. V1 reported this as an excellent score."
        )
    dd = float(np.sqrt(np.mean(np.square(downside))))
    if dd <= 0:
        raise DegenerateMetricError("Zero downside deviation")
    return (float(np.mean(returns)) - mar) / dd * float(np.sqrt(periods_per_year))


# --------------------------------------------------------------------------
# The metrics object
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Metrics:
    """Every field is either a real number or ``None``. Never a filler value."""

    n_trades: int
    years: float
    bars_per_year: float
    trades_per_year: float

    total_return_pct: float | None
    cagr_pct: float | None
    final_equity: float
    initial_equity: float

    sharpe: float | None                 # returns-based; the default
    sharpe_trade_based: float | None
    sortino: float | None
    calmar: float | None

    max_drawdown_abs: float
    max_drawdown_pct: float
    ulcer: float | None

    profit_factor: float | None
    expectancy: float
    win_rate_pct: float | None
    avg_trade: float
    median_trade: float
    avg_win: float | None
    avg_loss: float | None
    largest_win: float | None
    largest_loss: float | None
    tail_ratio: float | None

    time_in_market_pct: float | None
    avg_bars_held: float

    mae_mean: float | None
    mae_median: float | None
    mae_p95: float | None
    mfe_mean: float | None
    mfe_median: float | None
    mfe_p95: float | None
    edge_ratio: float | None

    gross_pnl: float
    total_costs: float
    cost_drag_pct_of_gross: float | None

    degenerate: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _maybe(strict: bool, degenerate: list[str], label: str, fn):
    """Run ``fn``; on a degeneracy either raise (strict) or record ``None``."""
    try:
        return fn()
    except DegenerateMetricError as exc:
        if strict:
            raise DegenerateMetricError(f"{label}: {exc}") from exc
        degenerate.append(f"{label}: {exc}")
        return None


def compute_metrics(
    *,
    trades: Sequence[Trade],
    equity_curve: pd.DataFrame,
    initial_equity: float,
    strict: bool = True,
    risk_free_annual: float = 0.0,
    equity_column: str = "equity",
) -> Metrics:
    """Compute the full metric set from a mark-to-market equity curve and trades.

    ``strict=True`` (the default) raises :class:`DegenerateMetricError` the
    moment a metric cannot be honestly computed. Set ``strict=False`` only for
    exploratory sweeps where you intend to handle ``None`` explicitly; any
    ranking code that treats ``None`` as "worst" is correct, and any code that
    treats it as "best" is the V1 bug returning.
    """
    if equity_column not in equity_curve.columns:
        raise KeyError(f"Equity curve has no {equity_column!r} column")
    if equity_curve.empty:
        raise DegenerateMetricError("Empty equity curve: nothing to measure")

    degenerate: list[str] = []
    eq = equity_curve[equity_column].to_numpy(dtype=np.float64)
    idx = equity_curve.index

    years = elapsed_years(idx)
    bars_per_year = annualisation_factor(len(eq), years)
    n_trades = len(trades)
    trades_per_year = n_trades / years if years > 0 else 0.0

    final_equity = float(eq[-1])
    if initial_equity <= 0:
        raise DegenerateMetricError("initial_equity must be positive")

    total_return_pct = (final_equity / initial_equity - 1.0) * 100.0

    def _cagr() -> float:
        if final_equity <= 0:
            raise DegenerateMetricError(
                "Final equity is non-positive; CAGR is undefined (the account was "
                "wiped out — report that, do not report a rate)."
            )
        return ((final_equity / initial_equity) ** (1.0 / years) - 1.0) * 100.0

    cagr_pct = _maybe(strict, degenerate, "cagr", _cagr)

    # -- returns-based risk-adjusted measures ------------------------------
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.diff(eq) / eq[:-1]
    rets = rets[np.isfinite(rets)]

    sharpe = _maybe(strict, degenerate, "sharpe", lambda: _sharpe(rets, bars_per_year, risk_free_annual))
    sortino = _maybe(strict, degenerate, "sortino", lambda: _sortino(rets, bars_per_year, risk_free_annual))

    # -- trade-based Sharpe -------------------------------------------------
    pnl = np.array([t.net_pnl for t in trades], dtype=np.float64)
    entry_equity = _equity_at(equity_curve, [t.entry_time for t in trades], equity_column, initial_equity)

    def _trade_sharpe() -> float:
        if n_trades < 2:
            raise DegenerateMetricError(f"Only {n_trades} trade(s)")
        base = np.where(entry_equity > 0, entry_equity, np.nan)
        r = pnl / base
        r = r[np.isfinite(r)]
        return _sharpe(r, max(trades_per_year, 1e-12), risk_free_annual)

    sharpe_trade_based = _maybe(strict, degenerate, "sharpe_trade_based", _trade_sharpe)

    # -- drawdown -----------------------------------------------------------
    dd_abs, dd_pct = max_drawdown(eq)
    ulcer = _maybe(strict, degenerate, "ulcer", lambda: ulcer_index(eq))

    def _calmar() -> float:
        if cagr_pct is None:
            raise DegenerateMetricError("CAGR unavailable")
        if dd_pct >= -1e-12:
            raise DegenerateMetricError(
                "Max drawdown is zero: Calmar is undefined (dividing by zero "
                "drawdown is how a strategy with one winning trade tops a table)."
            )
        return cagr_pct / abs(dd_pct)

    calmar = _maybe(strict, degenerate, "calmar", _calmar)

    # -- trade statistics ---------------------------------------------------
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    def _pf() -> float:
        if n_trades == 0:
            raise DegenerateMetricError("No trades")
        gross_loss = float(-losses.sum())
        if gross_loss <= 0:
            raise DegenerateMetricError(
                f"No losing trades in {n_trades} trade(s): profit factor is infinite, "
                "not excellent. Report the trade count instead."
            )
        return float(wins.sum()) / gross_loss

    profit_factor = _maybe(strict, degenerate, "profit_factor", _pf)

    def _tail() -> float:
        if rets.size < 20:
            raise DegenerateMetricError(f"Tail ratio needs >=20 returns, got {rets.size}")
        hi = float(np.percentile(rets, 95))
        lo = float(np.percentile(rets, 5))
        if lo >= 0 or abs(lo) < 1e-15:
            raise DegenerateMetricError("Non-negative 5th percentile: tail ratio undefined")
        return abs(hi) / abs(lo)

    tail_ratio = _maybe(strict, degenerate, "tail_ratio", _tail)

    expectancy = float(pnl.mean()) if n_trades else 0.0
    win_rate = (len(wins) / n_trades * 100.0) if n_trades else None
    avg_trade = expectancy
    median_trade = float(np.median(pnl)) if n_trades else 0.0
    avg_win = float(wins.mean()) if wins.size else None
    avg_loss = float(losses.mean()) if losses.size else None
    largest_win = float(wins.max()) if wins.size else None
    largest_loss = float(losses.min()) if losses.size else None

    # -- exposure -----------------------------------------------------------
    def _in_market() -> float:
        if "open_positions" not in equity_curve.columns:
            raise DegenerateMetricError(
                "Equity curve has no 'open_positions' column, so time in market "
                "cannot be measured. Returning NaN here would put a NaN into a "
                "ranking table; returning 0 would claim the strategy never traded."
            )
        return float((equity_curve["open_positions"].to_numpy() > 0).mean() * 100.0)

    in_market = _maybe(strict, degenerate, "time_in_market_pct", _in_market)
    avg_bars_held = float(np.mean([t.bars_held for t in trades])) if n_trades else 0.0

    # -- MAE / MFE ----------------------------------------------------------
    mae = np.array([t.max_adverse_excursion for t in trades], dtype=np.float64)
    mfe = np.array([t.max_favourable_excursion for t in trades], dtype=np.float64)
    if n_trades:
        mae_mean, mae_median = float(mae.mean()), float(np.median(mae))
        mae_p95 = float(np.percentile(np.abs(mae), 95))
        mfe_mean, mfe_median = float(mfe.mean()), float(np.median(mfe))
        mfe_p95 = float(np.percentile(mfe, 95))
        denom = float(np.abs(mae).mean())
        edge_ratio = (mfe_mean / denom) if denom > 0 else None
        if edge_ratio is None:
            degenerate.append("edge_ratio: mean |MAE| is zero")
    else:
        mae_mean = mae_median = mae_p95 = None
        mfe_mean = mfe_median = mfe_p95 = None
        edge_ratio = None

    gross = float(sum(t.gross_pnl for t in trades))
    total_costs = float(sum(t.total_costs for t in trades))
    cost_drag = (total_costs / abs(gross) * 100.0) if abs(gross) > 1e-12 else None
    if cost_drag is None and n_trades:
        degenerate.append("cost_drag_pct_of_gross: gross P&L is ~zero")

    return Metrics(
        n_trades=n_trades,
        years=years,
        bars_per_year=bars_per_year,
        trades_per_year=trades_per_year,
        total_return_pct=total_return_pct,
        cagr_pct=cagr_pct,
        final_equity=final_equity,
        initial_equity=float(initial_equity),
        sharpe=sharpe,
        sharpe_trade_based=sharpe_trade_based,
        sortino=sortino,
        calmar=calmar,
        max_drawdown_abs=dd_abs,
        max_drawdown_pct=dd_pct,
        ulcer=ulcer,
        profit_factor=profit_factor,
        expectancy=expectancy,
        win_rate_pct=win_rate,
        avg_trade=avg_trade,
        median_trade=median_trade,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        tail_ratio=tail_ratio,
        time_in_market_pct=in_market,
        avg_bars_held=avg_bars_held,
        mae_mean=mae_mean,
        mae_median=mae_median,
        mae_p95=mae_p95,
        mfe_mean=mfe_mean,
        mfe_median=mfe_median,
        mfe_p95=mfe_p95,
        edge_ratio=edge_ratio,
        gross_pnl=gross,
        total_costs=total_costs,
        cost_drag_pct_of_gross=cost_drag,
        degenerate=tuple(degenerate),
    )


def _equity_at(
    equity_curve: pd.DataFrame,
    times: Sequence[pd.Timestamp],
    column: str,
    fallback: float,
) -> np.ndarray:
    """As-of equity lookup (never reads forward) for per-trade return bases."""
    if not times:
        return np.array([], dtype=np.float64)
    idx = equity_curve.index
    pos = idx.searchsorted(pd.DatetimeIndex(times), side="right") - 1
    values = equity_curve[column].to_numpy(dtype=np.float64)
    out = np.empty(len(times), dtype=np.float64)
    for k, p in enumerate(pos):
        out[k] = fallback if p < 0 else values[p]
    return out
