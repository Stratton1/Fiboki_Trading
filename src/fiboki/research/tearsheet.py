"""Tearsheet analytics over STORED research results: maths only, no I/O.

Everything the research tearsheet, the run comparison, the Monte Carlo view,
the parameter surface and the walk-forward view draw is computed here, from
the bytes the research ledger already holds (``BacktestRecord.trades``,
``BacktestRecord.equity_curve``, ``BacktestRecord.metrics``) and from the
validation ladder's stored rung metrics. The frontend computes none of it.

Rules this module keeps
-----------------------
* **Nothing is re-run.** A statistic that needs a quantity the record does not
  hold is returned as ``None`` with a reason, never estimated quietly.
* **No invented frequency.** Drawdown, monthly returns and the rolling Sharpe
  are measured on the mark-to-market equity curve the engine wrote; the
  rolling Sharpe uses the same 17:00 New York trading day as
  :func:`fiboki.backtest.metrics.daily_equity`, so it is on the basis of the
  run's own ``sharpe`` figure.
* **Deterministic.** The Monte Carlo takes an explicit seed
  (:func:`fiboki.stats.bootstrap.require_rng` refuses ``None``) and returns it,
  so the same request draws the same fan.
* **Definitions travel with numbers.** :data:`DEFINITIONS` holds the definition
  and the formula for every figure a tearsheet shows; the API sends them, the
  page renders them as metric tooltips.

Known approximations (stated, not hidden)
-----------------------------------------
* R multiples: the planned risk of a trade is ``|entry - initial_stop| * size *
  contract_size * fx_rate_used``. The engine stores the FX rate at the EXIT, so
  a cross-currency trade's risk is converted at the exit rate, not the entry
  rate. Records filed before the engine stamped ``initial_stop`` (2026-10-18)
  have no R at all.
* MAE and MFE are the engine's own per-trade excursions (``Position``
  ``update_excursion`` on every bar, converted at the closing FX rate), read
  from the stored trade rows. Rows filed without the keys report no excursion.
* Turnover converts each trade's notional at the stored (exit) FX rate.
* Buy-and-hold is long one unit of account equity in the instrument's own
  price, without costs, financing or currency conversion.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fiboki.backtest.metrics import TRADING_DAY_END_HOUR, TRADING_DAY_TZ, daily_equity
from fiboki.stats.bootstrap import optimal_block_length, resample_with_compounding
from fiboki.stats.sharpe import probabilistic_sharpe_ratio, sharpe_moments
from fiboki.stats.stability import analyse_parameter_stability, neighbourhood_median_ratio

TEARSHEET_VERSION = "1.0.0"

#: The rolling-Sharpe window, in trading days (about one quarter).
ROLLING_SHARPE_DAYS = 63
#: Drawn series are thinned to at most this many points (min/max preserving).
MAX_SERIES_POINTS = 1_200
#: Monte Carlo fan: the percentiles drawn, and the drawdown table's rows.
FAN_PERCENTILES = (5.0, 25.0, 50.0, 75.0, 95.0)
DRAWDOWN_PERCENTILES = (50.0, 75.0, 90.0, 95.0, 99.0)
#: The default Monte Carlo seed. Shown beside every fan so a re-draw is exact.
DEFAULT_MC_SEED = 20261018


# ============================================================ definitions


@dataclass(frozen=True, slots=True)
class Definition:
    label: str
    definition: str
    formula: str


#: Every tearsheet figure, defined once. Keys are the stat keys the API sends.
DEFINITIONS: dict[str, Definition] = {
    "net_profit": Definition(
        "Net profit",
        "Final equity less initial equity, after every cost the engine charged.",
        "E_T - E_0",
    ),
    "total_return_pct": Definition(
        "Total return", "Net profit as a percentage of initial equity.", "(E_T / E_0 - 1) x 100"
    ),
    "cagr_pct": Definition(
        "CAGR",
        "Compound annual growth rate over the measured elapsed span of the equity curve.",
        "((E_T / E_0) ^ (1 / years) - 1) x 100",
    ),
    "sharpe": Definition(
        "Sharpe (daily)",
        "Mean over standard deviation of daily mark-to-market returns (trading days end "
        "17:00 New York), annualised by the measured trading days per year.",
        "mean(r_d) / sd(r_d) x sqrt(days per year)",
    ),
    "sharpe_lo_adjusted": Definition(
        "Sharpe (Lo-adjusted)",
        "The daily Sharpe corrected for autocorrelation of daily returns (Lo 2002).",
        "SR_daily x eta(q), eta(q) = q / sqrt(q + 2 sum (q-k) rho_k)",
    ),
    "psr": Definition(
        "Probabilistic Sharpe",
        "Probability that the true per-trade Sharpe exceeds zero, given the sample's "
        "length, skew and kurtosis (Bailey and Lopez de Prado 2012).",
        "Z[ SR x sqrt(T - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2) ]",
    ),
    "deflated_sharpe": Definition(
        "Deflated Sharpe",
        "The probabilistic Sharpe measured against the expected maximum Sharpe of N "
        "zero-skill trials, N being the trial count the experiment ledger records for "
        "this strategy's family or campaign. Below 0.95 the result is not "
        "distinguishable from the best of N coin flips.",
        "PSR(SR_0), SR_0 = sqrt(V) [ (1 - g) Z^-1(1 - 1/N) + g Z^-1(1 - 1/(N e)) ]",
    ),
    "max_drawdown_pct": Definition(
        "Max drawdown",
        "Deepest peak-to-trough fall of the mark-to-market equity curve, relative to "
        "the running peak.",
        "min_t (E_t / max_{s<=t} E_s - 1) x 100",
    ),
    "n_trades": Definition("Trades", "Closed trades in the run.", "count"),
    "win_rate_pct": Definition(
        "Win rate", "Share of closed trades with positive net P&L.", "wins / trades x 100"
    ),
    "expectancy": Definition(
        "Expectancy",
        "Mean net P&L per closed trade, in the account currency.",
        "sum(net P&L) / trades",
    ),
    "expectancy_r": Definition(
        "Expectancy (R)",
        "Mean R multiple per trade: net P&L over the risk planned at entry. Trades with "
        "no recorded stop are excluded (the sample size says how many remain).",
        "mean(net P&L_i / (|entry_i - stop_i| x size_i x contract x fx_i))",
    ),
    "profit_factor": Definition(
        "Profit factor",
        "Gross winning P&L over gross losing P&L. Undefined with no losing trade.",
        "sum(wins) / |sum(losses)|",
    ),
    "exposure_pct": Definition(
        "Exposure",
        "Share of the run's bars with at least one open position.",
        "bars in market / bars x 100",
    ),
    "turnover": Definition(
        "Turnover",
        "Round-trip traded notional per year, as a multiple of mean equity.",
        "sum(2 x |size| x contract x entry x fx) / mean(E) / years",
    ),
    "costs_paid": Definition(
        "Costs paid",
        "Spread, commission, slippage and financing the engine charged, in the account "
        "currency.",
        "sum(spread + commission + slippage + financing)",
    ),
    "cost_drag_pct": Definition(
        "Cost drag",
        "Costs as a percentage of gross P&L (absolute).",
        "costs / |gross P&L| x 100",
    ),
    "sortino": Definition(
        "Sortino",
        "Daily mean return over downside deviation, annualised.",
        "mean(r_d) / sqrt(mean(min(r_d, 0)^2)) x sqrt(days per year)",
    ),
    "calmar": Definition("Calmar", "CAGR over the absolute max drawdown.", "CAGR / |max DD|"),
    "avg_bars_held": Definition(
        "Average bars held", "Mean holding period, in the run's bars.", "mean(bars held)"
    ),
    "rolling_sharpe": Definition(
        "Rolling Sharpe",
        f"Sharpe of the trailing {ROLLING_SHARPE_DAYS} trading days of daily returns, "
        "annualised by the measured trading days per year.",
        "mean(r_d[t-w+1..t]) / sd(r_d[t-w+1..t]) x sqrt(days per year)",
    ),
    "monthly_return": Definition(
        "Monthly return",
        "Change of mark-to-market equity from the last point of the previous UTC month "
        "(or the initial equity) to the last point of the month.",
        "E_end(m) / E_end(m-1) - 1",
    ),
    "underwater": Definition(
        "Underwater",
        "Distance of equity below its running peak at every point.",
        "(E_t / max_{s<=t} E_s - 1) x 100",
    ),
    "mae": Definition(
        "MAE",
        "Maximum adverse excursion: the worst open P&L a trade reached, from the "
        "engine's per-bar high/low tracking, in the account currency.",
        "min_t (worst price_t - entry) x direction x size x contract x fx",
    ),
    "mfe": Definition(
        "MFE",
        "Maximum favourable excursion: the best open P&L a trade reached.",
        "max_t (best price_t - entry) x direction x size x contract x fx",
    ),
    "return_correlation": Definition(
        "Return correlation",
        "Pearson correlation of daily mark-to-market returns on the trading days both "
        "runs have.",
        "corr(r_a, r_b) over common days",
    ),
}


def definitions(keys: Iterable[str]) -> dict[str, Definition]:
    return {k: DEFINITIONS[k] for k in keys if k in DEFINITIONS}


# ============================================================ helpers


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _ts(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(str(value))
    except (ValueError, TypeError):
        return None
    if ts is pd.NaT:
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def equity_series(curve: Sequence[Mapping[str, Any]], column: str = "equity") -> pd.Series:
    """The stored equity curve as a UTC-indexed float series (sorted, deduplicated)."""
    times: list[pd.Timestamp] = []
    values: list[float] = []
    for row in curve:
        t = _ts(row.get("timestamp"))
        v = _num(row.get(column))
        if t is None or v is None:
            continue
        times.append(t)
        values.append(v)
    if not times:
        return pd.Series([], dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    series = pd.Series(values, index=pd.DatetimeIndex(times), dtype=float)
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series


def thin(series: pd.Series, max_points: int = MAX_SERIES_POINTS) -> pd.Series:
    """Deterministic min/max-preserving thinning, so a trough is never dropped."""
    n = len(series)
    if n <= max_points:
        return series
    buckets = max(1, max_points // 2)
    edges = np.linspace(0, n, buckets + 1).astype(int)
    keep: set[int] = {0, n - 1}
    values = series.to_numpy()
    for lo, hi in itertools.pairwise(edges):
        if hi <= lo:
            continue
        chunk = values[lo:hi]
        keep.add(lo + int(np.nanargmin(chunk)))
        keep.add(lo + int(np.nanargmax(chunk)))
    return series.iloc[sorted(keep)]


def points(series: pd.Series, max_points: int = MAX_SERIES_POINTS) -> list[tuple[pd.Timestamp, float | None]]:
    out: list[tuple[pd.Timestamp, float | None]] = []
    for t, v in thin(series, max_points).items():
        fv = float(v)
        out.append((pd.Timestamp(t), fv if math.isfinite(fv) else None))
    return out


# ============================================================ drawdown


def underwater(equity: pd.Series) -> pd.Series:
    """Percentage below the running peak, ``<= 0`` (``nan`` where the peak is not positive)."""
    if equity.empty:
        return equity
    peak = equity.cummax()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(peak > 0, (equity / peak - 1.0) * 100.0, np.nan)
    return pd.Series(out, index=equity.index, dtype=float)


@dataclass(frozen=True, slots=True)
class DrawdownPeriod:
    rank: int
    peak_at: pd.Timestamp
    trough_at: pd.Timestamp
    recovered_at: pd.Timestamp | None
    peak_equity: float
    trough_equity: float
    depth_pct: float
    #: Peak to trough, in calendar days.
    decline_days: float
    #: Trough to recovery, in calendar days; ``None`` while still under water.
    recovery_days: float | None
    #: Peak to recovery (or to the end of the curve when not recovered).
    length_days: float


def drawdown_periods(equity: pd.Series, top: int = 5) -> list[DrawdownPeriod]:
    """The ``top`` deepest distinct drawdowns, deepest first.

    A period opens at a running high, bottoms at the lowest point before equity
    regains that high, and closes at the first point at or above it (or stays
    open at the end of the curve).
    """
    if len(equity) < 2:
        return []
    values = equity.to_numpy(dtype=float)
    index = equity.index
    raw: list[tuple[int, int, int | None]] = []
    peak_i = 0
    trough_i: int | None = None
    for i in range(1, len(values)):
        if values[i] >= values[peak_i]:
            if trough_i is not None:
                raw.append((peak_i, trough_i, i))
                trough_i = None
            peak_i = i
        elif trough_i is None or values[i] < values[trough_i]:
            trough_i = i
    if trough_i is not None:
        raw.append((peak_i, trough_i, None))
    periods: list[tuple[float, int, int, int | None]] = []
    for p, t, r in raw:
        if values[p] <= 0:
            continue
        depth = (values[t] / values[p] - 1.0) * 100.0
        if depth < 0:
            periods.append((depth, p, t, r))
    periods.sort(key=lambda x: (x[0], x[1]))
    day = 86_400.0
    out: list[DrawdownPeriod] = []
    end_t = index[-1]
    for rank, (depth, p, t, r) in enumerate(periods[:top], start=1):
        recovered = index[r] if r is not None else None
        out.append(
            DrawdownPeriod(
                rank=rank,
                peak_at=index[p],
                trough_at=index[t],
                recovered_at=recovered,
                peak_equity=float(values[p]),
                trough_equity=float(values[t]),
                depth_pct=float(depth),
                decline_days=(index[t] - index[p]).total_seconds() / day,
                recovery_days=(
                    (recovered - index[t]).total_seconds() / day if recovered is not None else None
                ),
                length_days=((recovered or end_t) - index[p]).total_seconds() / day,
            )
        )
    return out


# ============================================================ monthly returns


@dataclass(frozen=True, slots=True)
class MonthlyCell:
    year: int
    month: int
    return_pct: float | None


@dataclass(frozen=True, slots=True)
class MonthlyTable:
    cells: list[MonthlyCell]
    #: Compounded calendar-year return, per year present.
    years: dict[int, float | None]


def monthly_returns(equity: pd.Series, initial_equity: float | None = None) -> MonthlyTable:
    """Calendar-month (UTC) returns of the mark-to-market curve.

    The first month is measured from ``initial_equity`` when given (else the
    curve's first point), every later month from the previous month's last
    point. A month with no point is absent, not zero.
    """
    if equity.empty:
        return MonthlyTable(cells=[], years={})
    naive = equity.index.tz_convert("UTC").tz_localize(None)
    period = naive.to_period("M")
    ends = pd.Series(equity.to_numpy(dtype=float), index=period).groupby(level=0).last()
    base = float(initial_equity) if initial_equity else float(equity.iloc[0])
    cells: list[MonthlyCell] = []
    previous = base
    for per, value in ends.items():
        ret = (value / previous - 1.0) * 100.0 if previous > 0 else None
        cells.append(MonthlyCell(year=int(per.year), month=int(per.month), return_pct=ret))
        previous = float(value)
    years: dict[int, float | None] = {}
    prev_year_end = base
    for year in sorted({c.year for c in cells}):
        year_end = float(ends[[p for p in ends.index if p.year == year][-1]])
        years[year] = (year_end / prev_year_end - 1.0) * 100.0 if prev_year_end > 0 else None
        prev_year_end = year_end
    return MonthlyTable(cells=cells, years=years)


# ============================================================ distributions


@dataclass(frozen=True, slots=True)
class Histogram:
    edges: list[float]
    counts: list[int]
    n: int
    mean: float | None
    median: float | None


def histogram(values: Sequence[float], *, max_bins: int = 40, min_bins: int = 5) -> Histogram:
    arr = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if arr.size == 0:
        return Histogram(edges=[], counts=[], n=0, mean=None, median=None)
    if float(arr.min()) == float(arr.max()):
        lo = float(arr.min())
        return Histogram(
            edges=[lo - 0.5, lo + 0.5], counts=[int(arr.size)], n=int(arr.size),
            mean=float(arr.mean()), median=float(np.median(arr)),
        )
    auto = np.histogram_bin_edges(arr, bins="fd")
    n_bins = int(min(max(len(auto) - 1, min_bins), max_bins))
    counts, edges = np.histogram(arr, bins=n_bins)
    return Histogram(
        edges=[float(e) for e in edges],
        counts=[int(c) for c in counts],
        n=int(arr.size),
        mean=float(arr.mean()),
        median=float(np.median(arr)),
    )


# ============================================================ trades


def trade_risk(trade: Mapping[str, Any], contract_size: float) -> float | None:
    """Planned risk at entry in the account currency, or ``None`` without a stop."""
    entry = _num(trade.get("entry_price"))
    stop = _num(trade.get("initial_stop"))
    size = _num(trade.get("size"))
    fx = _num(trade.get("fx_rate_used"))
    if entry is None or stop is None or size is None:
        return None
    risk = abs(entry - stop) * abs(size) * float(contract_size) * (fx if fx is not None else 1.0)
    return risk if risk > 0 else None


def r_multiple(trade: Mapping[str, Any], contract_size: float) -> float | None:
    risk = trade_risk(trade, contract_size)
    pnl = _num(trade.get("net_pnl"))
    if risk is None or pnl is None:
        return None
    return pnl / risk


@dataclass(frozen=True, slots=True)
class TradeExcursion:
    trade_id: str
    instrument: str
    direction: str
    entry_time: pd.Timestamp | None
    exit_time: pd.Timestamp | None
    net_pnl: float | None
    r_multiple: float | None
    mae: float | None
    mfe: float | None
    mae_r: float | None
    mfe_r: float | None
    exit_reason: str
    bars_held: int | None


def excursions(
    trades: Sequence[Mapping[str, Any]], contract_sizes: Mapping[str, float]
) -> list[TradeExcursion]:
    """Per-trade P&L, R, MAE and MFE as the engine recorded them (no re-run)."""
    out: list[TradeExcursion] = []
    for t in trades:
        instrument = str(t.get("instrument") or "")
        cs = float(contract_sizes.get(instrument.upper(), 1.0))
        risk = trade_risk(t, cs)
        mae = _num(t.get("max_adverse_excursion")) if "max_adverse_excursion" in t else None
        mfe = _num(t.get("max_favourable_excursion")) if "max_favourable_excursion" in t else None
        pnl = _num(t.get("net_pnl"))
        bars = t.get("bars_held")
        out.append(
            TradeExcursion(
                trade_id=str(t.get("trade_id") or ""),
                instrument=instrument,
                direction=str(t.get("direction") or ""),
                entry_time=_ts(t.get("entry_time")),
                exit_time=_ts(t.get("exit_time")),
                net_pnl=pnl,
                r_multiple=(pnl / risk) if (risk and pnl is not None) else None,
                mae=mae,
                mfe=mfe,
                mae_r=(mae / risk) if (risk and mae is not None) else None,
                mfe_r=(mfe / risk) if (risk and mfe is not None) else None,
                exit_reason=str(t.get("exit_reason") or ""),
                bars_held=int(bars) if isinstance(bars, int) and not isinstance(bars, bool) else None,
            )
        )
    return out


def trade_returns(trades: Sequence[Mapping[str, Any]], initial_equity: float) -> np.ndarray:
    """Per-trade returns on running equity, in order (the validation job's basis)."""
    equity = float(initial_equity)
    out: list[float] = []
    for trade in trades:
        pnl = _num(trade.get("net_pnl")) or 0.0
        if equity <= 0:
            break
        out.append(pnl / equity)
        equity += pnl
    return np.array(out, dtype=float)


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    spread: float
    commission: float
    slippage: float
    financing: float

    @property
    def total(self) -> float:
        return self.spread + self.commission + self.slippage + self.financing


def costs(trades: Sequence[Mapping[str, Any]]) -> CostBreakdown:
    def total(key: str) -> float:
        return float(sum(_num(t.get(key)) or 0.0 for t in trades))

    return CostBreakdown(
        spread=total("spread_cost"),
        commission=total("commission"),
        slippage=total("slippage_cost"),
        financing=total("financing_cost"),
    )


def turnover(
    trades: Sequence[Mapping[str, Any]],
    contract_sizes: Mapping[str, float],
    mean_equity: float | None,
    years: float | None,
) -> tuple[float | None, float]:
    """``(annual turnover as a multiple of mean equity, total round-trip notional)``."""
    notional = 0.0
    for t in trades:
        cs = float(contract_sizes.get(str(t.get("instrument") or "").upper(), 1.0))
        size = _num(t.get("size")) or 0.0
        entry = _num(t.get("entry_price")) or 0.0
        fx = _num(t.get("fx_rate_used"))
        notional += 2.0 * abs(size) * cs * abs(entry) * (fx if fx is not None else 1.0)
    if not mean_equity or mean_equity <= 0 or not years or years <= 0:
        return None, notional
    return notional / mean_equity / years, notional


@dataclass(frozen=True, slots=True)
class TradeStats:
    n: int
    net: float
    win_rate_pct: float | None
    profit_factor: float | None
    expectancy: float | None
    expectancy_r: float | None
    n_with_r: int


def trade_stats(trades: Sequence[Mapping[str, Any]], contract_sizes: Mapping[str, float]) -> TradeStats:
    pnls = [p for p in (_num(t.get("net_pnl")) for t in trades) if p is not None]
    rs = [
        r for r in (
            r_multiple(t, float(contract_sizes.get(str(t.get("instrument") or "").upper(), 1.0)))
            for t in trades
        ) if r is not None
    ]
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    return TradeStats(
        n=len(pnls),
        net=float(sum(pnls)),
        win_rate_pct=(sum(1 for p in pnls if p > 0) / len(pnls) * 100.0) if pnls else None,
        profit_factor=(wins / abs(losses)) if losses < 0 else None,
        expectancy=(float(np.mean(pnls)) if pnls else None),
        expectancy_r=(float(np.mean(rs)) if rs else None),
        n_with_r=len(rs),
    )


def by_instrument(
    trades: Sequence[Mapping[str, Any]], contract_sizes: Mapping[str, float]
) -> dict[str, TradeStats]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for t in trades:
        groups.setdefault(str(t.get("instrument") or "").upper(), []).append(t)
    return {k: trade_stats(v, contract_sizes) for k, v in sorted(groups.items())}


# ============================================================ Sharpe family


def daily_returns(equity: pd.Series, initial_equity: float | None = None) -> pd.Series:
    """Daily (17:00 New York) simple returns of the mark-to-market curve."""
    if len(equity) < 2:
        return pd.Series([], dtype=float)
    closes = daily_equity(equity, initial_equity=initial_equity)
    rets = closes.pct_change().dropna()
    return rets[np.isfinite(rets.to_numpy())]


def rolling_sharpe(
    returns: pd.Series, *, window: int = ROLLING_SHARPE_DAYS, days_per_year: float | None
) -> pd.Series:
    if returns.empty or len(returns) < window or not days_per_year or days_per_year <= 0:
        return pd.Series([], dtype=float)
    mean = returns.rolling(window, min_periods=window).mean()
    sd = returns.rolling(window, min_periods=window).std(ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = mean / sd.where(sd > 0) * math.sqrt(days_per_year)
    return out.dropna()


def measured_days_per_year(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    span = (returns.index[-1] - returns.index[0]).total_seconds() / (365.25 * 86_400.0)
    return len(returns) / span if span > 0 else None


@dataclass(frozen=True, slots=True)
class SharpeProbability:
    psr: float | None
    sr_per_trade: float | None
    n_obs: int
    skew: float | None
    kurtosis: float | None
    reason: str = ""


def per_trade_psr(trades: Sequence[Mapping[str, Any]], initial_equity: float) -> SharpeProbability:
    """PSR on the validation job's own basis (per-trade returns on running equity)."""
    returns = trade_returns(trades, initial_equity)
    if returns.size < 2 or float(np.std(returns, ddof=1)) <= 0.0:
        return SharpeProbability(
            None, None, int(returns.size), None, None,
            "fewer than two trades with non-zero dispersion",
        )
    m = sharpe_moments(returns)
    try:
        psr = probabilistic_sharpe_ratio(m.sr_hat, m.n_obs, m.skew, m.kurtosis, 0.0)
    except ValueError as exc:
        return SharpeProbability(None, m.sr_hat, m.n_obs, m.skew, m.kurtosis, str(exc))
    return SharpeProbability(psr, m.sr_hat, m.n_obs, m.skew, m.kurtosis)


# ============================================================ benchmark


def buy_and_hold(equity: pd.Series, closes: pd.Series, initial_equity: float) -> pd.Series:
    """``initial_equity`` held long in the instrument, on the equity curve's clock.

    Each equity timestamp takes the last close at or before it (no look-ahead);
    timestamps before the first close have no benchmark value.
    """
    if equity.empty or closes.empty:
        return pd.Series([], dtype=float)
    closes = closes.sort_index()
    aligned = closes.reindex(equity.index, method="ffill")
    first = aligned.dropna()
    if first.empty or float(first.iloc[0]) <= 0:
        return pd.Series([], dtype=float)
    return (aligned / float(first.iloc[0]) * float(initial_equity)).dropna()


# ============================================================ comparison


def rebased(equity: pd.Series, initial_equity: float | None = None, base: float = 100.0) -> pd.Series:
    start = float(initial_equity) if initial_equity else (float(equity.iloc[0]) if len(equity) else 0.0)
    if start <= 0:
        return pd.Series([], dtype=float)
    return equity / start * base


@dataclass(frozen=True, slots=True)
class CorrelationCell:
    a: str
    b: str
    rho: float | None
    n_common: int


def return_correlations(returns: Mapping[str, pd.Series], *, min_common: int = 20) -> list[CorrelationCell]:
    """Pairwise Pearson correlation of daily returns on common days only (no fill)."""
    names = list(returns)
    out: list[CorrelationCell] = []
    for i, a in enumerate(names):
        for b in names[i:]:
            if a == b:
                n = int(len(returns[a]))
                out.append(CorrelationCell(a, b, 1.0 if n >= 2 else None, n))
                continue
            joined = pd.concat([returns[a], returns[b]], axis=1, join="inner").dropna()
            n = int(len(joined))
            rho: float | None = None
            if n >= min_common:
                x = joined.iloc[:, 0].to_numpy()
                y = joined.iloc[:, 1].to_numpy()
                if float(np.std(x)) > 0 and float(np.std(y)) > 0:
                    rho = float(np.corrcoef(x, y)[0, 1])
            out.append(CorrelationCell(a, b, rho, n))
    return out


# ============================================================ Monte Carlo


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    basis: str
    seed: int
    n_paths: int
    n_steps: int
    block_length: float
    risk_fraction: float
    initial_equity: float
    ruin_fraction: float
    ruin_probability: float
    #: ``percentile -> equity per step`` (step 0 is the start), thinned.
    fan_steps: list[int]
    fan: dict[float, list[float]]
    final_equity_percentiles: dict[float, float]
    drawdown_percentiles: dict[float, float]
    realised_max_drawdown_pct: float | None
    realised_final_equity: float | None
    notes: list[str] = field(default_factory=list)


def monte_carlo(
    *,
    r_multiples: Sequence[float] | None,
    returns: Sequence[float] | None,
    initial_equity: float,
    risk_fraction: float,
    n_paths: int = 1_000,
    seed: int = DEFAULT_MC_SEED,
    ruin_fraction: float = 0.5,
    max_fan_points: int = 200,
) -> MonteCarloResult:
    """Resample the trade sequence with :func:`resample_with_compounding`.

    Basis ``r_multiple`` (every trade has an R) re-sizes each resampled trade
    off running equity at the run's own risk fraction. Basis ``trade_return``
    (stops not recorded) resamples per-trade returns on running equity, which
    is the same compounding with ``risk_fraction = 1``. The stationary bootstrap
    keeps serial dependence (block length from Politis and White, floored at 1
    under eight trades). Deterministic in ``seed``.
    """
    notes: list[str] = []
    if r_multiples is not None and len(r_multiples) >= 2:
        series = np.asarray(r_multiples, dtype=float)
        basis = "r_multiple"
        rf = float(risk_fraction)
    elif returns is not None and len(returns) >= 2:
        series = np.asarray(returns, dtype=float)
        basis = "trade_return"
        rf = 1.0
        notes.append(
            "Stops were not recorded for every trade, so the per-trade return on running "
            "equity is resampled instead of R multiples (sizing is not re-simulated)."
        )
    else:
        raise ValueError("a Monte Carlo of the trade sequence needs at least two trades")
    block = optimal_block_length(series).stationary if series.size >= 8 else 1.0
    sim = resample_with_compounding(
        series,
        n_paths=int(n_paths),
        risk_fraction=rf,
        initial_equity=float(initial_equity),
        ruin_fraction=float(ruin_fraction),
        ruin_basis="peak",
        block_length=block,
        rng=int(seed),
        keep_paths=True,
    )
    paths = sim.equity_paths
    assert paths is not None
    n_steps = paths.shape[1] - 1
    steps = sorted({int(round(s)) for s in np.linspace(0, n_steps, min(max_fan_points, n_steps + 1))})
    fan = {
        p: [float(v) for v in np.percentile(paths[:, steps], p, axis=0)] for p in FAN_PERCENTILES
    }
    # The realised path, for comparison: the same compounding over the trades in order.
    realised = np.empty(series.size + 1)
    realised[0] = float(initial_equity)
    for i, r in enumerate(series):
        realised[i + 1] = max(0.0, realised[i] + realised[i] * rf * r)
    peak = np.maximum.accumulate(realised)
    realised_dd = float(np.max(np.where(peak > 0, (peak - realised) / peak, 0.0)) * 100.0)
    return MonteCarloResult(
        basis=basis,
        seed=int(seed),
        n_paths=int(n_paths),
        n_steps=int(n_steps),
        block_length=float(block),
        risk_fraction=rf,
        initial_equity=float(initial_equity),
        ruin_fraction=float(ruin_fraction),
        ruin_probability=float(sim.ruin_probability),
        fan_steps=steps,
        fan=fan,
        final_equity_percentiles={p: sim.quantile(p / 100.0) for p in FAN_PERCENTILES},
        drawdown_percentiles={p: sim.drawdown_quantile(p / 100.0) * 100.0 for p in DRAWDOWN_PERCENTILES},
        realised_max_drawdown_pct=realised_dd,
        realised_final_equity=float(realised[-1]),
        notes=notes,
    )


# ============================================================ parameter surface


@dataclass(frozen=True, slots=True)
class SurfaceCell:
    x: float
    y: float
    score: float | None
    n_runs: int
    run_ids: tuple[str, ...]
    point_plateau_ratio: float | None = None
    neighbourhood_median_ratio: float | None = None
    neighbourhood_min: float | None = None
    on_plateau: bool = False
    isolated_peak: bool = False
    #: Per plateau gate: ``gate name -> passed``.
    plateau_checks: dict[str, bool] = field(default_factory=dict)


def _finite(v: float) -> float | None:
    return float(v) if v is not None and math.isfinite(float(v)) else None


def parameter_surface(
    observations: Sequence[tuple[float, float, float | None, str]],
    *,
    x_values: Sequence[float],
    y_values: Sequence[float],
    radius: int,
    plateau_gates: Sequence[Any],
) -> list[SurfaceCell]:
    """A 2D score surface with the ladder's plateau definition applied per cell.

    ``observations`` are ``(x, y, score, run_id)``; several runs in one cell
    are averaged (the cell says how many). Neighbourhood statistics come from
    :func:`fiboki.stats.stability.analyse_parameter_stability` over the
    evaluated cells, in grid-index space with the ladder's radius; a cell is
    ``on_plateau`` when every plateau gate of the production set passes for it
    (and its score is positive), exactly as rung 4 would judge it if it were
    the selected point.
    """
    grouped: dict[tuple[float, float], list[tuple[float | None, str]]] = {}
    for x, y, score, run_id in observations:
        grouped.setdefault((float(x), float(y)), []).append((score, run_id))
    scored: dict[tuple[float, float], float] = {}
    for key, rows in grouped.items():
        finite = [s for s, _ in rows if s is not None and math.isfinite(s)]
        if finite:
            scored[key] = float(np.mean(finite))
    stats: dict[tuple[float, float], Any] = {}
    if len(scored) >= 2:
        grid = [{"x": k[0], "y": k[1]} for k in scored]
        report = analyse_parameter_stability(grid, list(scored.values()), radius=radius)
        for point in report.points:
            stats[(point.params["x"], point.params["y"])] = point
    cells: list[SurfaceCell] = []
    for x in x_values:
        for y in y_values:
            key = (float(x), float(y))
            rows = grouped.get(key, [])
            score = scored.get(key)
            point = stats.get(key)
            if point is None:
                cells.append(
                    SurfaceCell(x=key[0], y=key[1], score=score, n_runs=len(rows),
                                run_ids=tuple(r for _, r in rows))
                )
                continue
            values = {
                "point_plateau_ratio": _finite(point.point_plateau_ratio),
                "plateau_neighbourhood_median_ratio": _finite(
                    neighbourhood_median_ratio(point.score, point.plateau_median_excluding)
                ),
                "plateau_neighbourhood_min": _finite(point.plateau_min_excluding),
            }
            checks: dict[str, bool] = {}
            for gate in plateau_gates:
                result = gate.evaluate(values.get(gate.metric))
                checks[gate.name] = bool(result.passed)
            cells.append(
                SurfaceCell(
                    x=key[0],
                    y=key[1],
                    score=score,
                    n_runs=len(rows),
                    run_ids=tuple(r for _, r in rows),
                    point_plateau_ratio=values["point_plateau_ratio"],
                    neighbourhood_median_ratio=values["plateau_neighbourhood_median_ratio"],
                    neighbourhood_min=values["plateau_neighbourhood_min"],
                    on_plateau=bool(checks) and all(checks.values()) and (score or 0.0) > 0.0,
                    isolated_peak=bool(point.is_isolated_peak),
                    plateau_checks=checks,
                )
            )
    return cells


# ============================================================ walk-forward


@dataclass(frozen=True, slots=True)
class FoldStability:
    n_folds: int
    oos_profitable_fraction: float | None
    mean_is_rate: float | None
    mean_oos_rate: float | None
    oos_rate_sd: float | None
    #: sd / |mean| of the OOS rate across folds; large means unstable.
    oos_rate_cv: float | None
    #: Per fold: OOS rate over IS rate.
    fold_efficiency: list[float | None]
    #: Folds whose selected parameters equal the previous fold's.
    parameter_repeats: int


def fold_stability(folds: Sequence[Mapping[str, Any]]) -> FoldStability:
    is_rates = [_num(f.get("is_rate")) for f in folds]
    oos_rates = [_num(f.get("oos_rate")) for f in folds]
    finite_oos = [r for r in oos_rates if r is not None]
    finite_is = [r for r in is_rates if r is not None]
    eff: list[float | None] = []
    for a, b in zip(is_rates, oos_rates, strict=True):
        eff.append((b / a) if (a not in (None, 0.0) and b is not None) else None)
    mean_oos = float(np.mean(finite_oos)) if finite_oos else None
    sd = float(np.std(finite_oos, ddof=1)) if len(finite_oos) > 1 else None
    repeats = 0
    for prev, cur in itertools.pairwise(folds):
        if prev.get("selected_params") and prev.get("selected_params") == cur.get("selected_params"):
            repeats += 1
    return FoldStability(
        n_folds=len(folds),
        oos_profitable_fraction=(
            sum(1 for r in finite_oos if r > 0) / len(finite_oos) if finite_oos else None
        ),
        mean_is_rate=float(np.mean(finite_is)) if finite_is else None,
        mean_oos_rate=mean_oos,
        oos_rate_sd=sd,
        oos_rate_cv=(sd / abs(mean_oos)) if (sd is not None and mean_oos not in (None, 0.0)) else None,
        fold_efficiency=eff,
        parameter_repeats=repeats,
    )


# ============================================================ universe correlation


def window_correlation(panel_returns: pd.DataFrame, window: int) -> tuple[pd.DataFrame, int]:
    """Correlation of the last ``window`` common log returns (causal, no fill)."""
    tail = panel_returns.dropna(how="any").tail(int(window))
    return tail.corr(), int(len(tail))


__all__ = [
    "DEFAULT_MC_SEED",
    "DEFINITIONS",
    "TEARSHEET_VERSION",
    "TRADING_DAY_END_HOUR",
    "TRADING_DAY_TZ",
    "buy_and_hold",
    "by_instrument",
    "costs",
    "daily_returns",
    "drawdown_periods",
    "equity_series",
    "excursions",
    "fold_stability",
    "histogram",
    "monte_carlo",
    "monthly_returns",
    "parameter_surface",
    "per_trade_psr",
    "rebased",
    "return_correlations",
    "rolling_sharpe",
    "trade_stats",
    "turnover",
    "underwater",
]
