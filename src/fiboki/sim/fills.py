"""The fill model: how an intention becomes a price, and what it costs.

What V1 got wrong, precisely
----------------------------
1. ``_apply_costs`` was called from the *entry* path only. Closing a position
   was free. Since spread is symmetric, V1 charged exactly half the true
   round-trip spread. On a measured EURUSD H4 run that single bug accounted for
   roughly 46% of reported net profit (the number is re-measured by
   ``tests/integration/test_both_leg_cost_impact.py``, not asserted from
   memory).
2. Stops filled at the stop level even when the bar opened far through it. A
   long stopped at 1.0950 on a bar that opened at 1.0800 was recorded as a
   -1.0950 exit. Every gap was a free 150-pip gift. This is why V1's tail risk
   looked survivable.
3. When a bar touched both the stop and the target, V1 took whichever branch
   its ``if`` happened to test first (the target). That is the single most
   optimistic assumption available and it was never written down.

Core conventions (important, read before extending)
---------------------------------------------------
* **Bars are MID prices.** Stop and take-profit levels are compared against mid
  high/low. The half-spread is then applied to the resolved level to produce a
  dealt price. This is the only defensible treatment of mid-quoted history; the
  alternative (assuming bid/ask bars) would require data we do not have.
* **Costs are charged on every leg.** ``simulate_entry`` and ``simulate_exit``
  both return a populated :class:`LegCosts`. There is no code path that opens
  or closes a position without one.
* **Slippage is adverse-only and applies to market and stop orders, not to
  take-profit limits.** A limit order does not slip against you; it either
  fills at its price or does not fill. The compensating pessimism is that we
  never grant a limit price improvement on a gap (see ``_worse_exit``).
* **Intrabar resolution is unknowable without tick data.** We default to
  stop-first because it is conservative, and expose the assumption as a policy
  so its cost can be measured rather than believed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from fiboki.core.enums import Direction, ExitReason
from fiboki.core.instruments import Instrument
from fiboki.sim.profiles import ExecutionProfile, MinStopPolicy, rng_for

__all__ = [
    "AlwaysOpenCalendar",
    "Bar",
    "EntryFill",
    "ExitFill",
    "FillSimulator",
    "FxSessionCalendar",
    "IntrabarPolicy",
    "LegCosts",
    "RejectReason",
    "SessionCalendar",
]


# --------------------------------------------------------------------------
# Value types
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bar:
    """One closed OHLC bar. Mid prices, tz-aware UTC timestamp."""

    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(
                f"Incoherent bar at {self.timestamp}: O={self.open} H={self.high} "
                f"L={self.low} C={self.close}. Repair or reject the data upstream; "
                "the fill model refuses to reason about impossible bars."
            )

    @property
    def hour_utc(self) -> int:
        return int(self.timestamp.hour)


class IntrabarPolicy(str, Enum):
    """How to resolve a bar that touches both the stop and the target.

    Without tick data the true resolution is UNKNOWABLE. Each of these is an
    assumption, not a measurement:

    * ``STOP_FIRST``    — conservative. The default. Assumes the worst ordering.
    * ``TARGET_FIRST``  — optimistic. Provided only so the gap between the two
                          can be quantified; never use it to publish a result.
    * ``PROPORTIONAL``  — draws the outcome with probability weighted by how
                          close each level is to the bar's open, using the
                          profile's seeded RNG. Unbiased in expectation under a
                          random-walk assumption, but adds Monte-Carlo noise.
    """

    STOP_FIRST = "stop_first"
    TARGET_FIRST = "target_first"
    PROPORTIONAL = "proportional"


class RejectReason(str, Enum):
    MARKET_CLOSED = "market_closed"
    STALE_PRICE = "stale_price"
    MIN_STOP_DISTANCE = "min_stop_distance"
    MIN_DEAL_SIZE = "min_deal_size"
    BROKER_REJECT = "broker_reject"
    SIZE_NOT_POSITIVE = "size_not_positive"


@dataclass(frozen=True, slots=True)
class LegCosts:
    """Costs for ONE leg of a round trip, in the currency each names.

    ``spread``, ``slippage`` and ``guaranteed_stop_premium`` are in the
    instrument's QUOTE currency because they are derived from price movement.
    ``commission`` carries its own currency because broker schedules are
    denominated independently of the instrument (IBKR quotes USD minima on
    EURGBP). The engine converts each with an explicit FX rate.
    """

    spread_quote: float = 0.0
    slippage_quote: float = 0.0
    guaranteed_stop_premium_quote: float = 0.0
    commission_amount: float = 0.0
    commission_ccy: str = "USD"

    @property
    def quote_total(self) -> float:
        return self.spread_quote + self.slippage_quote + self.guaranteed_stop_premium_quote


@dataclass(frozen=True, slots=True)
class EntryFill:
    """Result of attempting to open. ``filled`` is the only field to branch on."""

    filled: bool
    reject_reason: RejectReason | None = None
    filled_size: float = 0.0
    filled_price: float = 0.0
    mid_price: float = 0.0
    requested_size: float = 0.0
    effective_stop: float = 0.0
    half_spread: float = 0.0
    slippage: float = 0.0
    partial: bool = False
    stale: bool = False
    costs: LegCosts = field(default_factory=LegCosts)

    @property
    def rejected_size(self) -> float:
        return max(0.0, self.requested_size - self.filled_size)


@dataclass(frozen=True, slots=True)
class ExitFill:
    """Result of resolving an exit on a bar. ``exited`` False means 'still open'."""

    exited: bool
    reason: ExitReason | None = None
    exit_price: float = 0.0
    mid_price: float = 0.0
    half_spread: float = 0.0
    slippage: float = 0.0
    gapped: bool = False
    both_touched: bool = False
    stale: bool = False
    costs: LegCosts = field(default_factory=LegCosts)


# --------------------------------------------------------------------------
# Session calendars
# --------------------------------------------------------------------------


class SessionCalendar:
    """Base: always open. Subclass to model real venue closures."""

    def is_open(self, instrument: Instrument, when: pd.Timestamp) -> bool:
        return True


class AlwaysOpenCalendar(SessionCalendar):
    pass


class FxSessionCalendar(SessionCalendar):
    """FX 24/5, anchored to 17:00 New York: closed Friday 17:00 NY to Sunday 17:00 NY.

    The interbank week opens and closes at 17:00 America/New_York, which is
    22:00 UTC in northern winter and 21:00 UTC while New York observes daylight
    saving. The previous fixed ``22:00 UTC`` was right half the year: from
    March to November it treated the Sunday 21:00-22:00 UTC bar, which exists
    and trades, as closed, and let an order fill in the Friday 21:00-22:00 UTC
    hour after the market had shut.

    ``close_hour`` and ``open_hour`` are hours of the day IN ``tz`` (default
    17 and 17 in America/New_York). The comparison converts the UTC timestamp
    into ``tz`` first, so daylight saving is the zone database's job, not a
    constant's. Holidays are NOT modelled -- a documented gap, not an
    oversight. Holiday bars are usually absent from the data anyway, in which
    case the engine simply has nothing to fill against.
    """

    def __init__(
        self,
        close_hour: int = 17,
        open_hour: int = 17,
        tz: str = "America/New_York",
    ) -> None:
        if not (0 <= close_hour <= 23 and 0 <= open_hour <= 23):
            raise ValueError("close_hour and open_hour are hours of the day")
        self.close_hour = close_hour
        self.open_hour = open_hour
        self.tz = tz

    def is_open(self, instrument: Instrument, when: pd.Timestamp) -> bool:
        if not instrument.is_fx and instrument.trading_hours not in ("fx_24_5",):
            return True
        if when.tzinfo is None:
            raise ValueError(
                f"FxSessionCalendar needs a tz-aware timestamp, got {when}; a naive "
                "time cannot be placed in the New York trading week"
            )
        local = when.tz_convert(self.tz)
        dow = local.dayofweek  # Monday=0 ... Sunday=6
        if dow == 4 and local.hour >= self.close_hour:
            return False
        if dow == 5:
            return False
        return not (dow == 6 and local.hour < self.open_hour)


# --------------------------------------------------------------------------
# Price-ordering helpers
# --------------------------------------------------------------------------


def _worse_exit(direction: Direction, a: float, b: float) -> float:
    """The exit price less favourable to a position of ``direction``.

    For a long, a LOWER exit price is worse, so we take the minimum. This one
    rule produces every behaviour the gap requirement asks for:

    * long stop, bar opens below it   -> min(stop, open) = open   (gap loss taken)
    * long target, bar opens above it -> min(target, open) = target (no windfall)
    * short stop, bar opens above it  -> max(stop, open) = open   (gap loss taken)
    * short target, bar opens below it-> max(target, open) = target (no windfall)
    """
    return min(a, b) if direction is Direction.LONG else max(a, b)


def _worse_entry(direction: Direction, a: float, b: float) -> float:
    """The entry price less favourable to a position of ``direction``."""
    return max(a, b) if direction is Direction.LONG else min(a, b)


# --------------------------------------------------------------------------
# The simulator
# --------------------------------------------------------------------------


@dataclass(slots=True)
class FillSimulator:
    """Turns an order plus a bar into a price and a cost breakdown.

    Stateless apart from its configuration: every stochastic decision derives
    its generator from ``(profile.seed, bar_index, sequence)``, so two
    simulators built from the same profile produce identical output whatever
    order their calls arrive in.
    """

    profile: ExecutionProfile
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.STOP_FIRST
    calendar: SessionCalendar = field(default_factory=AlwaysOpenCalendar)
    reject_on_stale: bool = False
    stale_spread_multiple: float = 2.0

    # ---------------------------------------------------------------- stale

    def is_stale(
        self,
        previous_time: pd.Timestamp | None,
        current_time: pd.Timestamp,
        bar_interval: pd.Timedelta,
    ) -> bool:
        """True when the gap since the previous bar exceeds the profile's multiple.

        A stale price is not necessarily a bad bar — every Monday FX open is a
        49-hour gap — but it IS a price you could not have dealt on
        continuously. We flag it and (by default) widen the spread rather than
        silently pretending the market was liquid across the gap.
        """
        if previous_time is None:
            return False
        if bar_interval <= pd.Timedelta(0):
            raise ValueError("bar_interval must be positive to detect staleness")
        return (current_time - previous_time) > bar_interval * self.profile.stale_price_max_gap_multiple

    # ------------------------------------------------------- stop distance

    def enforce_min_stop(
        self, instrument: Instrument, direction: Direction, price: float, stop: float
    ) -> tuple[bool, float, RejectReason | None]:
        """Apply the broker's minimum stop distance.

        Returns ``(ok, effective_stop, reject_reason)``. Under ``WIDEN`` the
        stop is pushed OUT to the minimum, which honestly increases the risk
        taken relative to what the sizer assumed — callers that care must
        re-size, and the engine records the widened stop on the position.
        """
        min_dist = self.profile.min_stop_distance_price(instrument)
        if min_dist <= 0 or self.profile.min_stop_policy is MinStopPolicy.ALLOW:
            return True, stop, None
        if abs(price - stop) >= min_dist:
            return True, stop, None
        if self.profile.min_stop_policy is MinStopPolicy.REJECT:
            return False, stop, RejectReason.MIN_STOP_DISTANCE
        widened = price - min_dist if direction is Direction.LONG else price + min_dist
        return True, widened, None

    # -------------------------------------------------------------- entry

    def simulate_entry(
        self,
        *,
        instrument: Instrument,
        direction: Direction,
        size: float,
        bar: Bar,
        bar_index: int,
        stop: float,
        sequence: int = 0,
        previous_time: pd.Timestamp | None = None,
        bar_interval: pd.Timedelta | None = None,
        reference_price: float | None = None,
    ) -> EntryFill:
        """Open at the bar's OPEN (or ``reference_price``), charging entry costs.

        The engine must only ever call this for a bar strictly later than the
        bar that produced the signal. This function does not — and structurally
        cannot — see the signal bar, which is how look-ahead is prevented.
        """
        if size <= 0:
            return EntryFill(False, RejectReason.SIZE_NOT_POSITIVE, requested_size=size)

        if not self.calendar.is_open(instrument, bar.timestamp):
            return EntryFill(False, RejectReason.MARKET_CLOSED, requested_size=size)

        stale = False
        if bar_interval is not None:
            stale = self.is_stale(previous_time, bar.timestamp, bar_interval)
            if stale and self.reject_on_stale:
                return EntryFill(False, RejectReason.STALE_PRICE, requested_size=size, stale=True)

        mid = float(reference_price) if reference_price is not None else float(bar.open)

        # --- broker rejection (draw first so its entropy position is fixed) --
        rng = rng_for(self.profile.seed, bar_index, sequence)
        reject_draw = float(rng.random())
        partial_draw = float(rng.random())
        partial_fraction_draw = float(rng.random())

        if reject_draw < self.profile.rejection_probability:
            return EntryFill(False, RejectReason.BROKER_REJECT, requested_size=size, stale=stale)

        # --- minimum stop distance -----------------------------------------
        ok, effective_stop, reason = self.enforce_min_stop(instrument, direction, mid, stop)
        if not ok:
            return EntryFill(False, reason, requested_size=size, stale=stale)

        # --- partial fill ---------------------------------------------------
        filled_size = size
        partial = False
        if partial_draw < self.profile.partial_fill_probability:
            lo = self.profile.partial_fill_min_fraction
            fraction = lo + (1.0 - lo) * partial_fraction_draw
            filled_size = _round_down_to_step(size * fraction, instrument.size_step)
            partial = filled_size < size

        if filled_size < self.profile.min_deal_size or filled_size <= 0:
            return EntryFill(False, RejectReason.MIN_DEAL_SIZE, requested_size=size, stale=stale)

        # --- price ----------------------------------------------------------
        half = self.profile.half_spread_price(instrument, bar.hour_utc, mid)
        if stale:
            half *= self.stale_spread_multiple
        slip = self.profile.slippage.slippage_price(instrument, direction, mid, rng)

        price = mid + direction.sign * (half + slip)

        costs = LegCosts(
            spread_quote=half * filled_size * instrument.contract_size,
            slippage_quote=slip * filled_size * instrument.contract_size,
            commission_amount=self.profile.commission.commission(instrument, filled_size, price),
            commission_ccy=self.profile.commission.currency_for(instrument),
        )
        return EntryFill(
            filled=True,
            filled_size=filled_size,
            filled_price=price,
            mid_price=mid,
            requested_size=size,
            effective_stop=effective_stop,
            half_spread=half,
            slippage=slip,
            partial=partial,
            stale=stale,
            costs=costs,
        )

    # --------------------------------------------------------------- exit

    def resolve_exit(
        self,
        *,
        instrument: Instrument,
        direction: Direction,
        size: float,
        bar: Bar,
        bar_index: int,
        stop: float | None,
        take_profit: float | None,
        sequence: int = 0,
        previous_time: pd.Timestamp | None = None,
        bar_interval: pd.Timedelta | None = None,
    ) -> ExitFill:
        """Decide whether this bar closes the position, and at what price.

        Resolution order is chronological, which matters:

        1. The bar's OPEN is the first observable price. If it is already
           through a level, that level fills at the open (for a stop) or at the
           level (for a target — no price improvement). No ambiguity policy
           applies, because the open genuinely came first.
        2. Otherwise both levels are checked against the bar's range and, if
           both were touched, :class:`IntrabarPolicy` decides.
        """
        level_stop = None if stop is None else float(stop)
        level_tp = None if take_profit is None else float(take_profit)
        stale = (
            self.is_stale(previous_time, bar.timestamp, bar_interval)
            if bar_interval is not None
            else False
        )

        # ---- 1. gap at the open -------------------------------------------
        if level_stop is not None and _through(direction, bar.open, level_stop, is_stop=True):
            mid = _worse_exit(direction, level_stop, bar.open)
            return self._price_exit(
                instrument, direction, size, bar, bar_index, sequence,
                mid, ExitReason.STOP_LOSS, gapped=True, both_touched=False, slips=True,
                requested_level=level_stop, stale=stale,
            )
        if level_tp is not None and _through(direction, bar.open, level_tp, is_stop=False):
            mid = _worse_exit(direction, level_tp, bar.open)
            return self._price_exit(
                instrument, direction, size, bar, bar_index, sequence,
                mid, ExitReason.TAKE_PROFIT, gapped=True, both_touched=False, slips=False,
                stale=stale,
            )

        # ---- 2. intrabar touches ------------------------------------------
        stop_touched = level_stop is not None and _touched(direction, bar, level_stop, is_stop=True)
        tp_touched = level_tp is not None and _touched(direction, bar, level_tp, is_stop=False)

        if stop_touched and tp_touched:
            take_stop = self._resolve_ambiguity(bar, level_stop, level_tp, bar_index, sequence)
            if take_stop:
                return self._price_exit(
                    instrument, direction, size, bar, bar_index, sequence,
                    float(level_stop), ExitReason.STOP_LOSS,
                    gapped=False, both_touched=True, slips=True,
                    requested_level=level_stop, stale=stale,
                )
            return self._price_exit(
                instrument, direction, size, bar, bar_index, sequence,
                float(level_tp), ExitReason.TAKE_PROFIT,
                gapped=False, both_touched=True, slips=False, stale=stale,
            )

        if stop_touched:
            return self._price_exit(
                instrument, direction, size, bar, bar_index, sequence,
                float(level_stop), ExitReason.STOP_LOSS,
                gapped=False, both_touched=False, slips=True,
                requested_level=level_stop, stale=stale,
            )
        if tp_touched:
            return self._price_exit(
                instrument, direction, size, bar, bar_index, sequence,
                float(level_tp), ExitReason.TAKE_PROFIT,
                gapped=False, both_touched=False, slips=False, stale=stale,
            )
        return ExitFill(exited=False)

    def market_exit(
        self,
        *,
        instrument: Instrument,
        direction: Direction,
        size: float,
        bar: Bar,
        bar_index: int,
        reason: ExitReason,
        price: float | None = None,
        sequence: int = 0,
        previous_time: pd.Timestamp | None = None,
        bar_interval: pd.Timedelta | None = None,
    ) -> ExitFill:
        """Close at a chosen mid price (default: the bar's close), with costs."""
        mid = float(bar.close) if price is None else float(price)
        stale = (
            self.is_stale(previous_time, bar.timestamp, bar_interval)
            if bar_interval is not None
            else False
        )
        return self._price_exit(
            instrument, direction, size, bar, bar_index, sequence,
            mid, reason, gapped=False, both_touched=False, slips=True, stale=stale,
        )

    # ------------------------------------------------------------ internals

    def _resolve_ambiguity(
        self, bar: Bar, stop: float | None, tp: float | None, bar_index: int, sequence: int
    ) -> bool:
        """True => the stop filled first."""
        if self.intrabar_policy is IntrabarPolicy.STOP_FIRST:
            return True
        if self.intrabar_policy is IntrabarPolicy.TARGET_FIRST:
            return False
        # PROPORTIONAL: whichever level sits closer to the open is more likely
        # to have been reached first. p(stop first) = d_tp / (d_stop + d_tp).
        d_stop = abs(bar.open - float(stop))
        d_tp = abs(bar.open - float(tp))
        total = d_stop + d_tp
        if total <= 0:
            return True  # degenerate: both levels at the open, stay conservative
        p_stop_first = d_tp / total
        rng = rng_for(self.profile.seed, bar_index, 100_000 + sequence)
        return float(rng.random()) < p_stop_first

    def _price_exit(
        self,
        instrument: Instrument,
        direction: Direction,
        size: float,
        bar: Bar,
        bar_index: int,
        sequence: int,
        mid: float,
        reason: ExitReason,
        *,
        gapped: bool,
        both_touched: bool,
        slips: bool,
        requested_level: float | None = None,
        stale: bool = False,
    ) -> ExitFill:
        premium_quote = 0.0
        if (
            reason is ExitReason.STOP_LOSS
            and self.profile.use_guaranteed_stops
            and requested_level is not None
        ):
            # A guaranteed stop fills AT the level even through a gap, and never
            # slips. IG charges the premium only when the stop is triggered, so
            # that is when we charge it.
            mid = float(requested_level)
            gapped = False
            slips = False
            premium_quote = (
                self.profile.guaranteed_stop_premium_price(instrument)
                * size
                * instrument.contract_size
            )

        half = self.profile.half_spread_price(instrument, bar.hour_utc, mid)
        if stale:
            # Symmetry with simulate_entry: a price that could not have been
            # dealt continuously is quoted wider on the way OUT as well as on
            # the way in. Charging the widening on entries only would make the
            # exit leg systematically cheaper than the entry leg, which is a
            # subtler version of the V1 bug this module exists to kill.
            half *= self.stale_spread_multiple

        slip = 0.0
        if slips:
            rng = rng_for(self.profile.seed, bar_index, 200_000 + sequence)
            slip = self.profile.slippage.slippage_price(instrument, direction, mid, rng)

        # Exiting is the opposite trade: a long sells at the bid.
        price = mid - direction.sign * (half + slip)

        costs = LegCosts(
            spread_quote=half * size * instrument.contract_size,
            slippage_quote=slip * size * instrument.contract_size,
            guaranteed_stop_premium_quote=premium_quote,
            commission_amount=self.profile.commission.commission(instrument, size, price),
            commission_ccy=self.profile.commission.currency_for(instrument),
        )
        return ExitFill(
            exited=True,
            reason=reason,
            exit_price=price,
            mid_price=mid,
            half_spread=half,
            slippage=slip,
            gapped=gapped,
            both_touched=both_touched,
            stale=stale,
            costs=costs,
        )


# --------------------------------------------------------------------------
# Free functions
# --------------------------------------------------------------------------


def _through(direction: Direction, open_price: float, level: float, *, is_stop: bool) -> bool:
    """Did the bar OPEN already beyond ``level``?"""
    if direction is Direction.LONG:
        return open_price <= level if is_stop else open_price >= level
    return open_price >= level if is_stop else open_price <= level


def _touched(direction: Direction, bar: Bar, level: float, *, is_stop: bool) -> bool:
    """Did the bar's range reach ``level`` at any point?"""
    if direction is Direction.LONG:
        return bar.low <= level if is_stop else bar.high >= level
    return bar.high >= level if is_stop else bar.low <= level


def _round_down_to_step(size: float, step: float) -> float:
    if step <= 0:
        return size
    steps = int(size / step + 1e-9)
    return round(steps * step, 10)


def bars_from_frame(frame: pd.DataFrame) -> list[Bar]:
    """Materialise a tz-aware OHLC frame into :class:`Bar` objects."""
    required = ("open", "high", "low", "close")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise KeyError(f"OHLC frame is missing columns {missing}")
    if frame.index.tz is None:
        raise ValueError("OHLC frame index must be timezone-aware UTC")
    values = frame[list(required)].to_numpy(dtype=np.float64)
    idx = frame.index
    return [
        Bar(idx[i], float(values[i, 0]), float(values[i, 1]), float(values[i, 2]), float(values[i, 3]))
        for i in range(len(frame))
    ]
