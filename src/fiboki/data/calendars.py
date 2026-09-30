"""Session calendars: what "the market was open" means, per asset class.

Needed so that gap detection can tell the difference between a weekend (normal,
uninteresting) and a missing Tuesday afternoon (a hole in the data that will
silently distort every indicator that crosses it).

Sessions are defined in the venue's *local* time and converted through a real
tz database, so DST is handled by construction rather than by an offset
constant. FX trades Sunday 17:00 New York to Friday 17:00 New York; in UTC that
is 21:00 in summer and 22:00 in winter, which is exactly the kind of thing V1
got wrong by hardcoding one of the two.

Known approximation: the holiday sets here are fixed-date only (1 Jan, 25 Dec,
26 Dec). Moving holidays (Good Friday, Thanksgiving) are *not* modelled, so a
gap on those days is reported as unexpected. That is the deliberate direction to
be wrong in: a false alarm is cheap, a missed hole is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import AssetClass, Timeframe

NEW_YORK = "America/New_York"

# Fixed-date market holidays observed by essentially every FX/metals venue.
_FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset({(1, 1), (12, 25), (12, 26)})


@dataclass(frozen=True, slots=True)
class SessionCalendar:
    """A weekly trading session expressed in a venue-local timezone.

    ``open_weekday``/``close_weekday`` use Python's Monday=0 convention.

    ``daily_break`` is an optional ``(start_hour, end_hour)`` local-time window
    that is closed every day. It may wrap midnight: ``(17, 18)`` is the CME
    metals maintenance hour, while ``(15, 9)`` expresses a cash session that is
    only open 09:00-15:00 local. Wrapping matters — without it there is no way
    to express a venue that is closed for most of the day, and it would be
    modelled as open, which is worse than being modelled as absent.
    """

    name: str
    tz: str = NEW_YORK
    open_weekday: int = 6  # Sunday
    open_hour: int = 17
    close_weekday: int = 4  # Friday
    close_hour: int = 17
    daily_break: tuple[int, int] | None = None
    continuous: bool = False
    holidays: frozenset[tuple[int, int]] = field(default_factory=lambda: _FIXED_HOLIDAYS)

    def _zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def is_holiday(self, ts: pd.Timestamp) -> bool:
        local = ts.tz_convert(self.tz)
        return (local.month, local.day) in self.holidays

    def is_open(self, ts: pd.Timestamp) -> bool:
        """Is the venue open at this instant (tz-aware timestamp required)?"""
        if ts.tzinfo is None:
            raise ValueError("SessionCalendar.is_open requires a tz-aware timestamp")
        if self.continuous:
            return True
        local = ts.tz_convert(self.tz)
        if (local.month, local.day) in self.holidays:
            return False
        dow = local.weekday()
        hour = local.hour + local.minute / 60.0

        if self.daily_break is not None:
            brk_start, brk_end = self.daily_break
            if brk_start <= brk_end:
                in_break = brk_start <= hour < brk_end
            else:  # wraps midnight
                in_break = hour >= brk_start or hour < brk_end
            if in_break:
                return False

        # Week runs open_weekday@open_hour -> close_weekday@close_hour, wrapping
        # through the weekend.
        def _minutes(d: int, h: float) -> float:
            return d * 24 * 60 + h * 60

        start = _minutes(self.open_weekday, self.open_hour)
        end = _minutes(self.close_weekday, self.close_hour)
        now = _minutes(dow, hour)
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    def expected_bar_starts(
        self, start: pd.Timestamp, end: pd.Timestamp, timeframe: Timeframe
    ) -> pd.DatetimeIndex:
        """Bar-start instants expected strictly inside ``(start, end)``.

        Used to size a gap: how many bars *should* have been there. A weekend
        returns zero; a missing Tuesday returns the real count.
        """
        if self.continuous:
            grid = pd.date_range(
                start=start, end=end, freq=f"{timeframe.minutes}min", tz="UTC", inclusive="neither"
            )
            return grid
        grid = pd.date_range(
            start=start, end=end, freq=f"{timeframe.minutes}min", tz="UTC", inclusive="neither"
        )
        if len(grid) == 0:
            return grid
        mask = [self.is_open(ts) for ts in grid]
        return grid[mask]


FX_CALENDAR = SessionCalendar(name="fx_24_5")

# Spot metals follow the CME globex week with a one-hour daily settlement break.
METALS_CALENDAR = SessionCalendar(name="metals", daily_break=(17, 18))

ENERGY_CALENDAR = SessionCalendar(name="energy", daily_break=(17, 18))

# Cash index CFDs: quoted around the underlying cash session plus out-of-hours.
INDEX_CALENDAR = SessionCalendar(name="index", daily_break=(17, 18))

CRYPTO_CALENDAR = SessionCalendar(
    name="crypto_24_7", continuous=True, holidays=frozenset()
)

# Bond and agricultural/industrial-metal CFDs (registered from OANDA 2026-09-30).
# KNOWN APPROXIMATION: their real sessions are far shorter than this (the
# pricing fixture shows CORN, SOYBN and WHEAT last quoted at 18:19Z, UK10YB at
# 16:59Z, DE10YB at 19:59Z), and no session times are recorded anywhere in the
# repository to model them from. Modelled as the energy week, so gap detection
# EXPECTS bars in hours these markets are shut: false alarms, which is the
# direction this module chooses to be wrong in (module docstring).
COMMODITY_CALENDAR = SessionCalendar(name="commodity_cfd", daily_break=(17, 18))
BOND_CALENDAR = SessionCalendar(name="bond_cfd", daily_break=(17, 18))

_BY_ASSET_CLASS = {
    AssetClass.FX_MAJOR: FX_CALENDAR,
    AssetClass.FX_CROSS: FX_CALENDAR,
    AssetClass.METAL: METALS_CALENDAR,
    AssetClass.ENERGY: ENERGY_CALENDAR,
    AssetClass.INDEX: INDEX_CALENDAR,
    AssetClass.CRYPTO: CRYPTO_CALENDAR,
    AssetClass.EQUITY: INDEX_CALENDAR,
    AssetClass.COMMODITY: COMMODITY_CALENDAR,
    AssetClass.BOND: BOND_CALENDAR,
}


def calendar_for(symbol: str) -> SessionCalendar:
    """The session calendar for a registered instrument.

    Raises ``KeyError`` for an unregistered symbol — the registry refuses to
    guess contract specs, and this refuses to guess trading hours.
    """
    inst = instrument_registry.get(symbol)
    return _BY_ASSET_CLASS[inst.asset_class]


def observed_holidays(year: int, calendar: SessionCalendar) -> list[date]:
    """The fixed-date holidays this calendar models for a given year."""
    return [date(year, m, d) for (m, d) in sorted(calendar.holidays)]
