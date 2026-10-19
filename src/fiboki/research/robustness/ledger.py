"""Robustness diagnostics computed from a stored run's own ledger: maths only, no I/O.

Each function takes a :class:`~fiboki.research.robustness.boundary.ResearchSlice`
(the stored trades and equity curve with the holdout removed) and returns plain
data. Nothing here re-runs the engine and nothing here can change a verdict,
a gate or a threshold: these are ADVISORY figures reported beside the ladder.

* :func:`confidence_bands` (item 3): 5/50/95% block-bootstrap bands for CAGR,
  maximum drawdown, Sharpe, profit factor and win rate, through
  :func:`fiboki.stats.bootstrap.bootstrap_confidence_interval`.
* :func:`breakdowns` (item 5): by exit reason, by direction (the ledger records
  no entry rule), and by year, month, weekday, hour and session of ENTRY.
* :func:`consistency` (item 10): K equal sub-periods of the research window and
  every instrument of a pooled run, with the share of positive splits.
* :func:`objectives` (item 9): selectable ranking objectives, each with an
  explicit penalty below :data:`RANKING_MIN_TRADES` trades.
* :func:`optimisation_profile` (item 8): median versus peak over a parameter
  grid of runs, and the share of the grid that is profitable.
* :func:`fill_detail` (item 12): the fill rule each trade's fills met, from the
  flags the engine records, with the cost decomposition.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from fiboki.backtest.metrics import compute_metrics
from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason, Provenance
from fiboki.marketstate.features import SESSION_NAMES, session_flags
from fiboki.research import tearsheet as ts
from fiboki.research.robustness.boundary import ResearchSlice
from fiboki.stats.bootstrap import bootstrap_confidence_interval

__all__ = [
    "BAND_PERCENTILES",
    "OBJECTIVES",
    "RANKING_MIN_TRADES",
    "breakdowns",
    "confidence_bands",
    "consistency",
    "fill_detail",
    "objectives",
    "optimisation_profile",
    "rehydrate",
    "slice_metrics",
]

#: The trade count below which every ranking objective is penalised. A display
#: and ranking rule only: the ladder's own trade floor is in ``validation/gates.py``.
RANKING_MIN_TRADES = 80
BAND_PERCENTILES = (5.0, 50.0, 95.0)

_DIRECTION_LABEL = {"long": "long", "short": "short"}


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def rehydrate(t: Mapping[str, Any]) -> Trade:
    """A stored trade row as a :class:`Trade` (the fields the metrics read)."""
    return Trade(
        instrument=str(t.get("instrument") or ""),
        direction=Direction(str(t.get("direction") or "long")),
        size=float(t.get("size") or 0.0),
        entry_price=float(t.get("entry_price") or 0.0),
        exit_price=float(t.get("exit_price") or 0.0),
        entry_time=pd.Timestamp(str(t.get("entry_time"))),
        exit_time=pd.Timestamp(str(t.get("exit_time"))),
        exit_reason=ExitReason(str(t.get("exit_reason") or "end_of_data")),
        gross_pnl=float(t.get("gross_pnl") or 0.0),
        spread_cost=float(t.get("spread_cost") or 0.0),
        commission=float(t.get("commission") or 0.0),
        slippage_cost=float(t.get("slippage_cost") or 0.0),
        financing_cost=float(t.get("financing_cost") or 0.0),
        net_pnl=float(t.get("net_pnl") or 0.0),
        account_ccy=str(t.get("account_ccy") or ""),
        strategy_id=str(t.get("strategy_id") or ""),
        bars_held=int(t.get("bars_held") or 0),
        provenance=Provenance(str(t.get("provenance") or "backtest")),
        fx_rate_used=float(t.get("fx_rate_used") or 1.0),
    )


def _equity(slice_: ResearchSlice) -> pd.Series:
    return ts.equity_series(slice_.equity_curve)


def slice_metrics(trades: Sequence[Mapping[str, Any]], equity: pd.Series,
                  initial: float | None) -> dict[str, float | None]:
    """The engine's metric definitions over a slice (``backtest/metrics.py``)."""
    pnl = [float(t.get("net_pnl") or 0.0) for t in trades]
    out: dict[str, float | None] = {
        "n_trades": float(len(trades)),
        "net_profit": float(sum(pnl)),
        "win_rate_pct": (100.0 * sum(p > 0 for p in pnl) / len(pnl)) if pnl else None,
        "expectancy": (float(np.mean(pnl)) if pnl else None),
        "profit_factor": _profit_factor(pnl),
        "sharpe": None, "sortino": None, "calmar": None, "cagr_pct": None,
        "max_drawdown_pct": None, "total_return_pct": None,
    }
    if len(equity) >= 2 and initial and initial > 0:
        m = compute_metrics(trades=[rehydrate(t) for t in trades], equity_curve=equity.to_frame("equity"),
                            initial_equity=float(initial), strict=False)
        out.update({
            "sharpe": _f(m.sharpe), "sortino": _f(m.sortino), "calmar": _f(m.calmar),
            "cagr_pct": _f(m.cagr_pct), "max_drawdown_pct": _f(m.max_drawdown_pct),
            "total_return_pct": _f(m.total_return_pct),
        })
    return out


def _profit_factor(pnl: Sequence[float]) -> float | None:
    wins = sum(p for p in pnl if p > 0)
    losses = -sum(p for p in pnl if p < 0)
    if not pnl or losses == 0:
        return None
    return float(wins / losses)


# ================================================================== 3. bands


@dataclass(frozen=True, slots=True)
class Band:
    key: str
    label: str
    unit: str
    point: float | None
    p5: float | None
    p50: float | None
    p95: float | None
    basis: str
    n_obs: int
    block_length: float | None
    reason: str = ""


def _cagr(days_per_year: float) -> Callable[[np.ndarray], float]:
    def stat(r: np.ndarray) -> float:
        growth = float(np.prod(1.0 + r))
        if growth <= 0 or r.size == 0:
            return -100.0
        return (growth ** (days_per_year / r.size) - 1.0) * 100.0
    return stat


def _mdd(r: np.ndarray) -> float:
    path = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], path]))[1:]
    return float(np.min(path / peak - 1.0) * 100.0)


def _sharpe(days_per_year: float) -> Callable[[np.ndarray], float]:
    def stat(r: np.ndarray) -> float:
        sd = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
        return float(np.mean(r) / sd * math.sqrt(days_per_year)) if sd > 0 else 0.0
    return stat


def _pf(p: np.ndarray) -> float:
    losses = -float(p[p < 0].sum())
    wins = float(p[p > 0].sum())
    return wins / losses if losses > 0 else float("nan")


def _win(p: np.ndarray) -> float:
    return float((p > 0).mean() * 100.0) if p.size else float("nan")


def confidence_bands(slice_: ResearchSlice, *, initial: float | None, seed: int,
                     n_boot: int = 1000) -> dict[str, Any]:
    """5/50/95% stationary-bootstrap bands (Politis-Romano, Politis-White block length).

    CAGR, maximum drawdown and Sharpe resample the DAILY (17:00 New York)
    returns of the equity curve; profit factor and win rate resample the trade
    sequence. Each metric draws from its own generator, seeded ``[seed, i]``, so
    adding a metric never moves another's band. A band that needs more
    observations than the slice holds is returned with a reason instead.
    """
    equity = _equity(slice_)
    rets = ts.daily_returns(equity, initial)
    dpy = ts.measured_days_per_year(rets) or 252.0
    pnl = np.array([float(t.get("net_pnl") or 0.0) for t in slice_.trades], dtype=float)
    specs: list[tuple[str, str, str, np.ndarray, Callable[[np.ndarray], float], str]] = [
        ("cagr_pct", "CAGR", "pct", rets.to_numpy(dtype=float), _cagr(dpy), "daily returns"),
        ("max_drawdown_pct", "Maximum drawdown", "pct", rets.to_numpy(dtype=float), _mdd, "daily returns"),
        ("sharpe", "Sharpe (annualised)", "ratio", rets.to_numpy(dtype=float), _sharpe(dpy), "daily returns"),
        ("profit_factor", "Profit factor", "ratio", pnl, _pf, "trade net P&L"),
        ("win_rate_pct", "Win rate", "pct", pnl, _win, "trade net P&L"),
    ]
    bands: list[Band] = []
    for i, (key, label, unit, series, stat, basis) in enumerate(specs):
        if series.size < 8:
            bands.append(Band(key, label, unit, None, None, None, None, basis, int(series.size), None,
                              f"Needs at least 8 observations ({basis}); the research slice holds {series.size}."))
            continue
        ci = bootstrap_confidence_interval(series, stat, n_boot=n_boot, alpha=0.10,
                                           scheme="stationary", rng=np.random.default_rng([seed, i]))
        dist = ci.distribution[np.isfinite(ci.distribution)]
        if dist.size == 0:
            bands.append(Band(key, label, unit, _f(ci.point), None, None, None, basis, int(series.size),
                              ci.block_length, "Undefined on every resample (no losing trade)."))
            continue
        q = np.quantile(dist, [p / 100.0 for p in BAND_PERCENTILES])
        bands.append(Band(key, label, unit, _f(ci.point), _f(q[0]), _f(q[1]), _f(q[2]), basis,
                          int(series.size), float(ci.block_length),
                          "" if dist.size == ci.distribution.size else
                          f"{ci.distribution.size - dist.size} resample(s) undefined and dropped."))
    return {"bands": bands, "n_boot": int(n_boot), "seed": int(seed), "days_per_year": float(dpy),
            "n_daily_returns": int(rets.size), "n_trades": int(pnl.size)}


# ================================================================ 5. breakdowns


@dataclass(frozen=True, slots=True)
class Bucket:
    key: str
    n: int
    net_pnl: float
    win_rate_pct: float | None
    expectancy: float | None
    profit_factor: float | None
    avg_bars_held: float | None


def _bucket(key: str, rows: Sequence[Mapping[str, Any]]) -> Bucket:
    pnl = [float(r.get("net_pnl") or 0.0) for r in rows]
    bars = [int(r["bars_held"]) for r in rows if isinstance(r.get("bars_held"), int)]
    return Bucket(
        key=key, n=len(rows), net_pnl=float(sum(pnl)),
        win_rate_pct=(100.0 * sum(p > 0 for p in pnl) / len(pnl)) if pnl else None,
        expectancy=float(np.mean(pnl)) if pnl else None,
        profit_factor=_profit_factor(pnl),
        avg_bars_held=float(np.mean(bars)) if bars else None,
    )


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def breakdowns(slice_: ResearchSlice) -> dict[str, list[Bucket]]:
    """Trade results grouped by exit reason, direction, and the entry's calendar position.

    All calendar keys are the ENTRY bar's UTC timestamp; ``session`` is
    :func:`fiboki.marketstate.features.session_flags` of that timestamp (Tokyo,
    London and New York local hours, overlaps named as such).
    """
    rows = list(slice_.trades)
    groups: dict[str, dict[str, list[Mapping[str, Any]]]] = {
        "exit_reason": {}, "direction": {}, "year": {}, "month": {}, "weekday": {}, "hour": {}, "session": {},
    }
    times = [ts._ts(r.get("entry_time")) for r in rows]
    valid = [t for t in times if t is not None]
    sessions: dict[pd.Timestamp, str] = {}
    if valid:
        idx = pd.DatetimeIndex(sorted(set(valid)))
        codes = session_flags(idx)["session_code"].to_numpy()
        sessions = {t: SESSION_NAMES[int(c)] for t, c in zip(idx, codes, strict=True)}
    for r, t in zip(rows, times, strict=True):
        groups["exit_reason"].setdefault(str(r.get("exit_reason") or "unknown"), []).append(r)
        groups["direction"].setdefault(_DIRECTION_LABEL.get(str(r.get("direction")), "unknown"), []).append(r)
        if t is None:
            continue
        groups["year"].setdefault(f"{t.year}", []).append(r)
        groups["month"].setdefault(f"{t.month:02d} {_MONTHS[t.month - 1]}", []).append(r)
        groups["weekday"].setdefault(f"{t.weekday()} {_WEEKDAYS[t.weekday()]}", []).append(r)
        groups["hour"].setdefault(f"{t.hour:02d}", []).append(r)
        groups["session"].setdefault(sessions.get(t, "unknown"), []).append(r)
    return {name: [_bucket(k, v) for k, v in sorted(g.items())] for name, g in groups.items()}


# ============================================================== 10. consistency


@dataclass(frozen=True, slots=True)
class Split:
    key: str
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    metrics: dict[str, float | None]

    @property
    def positive(self) -> bool | None:
        net = self.metrics.get("net_profit")
        n = self.metrics.get("n_trades") or 0
        return None if (net is None or n == 0) else net > 0


def consistency(slice_: ResearchSlice, *, k: int, initial: float | None) -> dict[str, Any]:
    """K equal-time sub-periods of the research window, and every instrument.

    A trade belongs to the period in which it EXITED (when its P&L was
    realised). A period with no trade has no sign and is not counted in the
    share of positive splits; the count of empty splits is reported.
    """
    if k < 2:
        raise ValueError("consistency needs at least two sub-periods")
    equity = _equity(slice_)
    rows = list(slice_.trades)
    periods: list[Split] = []
    if len(equity) >= 2:
        first, last = equity.index[0], equity.index[-1]
        edges = [first + (last - first) * (i / k) for i in range(k + 1)]
        for i in range(k):
            lo, hi = edges[i], edges[i + 1]
            inside = [r for r in rows if (t := ts._ts(r.get("exit_time"))) is not None
                      and lo <= t and (t < hi or (i == k - 1 and t <= hi))]
            sub = equity[(equity.index >= lo) & ((equity.index < hi) | (i == k - 1))]
            opening = float(equity[equity.index < lo].iloc[-1]) if (equity.index < lo).any() else (initial or None)
            periods.append(Split(f"P{i + 1}", lo, hi, slice_metrics(inside, sub, opening)))
    by_inst: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_inst.setdefault(str(r.get("instrument") or "?").upper(), []).append(r)
    instruments = [Split(sym, None, None, slice_metrics(v, pd.Series(dtype=float), None))
                   for sym, v in sorted(by_inst.items())]

    def share(splits: list[Split]) -> tuple[float | None, int, int]:
        signed = [s.positive for s in splits if s.positive is not None]
        empty = sum(1 for s in splits if s.positive is None)
        return ((sum(signed) / len(signed)) if signed else None), len(signed), empty

    p_share, p_n, p_empty = share(periods)
    i_share, i_n, i_empty = share(instruments)
    return {
        "k": int(k), "periods": periods, "instruments": instruments,
        "period_positive_share": p_share, "period_signed": p_n, "period_empty": p_empty,
        "instrument_positive_share": i_share, "instrument_signed": i_n, "instrument_empty": i_empty,
    }


# =============================================================== 9. objectives


@dataclass(frozen=True, slots=True)
class ObjectiveSpec:
    key: str
    label: str
    unit: str
    definition: str
    #: The neutral value the penalty shrinks towards (0 for most, 1 for PF).
    neutral: float


OBJECTIVES: dict[str, ObjectiveSpec] = {
    "net_profit": ObjectiveSpec("net_profit", "Net profit", "account",
                                "Sum of net trade P&L over the research slice.", 0.0),
    "sharpe": ObjectiveSpec("sharpe", "Sharpe", "ratio",
                            "Annualised Sharpe of daily (17:00 New York) equity returns.", 0.0),
    "sortino": ObjectiveSpec("sortino", "Sortino", "ratio",
                             "Annualised mean daily return over downside deviation.", 0.0),
    "calmar": ObjectiveSpec("calmar", "Calmar", "ratio", "CAGR over the absolute maximum drawdown.", 0.0),
    "sqn": ObjectiveSpec("sqn", "SQN", "ratio",
                         "Van Tharp's system quality number: sqrt(n) x mean / sd of per-trade R "
                         "(net P&L when a stop was not recorded for every trade).", 0.0),
    "expectancy": ObjectiveSpec("expectancy", "Expectancy", "account", "Mean net P&L per trade.", 0.0),
    "profit_factor": ObjectiveSpec("profit_factor", "Profit factor", "ratio",
                                   "Gross winning P&L over gross losing P&L.", 1.0),
}


def _sqn(trades: Sequence[Mapping[str, Any]], sizes: Mapping[str, float]) -> tuple[float | None, str]:
    rs = [ts.r_multiple(t, sizes.get(str(t.get("instrument") or "").upper(), 1.0)) for t in trades]
    if trades and all(r is not None for r in rs):
        values, basis = np.array(rs, dtype=float), "R"
    else:
        values, basis = np.array([float(t.get("net_pnl") or 0.0) for t in trades], dtype=float), "net P&L"
    if values.size < 2:
        return None, basis
    sd = float(values.std(ddof=1))
    return (float(math.sqrt(values.size) * values.mean() / sd) if sd > 0 else None), basis


def penalised(raw: float | None, n_trades: int, neutral: float, floor: int = RANKING_MIN_TRADES) -> float | None:
    """``raw`` shrunk towards ``neutral`` by ``n / floor`` when ``n < floor``.

    Only the FAVOURABLE side is shrunk: a below-neutral value is left as it is,
    so the penalty can never make a losing strategy look less bad.
    """
    if raw is None:
        return None
    if n_trades >= floor or raw <= neutral:
        return raw
    return neutral + (raw - neutral) * (max(n_trades, 0) / floor)


def objectives(trades: Sequence[Mapping[str, Any]], equity: pd.Series, initial: float | None,
               sizes: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    """Every objective, raw and penalised, over one research slice."""
    m = slice_metrics(trades, equity, initial)
    sqn, sqn_basis = _sqn(trades, sizes)
    n = len(trades)
    raw = {"net_profit": m["net_profit"], "sharpe": m["sharpe"], "sortino": m["sortino"],
           "calmar": m["calmar"], "sqn": sqn, "expectancy": m["expectancy"],
           "profit_factor": m["profit_factor"]}
    out: dict[str, dict[str, Any]] = {}
    for key, spec in OBJECTIVES.items():
        value = raw[key]
        out[key] = {
            "raw": value, "score": penalised(value, n, spec.neutral),
            "penalty_factor": (1.0 if n >= RANKING_MIN_TRADES else n / RANKING_MIN_TRADES),
            "basis": sqn_basis if key == "sqn" else "",
        }
    return out


# ======================================================= 8. optimisation profile


def optimisation_profile(points: Sequence[tuple[dict[str, Any], float | None, int]]) -> dict[str, Any]:
    """Median-versus-peak over a grid: ``points`` are ``(params, objective, n_trades)``.

    System Parameter Permutation (Walton 2014) reads the MEDIAN of the grid as
    the expected performance of the system, because the peak is the quantity the
    search maximised. Points whose objective is undefined are counted, not
    dropped silently.
    """
    defined = [(p, v, n) for p, v, n in points if v is not None]
    values = np.array([v for _, v, _ in defined], dtype=float)
    out: dict[str, Any] = {"n_points": len(points), "n_defined": len(defined),
                           "n_undefined": len(points) - len(defined)}
    if values.size == 0:
        out.update({"peak": None, "median": None, "p25": None, "p75": None, "mean": None,
                    "profitable_share": None, "median_over_peak": None, "peak_params": {}})
        return out
    peak_i = int(np.argmax(values))
    peak = float(values[peak_i])
    median = float(np.median(values))
    out.update({
        "peak": peak, "median": median,
        "p25": float(np.quantile(values, 0.25)), "p75": float(np.quantile(values, 0.75)),
        "mean": float(values.mean()), "min": float(values.min()),
        "profitable_share": float((values > 0).mean()),
        "median_over_peak": (median / peak) if peak > 0 else None,
        "peak_params": dict(defined[peak_i][0]),
        "peak_trades": int(defined[peak_i][2]),
    })
    return out


# ============================================================ 12. fill detail

#: The engine's fill flags (``backtest/position.py``), and what each means.
FILL_RULES: dict[str, str] = {
    "both_touched": "The stop and a target were both inside one bar's range; the intrabar "
                    "policy decided which filled (the path inside a bar is unknowable).",
    "gapped": "The bar OPENED through the level: a stop filled at the worse open (gap loss "
              "taken), a target at the level (no gap gift).",
    "stale": "The bar arrived after a gap of more than the profile's stale multiple of the "
             "bar interval; the fill paid the stale spread multiple.",
    "entry_stale": "The ENTRY bar was stale (see stale).",
    "plain": "The level was touched inside the bar and nothing else applied.",
}


def fill_detail(slice_: ResearchSlice, *, intrabar_policy: str) -> dict[str, Any]:
    """Per trade: the fill rules its fills met and its cost decomposition.

    Reads ``fill_flags`` from the stored trade row (recorded by the engine since
    this field existed); a row without the key reports ``recorded = False`` and
    no rule, never a guessed one.
    """
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    recorded = 0
    for t in slice_.trades:
        flags = t.get("fill_flags")
        has = isinstance(flags, list | tuple)
        if has:
            recorded += 1
            rule_set = [str(f) for f in flags] or ["plain"]
            for f in rule_set:
                counts[f] = counts.get(f, 0) + 1
        else:
            rule_set = []
        gross = _f(t.get("gross_pnl"))
        rows.append({
            "trade_id": str(t.get("trade_id") or ""),
            "instrument": str(t.get("instrument") or ""),
            "direction": str(t.get("direction") or ""),
            "entry_time": ts._ts(t.get("entry_time")),
            "exit_time": ts._ts(t.get("exit_time")),
            "exit_reason": str(t.get("exit_reason") or ""),
            "recorded": has,
            "rules": rule_set,
            "gross_pnl": gross,
            "spread_cost": _f(t.get("spread_cost")),
            "slippage_cost": _f(t.get("slippage_cost")),
            "commission": _f(t.get("commission")),
            "financing_cost": _f(t.get("financing_cost")),
            "net_pnl": _f(t.get("net_pnl")),
        })
    return {"rows": rows, "counts": dict(sorted(counts.items())), "recorded": recorded,
            "intrabar_policy": intrabar_policy, "rules": FILL_RULES}
