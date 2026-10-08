"""The four :class:`~fiboki.risk.gateway.RiskContext` inputs that ran on zeros.

The defect this module closes
-----------------------------
``risk/gateway.py`` runs nineteen named checks and names every one of them in
``RiskDecision.checks_run``, allowed or blocked. Four of those checks were
reading fields that nothing in the tree ever populated:

======================================  ============================
``_check_daily_loss``                   ``RiskContext.daily_pnl``
``_check_weekly_loss``                  ``RiskContext.weekly_pnl``
``_check_max_correlated_exposure``      ``RiskContext.correlated_exposure``
volatility targeting in ``portfolio``   ``PortfolioSnapshot.realised_portfolio_vol``
======================================  ============================

All four default to ``0.0``. So the checks ran, appeared in the audit trail,
and could not fire. **A daily stop that reads zero is not a daily stop.** It is
worse than an absent one, because the audit trail says ``daily_loss`` was
evaluated and an operator reading it concludes the limit held.

That is a strictly nastier version of the V1 failure the gateway was written to
close. V1's ``RiskEngine.check_trade_allowed`` had zero call sites: the limits
were decorative and at least visibly so. Here the call site existed and the
DATA was decorative, which is the same outcome wearing evidence.

The V1 daily-reset defect, and why the boundary is derived and not stored
-------------------------------------------------------------------------
V1 kept a running daily-loss counter and reset it inside a 21:00 summary block.
The reset was therefore a side effect of a job: if the worker was down at 21:00
the counter never reset, the previous day's losses accumulated into the next,
and the daily stop either fired permanently or -- after a restart cleared the
counter to zero -- never fired at all. Either way the number did not describe a
day.

:class:`RealisedPnlLedger` has **no counter and no reset**. The day is derived
from the clock on every query: it is the set of trades whose ``exit_time`` falls
at or after ``utc_day_start(now)``. There is no state that a job could fail to
update and no accumulator that a restart could clear, so the class of bug is
structurally absent rather than defended against. The tests assert the boundary
crosses correctly with no job ever having run.

Realised, not marked
--------------------
Daily and weekly P&L here are REALISED: they come from closed trades. Open
positions are already covered by ``max_account_risk`` (risk-to-stop across the
book) and ``total_drawdown`` (peak-to-current equity, which includes the mark).
Folding unrealised P&L into the daily-loss check as well would double-count the
same exposure and make the daily stop fire on a position that had not lost
anything yet. ``include_unrealised`` exists for a caller that wants the other
behaviour and is OFF by default, and the choice is recorded on the window so a
decision record says which one was in force.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.contracts import Trade
from fiboki.portfolio.construction import CorrelationMatrix, PortfolioSnapshot

__all__ = [
    "PnlWindows",
    "RealisedPnlLedger",
    "correlated_exposure",
    "correlation_from_frames",
    "realised_portfolio_vol",
    "utc_day_start",
    "utc_week_start",
]


# ==========================================================================
# Clock-derived boundaries
# ==========================================================================


def utc_day_start(now: pd.Timestamp) -> pd.Timestamp:
    """Midnight UTC at or before ``now``.

    UTC and not the broker's rollover hour. The two are different boundaries
    for different purposes: financing rolls at the venue's 21:00 because that
    is when the venue charges it, and a *risk* day is a calendar day because
    that is what "I will not lose more than 3% in a day" means to the person
    who set the limit. Using the financing boundary for the loss limit would
    make the daily stop reset in the middle of the London afternoon.
    """
    _require_utc(now, "now")
    return now.normalize()


def utc_week_start(now: pd.Timestamp) -> pd.Timestamp:
    """Monday 00:00 UTC at or before ``now``.

    ISO weeks, so the boundary falls where the FX week is quietest rather than
    in the middle of it. A Sunday-start week would reset the weekly loss limit
    a few hours after the Sydney open, which is when a gap-driven loss is most
    likely to have just happened.
    """
    _require_utc(now, "now")
    return now.normalize() - pd.Timedelta(days=int(now.dayofweek))


def _require_utc(ts: pd.Timestamp, name: str) -> None:
    if ts.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware UTC. A naive timestamp here would "
            "make the day boundary depend on the host's locale, which is how a "
            "daily stop resets at the wrong time in a different region."
        )


# ==========================================================================
# The windows
# ==========================================================================


@dataclass(frozen=True, slots=True)
class PnlWindows:
    """Realised P&L over the current UTC day and ISO week, with its boundaries.

    The boundaries are returned, not just the numbers, because "the daily stop
    did not fire" and "the daily stop measured the wrong day" look identical
    from the number alone.
    """

    now: pd.Timestamp
    day_start: pd.Timestamp
    week_start: pd.Timestamp
    daily_pnl: float
    weekly_pnl: float
    daily_trades: int
    weekly_trades: int
    includes_unrealised: bool = False
    unrealised: float = 0.0

    def as_row(self) -> dict[str, Any]:
        return {
            "now": self.now.isoformat(),
            "day_start": self.day_start.isoformat(),
            "week_start": self.week_start.isoformat(),
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "daily_trades": self.daily_trades,
            "weekly_trades": self.weekly_trades,
            "includes_unrealised": self.includes_unrealised,
            "unrealised": self.unrealised,
        }


class RealisedPnlLedger:
    """Realised P&L over clock-derived UTC day and week windows.

    NO COUNTER. NO RESET. NO JOB. Every query re-derives its boundary from the
    ``now`` it is given and filters the closed trades by ``exit_time``. That is
    the whole implementation, and it is the whole point: there is no state for
    a dead worker to fail to reset and none for a restart to clear, so V1's
    "the 21:00 job did not run so the daily stop never fired again" cannot
    recur here in any form.

    ``trades`` is a callable rather than a list so the ledger reads whatever
    its source currently holds -- the paper broker's ``book.trades``, a
    database query, a journal reader -- without anybody having to remember to
    push new trades into it. A ledger that has to be told about a trade is a
    ledger that will one day not be told about a trade.
    """

    def __init__(
        self,
        trades: Callable[[], Sequence[Trade]] | Sequence[Trade],
        *,
        account_ccy: str = "GBP",
        unrealised: Callable[[], float] | None = None,
        include_unrealised: bool = False,
        strategy_ids: frozenset[str] | None = None,
    ) -> None:
        self._trades = trades if callable(trades) else (lambda: trades)
        self.account_ccy = account_ccy
        self._unrealised = unrealised
        self.include_unrealised = bool(include_unrealised)
        #: Restrict the windows to these strategies. ``None`` means the whole
        #: account, which is what a book-level loss limit means.
        self.strategy_ids = strategy_ids

    # ------------------------------------------------------------------

    def trades(self) -> tuple[Trade, ...]:
        rows = tuple(self._trades() or ())
        if self.strategy_ids is None:
            return rows
        return tuple(t for t in rows if t.strategy_id in self.strategy_ids)

    def windows(self, now: pd.Timestamp) -> PnlWindows:
        """The current day and week, derived from ``now`` and nothing else."""
        day_start = utc_day_start(now)
        week_start = utc_week_start(now)
        daily = 0.0
        weekly = 0.0
        daily_n = 0
        weekly_n = 0
        for trade in self.trades():
            exit_time = trade.exit_time
            if exit_time is None:
                continue
            if exit_time.tzinfo is None:
                raise ValueError(
                    f"Trade {trade.trade_id} has a naive exit_time. Every "
                    "timestamp in this platform is UTC; a naive one here would "
                    "land in the wrong day and the daily stop would measure the "
                    "wrong window."
                )
            if exit_time > now:
                # A trade closing in the future is a clock or a replay defect.
                # Counting it would let tomorrow's loss block today's entry.
                continue
            if exit_time >= week_start:
                weekly += float(trade.net_pnl)
                weekly_n += 1
            if exit_time >= day_start:
                daily += float(trade.net_pnl)
                daily_n += 1

        unrealised = 0.0
        if self.include_unrealised and self._unrealised is not None:
            unrealised = float(self._unrealised())
            daily += unrealised
            weekly += unrealised
        return PnlWindows(
            now=now,
            day_start=day_start,
            week_start=week_start,
            daily_pnl=daily,
            weekly_pnl=weekly,
            daily_trades=daily_n,
            weekly_trades=weekly_n,
            includes_unrealised=self.include_unrealised,
            unrealised=unrealised,
        )


# ==========================================================================
# Correlated exposure
# ==========================================================================


def correlated_exposure(
    instrument: str,
    *,
    snapshot: PortfolioSnapshot,
    threshold: float,
    correlation: CorrelationMatrix | None = None,
) -> float:
    """Gross notional held in instruments correlated with ``instrument``.

    This is the number ``RiskGateway._check_max_correlated_exposure`` adds the
    candidate plan's notional to. It was always zero, so the check compared
    ``plan_notional`` alone against a 2000%-of-equity cap and could not fire
    except on a single position larger than twenty times equity.

    Three decisions, each of which would be a silent distortion the other way:

    * the instrument's OWN exposure is included. ``rho(x, x) == 1``, which is
      above any threshold, and a second position in the same instrument is the
      most correlated exposure available. Excluding it would make the check
      blind to the one case it obviously covers;
    * the notional is taken as an ABSOLUTE value. A long EURUSD and a short
      EURUSD are not a hedge for the purposes of a gap: they are two positions
      that both have to be got out of. Netting here would understate exactly
      the risk this limit exists to bound;
    * an UNMEASURED pair uses ``CorrelationMatrix.default``, which the
      portfolio deliberately sets positive. "We have not measured it" is not
      "it is uncorrelated".
    """
    matrix = correlation or snapshot.instrument_correlation
    total = 0.0
    for symbol, notional in snapshot.instrument_exposure.items():
        rho = float(matrix.get(instrument, str(symbol)))
        if abs(rho) >= threshold:
            total += abs(float(notional))
    return total


# ==========================================================================
# Realised portfolio volatility
# ==========================================================================


def realised_portfolio_vol(
    equity: Sequence[float] | pd.Series | Mapping[Any, float],
    *,
    periods_per_year: float | None = None,
    min_observations: int = 20,
    index: pd.DatetimeIndex | None = None,
) -> float:
    """Annualised realised volatility of the equity curve, as a FRACTION.

    Feeds ``PortfolioSnapshot.realised_portfolio_vol``, which
    ``portfolio/construction.py`` uses for volatility targeting. It was always
    0.0, and ``test_an_unmeasured_portfolio_vol_is_neutral_not_a_free_scale_up``
    pins the consequence: an unmeasured vol is NEUTRAL, so vol targeting was
    silently disabled on every live path while remaining configured and tested.

    Returns 0.0 -- meaning "unmeasured", which the construction layer treats as
    neutral -- when there are fewer than ``min_observations`` returns. A vol
    estimated from three bars is not a small sample, it is noise, and feeding it
    into a scaling rule would move real size for no reason.

    ``periods_per_year`` is inferred from the index spacing when an index is
    available, because an hourly equity curve and a daily one annualise by very
    different factors and guessing 252 for both overstates the hourly one by a
    factor of five.
    """
    series = _as_series(equity, index)
    if series is None or len(series) < min_observations + 1:
        return 0.0
    returns = series.ffill().pct_change(fill_method=None).dropna()
    returns = returns[np.isfinite(returns.to_numpy())]
    if len(returns) < min_observations:
        return 0.0
    scale = periods_per_year
    if scale is None:
        scale = _periods_per_year(series.index)
    if scale is None or scale <= 0:
        return 0.0
    sigma = float(returns.std(ddof=1))
    if not np.isfinite(sigma) or sigma <= 0:
        return 0.0
    return float(sigma * np.sqrt(scale))


def _as_series(
    equity: Sequence[float] | pd.Series | Mapping[Any, float],
    index: pd.DatetimeIndex | None,
) -> pd.Series | None:
    if isinstance(equity, pd.Series):
        series = equity
    elif isinstance(equity, Mapping):
        series = pd.Series(dict(equity))
    else:
        values = list(equity)
        if not values:
            return None
        series = pd.Series(values, index=index if index is not None else None)
    series = series.astype(float).dropna()
    return None if series.empty else series


def _periods_per_year(index: Any) -> float | None:
    """Annualisation factor from the observed spacing, or None if unknowable."""
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 3:
        # No timestamps: refuse to guess. A wrong annualisation factor is a
        # wrong risk scalar, and there is no safe default.
        return None
    deltas = pd.Series(index).diff().dropna()
    if deltas.empty:
        return None
    median = deltas.median()
    seconds = float(median.total_seconds())
    if seconds <= 0:
        return None
    return (365.0 * 24.0 * 60.0 * 60.0) / seconds


def correlation_from_frames(
    frames: Mapping[str, pd.DataFrame],
    *,
    column: str = "close",
    min_observations: int = 30,
    default: float = 0.30,
) -> CorrelationMatrix:
    """Pairwise return correlation of the instruments actually being traded.

    The gateway's ``max_correlated_exposure`` check needs a matrix and nothing
    in a running process built one, so the check read a zero. This builds it
    from the same price frames the session is replaying, which is the honest
    source available to a worker: it is measured on OUR data, over exactly the
    window we are trading, rather than assumed.

    Two deliberate conservatisms:

    * pairs with fewer than ``min_observations`` overlapping returns are left
      UNMEASURED and therefore read ``default``, which is positive. "Not enough
      data" must not resolve to "uncorrelated", because uncorrelated is the
      maximally permissive answer at exactly the point we know least;
    * the estimate is a plain Pearson correlation of simple returns over the
      whole supplied window. It is therefore a long-run average and will
      UNDERSTATE correlation in a crisis, when correlations converge on one.
      That is a documented approximation, not a hidden one: a regime-conditional
      estimate belongs in ``marketstate`` and does not exist yet.
    """
    closes: dict[str, pd.Series] = {}
    for symbol, frame in frames.items():
        if column not in frame.columns:
            continue
        series = frame[column].astype(float)
        closes[str(symbol)] = series
    if len(closes) < 2:
        return CorrelationMatrix(default=default)

    # Explicit forward-fill: the behaviour pandas' deprecated default gave, kept
    # so a pandas upgrade cannot silently change the correlation the gateway uses.
    returns = (
        pd.DataFrame(closes).sort_index().ffill().pct_change(fill_method=None).dropna(how="all")
    )
    labels = sorted(returns.columns)
    pairs: dict[tuple[str, str], float] = {}
    for i, a in enumerate(labels):
        for b in labels[i + 1 :]:
            both = returns[[a, b]].dropna()
            if len(both) < min_observations:
                continue
            rho = float(both[a].corr(both[b]))
            if np.isfinite(rho):
                pairs[(a, b)] = rho
    if not pairs:
        return CorrelationMatrix(
            tuple(labels),
            tuple(
                tuple(1.0 if x == y else default for y in labels) for x in labels
            ),
            default,
        )
    return CorrelationMatrix.from_mapping(pairs, default=default)
