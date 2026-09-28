"""Economic-event calendar: interface, file-backed implementation, blackout queries.

**No network.** This module reads events from files you supply and nothing else.
That is a deliberate boundary: a research platform that silently fetches a
calendar cannot reproduce a backtest six months later, because the vendor will
have revised the history.

What ships here:

* :class:`EconomicEvent` — one dated release, with actual/forecast/previous.
* :class:`EconomicCalendar` — the interface everything else codes against.
* :class:`InMemoryEconomicCalendar` — the implementation, loaded from JSON or CSV.
* :data:`RECURRING_EVENT_TYPES` — a curated fixture of the recurring event
  *types* that matter (FOMC, NFP, CPI, ECB, BoE, BoJ, GDP, PMI, retail sales,
  employment), with currency, impact and the usual release time.

What ships as **dated** data: :data:`OFFICIAL_EVENTS_FIXTURE`, a scheduled-events
file built from official publishers only (Federal Reserve, ECB, Bank of England,
Bank of Japan, US BLS, UK ONS), loaded by :func:`load_official_calendar`. It
covers USD, EUR, GBP and JPY from 2024-01-01 to the fixture's declared end, and
it carries scheduled TIMES only: no actual/forecast/previous values. It was
fetched once, by hand, and committed; this module still performs no network
access at runtime.

What does **not** ship here: dated events before 2024, any other currency, and
any realised figure. Outside the declared span an empty or non-covering calendar
answers "not in blackout" for every bar, which is the *dangerous* default, so
:meth:`EconomicCalendar.assert_populated` exists for any caller that must not
run blind, and :data:`USER_ACTION_NOTE` records the remaining gap verbatim for
the USER_ACTIONS document.

Events with no fixed release time (the Bank of Japan announces when its meeting
ends) carry ``time_known=False`` and a ``window_end``: blackout queries treat the
whole ``[event_time, window_end]`` span as the event, then add the margins.

Causality: an event's *scheduled time* is legitimately known in advance —
central bank calendars are published months ahead, so a strategy may avoid a
known FOMC date without looking into the future. An event's *actual value* is
not: :meth:`EconomicEvent.actual_as_of` returns the actual only once the release
time has passed, and returns ``None`` before it. Using ``event.actual`` directly
inside a backtest is a look-ahead bug; the method exists so that it does not
have to be.
"""
from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import AssetClass

CALENDAR_VERSION = "1.0.0"

FIXTURE_DIR = Path(__file__).parent / "fixtures"
RECURRING_EVENTS_FIXTURE = FIXTURE_DIR / "recurring_economic_events.json"

OFFICIAL_EVENTS_FIXTURE = FIXTURE_DIR / "scheduled_events_official.json"

#: Verbatim text for the USER_ACTIONS document. Do not soften it.
USER_ACTION_NOTE = (
    "ECONOMIC CALENDAR: DATED EVENTS ARE NOT SUPPLIED BEFORE 2024-01-01, AFTER "
    "THE OFFICIAL FIXTURE'S DECLARED END, OR FOR ANY CURRENCY OTHER THAN USD, "
    "EUR, GBP AND JPY.\n"
    "fiboki.marketstate.calendar ships the event-type taxonomy, a complete "
    "blackout-window implementation and, since 2026-09-28, "
    "fixtures/scheduled_events_official.json: scheduled FOMC, ECB, BoE and BoJ "
    "decisions, US NFP and CPI, and UK CPI, monthly GDP and labour-market "
    "releases, taken from the publishers' own pages (load_official_calendar()). "
    "It performs NO network access; the fixture was fetched once and committed.\n"
    "Outside that coverage an empty or non-covering calendar answers False to "
    "every blackout query, which means backtests and paper bots will trade "
    "straight through FOMC and NFP there.\n"
    "USER ACTION REQUIRED:\n"
    "  1. For backtests before 2024, or for AUD, CAD, CHF, NZD or other "
    "currencies, obtain a dated calendar from an external feed whose licence "
    "permits your use (official publishers first; several commercial "
    "aggregators forbid automated extraction and redistribution).\n"
    "  2. Convert it to the JSON or CSV shape documented in "
    "fiboki.marketstate.calendar (event_time must be tz-aware UTC).\n"
    "  3. Load it with load_events_json()/load_events_csv() and hand the "
    "resulting InMemoryEconomicCalendar to the state engine.\n"
    "  4. Refresh scheduled_events_official.json before its declared end "
    "(fiboki calendar status prints it); future dates are schedules and can "
    "move.\n"
    "  5. Call EconomicCalendar.assert_populated() in any pipeline that must "
    "not silently run without event data; paper sessions do so by default.\n"
    "KNOWN LIMITATION: released figures get revised. A feed pulled today gives "
    "REVISED actuals for historical dates, not the print the market traded. "
    "Backtests conditioned on 'actual vs forecast' are therefore optimistic "
    "unless the feed preserves first prints. The official fixture stores no "
    "figures at all, only scheduled times."
)


class CalendarError(ValueError):
    """The calendar cannot answer that honestly."""


class ImpactLevel(str, Enum):
    """How much the market typically moves on this release.

    Ordinal, and ordered — ``ImpactLevel.HIGH >= ImpactLevel.MEDIUM`` works.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, ImpactLevel):
            return self.rank >= other.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, ImpactLevel):
            return self.rank > other.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, ImpactLevel):
            return self.rank <= other.rank
        return NotImplemented

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, ImpactLevel):
            return self.rank < other.rank
        return NotImplemented


# =====================================================================
# Event model
# =====================================================================


@dataclass(frozen=True, slots=True)
class RecurringEventType:
    """A *type* of recurring release. Carries no dates — see the module docstring."""

    key: str
    name: str
    currency: str
    impact: ImpactLevel
    typical_schedule: str
    typical_release_utc: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "currency": self.currency,
            "impact": self.impact.value,
            "typical_schedule": self.typical_schedule,
            "typical_release_utc": self.typical_release_utc,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RecurringEventType:
        return cls(
            key=str(raw["key"]),
            name=str(raw["name"]),
            currency=str(raw["currency"]).upper(),
            impact=ImpactLevel(str(raw["impact"]).lower()),
            typical_schedule=str(raw["typical_schedule"]),
            typical_release_utc=str(raw["typical_release_utc"]),
            notes=str(raw.get("notes", "")),
        )


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    """One dated release.

    ``window_end`` is for a release with no fixed time (the Bank of Japan): the
    event is then the whole span ``[event_time, window_end]`` and
    ``time_known`` is False. For an ordinary release it is ``None`` and the
    event is the instant ``event_time``.
    """

    event_time: pd.Timestamp
    currency: str
    name: str
    impact: ImpactLevel
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    recurring_key: str | None = None
    source: str = "unknown"
    event_id: str = ""
    source_url: str = ""
    retrieved_at: str = ""
    time_known: bool = True
    window_end: pd.Timestamp | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ts = pd.Timestamp(self.event_time)
        if ts.tzinfo is None:
            raise CalendarError(
                f"{self.name}: event_time must be tz-aware UTC. A naive event "
                "time is how a blackout window ends up five hours out — the same "
                "class of bug as the HistData EST stamps."
            )
        object.__setattr__(self, "event_time", ts.tz_convert("UTC"))
        object.__setattr__(self, "currency", str(self.currency).upper())
        object.__setattr__(self, "tags", tuple(str(t) for t in self.tags))
        if self.window_end is not None:
            end = pd.Timestamp(self.window_end)
            if end.tzinfo is None:
                raise CalendarError(f"{self.name}: window_end must be tz-aware UTC")
            end = end.tz_convert("UTC")
            if end < self.event_time:
                raise CalendarError(f"{self.name}: window_end is before event_time")
            object.__setattr__(self, "window_end", end)
        if not self.event_id:
            object.__setattr__(
                self,
                "event_id",
                f"{self.currency}:{self.name}:{self.event_time.isoformat()}",
            )

    @property
    def span_end(self) -> pd.Timestamp:
        """The last instant the event can occur at: ``window_end`` or ``event_time``."""
        return self.window_end if self.window_end is not None else self.event_time

    def actual_as_of(self, now: pd.Timestamp) -> float | None:
        """The actual figure, but only if it has actually been released.

        Reading ``event.actual`` inside a backtest at a bar before the release
        is look-ahead. This method is the causal accessor.
        """
        ts = pd.Timestamp(now)
        if ts.tzinfo is None:
            raise CalendarError("actual_as_of needs a tz-aware timestamp")
        return self.actual if ts >= self.span_end else None

    def surprise(self, now: pd.Timestamp) -> float | None:
        """``actual - forecast``, once released and only if both are known."""
        a = self.actual_as_of(now)
        if a is None or self.forecast is None:
            return None
        return a - self.forecast

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_time": self.event_time.isoformat(),
            "currency": self.currency,
            "name": self.name,
            "impact": self.impact.value,
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
            "recurring_key": self.recurring_key,
            "source": self.source,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "time_known": self.time_known,
            "window_end": None if self.window_end is None else self.window_end.isoformat(),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EconomicEvent:
        def _num(key: str) -> float | None:
            v = raw.get(key)
            if v is None or v == "":
                return None
            return float(v)

        ts = pd.Timestamp(raw["event_time"])
        if ts.tzinfo is None:
            raise CalendarError(
                f"event {raw.get('name')!r} has a timezone-naive event_time "
                f"({raw['event_time']!r}). Declare the zone in the file; this "
                "loader will not guess one."
            )
        # ``event_type`` is accepted as an alias for ``recurring_key``: the
        # official fixture names the taxonomy key that way.
        key = raw.get("recurring_key") or raw.get("event_type")
        window_end = raw.get("window_end")
        if window_end not in (None, ""):
            window_end = pd.Timestamp(window_end)
            if window_end.tzinfo is None:
                raise CalendarError(
                    f"event {raw.get('name')!r} has a timezone-naive window_end"
                )
        else:
            window_end = None
        tags = raw.get("tags") or ()
        if isinstance(tags, str):
            tags = tuple(t for t in tags.split("|") if t)
        time_known = raw.get("time_known", True)
        if isinstance(time_known, str):
            time_known = time_known.strip().lower() not in ("false", "0", "no")
        return cls(
            event_time=ts,
            currency=str(raw["currency"]),
            name=str(raw["name"]),
            impact=ImpactLevel(str(raw["impact"]).lower()),
            actual=_num("actual"),
            forecast=_num("forecast"),
            previous=_num("previous"),
            recurring_key=(str(key) if key else None),
            source=str(raw.get("source", "unknown")),
            event_id=str(raw.get("event_id", "")),
            source_url=str(raw.get("source_url") or ""),
            retrieved_at=str(raw.get("retrieved_at") or ""),
            time_known=bool(time_known),
            window_end=window_end,
            tags=tuple(tags),
        )


@dataclass(frozen=True, slots=True)
class BlackoutWindow:
    """A period during which an instrument should not be traded."""

    start: pd.Timestamp
    end: pd.Timestamp
    events: tuple[EconomicEvent, ...]

    @property
    def reason(self) -> str:
        return "; ".join(_describe(e) for e in self.events)

    def contains(self, ts: pd.Timestamp) -> bool:
        return self.start <= pd.Timestamp(ts) <= self.end


@dataclass(frozen=True, slots=True)
class CalendarCoverage:
    """What the calendar actually knows. Check this before trusting a blackout."""

    n_events: int
    first_event: pd.Timestamp | None
    last_event: pd.Timestamp | None
    currencies: tuple[str, ...]
    by_impact: dict[str, int] = field(default_factory=dict)
    sources: tuple[str, ...] = ()
    #: The span the calendar's publisher CLAIMS to be complete over. When set,
    #: :meth:`covers` uses it instead of first/last event: a calendar enumerated
    #: from 2024-01-01 knows that nothing happened between midnight and the
    #: first release, which first/last event cannot express.
    declared_start: pd.Timestamp | None = None
    declared_end: pd.Timestamp | None = None
    by_source: dict[str, int] = field(default_factory=dict)

    @property
    def is_populated(self) -> bool:
        return self.n_events > 0

    def covers(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        if not self.is_populated:
            return False
        assert self.first_event is not None and self.last_event is not None
        lo = self.declared_start if self.declared_start is not None else self.first_event
        hi = self.declared_end if self.declared_end is not None else self.last_event
        return lo <= pd.Timestamp(start) and hi >= pd.Timestamp(end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_events": self.n_events,
            "first_event": str(self.first_event),
            "last_event": str(self.last_event),
            "currencies": list(self.currencies),
            "by_impact": dict(self.by_impact),
            "sources": list(self.sources),
            "by_source": dict(self.by_source),
            "declared_start": None if self.declared_start is None else str(self.declared_start),
            "declared_end": None if self.declared_end is None else str(self.declared_end),
            "is_populated": self.is_populated,
        }


# =====================================================================
# Instrument -> currency exposure
# =====================================================================

#: Metals and indices are quoted in a currency and driven by that currency's
#: macro even though the "base" is not a currency at all. Stated explicitly
#: rather than inferred, because inferring it is how XAUUSD ends up exposed to
#: a currency called "XAU".
_NON_CURRENCY_BASES = {"XAU", "XAG", "WTI", "BCO"}


def instrument_currencies(symbol: str) -> tuple[str, ...]:
    """Currencies whose economic releases move this instrument.

    Raises for an unregistered symbol: the instrument registry refuses to guess
    contract specs and this refuses to guess macro exposure.
    """
    inst = instrument_registry.get(symbol)
    out: list[str] = []
    for code in (inst.base, inst.quote):
        c = code.upper()
        if c in _NON_CURRENCY_BASES:
            continue
        if instrument_registry.is_iso_currency(c):
            out.append(c)
    if inst.asset_class is AssetClass.INDEX and not out:
        out.append(inst.quote.upper())
    return tuple(dict.fromkeys(out))


# =====================================================================
# Interface
# =====================================================================


class EconomicCalendar(ABC):
    """The interface every consumer codes against.

    Implementations must be *causal-safe by construction*: no method returns a
    released ``actual`` for an event that has not happened yet. Scheduled times
    are fair game; realised numbers are not.
    """

    @abstractmethod
    def all_events(self) -> tuple[EconomicEvent, ...]:
        """Every event known to this calendar, sorted by time."""

    def declared_span(self) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        """The span this calendar claims to be complete over, if it declares one."""
        return (None, None)

    # -- derived, implemented once here ------------------------------

    def coverage(self) -> CalendarCoverage:
        events = self.all_events()
        declared_start, declared_end = self.declared_span()
        if not events:
            return CalendarCoverage(0, None, None, (), {}, ())
        by_impact: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for e in events:
            by_impact[e.impact.value] = by_impact.get(e.impact.value, 0) + 1
            by_source[e.source] = by_source.get(e.source, 0) + 1
        return CalendarCoverage(
            n_events=len(events),
            first_event=events[0].event_time,
            last_event=events[-1].event_time,
            currencies=tuple(sorted({e.currency for e in events})),
            by_impact=by_impact,
            sources=tuple(sorted({e.source for e in events})),
            declared_start=declared_start,
            declared_end=declared_end,
            by_source=dict(sorted(by_source.items())),
        )

    def assert_populated(self, *, start: pd.Timestamp | None = None,
                         end: pd.Timestamp | None = None,
                         currencies: Sequence[str] | None = None) -> None:
        """Raise unless the calendar actually has events (optionally, covering a span).

        Call this from any pipeline that must not run blind. Without it, an
        empty calendar reports "no blackout" for every bar in history, and a
        backtest trades through every NFP with no warning at all.

        ``currencies``, when given, must each have at least one event: a
        calendar full of USD releases says nothing about an AUD instrument.
        """
        cov = self.coverage()
        if not cov.is_populated:
            raise CalendarError(
                "economic calendar is empty: every blackout query will return "
                "False and every event filter will pass. " + USER_ACTION_NOTE
            )
        if start is not None and end is not None and not cov.covers(start, end):
            lo = cov.declared_start if cov.declared_start is not None else cov.first_event
            hi = cov.declared_end if cov.declared_end is not None else cov.last_event
            raise CalendarError(
                f"economic calendar covers {lo} .. {hi}, "
                f"which does not span the requested {start} .. {end}. "
                + USER_ACTION_NOTE
            )
        if currencies:
            missing = sorted({c.upper() for c in currencies} - set(cov.currencies))
            if missing:
                raise CalendarError(
                    f"economic calendar has no events for {missing} (it covers "
                    f"{list(cov.currencies)}). " + USER_ACTION_NOTE
                )

    def events_between(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        *,
        currencies: Sequence[str] | None = None,
        min_impact: ImpactLevel | str | None = None,
    ) -> tuple[EconomicEvent, ...]:
        """Events in the closed interval ``[start, end]``, filtered."""
        lo, hi = _as_utc(start), _as_utc(end)
        if lo > hi:
            raise CalendarError("events_between: start is after end")
        want = {c.upper() for c in currencies} if currencies else None
        floor = ImpactLevel(min_impact) if min_impact is not None else None
        out = []
        for e in self.all_events():
            if e.event_time < lo or e.event_time > hi:
                continue
            if want is not None and e.currency not in want:
                continue
            if floor is not None and e.impact.rank < floor.rank:
                continue
            out.append(e)
        return tuple(out)

    def next_event(
        self,
        after: pd.Timestamp,
        *,
        currencies: Sequence[str] | None = None,
        min_impact: ImpactLevel | str | None = None,
    ) -> EconomicEvent | None:
        ts = _as_utc(after)
        want = {c.upper() for c in currencies} if currencies else None
        floor = ImpactLevel(min_impact) if min_impact is not None else None
        for e in self.all_events():
            if e.event_time < ts:
                continue
            if want is not None and e.currency not in want:
                continue
            if floor is not None and e.impact.rank < floor.rank:
                continue
            return e
        return None

    # -- blackout ----------------------------------------------------

    def events_near(
        self,
        instrument: str,
        ts: pd.Timestamp,
        *,
        minutes_before: int = 30,
        minutes_after: int = 30,
        min_impact: ImpactLevel | str = ImpactLevel.HIGH,
    ) -> tuple[EconomicEvent, ...]:
        """Relevant events whose blackout window contains ``ts``.

        The window around an event at ``T`` is ``[T - minutes_before,
        T + minutes_after]``. A bar at ``ts`` is in blackout when ``ts`` falls in
        that closed interval for at least one relevant event.
        """
        return self.events_near_currencies(
            instrument_currencies(instrument),
            ts,
            minutes_before=minutes_before,
            minutes_after=minutes_after,
            min_impact=min_impact,
        )

    def events_near_currencies(
        self,
        currencies: Sequence[str],
        ts: pd.Timestamp,
        *,
        minutes_before: int = 30,
        minutes_after: int = 30,
        min_impact: ImpactLevel | str = ImpactLevel.HIGH,
    ) -> tuple[EconomicEvent, ...]:
        """As :meth:`events_near`, for explicit currencies rather than an instrument.

        An event spanning ``[T0, T1]`` (``T0 == T1`` for a fixed-time release)
        blacks out ``ts`` iff ``T0 - minutes_before <= ts <= T1 + minutes_after``.
        """
        if minutes_before < 0 or minutes_after < 0:
            raise CalendarError("blackout margins must be non-negative")
        now = _as_utc(ts)
        ccys = {c.upper() for c in currencies}
        floor = ImpactLevel(min_impact)
        before = pd.Timedelta(minutes=minutes_before)
        after = pd.Timedelta(minutes=minutes_after)
        return tuple(
            e
            for e in self.all_events()
            if e.currency in ccys
            and e.impact.rank >= floor.rank
            and e.event_time - before <= now <= e.span_end + after
        )

    def in_blackout(
        self,
        instrument: str,
        ts: pd.Timestamp,
        *,
        minutes_before: int = 30,
        minutes_after: int = 30,
        min_impact: ImpactLevel | str = ImpactLevel.HIGH,
    ) -> bool:
        """Is instrument X within N minutes of a high-impact event?"""
        return bool(
            self.events_near(
                instrument,
                ts,
                minutes_before=minutes_before,
                minutes_after=minutes_after,
                min_impact=min_impact,
            )
        )

    def blackout_mask(
        self,
        instrument: str,
        index: pd.DatetimeIndex,
        *,
        minutes_before: int = 30,
        minutes_after: int = 30,
        min_impact: ImpactLevel | str = ImpactLevel.HIGH,
    ) -> pd.Series:
        """Vectorised blackout flag over a bar index.

        A bar is blacked out when its *start* falls inside a window. For bar-close
        decision-making on an H4 clock a 30-minute window is meaningless — set the
        margins in the same units as the bars you trade, or accept that a
        sub-bar window will rarely fire.
        """
        if index.tz is None:
            raise CalendarError("blackout_mask needs a tz-aware index")
        ccys = instrument_currencies(instrument)
        floor = ImpactLevel(min_impact)
        relevant = [
            e
            for e in self.all_events()
            if e.currency in ccys and e.impact.rank >= floor.rank
        ]
        mask = pd.Series(False, index=index)
        if not relevant:
            return mask
        before = pd.Timedelta(minutes=minutes_before)
        after = pd.Timedelta(minutes=minutes_after)
        arr = index.tz_convert("UTC")
        flags = mask.to_numpy()
        for e in relevant:
            lo = e.event_time - before
            hi = e.span_end + after
            flags |= np.asarray((arr >= lo) & (arr <= hi))
        return pd.Series(flags, index=index)

    def blackout_windows(
        self,
        instrument: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        *,
        minutes_before: int = 30,
        minutes_after: int = 30,
        min_impact: ImpactLevel | str = ImpactLevel.HIGH,
        merge: bool = True,
    ) -> tuple[BlackoutWindow, ...]:
        """Blackout windows overlapping ``[start, end]``, optionally merged."""
        lo, hi = _as_utc(start), _as_utc(end)
        before = pd.Timedelta(minutes=minutes_before)
        after = pd.Timedelta(minutes=minutes_after)
        ccys = instrument_currencies(instrument)
        floor = ImpactLevel(min_impact)
        raw: list[BlackoutWindow] = []
        for e in self.all_events():
            if e.currency not in ccys or e.impact.rank < floor.rank:
                continue
            w_lo, w_hi = e.event_time - before, e.span_end + after
            if w_hi < lo or w_lo > hi:
                continue
            raw.append(BlackoutWindow(w_lo, w_hi, (e,)))
        if not merge or len(raw) < 2:
            return tuple(raw)
        raw.sort(key=lambda w: w.start)
        merged: list[BlackoutWindow] = [raw[0]]
        for w in raw[1:]:
            last = merged[-1]
            if w.start <= last.end:
                merged[-1] = BlackoutWindow(
                    last.start, max(last.end, w.end), last.events + w.events
                )
            else:
                merged.append(w)
        return tuple(merged)


class InMemoryEconomicCalendar(EconomicCalendar):
    """The file-backed implementation. Events are held sorted and immutable."""

    def __init__(
        self,
        events: Iterable[EconomicEvent] = (),
        *,
        declared_start: pd.Timestamp | None = None,
        declared_end: pd.Timestamp | None = None,
    ) -> None:
        seen: dict[str, EconomicEvent] = {}
        for e in events:
            seen[e.event_id] = e
        self._events = tuple(sorted(seen.values(), key=lambda e: (e.event_time, e.name)))
        self._declared_start = None if declared_start is None else _as_utc(declared_start)
        self._declared_end = None if declared_end is None else _as_utc(declared_end)
        if (
            self._declared_start is not None
            and self._declared_end is not None
            and self._declared_start > self._declared_end
        ):
            raise CalendarError("declared_start is after declared_end")

    def all_events(self) -> tuple[EconomicEvent, ...]:
        return self._events

    def declared_span(self) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        return (self._declared_start, self._declared_end)

    def __len__(self) -> int:
        return len(self._events)

    def with_events(self, more: Iterable[EconomicEvent]) -> InMemoryEconomicCalendar:
        """A new calendar with additional events. Never mutates in place.

        The declared span is kept: it is a claim about the ORIGINAL sources,
        and extra events neither widen nor narrow it.
        """
        return InMemoryEconomicCalendar(
            [*self._events, *more],
            declared_start=self._declared_start,
            declared_end=self._declared_end,
        )

    def to_json(self) -> str:
        return json.dumps([e.to_dict() for e in self._events], indent=2)

    # -- loaders -----------------------------------------------------

    @classmethod
    def from_json(cls, path: str | Path) -> InMemoryEconomicCalendar:
        return cls(load_events_json(path))

    @classmethod
    def from_csv(cls, path: str | Path) -> InMemoryEconomicCalendar:
        return cls(load_events_csv(path))

    @classmethod
    def empty(cls) -> InMemoryEconomicCalendar:
        """An explicitly empty calendar.

        Useful in tests and as a deliberate "we know we have no event data"
        marker — which is honest, unlike an implicit ``None`` that some caller
        later interprets as "nothing was happening".
        """
        return cls(())


def _describe(e: EconomicEvent) -> str:
    if e.window_end is None:
        return f"{e.currency} {e.name} @ {e.event_time}"
    return f"{e.currency} {e.name} @ {e.event_time} .. {e.window_end} (time not fixed)"


def _as_utc(ts: pd.Timestamp) -> pd.Timestamp:
    out = pd.Timestamp(ts)
    if out.tzinfo is None:
        raise CalendarError(
            f"{ts!r} is timezone-naive; the calendar will not guess a zone"
        )
    return out.tz_convert("UTC")


# =====================================================================
# File loaders
# =====================================================================

#: Columns a CSV must carry. ``event_time`` must include an offset (e.g.
#: ``2019-07-31T18:00:00+00:00``).
CSV_REQUIRED_COLUMNS = ("event_time", "currency", "name", "impact")
CSV_OPTIONAL_COLUMNS = (
    "actual", "forecast", "previous", "recurring_key", "source",
    "source_url", "retrieved_at", "time_known", "window_end", "tags",
)


def load_events_json(path: str | Path) -> tuple[EconomicEvent, ...]:
    """Load events from a JSON list of objects (see :meth:`EconomicEvent.to_dict`)."""
    p = Path(path)
    if not p.exists():
        raise CalendarError(f"economic event file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "events" in raw:
        raw = raw["events"]
    if not isinstance(raw, list):
        raise CalendarError(f"{p}: expected a JSON list of events")
    return tuple(EconomicEvent.from_dict(item) for item in raw)


def load_events_csv(path: str | Path) -> tuple[EconomicEvent, ...]:
    """Load events from a CSV with :data:`CSV_REQUIRED_COLUMNS`."""
    p = Path(path)
    if not p.exists():
        raise CalendarError(f"economic event file not found: {p}")
    with p.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in CSV_REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise CalendarError(f"{p}: missing required columns {missing}")
        return tuple(EconomicEvent.from_dict(dict(row)) for row in reader)


def official_calendar_manifest(path: str | Path | None = None) -> dict[str, Any]:
    """The official fixture's header: sources used and skipped, coverage, counts.

    Everything in the file except the ``events`` list. The CLI prints it; tests
    check the per-source counts in it against the events themselves.
    """
    p = Path(path) if path is not None else OFFICIAL_EVENTS_FIXTURE
    if not p.exists():
        raise CalendarError(f"official event fixture not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "events" not in raw:
        raise CalendarError(f"{p}: expected an object with an 'events' list")
    return {k: v for k, v in raw.items() if k != "events"}


def load_official_calendar(path: str | Path | None = None) -> InMemoryEconomicCalendar:
    """The committed official scheduled-events calendar, with its declared span.

    Reads :data:`OFFICIAL_EVENTS_FIXTURE` (or ``path``). No network: the file
    was fetched from the publishers' own pages and committed; see its header
    for every URL, the time convention per source and what was left out.
    """
    p = Path(path) if path is not None else OFFICIAL_EVENTS_FIXTURE
    manifest = official_calendar_manifest(p)
    cov = manifest.get("coverage") or {}
    start = cov.get("declared_start")
    end = cov.get("declared_end")
    return InMemoryEconomicCalendar(
        load_events_json(p),
        declared_start=None if not start else pd.Timestamp(start),
        declared_end=None if not end else pd.Timestamp(end),
    )


def load_recurring_event_types(
    path: str | Path | None = None,
) -> tuple[RecurringEventType, ...]:
    """The curated recurring-event taxonomy shipped with the package."""
    p = Path(path) if path is not None else RECURRING_EVENTS_FIXTURE
    if not p.exists():
        raise CalendarError(f"recurring event fixture not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return tuple(RecurringEventType.from_dict(item) for item in raw["event_types"])


def recurring_types_for(symbol: str) -> tuple[RecurringEventType, ...]:
    """Recurring event types whose currency is relevant to ``symbol``."""
    ccys = set(instrument_currencies(symbol))
    return tuple(t for t in load_recurring_event_types() if t.currency in ccys)


def synthesise_events(
    types: Sequence[RecurringEventType],
    times: Sequence[pd.Timestamp],
    *,
    source: str = "synthetic",
) -> tuple[EconomicEvent, ...]:
    """Build dated events by pairing types with caller-supplied timestamps.

    Explicitly a *test and demonstration* helper. It does not know when FOMC
    actually met; the caller supplies the dates. Its ``source`` defaults to
    ``"synthetic"`` so a synthesised event can never be mistaken for a real one
    in a stored result.
    """
    if len(types) != len(times):
        raise CalendarError("synthesise_events needs one timestamp per type")
    return tuple(
        EconomicEvent(
            event_time=_as_utc(ts),
            currency=t.currency,
            name=t.name,
            impact=t.impact,
            recurring_key=t.key,
            source=source,
        )
        for t, ts in zip(types, times, strict=True)
    )
