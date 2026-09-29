"""The position lifecycle, owned in ONE place and driven by two callers.

Why this module exists
----------------------
``backtest/engine.py`` grew the full DSL exit vocabulary — multi-leg partial
take-profits with allocations, trailing stops with ``activate_after_r``,
breakeven, time stops, cooldown, reversal and event blackouts — while
``broker/paper.py`` still held a single take-profit and no trail. The two paths
had *separately written* position management again, which is precisely the V1
defect the paper adapter was built to close: a paper divergence could not be
attributed, because alpha decay and a code disagreement looked the same.

The fix is structural rather than disciplinary. Everything that decides what
happens to an open position — when it scales out, when the stop moves, when the
clock runs out, what each closing fill costs and what the resulting ledger row
says — lives in :class:`PositionBook`. The backtester owns the timeline, the
strategy call and the equity arrays; the paper adapter owns the venue API, the
acks and the broker references. Neither owns an exit rule, so parity is a
property of the object graph and not of anybody's memory.

What each caller still owns
---------------------------
``BacktestEngine``
    the union timeline, the per-bar strategy invocation, sizing, the equity
    arrays and the bankruptcy guard.
``PaperBroker``
    ``place_order``/``close_position``, the idempotency map, ``OrderAck``
    bookkeeping and the equity-curve rows.

The seam between them is :class:`BarSlice`: everything the book needs to know
about "right now", assembled by the caller from whatever it happens to have —
a frame and an index in the backtester's case, a dict of freshly arrived bars in
the paper adapter's. The book never reaches for a bar it was not handed, which
is what lets the same object run against a whole frame and against a live feed.

The operational cost of a client-side exit, stated plainly
-----------------------------------------------------------
A venue holds **one** stop and **one** limit per position. A multi-leg scale-out
and a trailing stop are therefore *managed here*, in our process, which means
they depend on this process being alive. If the worker dies between bars, the
position keeps the last stop and target actually attached at the venue and
nothing trails it. That is a real operational risk, it is not fixable by writing
better client code, and it is recorded in ``docs/v2/USER_ACTIONS.md``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fiboki.backtest.exits import (
    DEFAULT_EXIT_POLICY,
    BlackoutSource,
    ExitPolicy,
    ReversalMode,
)
from fiboki.core.contracts import Position, Trade
from fiboki.core.enums import Direction, ExitReason, Provenance
from fiboki.core.instruments import Instrument
from fiboki.core.money import FxRateSource, round_size
from fiboki.sim.fills import Bar, FillSimulator, LegCosts

__all__ = [
    "FINANCING_DAY_RULE",
    "AdvanceResult",
    "BarSlice",
    "BookConfig",
    "CostBreakdown",
    "EntryEvent",
    "ExitLeg",
    "ManagedPosition",
    "PendingClose",
    "PendingEntry",
    "PositionBook",
    "TakeProfitLeg",
    "financing_nights",
    "plan_legs",
    "snap_to_step",
]


# --------------------------------------------------------------------------
# Size arithmetic
# --------------------------------------------------------------------------


def snap_to_step(size: float, step: float, rel_tol: float = 1e-9) -> float:
    """Absorb binary representation error before flooring to the size step.

    ``1000 / (0.0050 * 0.80)`` evaluates to 249999.99999999997 in IEEE 754, not
    250000. ``round_size`` would floor that to 249,999 and lose a unit of
    exposure for a reason that has nothing to do with risk — and worse, the
    loss would depend on the exact FX rate, so the same sizing rule would give
    different answers on different days for no economic reason.

    This snaps a value lying within ``rel_tol`` of a step boundary onto that
    boundary, then leaves the flooring to ``round_size``. The adjustment is at
    most one part in a billion, which is orders of magnitude smaller than any
    instrument's size step, so it cannot manufacture risk the rule did not
    intend.
    """
    if step <= 0 or size <= 0:
        return size
    nearest = round(size / step)
    if nearest > 0 and abs(size - nearest * step) <= rel_tol * max(size, step):
        return nearest * step
    return size


@dataclass(slots=True)
class TakeProfitLeg:
    """One planned scale-out: a price and the size it is meant to close."""

    price: float
    size: float


def plan_legs(
    instrument: Instrument,
    filled_size: float,
    prices: Sequence[float],
    allocations: Sequence[float],
) -> list[TakeProfitLeg]:
    """Turn target prices plus allocation fractions into dealable leg sizes.

    Rules, each of which exists because the alternative is a silent distortion:

    * **No allocations** means the pre-multi-leg behaviour — the first price is
      a full-size target and the rest are ignored. That is what every signal
      built before this existed meant, and changing it silently would restate
      every stored result without saying so.
    * Each leg's size is floored to the instrument's size step, because a size
      the venue cannot deal is not a size.
    * When the allocations sum to 1.0 the LAST leg takes the exact remainder
      rather than its own rounded share, so flooring cannot leave a dust
      position that then has to be closed by some other rule.
    * When they sum to LESS than 1.0 the shortfall is deliberate: the remainder
      rides to the stop, the trail, the time stop or the end of data. That is
      what ``macd_ema_trend_hybrid``'s 0.4 / 0.3 split means.
    * A leg that floors to zero is dropped. It cannot be dealt, and pretending
      otherwise would make the position close in more pieces than it has.
    """
    if not prices:
        return []
    if not allocations:
        return [TakeProfitLeg(float(prices[0]), filled_size)]

    total = sum(allocations)
    closes_everything = total >= 1.0 - 1e-9
    legs: list[TakeProfitLeg] = []
    allocated = 0.0
    last = len(prices) - 1
    for k, (price, allocation) in enumerate(zip(prices, allocations, strict=True)):
        if k == last and closes_everything:
            size = filled_size - allocated
        else:
            size = round_size(
                instrument,
                snap_to_step(filled_size * float(allocation), instrument.size_step),
            )
        size = min(size, filled_size - allocated)
        if size <= 0:
            continue
        legs.append(TakeProfitLeg(float(price), size))
        allocated += size
        if allocated >= filled_size - 1e-12:
            break
    return legs


# --------------------------------------------------------------------------
# Ledger shapes
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CostBreakdown:
    """Totals in the ACCOUNT currency."""

    spread: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    financing: float = 0.0
    guaranteed_stop_premium: float = 0.0
    entry_leg_spread: float = 0.0
    exit_leg_spread: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.spread
            + self.commission
            + self.slippage
            + self.financing
            + self.guaranteed_stop_premium
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "spread": self.spread,
            "commission": self.commission,
            "slippage": self.slippage,
            "financing": self.financing,
            "guaranteed_stop_premium": self.guaranteed_stop_premium,
            "entry_leg_spread": self.entry_leg_spread,
            "exit_leg_spread": self.exit_leg_spread,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class ExitLeg:
    """One FILL that closed part (or all) of a position.

    The audit trail behind a scaled-out :class:`~fiboki.core.contracts.Trade`.
    Every monetary field is in the account currency and
    ``net_pnl == gross_pnl - spread_cost - commission - slippage_cost -
    financing_cost`` holds on each leg individually, so the aggregate row can be
    reconstructed from these with a calculator.
    """

    position_seq: int
    instrument: str
    direction: str
    ordinal: int
    size: float
    exit_price: float
    exit_time: pd.Timestamp
    exit_reason: str
    gross_pnl: float
    spread_cost: float
    commission: float
    slippage_cost: float
    financing_cost: float
    net_pnl: float
    final: bool


# --------------------------------------------------------------------------
# Internal bookkeeping
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ManagedPosition:
    """One open position and every piece of state its exit rules need."""

    seq: int
    position: Position
    instrument: Instrument
    entry_mid: float
    entry_bar_index: int
    take_profit: float | None
    entry_costs_account: dict[str, float]
    financing_account: float = 0.0
    last_financing_time: pd.Timestamp | None = None
    entry_fx: float = 1.0

    # -- multi-leg / trailing state ---------------------------------------
    #: Size dealt at entry. Never mutated, unlike ``position.size``, which is
    #: the size still open. MAE/MFE are stated against this.
    entry_size: float = 0.0
    legs: list[TakeProfitLeg] = field(default_factory=list)
    next_leg: int = 0
    #: ``|entry - initial stop|`` in price units: the denominator of every R.
    risk_per_unit: float = 0.0
    initial_stop: float = 0.0
    #: Highest high (long) / lowest low (short) seen since entry, INCLUSIVE of
    #: the entry bar. The chandelier's anchor. NaN until the first bar.
    extreme: float = float("nan")
    breakeven_done: bool = False
    stop_trailed: bool = False
    #: What last moved the protective stop: ``"breakeven"`` or ``"trailing"``.
    #: Empty while the stop is still where it was placed. A trail overwrites
    #: breakeven and is never overwritten by it, because a trail only ever
    #: improves on the level breakeven produced.
    stop_moved_by: str = ""
    reversal_pending: bool = False
    #: Accumulated across the closing fills, so the final ``Trade`` is the sum.
    realised_gross: float = 0.0
    realised_spread: float = 0.0
    realised_commission: float = 0.0
    realised_slippage: float = 0.0
    realised_financing: float = 0.0
    realised_net: float = 0.0
    exit_value: float = 0.0  # Σ exit_mid * size, for the size-weighted mean
    n_legs_closed: int = 0
    single_leg_exit_mid: float = 0.0
    #: Whatever the caller needs to correlate this with its own record: an
    #: ``OrderAck``'s broker reference in the paper adapter, nothing at all in
    #: the backtester. The book never reads it.
    ref: Any = None

    def update_extreme(self, bar: Bar) -> None:
        best = bar.high if self.position.direction.sign > 0 else bar.low
        if not np.isfinite(self.extreme):
            self.extreme = best
        elif self.position.direction.sign > 0:
            self.extreme = max(self.extreme, best)
        else:
            self.extreme = min(self.extreme, best)

    def active_target(self) -> float | None:
        if self.next_leg >= len(self.legs):
            return None
        return self.legs[self.next_leg].price

    def r_multiple(self) -> float:
        if self.risk_per_unit <= 0:
            return 0.0
        return self.position.max_favourable_excursion / self.risk_per_unit


@dataclass(slots=True)
class PendingEntry:
    """An order awaiting the next actionable bar's open.

    It carries the full take-profit LADDER rather than a single target, because
    that is what a multi-leg document declares and what the venue-side manager
    has to honour. ``instrument`` is the resolved :class:`Instrument`, so the
    book never looks one up.
    """

    seq: int
    instrument: Instrument
    direction: Direction
    size: float
    stop_price: float
    take_profit_prices: tuple[float, ...]
    take_profit_allocations: tuple[float, ...]
    strategy_id: str
    actionable_index: int
    created_index: int
    ref: Any = None


@dataclass(slots=True)
class PendingClose:
    """A close scheduled by an opposite signal, actionable at a later bar's open.

    The signal was produced on a CLOSED bar, so the engine may not act on it at
    that bar's close — the same rule that makes an entry actionable no earlier
    than the next bar's open applies to a reversal's exit leg.
    """

    seq: int
    position_seq: int
    instrument: str
    actionable_index: int


# --------------------------------------------------------------------------
# The per-step view of the world
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BarSlice:
    """Everything the book is allowed to know about one timeline step.

    ``bars`` holds ONLY the instruments that have a bar at exactly this step. An
    instrument missing from it is not trading and every rule treats it that way:
    a pending order waits, an open position is carried, and nothing is priced.

    ``last_closes`` is the marking price for instruments that have no bar here
    but do have an open position — the backtester's "last close at or before
    this index" and the paper adapter's "the most recent bar we were handed".
    """

    index: int
    timestamp: pd.Timestamp
    bars: Mapping[str, Bar]
    previous_timestamp: pd.Timestamp | None = None
    previous_times: Mapping[str, pd.Timestamp | None] = field(default_factory=dict)
    intervals: Mapping[str, pd.Timedelta | None] = field(default_factory=dict)
    last_closes: Mapping[str, float] = field(default_factory=dict)
    #: The most recent bar at or before this step, for instruments with no bar
    #: HERE. Only a market close out of session reads it — the bankruptcy guard,
    #: the end of data and an operator flatten. Each caller stamps it the way it
    #: always has, because ``bar.timestamp`` feeds the staleness test.
    carried_bars: Mapping[str, Bar] = field(default_factory=dict)
    #: ``instrument -> column -> value`` on this bar. Feeds trailing rules that
    #: read an ATR or an indicator line. A missing column reads as NaN, which
    #: every trailing rule treats as "this bar cannot say".
    series: Mapping[str, Mapping[str, float]] = field(default_factory=dict)

    def has(self, symbol: str) -> bool:
        return symbol in self.bars

    def bar(self, symbol: str) -> Bar:
        return self.bars[symbol]

    def bar_or_last(self, symbol: str) -> Bar | None:
        return self.bars.get(symbol) or self.carried_bars.get(symbol)

    def mark(self, symbol: str) -> float:
        bar = self.bars.get(symbol)
        if bar is not None:
            return bar.close
        value = self.last_closes.get(symbol)
        if value is None:
            raise KeyError(f"No mark available for {symbol} at {self.timestamp}")
        return float(value)

    def previous_time(self, symbol: str) -> pd.Timestamp | None:
        return self.previous_times.get(symbol)

    def interval(self, symbol: str) -> pd.Timedelta | None:
        return self.intervals.get(symbol)

    def value(self, symbol: str, column: str | None) -> float:
        if not column:
            return float("nan")
        columns = self.series.get(symbol)
        if not columns:
            return float("nan")
        found = columns.get(column)
        return float("nan") if found is None else float(found)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntryEvent:
    """A pending order that reached a decision on this step."""

    pending: PendingEntry
    filled: bool
    reason: str = ""
    fill: Any = None
    managed: ManagedPosition | None = None


@dataclass(slots=True)
class AdvanceResult:
    """What one :meth:`PositionBook.advance` did, for the caller's own records."""

    entries: list[EntryEvent] = field(default_factory=list)
    legs: list[ExitLeg] = field(default_factory=list)
    #: The raw ``ExitFill`` behind each entry of :attr:`legs`, aligned 1:1. The
    #: leg carries the ACCOUNT-currency ledger arithmetic; the fill carries the
    #: dealt price and the half-spread, which a venue ack has to report.
    fills: list[Any] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    realised_delta: float = 0.0

    @property
    def filled(self) -> list[EntryEvent]:
        return [e for e in self.entries if e.filled]

    @property
    def rejected(self) -> list[EntryEvent]:
        return [e for e in self.entries if not e.filled]


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BookConfig:
    """The execution-relevant settings both callers must agree on.

    Anything here that differs between a backtest config and the paper config
    used for the same strategy makes the parity test meaningless, which is why
    both callers build this from their own config rather than restating the
    fields.
    """

    account_ccy: str = "GBP"
    max_concurrent: int = 1
    max_per_instrument: int = 1
    charge_financing: bool = True
    financing_rollover_hour_utc: int = 21
    strategy_id: str = "unnamed"
    provenance: Provenance = Provenance.BACKTEST


# --------------------------------------------------------------------------
# The book
# --------------------------------------------------------------------------


class PositionBook:
    """The position lifecycle: entries, exits, costs and the resulting ledger.

    The book owns balance and realised P&L because every closing fill moves
    both, and splitting that across two callers is how the two ledgers drifted
    in the first place. It does NOT own equity: marking to market is the
    caller's, because the backtester writes into preallocated arrays and the
    paper adapter appends rows.
    """

    def __init__(
        self,
        *,
        sim: FillSimulator,
        fx: FxRateSource,
        config: BookConfig,
        policy: ExitPolicy | None = None,
        blackout: BlackoutSource | None = None,
        initial_balance: float = 0.0,
        latency_bars: int = 0,
        financing_profile: Any = None,
    ) -> None:
        self.sim = sim
        self.fx = fx
        self.config = config
        self.policy = policy or DEFAULT_EXIT_POLICY
        self.blackout = blackout
        self.latency_bars = int(latency_bars)
        self.financing = financing_profile

        self.balance = float(initial_balance)
        self.realised = 0.0
        self.costs = CostBreakdown()
        self.trades: list[Trade] = []
        self.exit_legs: list[ExitLeg] = []
        self.rejections: dict[str, int] = {}

        #: The one monotonic sequence both callers share. It feeds the fill
        #: simulator's counter-based RNG, so an extra increment on one side and
        #: not the other silently decorrelates the two price paths. Owning it
        #: here is what makes that impossible.
        self.seq = 0
        self.open: list[ManagedPosition] = []
        self.pending: list[PendingEntry] = []
        self.pending_closes: list[PendingClose] = []
        #: Bar index up to and including which this instrument refuses a new
        #: fill, written when a position on it closes.
        self.cooldown_until: dict[str, int] = {}

    # ------------------------------------------------------------ queueing

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def queue_entry(self, entry: PendingEntry) -> PendingEntry:
        self.pending.append(entry)
        return entry

    def schedule_reversal(
        self, instrument: str, direction: Direction, *, index: int
    ) -> list[PendingClose]:
        """Arrange for every opposite-direction position to be closed.

        Returns the closes scheduled, which is empty when there is nothing to
        reverse. A position that already has a close scheduled is skipped: a
        second opposite signal on the next bar must not queue a second close of
        the same position.
        """
        if self.policy.reversal is ReversalMode.IGNORE:
            return []
        scheduled: list[PendingClose] = []
        for op in self.open:
            if (
                op.position.instrument == instrument
                and op.position.direction is not direction
                and not op.reversal_pending
            ):
                op.reversal_pending = True
                close = PendingClose(
                    seq=self.next_seq(),
                    position_seq=op.seq,
                    instrument=instrument,
                    actionable_index=index + 1 + self.latency_bars,
                )
                self.pending_closes.append(close)
                scheduled.append(close)
        return scheduled

    def bump(self, key: str) -> None:
        self.rejections[key] = self.rejections.get(key, 0) + 1

    # ------------------------------------------------------------- advance

    def advance(self, slice_: BarSlice) -> AdvanceResult:
        """Steps 1, 1b, 2 and 3 of the per-bar loop, in that exact order.

        The order is not arbitrary. Financing is charged for nights crossed
        before anything is priced; a reversal's close runs BEFORE the
        replacement order tries to fill, or ``max_per_instrument`` would refuse
        the position that is supposed to be taking this one's place; entries
        fill at this bar's open; and exits are resolved for every open position
        INCLUDING the one opened on this very bar, because gap risk is real and
        a position is exposed the instant it exists.
        """
        result = AdvanceResult()
        before = self.realised
        self._charge_financing(slice_)
        self._run_pending_closes(slice_, result)
        self._fill_entries(slice_, result)
        self._resolve_exits(slice_, result)
        result.realised_delta = self.realised - before
        return result

    # -- 1. financing ----------------------------------------------------

    def _charge_financing(self, slice_: BarSlice) -> None:
        cfg = self.config
        if not cfg.charge_financing or slice_.previous_timestamp is None:
            return
        if self.financing is None:
            return
        ts = slice_.timestamp
        for op in self.open:
            # Business-day rollovers, weighted: the triple day carries the
            # weekend, Saturday and Sunday carry nothing (``financing_nights``).
            nights = financing_nights(
                op.last_financing_time or op.position.entry_time,
                ts,
                cfg.financing_rollover_hour_utc,
                op.instrument.asset_class,
            )
            if not nights:
                continue
            mark = slice_.mark(op.position.instrument)
            charge_quote = (
                self.financing.nightly_charge(
                    op.instrument, op.position.direction, op.position.size, mark
                )
                * nights
            )
            ccy = self.financing.currency_for(op.instrument)
            charge_acct = charge_quote * self._rate(ccy, ts)
            op.financing_account += charge_acct
            op.position.financing_accrued += charge_acct
            op.last_financing_time = ts

    # -- 1b. reversals ---------------------------------------------------

    def _run_pending_closes(self, slice_: BarSlice, result: AdvanceResult) -> None:
        if not self.pending_closes:
            return
        i = slice_.index
        ts = slice_.timestamp
        still: list[PendingClose] = []
        by_seq = {op.seq: op for op in self.open}
        for close_order in self.pending_closes:
            if close_order.actionable_index > i:
                still.append(close_order)
                continue
            op = by_seq.get(close_order.position_seq)
            if op is None:  # already exited on its own terms
                continue
            sym = close_order.instrument
            if not slice_.has(sym):
                still.append(close_order)
                continue
            bar = slice_.bar(sym)
            exit_fill = self.sim.market_exit(
                instrument=op.instrument,
                direction=op.position.direction,
                size=op.position.size,
                bar=bar,
                bar_index=i,
                reason=ExitReason.OPPOSITE_SIGNAL,
                price=bar.open,
                sequence=op.seq,
                previous_time=slice_.previous_time(sym),
                bar_interval=slice_.interval(sym),
            )
            leg = self._close_fill(op, exit_fill, ts, op.position.size, final=True)
            self.exit_legs.append(leg)
            result.legs.append(leg)
            result.fills.append(exit_fill)
            self.balance += leg.net_pnl
            self.realised += leg.net_pnl
            trade = self._finalise(op, ts, ExitReason.OPPOSITE_SIGNAL)
            self.trades.append(trade)
            result.trades.append(trade)
            self.open = [p for p in self.open if p.seq != op.seq]
            # A reversal exit does NOT arm the cooldown: the cooldown exists to
            # stop a strategy re-entering the trade it just left, and a reversal
            # is by definition the other trade.
        self.pending_closes = still

    # -- 2. entries ------------------------------------------------------

    def _fill_entries(self, slice_: BarSlice, result: AdvanceResult) -> None:
        cfg = self.config
        i = slice_.index
        ts = slice_.timestamp
        still: list[PendingEntry] = []
        for order in self.pending:
            if order.actionable_index > i:
                still.append(order)
                continue
            sym = order.instrument.symbol
            if not slice_.has(sym):
                still.append(order)  # instrument not trading: wait
                continue
            if i <= self.cooldown_until.get(sym, -1):
                self.bump("cooldown")
                result.entries.append(EntryEvent(order, False, "cooldown"))
                continue
            blocked = self._event_block(sym, ts)
            if blocked is not None:
                self.bump(blocked)
                result.entries.append(EntryEvent(order, False, blocked))
                continue
            if len(self.open) >= cfg.max_concurrent:
                self.bump("max_concurrent")
                result.entries.append(EntryEvent(order, False, "max_concurrent"))
                continue
            if (
                sum(1 for p in self.open if p.position.instrument == sym)
                >= cfg.max_per_instrument
            ):
                self.bump("max_per_instrument")
                result.entries.append(EntryEvent(order, False, "max_per_instrument"))
                continue

            bar = slice_.bar(sym)
            fill = self.sim.simulate_entry(
                instrument=order.instrument,
                direction=order.direction,
                size=order.size,
                bar=bar,
                bar_index=i,
                stop=order.stop_price,
                sequence=order.seq,
                previous_time=slice_.previous_time(sym),
                bar_interval=slice_.interval(sym),
            )
            if not fill.filled:
                reason = fill.reject_reason.value if fill.reject_reason else "unknown"
                self.bump(reason)
                result.entries.append(EntryEvent(order, False, reason, fill))
                continue

            fx_rate = self._rate(order.instrument.quote, ts)
            entry_costs = self._costs_to_account(order.instrument, fill.costs, ts)
            self.costs.spread += entry_costs["spread"]
            self.costs.entry_leg_spread += entry_costs["spread"]
            self.costs.commission += entry_costs["commission"]
            self.costs.slippage += entry_costs["slippage"]
            self.costs.guaranteed_stop_premium += entry_costs["premium"]

            legs = plan_legs(
                order.instrument,
                fill.filled_size,
                order.take_profit_prices,
                self._allocations_for(order),
            )
            tp = legs[0].price if legs else None
            pos = Position(
                instrument=sym,
                direction=order.direction,
                size=fill.filled_size,
                entry_price=fill.mid_price,
                entry_time=ts,
                stop_loss=fill.effective_stop,
                take_profit_targets=[leg.price for leg in legs],
                strategy_id=order.strategy_id or cfg.strategy_id,
                venue_ref=order.ref if isinstance(order.ref, str) else None,
            )
            managed = ManagedPosition(
                seq=self.next_seq(),
                position=pos,
                instrument=order.instrument,
                entry_mid=fill.mid_price,
                entry_bar_index=i,
                take_profit=tp,
                entry_costs_account=entry_costs,
                last_financing_time=ts,
                entry_fx=fx_rate,
                entry_size=fill.filled_size,
                legs=legs,
                risk_per_unit=abs(fill.mid_price - fill.effective_stop),
                initial_stop=fill.effective_stop,
                ref=order.ref,
            )
            self.open.append(managed)
            result.entries.append(EntryEvent(order, True, "", fill, managed))
        self.pending = still

    # -- 3. exits --------------------------------------------------------

    def _resolve_exits(self, slice_: BarSlice, result: AdvanceResult) -> None:
        i = slice_.index
        ts = slice_.timestamp
        survivors: list[ManagedPosition] = []
        for op in self.open:
            sym = op.position.instrument
            if not slice_.has(sym):
                survivors.append(op)
                continue
            bar = slice_.bar(sym)
            op.position.update_excursion(bar.high, bar.low)
            op.update_extreme(bar)
            if i > op.entry_bar_index:
                op.position.bars_held += 1

            closed_reason: ExitReason | None = None
            # A bar can take more than one take-profit leg. The loop is bounded
            # by the number of legs plus the final close, so it cannot spin even
            # if a degenerate level resolves repeatedly.
            for _ in range(len(op.legs) + 1):
                decision = self.sim.resolve_exit(
                    instrument=op.instrument,
                    direction=op.position.direction,
                    size=op.position.size,
                    bar=bar,
                    bar_index=i,
                    stop=op.position.stop_loss,
                    take_profit=op.active_target(),
                    sequence=op.seq,
                    previous_time=slice_.previous_time(sym),
                    bar_interval=slice_.interval(sym),
                )
                if not decision.exited:
                    break
                close_size, final = size_for_exit(op, decision)
                leg_fill = decision
                if not final:
                    # Re-price the LEG so its costs are for the size that
                    # actually left. ``rng_for`` is counter-based, so the second
                    # call sees the same draws and the same decision.
                    leg_fill = self.sim.resolve_exit(
                        instrument=op.instrument,
                        direction=op.position.direction,
                        size=close_size,
                        bar=bar,
                        bar_index=i,
                        stop=op.position.stop_loss,
                        take_profit=op.active_target(),
                        sequence=op.seq,
                        previous_time=slice_.previous_time(sym),
                        bar_interval=slice_.interval(sym),
                    )
                reason = self._exit_reason(op, leg_fill)
                leg = self._close_fill(op, leg_fill, ts, close_size, final=final)
                self.exit_legs.append(leg)
                result.legs.append(leg)
                result.fills.append(leg_fill)
                self.balance += leg.net_pnl
                self.realised += leg.net_pnl
                if leg_fill.reason is ExitReason.TAKE_PROFIT:
                    op.next_leg += 1
                if final:
                    closed_reason = reason
                    break

            if closed_reason is None and self._time_stop_hit(op):
                exit_fill = self.sim.market_exit(
                    instrument=op.instrument,
                    direction=op.position.direction,
                    size=op.position.size,
                    bar=bar,
                    bar_index=i,
                    reason=ExitReason.TIME_STOP,
                    sequence=op.seq,
                    previous_time=slice_.previous_time(sym),
                    bar_interval=slice_.interval(sym),
                )
                leg = self._close_fill(op, exit_fill, ts, op.position.size, final=True)
                self.exit_legs.append(leg)
                result.legs.append(leg)
                result.fills.append(exit_fill)
                self.balance += leg.net_pnl
                self.realised += leg.net_pnl
                closed_reason = ExitReason.TIME_STOP

            if closed_reason is not None:
                trade = self._finalise(op, ts, closed_reason)
                self.trades.append(trade)
                result.trades.append(trade)
                if self.policy.cooldown_bars_after_exit > 0:
                    self.cooldown_until[sym] = i + self.policy.cooldown_bars_after_exit
                continue

            # Still open. The protective stop is re-derived from the bar that
            # has just CLOSED and takes effect from the next bar, which is what
            # keeps a trailing stop free of look-ahead.
            self._update_protective_stop(op, bar, slice_, sym)
            survivors.append(op)
        self.open = survivors

    # ------------------------------------------------------------ flatten

    def flatten(
        self,
        slice_: BarSlice,
        *,
        reason: ExitReason,
        positions: Sequence[ManagedPosition] | None = None,
    ) -> AdvanceResult:
        """Close positions at market NOW. The kill switch's FLATTEN and the
        bankruptcy guard and the end of data all arrive here."""
        result = AdvanceResult()
        before = self.realised
        ts = slice_.timestamp
        targets = list(positions if positions is not None else self.open)
        for op in targets:
            sym = op.position.instrument
            bar = slice_.bar_or_last(sym)
            if bar is None:
                continue
            exit_fill = self.sim.market_exit(
                instrument=op.instrument,
                direction=op.position.direction,
                size=op.position.size,
                bar=bar,
                bar_index=slice_.index,
                reason=reason,
                sequence=op.seq,
                previous_time=slice_.previous_time(sym),
                bar_interval=slice_.interval(sym),
            )
            leg = self._close_fill(op, exit_fill, ts, op.position.size, final=True)
            self.exit_legs.append(leg)
            result.legs.append(leg)
            result.fills.append(exit_fill)
            self.balance += leg.net_pnl
            self.realised += leg.net_pnl
            trade = self._finalise(op, ts, reason)
            self.trades.append(trade)
            result.trades.append(trade)
            if op in self.open:
                self.open.remove(op)
        result.realised_delta = self.realised - before
        return result

    # ------------------------------------------------------------- marking

    def mark_to_market(self, slice_: BarSlice) -> tuple[float, float]:
        """``(unrealised, gross exposure)`` in the account currency."""
        unrealised = 0.0
        exposure = 0.0
        ts = slice_.timestamp
        for op in self.open:
            mark = slice_.mark(op.position.instrument)
            rate = self._rate(op.instrument.quote, ts)
            unrealised += (
                op.position.unrealised_quote(mark, op.instrument.contract_size) * rate
            )
            unrealised -= op.financing_account
            exposure += abs(mark * op.position.size * op.instrument.contract_size * rate)
        return unrealised, exposure

    # -------------------------------------------------------------- rules

    def _allocations_for(self, order: PendingEntry) -> tuple[float, ...]:
        """Per-leg allocations for this order, or the policy's fallback.

        The order wins when it carries them, because only the compiler knows
        which declared leg produced which price once the prices have been sorted
        by distance and de-duplicated. An order built by hand (a golden test, a
        strategy that is not a compiled document) falls back to the policy, and
        a policy that declares none falls back to the pre-multi-leg behaviour:
        the first price is a full-size target.
        """
        if order.take_profit_allocations:
            return tuple(order.take_profit_allocations)
        allocations = self.policy.allocations
        if allocations and len(allocations) >= len(order.take_profit_prices):
            return tuple(allocations[: len(order.take_profit_prices)])
        return ()

    def _exit_reason(self, op: ManagedPosition, fill: Any) -> ExitReason:
        """STOP_LOSS becomes BREAKEVEN or TRAILING_STOP once the stop has moved.

        Reported, not inferred: ``op.stop_moved_by`` records which rule actually
        moved the level, so a strategy with a trail declared but never activated
        still reports its stops as stops, and a strategy with no trail at all
        can no longer report a ``trailing_stop``.

        The two are worth separating because they answer different questions. A
        breakeven stop-out is a trade that went far enough to de-risk and then
        came back — the breakeven rule paid for itself or cost an edge. A
        trailing stop-out is a trade that was given back a share of an open
        profit. Conflating them, as this engine did before ``BREAKEVEN`` existed,
        makes the breakeven rule invisible in every exit-reason breakdown.
        """
        reason = fill.reason or ExitReason.END_OF_DATA
        if reason is ExitReason.STOP_LOSS and op.stop_trailed:
            if op.stop_moved_by == "breakeven":
                return ExitReason.BREAKEVEN
            return ExitReason.TRAILING_STOP
        return reason

    def _time_stop_hit(self, op: ManagedPosition) -> bool:
        limit = self.policy.max_bars_in_trade
        return limit is not None and op.position.bars_held >= limit

    def _event_block(self, instrument: str, ts: pd.Timestamp) -> str | None:
        events = self.policy.events
        if events is None:
            return None
        return events.blocks(
            instrument,
            ts,
            rollover_hour=self.config.financing_rollover_hour_utc,
            calendar=self.blackout,
        )

    def _update_protective_stop(
        self, op: ManagedPosition, bar: Bar, slice_: BarSlice, sym: str
    ) -> None:
        """Move the stop, in the favourable direction ONLY, from a closed bar.

        Three things are true of every candidate level computed here:

        1. it is derived from bars up to and including the one that has just
           closed, and takes effect from the NEXT bar, so a trailing stop can
           never fill on the bar that created it;
        2. it is applied only when it improves the stop for the position's
           direction, so a trail cannot widen risk;
        3. it is NOT clamped to the current price. A chandelier can legitimately
           land above a long's close on a low-ATR bar, and the position is then
           stopped out at the next bar's open. Clamping would be a silent
           favour, and the whole point of this engine is not to grant those.
        """
        if op.risk_per_unit <= 0:
            return
        policy = self.policy
        sign = op.position.direction.sign
        current = op.position.stop_loss
        proposed = current
        r_now = op.r_multiple()

        moved_by = ""
        if (
            policy.breakeven_at_r is not None
            and not op.breakeven_done
            and r_now >= policy.breakeven_at_r
        ):
            op.breakeven_done = True
            if (op.entry_mid - proposed) * sign > 0:
                proposed = op.entry_mid
                moved_by = "breakeven"

        trail = policy.trailing
        if trail.active and r_now >= trail.activate_after_r:
            candidate = trail.candidate(
                sign=sign,
                extreme=op.extreme,
                close=bar.close,
                atr=slice_.value(sym, trail.atr_column),
                level=slice_.value(sym, trail.level_column),
            )
            if candidate is not None and (candidate - proposed) * sign > 0:
                proposed = candidate
                moved_by = "trailing"

        if (proposed - current) * sign > 0:
            op.position.stop_loss = proposed
            op.stop_trailed = True
            # A trail that has taken over never hands the label back: the level
            # in force came from the trail, whatever moved it first.
            if moved_by and op.stop_moved_by != "trailing":
                op.stop_moved_by = moved_by

    # ----------------------------------------------------------- the money

    def _close_fill(
        self,
        op: ManagedPosition,
        exit_fill: Any,
        ts: pd.Timestamp,
        size: float,
        *,
        final: bool,
    ) -> ExitLeg:
        """Realise ONE closing fill and fold it into the position's totals.

        Entry costs and accrued financing are carried out of a shrinking pool
        rather than recomputed, so the legs of a scaled-out position sum to
        exactly one round trip's costs with no residue on the last one.
        """
        instr = op.instrument
        costs = self.costs
        exit_costs = self._costs_to_account(instr, exit_fill.costs, ts)
        costs.spread += exit_costs["spread"]
        costs.exit_leg_spread += exit_costs["spread"]
        costs.commission += exit_costs["commission"]
        costs.slippage += exit_costs["slippage"]
        costs.guaranteed_stop_premium += exit_costs["premium"]

        share = 1.0 if final else (size / op.position.size if op.position.size > 0 else 1.0)
        entry_spread = op.entry_costs_account["spread"] * share
        entry_commission = op.entry_costs_account["commission"] * share
        entry_slippage = op.entry_costs_account["slippage"] * share
        entry_premium = op.entry_costs_account["premium"] * share
        if final:
            op.entry_costs_account = {
                "spread": 0.0,
                "commission": 0.0,
                "slippage": 0.0,
                "premium": 0.0,
            }
        else:
            op.entry_costs_account = {
                "spread": op.entry_costs_account["spread"] - entry_spread,
                "commission": op.entry_costs_account["commission"] - entry_commission,
                "slippage": op.entry_costs_account["slippage"] - entry_slippage,
                "premium": op.entry_costs_account["premium"] - entry_premium,
            }
        financing = op.financing_account if final else op.financing_account * share
        op.financing_account -= financing
        costs.financing += financing

        fx_rate = self._rate(instr.quote, ts)
        gross = (
            (exit_fill.mid_price - op.entry_mid)
            * op.position.direction.sign
            * size
            * instr.contract_size
        ) * fx_rate

        spread_cost = entry_spread + exit_costs["spread"]
        commission = entry_commission + exit_costs["commission"]
        slippage_cost = entry_slippage + exit_costs["slippage"]
        # The guaranteed-stop premium is a commission-like charge; folding it in
        # keeps `net = gross - the four named costs` exactly true.
        commission += entry_premium + exit_costs["premium"]
        net = gross - spread_cost - commission - slippage_cost - financing

        reason = self._exit_reason(op, exit_fill)
        op.realised_gross += gross
        op.realised_spread += spread_cost
        op.realised_commission += commission
        op.realised_slippage += slippage_cost
        op.realised_financing += financing
        op.realised_net += net
        op.exit_value += exit_fill.mid_price * size
        op.n_legs_closed += 1
        op.single_leg_exit_mid = exit_fill.mid_price
        op.position.size = 0.0 if final else op.position.size - size

        return ExitLeg(
            position_seq=op.seq,
            instrument=op.position.instrument,
            direction=op.position.direction.value,
            ordinal=op.n_legs_closed - 1,
            size=size,
            exit_price=exit_fill.mid_price,
            exit_time=ts,
            exit_reason=reason.value,
            gross_pnl=gross,
            spread_cost=spread_cost,
            commission=commission,
            slippage_cost=slippage_cost,
            financing_cost=financing,
            net_pnl=net,
            final=final,
        )

    def _finalise(
        self, op: ManagedPosition, ts: pd.Timestamp, reason: ExitReason
    ) -> Trade:
        """The ONE ledger row for a position, assembled from its closing fills."""
        cfg = self.config
        instr = op.instrument
        fx_rate = self._rate(instr.quote, ts)
        if op.n_legs_closed == 1:
            # Verbatim, so a single-leg position's row is bit-for-bit what it
            # was before this engine could scale out.
            exit_price = op.single_leg_exit_mid
        else:
            exit_price = op.exit_value / op.entry_size

        unit_to_account = op.entry_size * instr.contract_size * fx_rate
        return Trade(
            instrument=op.position.instrument,
            direction=op.position.direction,
            size=op.entry_size,
            entry_price=op.entry_mid,
            exit_price=exit_price,
            entry_time=op.position.entry_time,
            exit_time=ts,
            exit_reason=reason,
            gross_pnl=op.realised_gross,
            spread_cost=op.realised_spread,
            commission=op.realised_commission,
            slippage_cost=op.realised_slippage,
            financing_cost=op.realised_financing,
            net_pnl=op.realised_net,
            account_ccy=cfg.account_ccy,
            strategy_id=op.position.strategy_id or cfg.strategy_id,
            bars_held=op.position.bars_held,
            max_adverse_excursion=op.position.max_adverse_excursion * unit_to_account,
            max_favourable_excursion=op.position.max_favourable_excursion * unit_to_account,
            provenance=cfg.provenance,
            fx_rate_used=fx_rate,
        )

    def _costs_to_account(
        self, instrument: Instrument, leg: LegCosts, ts: pd.Timestamp
    ) -> dict[str, float]:
        quote_rate = self._rate(instrument.quote, ts)
        comm_rate = self._rate(leg.commission_ccy, ts)
        return {
            "spread": leg.spread_quote * quote_rate,
            "slippage": leg.slippage_quote * quote_rate,
            "premium": leg.guaranteed_stop_premium_quote * quote_rate,
            "commission": leg.commission_amount * comm_rate,
        }

    def _rate(self, from_ccy: str, when: pd.Timestamp) -> float:
        return float(self.fx.rate(from_ccy, self.config.account_ccy, when))


# --------------------------------------------------------------------------
# Free functions the callers share
# --------------------------------------------------------------------------


def size_for_exit(op: ManagedPosition, fill: Any) -> tuple[float, bool]:
    """How much this fill closes, and whether that empties the position.

    A take-profit leg closes its own allocated size. Anything else — a stop, a
    time stop, a margin call — closes the lot. Two guards:

    * a leg can never close more than is still open;
    * if closing the leg would leave a residue below the instrument's minimum
      dealable size, the residue goes with it. Leaving a stub nobody could
      actually deal is not a smaller position, it is a position the broker will
      not let you out of.
    """
    remaining = op.position.size
    if fill.reason is not ExitReason.TAKE_PROFIT or op.next_leg >= len(op.legs):
        return remaining, True
    size = min(op.legs[op.next_leg].size, remaining)
    residue = remaining - size
    if residue > 0.0 and residue < op.instrument.min_size - 1e-12:
        return remaining, True
    if size <= 0.0:
        return remaining, True
    return size, residue <= 1e-12


def nights_between(last: pd.Timestamp, now: pd.Timestamp, rollover_hour: int) -> int:
    """Number of ``rollover_hour`` UTC crossings in the half-open interval (last, now].

    A CALENDAR count: every day's crossing counts once, Saturdays and Sundays
    included, and no day counts three times. It is not the financing rule --
    :func:`financing_nights` is -- and is kept because it is the building block
    and because its tests pin the crossing arithmetic.

    (The docstring used to say this was the financing rule with "no weekend
    triple swap" and that it understated Wednesday holds. Both halves were
    wrong: charging all seven calendar nights happens to give the right WEEKLY
    total, but a position held only over a weekend paid two nights it should
    not have, and one held only over Wednesday paid one night instead of
    three.)
    """
    if now <= last:
        return 0
    anchor = last.normalize() + pd.Timedelta(hours=rollover_hour)
    if anchor <= last:
        anchor += pd.Timedelta(days=1)
    if anchor > now:
        return 0
    return int((now - anchor) // pd.Timedelta(days=1)) + 1


#: The version of the financing-day rule, recorded where financing is
#: described. ``business_day_triple_v1``: Monday to Friday rollovers count, the
#: asset class's triple day counts three, Saturday and Sunday count nothing.
FINANCING_DAY_RULE = "business_day_triple_v1"

#: The weekday that carries the weekend's financing (Monday=0 ... Sunday=6).
#: Spot FX and spot metals settle T+2, so Wednesday's rollover moves the value
#: date from Friday to Monday and is charged three nights. CFD indices,
#: energy and single equities charge the weekend on Friday. Crypto trades seven
#: days and is charged every calendar night, with no triple day (``None``).
TRIPLE_ROLLOVER_WEEKDAY: dict[str, int | None] = {
    "fx_major": 2,
    "fx_cross": 2,
    "metal": 2,
    "index": 4,
    "energy": 4,
    "equity": 4,
    "crypto": None,
}


def financing_nights(
    last: pd.Timestamp,
    now: pd.Timestamp,
    rollover_hour: int,
    asset_class: Any,
) -> int:
    """Nights of financing owed for the rollovers in the half-open interval (last, now].

    Each ``rollover_hour`` UTC crossing is weighted by the weekday it falls on:

    * Monday to Friday: 1, except the asset class's triple day, which is 3;
    * Saturday and Sunday: 0 (there is no rollover while the market is shut);
    * crypto: every calendar day 1, no triple day.

    A full Monday-to-Monday week therefore sums to 4 + 3 = 7 nights, exactly
    what the calendar count gave, but the nights now land on the right days: a
    Friday-to-Monday hold of FX pays 1 (Friday), not 3, and a Tuesday-to-
    Thursday hold pays 1 + 3 = 4, not 2. Uses the UTC date of each crossing,
    so the engine's insistence on a UTC index is what keeps the weekday right.

    Known approximation: holidays are not modelled (a holiday rollover is
    charged as a normal day), and the rollover hour is a fixed UTC hour, so
    it sits one hour away from 17:00 New York for half the year.
    """
    count = nights_between(last, now, rollover_hour)
    if count == 0:
        return 0
    key = getattr(asset_class, "value", asset_class)
    if key not in TRIPLE_ROLLOVER_WEEKDAY:
        raise KeyError(
            f"no financing-day rule for asset class {key!r}; add one to "
            "TRIPLE_ROLLOVER_WEEKDAY rather than charging it calendar nights"
        )
    triple = TRIPLE_ROLLOVER_WEEKDAY[key]
    if triple is None:
        return count
    anchor = last.normalize() + pd.Timedelta(hours=rollover_hour)
    if anchor <= last:
        anchor += pd.Timedelta(days=1)
    first_dow = int(anchor.dayofweek)
    total = 0
    for k in range(count):
        dow = (first_dow + k) % 7
        if dow >= 5:
            continue
        total += 3 if dow == triple else 1
    return total
