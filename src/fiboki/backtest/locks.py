"""Instrument locks: bar-indexed cooldowns and stop-streak locks, in ONE place.

What this is
------------
A strategy document may declare, in its optional ``locks`` block, two rules that
refuse NEW entries for a while after something has happened:

``cooldown_bars_after_close``
    after a position of this strategy on an instrument closes, stand aside on
    that instrument for the next N bars;
``stop_streak``
    after ``n_stops`` stop-outs within ``lookback_bars`` bars, stand aside for
    ``lock_bars`` bars, on the instrument, on the whole strategy or on the whole
    book, according to ``scope``.

Pattern after freqtrade ``plugins/protections`` (GPL-3.0, not copied), with two
deliberate corrections:

* freqtrade counts a lock in WALL-CLOCK minutes, so a four-candle H1 lock set on
  Friday at 21:00 expires on Saturday at 01:00, over a weekend in which no candle
  exists. Here a lock is counted in SESSION BARS on the instrument's own trading
  calendar: the same lock expires on Monday at 01:00, four real bars later.
* freqtrade leaves protections off in backtests unless asked. Here there is no
  switch: a document that declares locks is locked in the engine, and the risk
  gateway enforces the same rule for paper, demo and live, computed by this
  module from the same inputs.

Why a session CLOCK and not the engine's loop counter
-----------------------------------------------------
Every index in this module is a :class:`SessionBarClock` ordinal: the number of
in-session bar slots of the strategy's timeframe between a fixed anchor and a
timestamp, with the weekend closure removed. Two alternatives were rejected:

* the engine's timeline index ``i`` counts a bar whenever ANY instrument in the
  run has one, so on a mixed book an FX lock would be consumed by another
  market's bars;
* the instrument's own row counter cannot be recovered after a restart, because
  every durable ledger records timestamps, not loop counters.

A clock that is a pure function of the timestamp gives the same number in the
backtester, in the paper worker and in a gateway that has just been restarted
and has nothing but the trade ledger to go on. That is the whole reason the
three agree.

When a lock applies: the DECISION bar
-------------------------------------
A lock is evaluated on the CLOSED bar that produced the signal -- the bar the
engine's strategy step runs on, the bar the risk gateway is asked about. A close
on session bar ``c`` with ``cooldown_bars_after_close = N`` refuses signals on
bars ``c .. c+N-1``; the first permitted signal is on bar ``c+N`` and fills at
the next open. So N whole bars after the closing bar pass with no new fill,
which is what "stand aside for N bars" means, and which is exactly the fill-bar
arithmetic of the pre-existing ``position_management.cooldown_bars_after_exit``
on a single-instrument run with no latency.

A lock never blocks an exit, a reduction, a protective amendment or a reversal's
closing leg. It refuses new risk and nothing else.

Approximations, stated
----------------------
* The session calendar models the WEEKEND only: the interbank week, closed from
  Friday 17:00 to Sunday 17:00 America/New_York, for every trading-hours class
  currently registered. It is the SIM session calendar
  (:class:`fiboki.sim.fills.FxSessionCalendar`, its ``close_hour``,
  ``open_hour`` and ``tz``), so the closure is 22:00 UTC in northern winter and
  21:00 UTC while New York observes daylight saving, exactly as the fill
  simulator's. (Until round 4 this module used a fixed 22:00 UTC, which was an
  hour late for most of the year.) Daily maintenance breaks on index and
  energy CFDs and exchange holidays count as session bars. A holiday therefore
  consumes lock bars that no market printed.
* A slot counts as in-session when at least half of it is open. On H1 and below
  that is exact. On H4, in winter the Friday 20:00 and Sunday 20:00 UTC slots
  both count (each is half open: 31 slots a week); in summer the Friday 20:00
  slot is only a quarter open and does not count, while the Sunday 20:00 slot
  is three quarters open and does (30 a week). On D1 Sunday never counts. A bar
  stamped inside a closed slot (a D1 bar stamped Sunday 22:00 by some feeds)
  shares the ordinal of the last open slot before it, which keeps a lock in
  force one bar LONGER, never shorter.
* A close whose exit reason is unknown -- a closing intent recovered from the
  intent ledger, which does not record why a position closed -- is counted as a
  stop-out for ``stop_streak``. Not knowing whether it was a stop-out does not
  make it not one; the error, if any, is a lock that holds when it need not.
* ``scope: global`` counts every strategy's stop-outs in the book it is attached
  to and locks every strategy in it. A single-strategy backtest cannot see other
  strategies, so for a global rule parity with the backtest is exact only for a
  book running one strategy. Stated here and in ``STRATEGY_STANDARD.md``.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.sim.fills import FxSessionCalendar

__all__ = [
    "ClosedTrade",
    "LedgerLockView",
    "LockBook",
    "LockPolicy",
    "LockPolicyUnknown",
    "LockReason",
    "LockRegistration",
    "LockScope",
    "LockSource",
    "LockStateUnavailable",
    "SessionBarClock",
    "StopStreakRule",
    "UnknownSessionError",
    "closes_from_intents",
    "timeframe_for_interval",
]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class UnknownSessionError(ValueError):
    """An instrument whose trading calendar this module has not been taught.

    Refused rather than defaulted to "always open", because an always-open
    calendar is precisely the wall-clock lock this module exists to replace.
    """


class LockStateUnavailable(RuntimeError):
    """The lock state cannot be established. The gateway blocks on this."""


class LockPolicyUnknown(LockStateUnavailable):
    """A strategy declares locks but no policy is registered for it."""


# --------------------------------------------------------------------------
# The session clock
# --------------------------------------------------------------------------

_MINUTE_NS = 60_000_000_000
_WEEK_MINUTES = 7 * 24 * 60
#: Monday 1970-01-05 00:00 UTC. Slots are aligned to Monday midnight UTC.
_ANCHOR = pd.Timestamp("1970-01-05", tz="UTC")
_ANCHOR_DT = datetime(1970, 1, 5, tzinfo=UTC)

#: The interbank week, from THE sim session calendar: closed Friday 17:00 to
#: Sunday 17:00 America/New_York (21:00 or 22:00 UTC with daylight saving).
_INTERBANK_WEEKEND = FxSessionCalendar()

#: The weekly closure calendar per ``Instrument.trading_hours``. Every class
#: registered in ``core/instruments.py`` today closes for the interbank weekend.
#: Index and energy CFDs also have daily breaks, which are NOT modelled (module
#: docstring).
SESSION_CLOSURES: dict[str, FxSessionCalendar] = {
    "fx_24_5": _INTERBANK_WEEKEND,
    "index": _INTERBANK_WEEKEND,
    "energy": _INTERBANK_WEEKEND,
}

#: What a lock fingerprint says the bars were counted on.
LOCK_CALENDAR_ID = "session_bars:interbank_weekend:fri17-sun17_america_new_york"


def timeframe_for_interval(interval: pd.Timedelta) -> Timeframe:
    """The :class:`Timeframe` whose bar is exactly ``interval``, or raise."""
    minutes = interval / pd.Timedelta(minutes=1)
    for tf in Timeframe:
        if float(tf.minutes) == float(minutes):
            return tf
    raise ValueError(
        f"a bar interval of {interval} is not a registered timeframe. A lock is "
        "counted in bars of a known size; counting it in bars of a guessed size "
        "is counting it in nothing."
    )


@dataclass(frozen=True, slots=True)
class SessionBarClock:
    """Session-bar ordinals for one trading calendar and one timeframe.

    ``index(ts)`` is the number of in-session slots, of ``timeframe``, whose
    start is at or before ``ts``, minus one, counted from a fixed Monday anchor.
    It is a pure function of the timestamp, so any process computing it for the
    same timestamp gets the same integer, which is what lets a restarted
    gateway rebuild a lock from a ledger of timestamps.
    """

    trading_hours: str
    timeframe: Timeframe

    @classmethod
    def for_instrument(cls, instrument: str, timeframe: Timeframe | str) -> SessionBarClock:
        hours = get_instrument(instrument).trading_hours
        if not isinstance(SESSION_CLOSURES.get(hours), FxSessionCalendar):
            raise UnknownSessionError(
                f"{instrument}: trading_hours {hours!r} has no session calendar in "
                "fiboki.backtest.locks.SESSION_CLOSURES; add one rather than "
                "counting its locks on a clock that never closes"
            )
        return cls(trading_hours=hours, timeframe=Timeframe(timeframe))

    def index(self, ts: pd.Timestamp) -> int:
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is None:
            raise ValueError("SessionBarClock.index needs a timezone-aware UTC timestamp")
        minutes = int((stamp - _ANCHOR).value) // _MINUTE_NS
        slot_minutes = self.timeframe.minutes
        slot = minutes // slot_minutes
        if _WEEK_MINUTES % slot_minutes:
            raise ValueError(f"a {slot_minutes}-minute slot does not tile a week")
        week, r = divmod(slot, _WEEK_MINUTES // slot_minutes)
        calendar = SESSION_CLOSURES[self.trading_hours]
        prefix, _per_week = _template(_week_closure(_calendar_key(calendar), week), slot_minutes)
        return _open_slots_before(calendar, slot_minutes, week) + prefix[r + 1] - 1

    def bars_between(self, earlier: pd.Timestamp, later: pd.Timestamp) -> int:
        """Session bars from ``earlier``'s bar to ``later``'s bar."""
        return self.index(later) - self.index(earlier)

    @property
    def key(self) -> str:
        return f"{self.trading_hours}/{self.timeframe.value}"


_CalendarKey = tuple[str, int, int]


def _calendar_key(calendar: FxSessionCalendar) -> _CalendarKey:
    return (str(calendar.tz), int(calendar.close_hour), int(calendar.open_hour))


@lru_cache(maxsize=16384)
def _week_closure(key: _CalendarKey, week: int) -> tuple[tuple[int, int], ...]:
    """The week's closure as minutes after that week's Monday 00:00 UTC.

    Friday ``close_hour`` to Sunday ``open_hour`` in the calendar's zone, each
    converted to UTC by the zone database, so a daylight-saving change (which
    in New York happens on a Sunday at 02:00 local, INSIDE the closure) gives
    a 47- or 49-hour weekend rather than a constant's 48.
    """
    tz_name, close_hour, open_hour = key
    zone = ZoneInfo(tz_name)
    monday = _ANCHOR_DT + pd.Timedelta(weeks=week).to_pytimedelta()
    friday = (monday + pd.Timedelta(days=4).to_pytimedelta()).date()
    sunday = (monday + pd.Timedelta(days=6).to_pytimedelta()).date()
    close = datetime(friday.year, friday.month, friday.day, close_hour, tzinfo=zone)
    reopen = datetime(sunday.year, sunday.month, sunday.day, open_hour, tzinfo=zone)
    start = int((close.astimezone(UTC) - monday).total_seconds()) // 60
    end = int((reopen.astimezone(UTC) - monday).total_seconds()) // 60
    return ((start, end),)


_CUMULATIVE: dict[tuple[_CalendarKey, int], list[int]] = {}
_CUMULATIVE_LOCK = threading.Lock()


def _open_slots_before(calendar: FxSessionCalendar, slot_minutes: int, week: int) -> int:
    """In-session slots in every week from the anchor up to (not incl.) ``week``.

    Weeks differ (a summer week has a different Friday and Sunday slot count on
    H4), so the ordinal is a running sum. Kept per calendar and slot size and
    extended on demand; a pure function of its arguments.
    """
    if week < 0:
        raise ValueError("SessionBarClock counts from 1970-01-05; an earlier timestamp is not a bar")
    key = _calendar_key(calendar)
    with _CUMULATIVE_LOCK:
        sums = _CUMULATIVE.setdefault((key, slot_minutes), [0])
        while len(sums) <= week:
            w = len(sums) - 1
            sums.append(sums[-1] + _template(_week_closure(key, w), slot_minutes)[1])
        return sums[week]


@lru_cache(maxsize=256)
def _template(
    closures: tuple[tuple[int, int], ...], slot_minutes: int
) -> tuple[tuple[int, ...], int]:
    """``(prefix, open_slots_per_week)`` for one week's closure and slot size.

    ``prefix[k]`` is the number of in-session slots among the first ``k`` slots
    of the week. A slot is in session when at least half of its minutes are.
    Computed at minute resolution; only four closures exist (winter, summer and
    the two changeover weekends), so this runs a handful of times per process.
    """
    if _WEEK_MINUTES % slot_minutes:
        raise ValueError(f"a {slot_minutes}-minute slot does not tile a week")
    slots = _WEEK_MINUTES // slot_minutes
    prefix = [0]
    for k in range(slots):
        start, end = k * slot_minutes, (k + 1) * slot_minutes
        closed = sum(
            max(0, min(end, c_end) - max(start, c_start)) for c_start, c_end in closures
        )
        is_open = 2 * (slot_minutes - closed) >= slot_minutes
        prefix.append(prefix[-1] + (1 if is_open else 0))
    return tuple(prefix), prefix[-1]


# --------------------------------------------------------------------------
# Policy (engine data, built from the DSL by fiboki.backtest.exits)
# --------------------------------------------------------------------------


class LockScope(str, Enum):
    """What a stop streak counts, and what it locks.

    ``INSTRUMENT``
        this strategy's stop-outs on one instrument; locks this strategy on it.
    ``STRATEGY``
        this strategy's stop-outs on any instrument; locks this strategy on all.
    ``GLOBAL``
        every strategy's stop-outs in the book; locks every strategy in it.
    """

    INSTRUMENT = "instrument"
    STRATEGY = "strategy"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class StopStreakRule:
    n_stops: int
    lookback_bars: int
    lock_bars: int
    scope: LockScope = LockScope.INSTRUMENT

    def __post_init__(self) -> None:
        if self.n_stops < 2:
            raise ValueError("stop_streak.n_stops must be >= 2; one stop is not a streak")
        if self.lookback_bars < 1:
            raise ValueError("stop_streak.lookback_bars must be >= 1")
        if self.lock_bars < 1:
            raise ValueError("stop_streak.lock_bars must be >= 1")

    def fingerprint(self) -> dict[str, Any]:
        return {
            "n_stops": int(self.n_stops),
            "lookback_bars": int(self.lookback_bars),
            "lock_bars": int(self.lock_bars),
            "scope": self.scope.value,
        }


@dataclass(frozen=True, slots=True)
class LockPolicy:
    """The lock rules one strategy declared. Plain, frozen, hashable."""

    cooldown_bars_after_close: int = 0
    stop_streak: StopStreakRule | None = None

    def __post_init__(self) -> None:
        if self.cooldown_bars_after_close < 0:
            raise ValueError("cooldown_bars_after_close must be >= 0")

    @property
    def active(self) -> bool:
        return self.cooldown_bars_after_close > 0 or self.stop_streak is not None

    def fingerprint(self) -> dict[str, Any]:
        return {
            "cooldown_bars_after_close": int(self.cooldown_bars_after_close),
            "stop_streak": None if self.stop_streak is None else self.stop_streak.fingerprint(),
            "calendar": LOCK_CALENDAR_ID,
        }


@dataclass(frozen=True, slots=True)
class LockRegistration:
    """A policy and the bar size its counts are expressed in."""

    policy: LockPolicy
    timeframe: Timeframe


# --------------------------------------------------------------------------
# Inputs and outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """The four facts a lock needs about a closed position.

    ``exit_reason`` is the persisted :class:`~fiboki.core.enums.ExitReason`
    VALUE, or ``None`` when the ledger it was read from does not say.
    """

    strategy_id: str
    instrument: str
    exit_time: pd.Timestamp
    exit_reason: str | None

    @property
    def is_stop_out(self) -> bool:
        """The unmoved protective stop was hit, or we cannot tell that it was not.

        Only ``stop_loss``. A take-profit, a time stop, a reversal, a trailing
        stop and a breakeven stop are not stop-outs: the last two are exits at a
        level the position had already earned, and counting them would lock a
        strategy for doing what its trail is for.
        """
        return self.exit_reason is None or self.exit_reason == "stop_loss"

    @classmethod
    def of(cls, record: Any) -> ClosedTrade:
        """From a :class:`ClosedTrade` or anything shaped like a ``Trade``."""
        if isinstance(record, ClosedTrade):
            return record
        reason = getattr(record, "exit_reason", None)
        value = getattr(reason, "value", reason)
        return cls(
            strategy_id=str(record.strategy_id or ""),
            instrument=str(record.instrument).upper(),
            exit_time=pd.Timestamp(record.exit_time),
            exit_reason=None if value is None else str(value),
        )


@dataclass(frozen=True, slots=True)
class LockReason:
    """One armed lock. Returned by :meth:`LockBook.is_locked` when it applies."""

    rule: str
    scope: LockScope
    strategy_id: str
    #: ``None`` for a strategy- or book-wide lock.
    instrument: str | None
    armed_at: pd.Timestamp
    armed_index: int
    #: The LAST session bar on which a new entry is refused (inclusive).
    until_index: int
    clock: SessionBarClock

    @property
    def bars(self) -> int:
        return self.until_index - self.armed_index + 1

    def covers(self, bar_time: pd.Timestamp) -> bool:
        idx = self.clock.index(bar_time)
        return self.armed_index <= idx <= self.until_index

    def applies_to(self, instrument: str, strategy_id: str) -> bool:
        if self.scope is LockScope.GLOBAL:
            return True
        if self.strategy_id != strategy_id:
            return False
        return self.scope is LockScope.STRATEGY or self.instrument == instrument

    @property
    def code(self) -> str:
        """The named reason recorded on a refusal. Deterministic."""
        return (
            f"{self.rule}:{self.scope.value}:{self.strategy_id}:"
            f"{self.instrument or '*'}:armed={self.armed_at.isoformat()}:"
            f"bars={self.bars}:{self.clock.key}"
        )

    def as_row(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "scope": self.scope.value,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "armed_at": self.armed_at.isoformat(),
            "armed_index": self.armed_index,
            "until_index": self.until_index,
            "clock": self.clock.key,
        }


# --------------------------------------------------------------------------
# The book
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _StopEvent:
    seq: int
    strategy_id: str
    instrument: str
    exit_time: pd.Timestamp


@dataclass(slots=True)
class LockBook:
    """Every lock armed so far, and the one question: may this entry be made?

    Pure bookkeeping over :class:`ClosedTrade` records. It performs no I/O and
    reads no clock of its own: the time it answers about is the ``bar_time`` it
    is asked about, and the only history it has is the closes it was told of.
    That is what lets :meth:`rebuild` reproduce, from a ledger, exactly the book
    an uninterrupted process would hold.

    ``registrations`` maps a strategy id to its policy. ``default`` applies to
    any strategy with no explicit registration -- the backtester's case, where
    one run is one document.
    """

    registrations: Mapping[str, LockRegistration] = field(default_factory=dict)
    default: LockRegistration | None = None
    #: Latest lock per ``(rule, scope, strategy, instrument)``. A later lock on
    #: the same key always ends at or after an earlier one, and no question can
    #: be asked about a bar before a close the book has already seen, so the
    #: earlier one can never be the deciding lock again.
    _active: dict[tuple[str, str, str, str], LockReason] = field(default_factory=dict)
    #: Every lock ever armed, in arming order. The audit trail.
    history: list[LockReason] = field(default_factory=list)
    _stops: list[_StopEvent] = field(default_factory=list)
    _consumed: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _seen: set[str] = field(default_factory=set)
    _seq: int = 0

    # ------------------------------------------------------------ queries

    def registration_for(self, strategy_id: str) -> LockRegistration | None:
        return self.registrations.get(strategy_id, self.default)

    def is_locked(
        self, instrument: str, strategy_id: str, bar_time: pd.Timestamp
    ) -> LockReason | None:
        """The lock refusing a NEW entry decided on ``bar_time``, or ``None``."""
        symbol = instrument.upper()
        for key in sorted(self._active):
            lock = self._active[key]
            if lock.applies_to(symbol, strategy_id) and lock.covers(bar_time):
                return lock
        return None

    @property
    def active(self) -> tuple[LockReason, ...]:
        return tuple(self._active[k] for k in sorted(self._active))

    # ------------------------------------------------------------ updates

    def on_close(self, trade: Any) -> tuple[LockReason, ...]:
        """Record one closed position; return the locks it armed."""
        closed = ClosedTrade.of(trade)
        if closed.exit_time.tzinfo is None:
            raise ValueError("a close must carry a timezone-aware UTC exit_time")
        self._seen.add(closed.strategy_id)
        self._seq += 1
        armed: list[LockReason] = []

        own = self.registration_for(closed.strategy_id)
        if own is not None and own.policy.cooldown_bars_after_close > 0:
            clock = SessionBarClock.for_instrument(closed.instrument, own.timeframe)
            idx = clock.index(closed.exit_time)
            armed.append(
                self._arm(
                    LockReason(
                        rule="cooldown",
                        scope=LockScope.INSTRUMENT,
                        strategy_id=closed.strategy_id,
                        instrument=closed.instrument,
                        armed_at=closed.exit_time,
                        armed_index=idx,
                        until_index=idx + own.policy.cooldown_bars_after_close - 1,
                        clock=clock,
                    )
                )
            )

        if closed.is_stop_out:
            self._stops.append(
                _StopEvent(
                    self._seq, closed.strategy_id, closed.instrument, closed.exit_time
                )
            )
            for declarer, reg in self._streak_declarers(closed):
                lock = self._evaluate_streak(declarer, reg, closed)
                if lock is not None:
                    armed.append(lock)
        return tuple(armed)

    @classmethod
    def rebuild(
        cls,
        closes: Iterable[Any],
        *,
        registrations: Mapping[str, LockRegistration] | None = None,
        default: LockRegistration | None = None,
    ) -> LockBook:
        """The book an uninterrupted process would hold, from a ledger.

        Closes are replayed in ``exit_time`` order; ties keep ledger order, which
        is the order the position book appended them in.
        """
        book = cls(registrations=dict(registrations or {}), default=default)
        records = [ClosedTrade.of(c) for c in closes]
        for record in sorted(records, key=lambda r: r.exit_time):  # stable
            book.on_close(record)
        return book

    # ------------------------------------------------------------ internals

    def _arm(self, lock: LockReason) -> LockReason:
        key = (lock.rule, lock.scope.value, lock.strategy_id, lock.instrument or "*")
        if lock.scope is LockScope.GLOBAL:
            key = (lock.rule, lock.scope.value, "*", "*")
        self._active[key] = lock
        self.history.append(lock)
        return lock

    def _streak_declarers(
        self, closed: ClosedTrade
    ) -> list[tuple[str, LockRegistration]]:
        """Every strategy whose stop-streak rule counts this stop-out."""
        candidates: dict[str, LockRegistration] = dict(self.registrations)
        if self.default is not None:
            for sid in self._seen:
                candidates.setdefault(sid, self.default)
        out: list[tuple[str, LockRegistration]] = []
        for sid in sorted(candidates):
            reg = candidates[sid]
            rule = reg.policy.stop_streak
            if rule is None:
                continue
            if rule.scope is LockScope.GLOBAL or sid == closed.strategy_id:
                out.append((sid, reg))
        return out

    def _evaluate_streak(
        self, declarer: str, reg: LockRegistration, closed: ClosedTrade
    ) -> LockReason | None:
        rule = reg.policy.stop_streak
        assert rule is not None
        clock = SessionBarClock.for_instrument(closed.instrument, reg.timeframe)
        now_idx = clock.index(closed.exit_time)
        floor = now_idx - rule.lookback_bars  # exclusive
        if rule.scope is LockScope.INSTRUMENT:
            consume_key = (declarer, rule.scope.value, closed.instrument)
        elif rule.scope is LockScope.STRATEGY:
            consume_key = (declarer, rule.scope.value, "*")
        else:
            consume_key = ("*", rule.scope.value, "*")
        consumed = self._consumed.get(consume_key, 0)

        count = 0
        for event in reversed(self._stops):
            if event.seq <= consumed:
                break
            if clock.index(event.exit_time) <= floor:
                break
            if rule.scope is LockScope.INSTRUMENT and (
                event.strategy_id != declarer or event.instrument != closed.instrument
            ):
                continue
            if rule.scope is LockScope.STRATEGY and event.strategy_id != declarer:
                continue
            count += 1
        if count < rule.n_stops:
            return None
        # A streak is consumed by the lock it arms: the stops that armed this
        # lock do not also count toward the next one. Otherwise, with a
        # lookback longer than the lock, a single bad cluster would re-arm on
        # every later stop and the effective lock length would depend on the
        # lookback in a way nobody declared.
        self._consumed[consume_key] = self._seq
        return self._arm(
            LockReason(
                rule="stop_streak",
                scope=rule.scope,
                strategy_id=declarer if rule.scope is not LockScope.GLOBAL else "*",
                instrument=closed.instrument if rule.scope is LockScope.INSTRUMENT else None,
                armed_at=closed.exit_time,
                armed_index=now_idx,
                until_index=now_idx + rule.lock_bars - 1,
                clock=clock,
            )
        )


# --------------------------------------------------------------------------
# What the risk gateway reads
# --------------------------------------------------------------------------


class LockSource(Protocol):
    """The one question ``risk/gateway.py`` asks about locks.

    Implementations raise :class:`LockStateUnavailable` when they cannot answer;
    the gateway blocks on that. ``declared`` says the signal's own document
    declares locks, so a source with no policy for that strategy must refuse
    rather than answer "not locked".
    """

    def lock_for(
        self,
        instrument: str,
        strategy_id: str,
        bar_time: pd.Timestamp,
        *,
        declared: bool,
        timeframe: str | None = None,
    ) -> LockReason | None: ...


class LedgerLockView:
    """Lock state REBUILT from a durable ledger on every question.

    Holds no lock state of its own, so there is nothing for a restart to forget:
    the answer is a pure function of the ledger and the bar asked about. The
    ledger is a callable returning closed-trade records (``Trade`` rows from the
    position ledger, :class:`ClosedTrade` values, or the output of
    :func:`closes_from_intents`). A callable that raises makes the state
    unavailable, which the gateway turns into a refusal.

    Only closes at or before ``bar_time`` are replayed: a close the ledger
    records after the decision bar did not exist when the decision was made.
    """

    def __init__(
        self,
        registrations: Mapping[str, LockRegistration],
        closes: Callable[[], Iterable[Any]],
    ) -> None:
        self.registrations = dict(registrations)
        self._closes = closes

    def lock_for(
        self,
        instrument: str,
        strategy_id: str,
        bar_time: pd.Timestamp,
        *,
        declared: bool,
        timeframe: str | None = None,
    ) -> LockReason | None:
        reg = self.registrations.get(strategy_id)
        if declared and reg is None:
            raise LockPolicyUnknown(
                f"{strategy_id} declares locks but no lock policy is registered for it"
            )
        if (
            declared
            and reg is not None
            and timeframe is not None
            and Timeframe(timeframe) is not reg.timeframe
        ):
            raise LockStateUnavailable(
                f"{strategy_id}: signal timeframe {timeframe} does not match the "
                f"registered lock timeframe {reg.timeframe.value}; its locks would "
                "be counted in bars of the wrong size"
            )
        try:
            records = [ClosedTrade.of(c) for c in self._closes()]
        except Exception as exc:
            raise LockStateUnavailable(
                f"the ledger could not be read: {type(exc).__name__}: {exc}"
            ) from exc
        stamp = pd.Timestamp(bar_time)
        book = LockBook.rebuild(
            (r for r in records if r.exit_time <= stamp),
            registrations=self.registrations,
        )
        return book.is_locked(instrument, strategy_id, stamp)


def closes_from_intents(intents: Iterable[Any]) -> tuple[ClosedTrade, ...]:
    """Position closes recorded in the ORDER-INTENT ledger.

    A full close is written by ``ExecutionService.close`` as an intent with
    ``extra["closing"]`` set; its ``updated_at`` in state ``closed`` is the close
    time. The intent ledger does not record WHY a position closed, so the exit
    reason is read from ``extra["exit_reason"]`` when a writer supplied it and is
    otherwise ``None`` -- which :attr:`ClosedTrade.is_stop_out` counts as a
    stop-out. Cooldowns recovered from here are exact; stop streaks recovered
    from here can only be more restrictive than the truth. The position (trade)
    ledger, which carries the exit reason, is the precise source.
    """
    out: list[ClosedTrade] = []
    for intent in intents:
        extra = getattr(intent, "extra", None) or {}
        state = getattr(getattr(intent, "state", None), "value", getattr(intent, "state", ""))
        if not extra.get("closing") or state != "closed":
            continue
        reason = extra.get("exit_reason")
        out.append(
            ClosedTrade(
                strategy_id=str(intent.strategy_id or ""),
                instrument=str(intent.instrument).upper(),
                exit_time=pd.Timestamp(intent.updated_at),
                exit_reason=None if reason is None else str(reason),
            )
        )
    return tuple(out)
