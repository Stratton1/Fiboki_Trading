"""Stress suite: how fast does a result die when the assumptions move?

A backtest is a point estimate produced under assumptions that are all optimistic
by default - static spreads, zero slippage, every fill granted, the sample
starting exactly where it starts.  Each function here perturbs ONE assumption
across a grid and returns the whole metric curve, because the shape is the
finding.  A strategy whose profit halves between 1x and 1.5x spread is a spread
artefact; one that degrades linearly out to 5x is a strategy.

Everything operates on closed :class:`fiboki.core.contracts.Trade` objects and
returns a :class:`StressCurve`.  Deterministic stresses give one value per level
unless ``n_samples > 1``, which bootstraps the trade sequence at each level so the
curve carries sampling uncertainty as well as stress sensitivity.

Known approximations, stated rather than hidden:

* Execution-delay stress does not re-walk bar data; it rescales the captured move
  (or charges an adverse penalty).  A true delayed-fill test belongs in the
  backtest engine, at bar level.
* Spread and slippage stresses scale the costs already recorded on each trade.
  A trade recorded with zero spread cost cannot be spread-stressed - the suite
  reports that rather than inventing a cost basis.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from fiboki.core.contracts import Trade

__all__ = [
    "StressCurve",
    "end_date_stress",
    "execution_delay_stress",
    "expectancy",
    "max_drawdown",
    "missing_fill_stress",
    "net_profit",
    "profit_factor",
    "random_deletion_stress",
    "run_stress_suite",
    "slippage_stress",
    "spread_multiplier_stress",
    "start_date_stress",
    "trade_sharpe",
]

TradeMetric = Callable[[Sequence[Trade]], float]


# --------------------------------------------------------------- metrics


def net_profit(trades: Sequence[Trade]) -> float:
    """Total net P&L in the account currency."""
    return float(sum(t.net_pnl for t in trades))


def profit_factor(trades: Sequence[Trade]) -> float:
    """Gross wins / gross losses.  ``inf`` when there are no losers, 0.0 when empty."""
    wins = sum(t.net_pnl for t in trades if t.net_pnl > 0.0)
    losses = -sum(t.net_pnl for t in trades if t.net_pnl < 0.0)
    if not trades:
        return 0.0
    if losses == 0.0:
        return float("inf") if wins > 0.0 else 0.0
    return float(wins / losses)


def trade_sharpe(trades: Sequence[Trade]) -> float:
    """Per-TRADE Sharpe of net P&L.  Not annualised - it has no time basis."""
    if len(trades) < 2:
        return 0.0
    pnl = np.array([t.net_pnl for t in trades], dtype=float)
    sd = pnl.std(ddof=1)
    return float(pnl.mean() / sd) if sd > 0.0 else 0.0


def expectancy(trades: Sequence[Trade]) -> float:
    """Mean net P&L per trade."""
    return float(np.mean([t.net_pnl for t in trades])) if trades else 0.0


def max_drawdown(trades: Sequence[Trade]) -> float:
    """Largest peak-to-trough fall of the cumulative net P&L, as a POSITIVE number."""
    if not trades:
        return 0.0
    order = sorted(trades, key=lambda t: t.exit_time)
    equity = np.cumsum([t.net_pnl for t in order])
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
    return float(np.max(peak - equity))


# ----------------------------------------------------------- curve object


@dataclass(frozen=True, slots=True)
class StressCurve:
    """The metric's response to one perturbation, level by level."""

    name: str
    metric_name: str
    level_name: str
    levels: tuple[float, ...]
    samples: tuple[np.ndarray, ...] = field(repr=False)
    """One array of metric values per level (length 1 for a deterministic stress)."""
    baseline: float = 0.0
    notes: str = ""

    @property
    def median(self) -> np.ndarray:
        return np.array([float(np.median(s)) for s in self.samples])

    @property
    def mean(self) -> np.ndarray:
        return np.array([float(np.mean(s)) for s in self.samples])

    def quantile(self, q: float) -> np.ndarray:
        return np.array([float(np.quantile(s, q)) for s in self.samples])

    @property
    def retention(self) -> np.ndarray:
        """Median metric as a fraction of the unstressed baseline.

        Undefined (``nan``) when the baseline is zero - a strategy with no
        baseline edge cannot be said to retain any of it.
        """
        if self.baseline == 0.0:
            return np.full(len(self.levels), np.nan)
        return self.median / self.baseline

    @property
    def breaking_level(self) -> float | None:
        """First level at which the median metric turns non-positive, if any."""
        med = self.median
        for lvl, value in zip(self.levels, med, strict=True):
            if value <= 0.0:
                return float(lvl)
        return None

    def as_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                self.level_name: self.levels,
                "median": self.median,
                "mean": self.mean,
                "q05": self.quantile(0.05),
                "q95": self.quantile(0.95),
                "retention": self.retention,
            }
        )


# ------------------------------------------------------- trade repricing


def _reprice(
    trade: Trade,
    *,
    spread_multiplier: float = 1.0,
    extra_slippage: float = 0.0,
    gross_scale: float = 1.0,
) -> Trade:
    """Rebuild a trade under stressed costs, preserving any unexplained residual.

    ``net_pnl`` is recomputed as ``gross - costs + residual`` where ``residual``
    is whatever the original trade's net could not be explained by its own gross
    and cost fields.  That keeps engine-specific accounting intact instead of
    quietly overwriting it.
    """
    residual = trade.net_pnl - (trade.gross_pnl - trade.total_costs)
    gross = trade.gross_pnl * gross_scale
    spread = trade.spread_cost * spread_multiplier
    slippage = trade.slippage_cost + extra_slippage
    costs = spread + trade.commission + slippage + trade.financing_cost
    return replace(
        trade,
        gross_pnl=gross,
        spread_cost=spread,
        slippage_cost=slippage,
        net_pnl=gross - costs + residual,
    )


def _resample(
    trades: Sequence[Trade], n_samples: int, rng: np.random.Generator | int | None
) -> list[list[Trade]]:
    """``n_samples`` bootstrap redraws of the trade sequence (1 -> the sequence itself)."""
    if n_samples <= 1:
        return [list(trades)]
    gen = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    n = len(trades)
    return [[trades[i] for i in gen.integers(0, n, size=n)] for _ in range(n_samples)]


def _require(trades: Sequence[Trade]) -> Sequence[Trade]:
    if len(trades) == 0:
        raise ValueError("no trades to stress")
    return trades


# --------------------------------------------------------------- stresses


def spread_multiplier_stress(
    trades: Sequence[Trade],
    multipliers: Sequence[float] = (1.0, 1.25, 1.5, 2.0, 3.0, 5.0),
    metric: TradeMetric = net_profit,
    *,
    n_samples: int = 1,
    rng: np.random.Generator | int | None = None,
) -> StressCurve:
    """Scale every recorded spread cost.

    The default grid runs to 5x because a research spread of 1 pip on EURUSD is a
    weekday-lunchtime spread; news, rollover and thin sessions are multiples of
    it, and V2 research still uses a STATIC spread assumption.
    """
    trades = _require(trades)
    recorded = sum(t.spread_cost for t in trades)
    note = (
        "WARNING: recorded spread cost is zero, so this curve is flat by "
        "construction and says nothing about spread sensitivity."
        if recorded == 0.0
        else ""
    )
    samples: list[np.ndarray] = []
    for m in multipliers:
        stressed = [_reprice(t, spread_multiplier=float(m)) for t in trades]
        samples.append(np.array([metric(s) for s in _resample(stressed, n_samples, rng)]))
    return StressCurve(
        name="spread_multiplier",
        metric_name=getattr(metric, "__name__", "metric"),
        level_name="spread_multiplier",
        levels=tuple(float(m) for m in multipliers),
        samples=tuple(samples),
        baseline=float(metric(list(trades))),
        notes=note,
    )


def slippage_stress(
    trades: Sequence[Trade],
    levels: Sequence[float] = (0.0, 0.25, 0.5, 1.0, 2.0),
    metric: TradeMetric = net_profit,
    *,
    basis: str = "spread_multiple",
    n_samples: int = 1,
    rng: np.random.Generator | int | None = None,
) -> StressCurve:
    """Add slippage on top of whatever the trades already carry.

    ``basis="spread_multiple"`` charges ``level * spread_cost`` per trade, which
    scales sensibly across instruments; ``basis="currency"`` charges a flat
    account-currency amount per trade.  Fiboki research defaults to ZERO
    slippage, so level 0.0 is the as-researched case and every other level is the
    honest one.
    """
    trades = _require(trades)
    if basis not in ("spread_multiple", "currency"):
        raise ValueError("basis must be 'spread_multiple' or 'currency'")
    samples: list[np.ndarray] = []
    for lvl in levels:
        stressed = [
            _reprice(
                t,
                extra_slippage=float(lvl) * (t.spread_cost if basis == "spread_multiple" else 1.0),
            )
            for t in trades
        ]
        samples.append(np.array([metric(s) for s in _resample(stressed, n_samples, rng)]))
    return StressCurve(
        name="slippage",
        metric_name=getattr(metric, "__name__", "metric"),
        level_name=f"slippage_{basis}",
        levels=tuple(float(x) for x in levels),
        samples=tuple(samples),
        baseline=float(metric(list(trades))),
    )


def execution_delay_stress(
    trades: Sequence[Trade],
    levels: Sequence[float] = (0.0, 0.05, 0.10, 0.20, 0.35),
    metric: TradeMetric = net_profit,
    *,
    mode: str = "capture",
    n_samples: int = 1,
    rng: np.random.Generator | int | None = None,
) -> StressCurve:
    """Entering late, approximated without bar data.

    ``mode="capture"`` (default) scales gross P&L by ``1 - level``: a delay costs
    a share of the move, symmetrically, so losers shrink too.  This is the
    optimistic reading and is the right one for a signal that fires mid-move.

    ``mode="adverse"`` charges ``level * |gross|`` as pure cost, never a benefit.
    This is the pessimistic reading, appropriate when the entry edge is a level
    (Fibonacci retracement, cloud edge) where being late means the level is gone.

    Neither re-walks the bars.  Run both and report the pair; if the strategy only
    survives ``capture``, its edge depends on being early.
    """
    trades = _require(trades)
    if mode not in ("capture", "adverse"):
        raise ValueError("mode must be 'capture' or 'adverse'")
    samples: list[np.ndarray] = []
    for lvl in levels:
        f = float(lvl)
        if mode == "capture":
            stressed = [_reprice(t, gross_scale=1.0 - f) for t in trades]
        else:
            stressed = [_reprice(t, extra_slippage=f * abs(t.gross_pnl)) for t in trades]
        samples.append(np.array([metric(s) for s in _resample(stressed, n_samples, rng)]))
    return StressCurve(
        name=f"execution_delay_{mode}",
        metric_name=getattr(metric, "__name__", "metric"),
        level_name="delay_fraction",
        levels=tuple(float(x) for x in levels),
        samples=tuple(samples),
        baseline=float(metric(list(trades))),
        notes="approximation: no bar-level re-simulation of the fill",
    )


def random_deletion_stress(
    trades: Sequence[Trade],
    fractions: Sequence[float] = (0.0, 0.05, 0.10, 0.20, 0.35),
    metric: TradeMetric = net_profit,
    *,
    n_samples: int = 200,
    rng: np.random.Generator | int | None = None,
) -> StressCurve:
    """Delete a random fraction of trades, repeatedly.

    Tests concentration: if removing 5% of trades at random can wipe out the
    result in a meaningful share of draws, the edge lives in a handful of trades
    and the sample size is a fiction.
    """
    trades = _require(trades)
    gen = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    n = len(trades)
    samples: list[np.ndarray] = []
    for frac in fractions:
        keep = max(1, int(round(n * (1.0 - float(frac)))))
        vals = np.empty(n_samples)
        for i in range(n_samples):
            idx = gen.choice(n, size=keep, replace=False)
            vals[i] = metric([trades[int(j)] for j in sorted(idx.tolist())])
        samples.append(vals)
    return StressCurve(
        name="random_deletion",
        metric_name=getattr(metric, "__name__", "metric"),
        level_name="deleted_fraction",
        levels=tuple(float(f) for f in fractions),
        samples=tuple(samples),
        baseline=float(metric(list(trades))),
    )


def _sorted_by_entry(trades: Sequence[Trade]) -> list[Trade]:
    return sorted(trades, key=lambda t: t.entry_time)


def start_date_stress(
    trades: Sequence[Trade],
    fractions: Sequence[float] = (0.0, 0.05, 0.10, 0.20, 0.35),
    metric: TradeMetric = net_profit,
) -> StressCurve:
    """Drop the earliest fraction of trades.

    A result that only exists when the sample starts in a particular month is a
    statement about that month.  Deterministic: one value per level.
    """
    trades = _require(trades)
    ordered = _sorted_by_entry(trades)
    n = len(ordered)
    samples: list[np.ndarray] = []
    for frac in fractions:
        cut = min(n - 1, int(round(n * float(frac))))
        samples.append(np.array([metric(ordered[cut:])]))
    return StressCurve(
        name="start_date",
        metric_name=getattr(metric, "__name__", "metric"),
        level_name="start_trimmed_fraction",
        levels=tuple(float(f) for f in fractions),
        samples=tuple(samples),
        baseline=float(metric(ordered)),
    )


def end_date_stress(
    trades: Sequence[Trade],
    fractions: Sequence[float] = (0.0, 0.05, 0.10, 0.20, 0.35),
    metric: TradeMetric = net_profit,
) -> StressCurve:
    """Drop the latest fraction of trades - the mirror of :func:`start_date_stress`.

    Particularly sharp for research that was iterated until it worked: the most
    recent period is the one the researcher saw most often.
    """
    trades = _require(trades)
    ordered = _sorted_by_entry(trades)
    n = len(ordered)
    samples: list[np.ndarray] = []
    for frac in fractions:
        cut = max(1, n - int(round(n * float(frac))))
        samples.append(np.array([metric(ordered[:cut])]))
    return StressCurve(
        name="end_date",
        metric_name=getattr(metric, "__name__", "metric"),
        level_name="end_trimmed_fraction",
        levels=tuple(float(f) for f in fractions),
        samples=tuple(samples),
        baseline=float(metric(ordered)),
    )


def missing_fill_stress(
    trades: Sequence[Trade],
    probabilities: Sequence[float] = (0.0, 0.02, 0.05, 0.10, 0.20),
    metric: TradeMetric = net_profit,
    *,
    bias: str = "random",
    n_samples: int = 200,
    rng: np.random.Generator | int | None = None,
) -> StressCurve:
    """Some orders never get filled.

    ``bias="random"`` misses uniformly.  ``bias="worst"`` misses the BEST trades
    first, which is the realistic failure: the signals that run hardest are the
    ones that gap through a limit or reject on a fast market.  ``bias="best"``
    misses the worst trades and exists only as the optimistic bound.

    With ``bias != "random"`` the outcome is deterministic per level, so the
    sample array has length 1.
    """
    trades = _require(trades)
    if bias not in ("random", "worst", "best"):
        raise ValueError("bias must be 'random', 'worst' or 'best'")
    n = len(trades)
    gen = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    ranked = sorted(range(n), key=lambda i: trades[i].net_pnl, reverse=(bias == "worst"))
    samples: list[np.ndarray] = []
    for p in probabilities:
        prob = float(p)
        if bias == "random":
            vals = np.empty(n_samples)
            for i in range(n_samples):
                filled = gen.random(n) >= prob
                if not filled.any():
                    filled[gen.integers(0, n)] = True
                vals[i] = metric([t for t, keep in zip(trades, filled, strict=True) if keep])
            samples.append(vals)
        else:
            drop = set(ranked[: int(round(n * prob))])
            kept = [t for i, t in enumerate(trades) if i not in drop] or [trades[ranked[-1]]]
            samples.append(np.array([metric(kept)]))
    return StressCurve(
        name=f"missing_fill_{bias}",
        metric_name=getattr(metric, "__name__", "metric"),
        level_name="miss_probability",
        levels=tuple(float(p) for p in probabilities),
        samples=tuple(samples),
        baseline=float(metric(list(trades))),
    )


def run_stress_suite(
    trades: Sequence[Trade],
    metric: TradeMetric = net_profit,
    *,
    n_samples: int = 200,
    rng: np.random.Generator | int | None = None,
) -> dict[str, StressCurve]:
    """Every stress at its default grid, keyed by curve name.

    Intended as the operator-facing entry point: one call, one table per
    assumption, and a ``breaking_level`` per curve to sort promotion candidates by
    fragility rather than by headline profit.
    """
    gen = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    curves = [
        spread_multiplier_stress(trades, metric=metric),
        slippage_stress(trades, metric=metric),
        execution_delay_stress(trades, metric=metric, mode="capture"),
        execution_delay_stress(trades, metric=metric, mode="adverse"),
        random_deletion_stress(trades, metric=metric, n_samples=n_samples, rng=gen),
        start_date_stress(trades, metric=metric),
        end_date_stress(trades, metric=metric),
        missing_fill_stress(trades, metric=metric, bias="random", n_samples=n_samples, rng=gen),
        missing_fill_stress(trades, metric=metric, bias="worst"),
    ]
    return {c.name: c for c in curves}
