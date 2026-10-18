"""Market session clocks: which FX sessions are open now, from one clock.

Each session is defined in its own city's local time, so daylight saving moves
it correctly (London 08:00 to 17:00 local is 07:00 to 16:00 UTC in summer and
08:00 to 17:00 UTC in winter). The FX week runs from Sunday 17:00 to Friday
17:00 New York time; outside it every FX session is closed whatever its local
hours say. Crypto venues trade continuously.

These are CONVENTIONAL session hours used to orient an operator. They are not
a trading calendar: no strategy, gate or sizing decision reads this module,
and public holidays are not modelled (stated in every payload).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

__all__ = ["SESSIONS", "MarketSessionView", "fx_week_open", "market_sessions"]

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class SessionSpec:
    key: str
    label: str
    tz: str
    opens: time
    closes: time


SESSIONS: tuple[SessionSpec, ...] = (
    SessionSpec("sydney", "Sydney", "Australia/Sydney", time(7, 0), time(16, 0)),
    SessionSpec("tokyo", "Tokyo", "Asia/Tokyo", time(9, 0), time(18, 0)),
    SessionSpec("london", "London", "Europe/London", time(8, 0), time(17, 0)),
    SessionSpec("new_york", "New York", "America/New_York", time(8, 0), time(17, 0)),
)


class MarketSessionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    timezone: str
    #: Local time in the session's city, ``HH:MM``.
    local_time: str
    open: bool
    #: The next open (when closed) or close (when open), UTC.
    next_change_at: datetime
    detail: str


def fx_week_open(now: datetime) -> bool:
    """Sunday 17:00 to Friday 17:00, New York time."""
    local = now.astimezone(NEW_YORK)
    day, t = local.weekday(), local.time()
    if day == 5:  # Saturday
        return False
    if day == 6:  # Sunday
        return t >= time(17, 0)
    if day == 4:  # Friday
        return t < time(17, 0)
    return True


def _fx_week_next_change(now: datetime) -> datetime:
    """The next FX week open or close, UTC."""
    local = now.astimezone(NEW_YORK)
    if fx_week_open(now):
        days = (4 - local.weekday()) % 7
        close = datetime.combine(local.date() + timedelta(days=days), time(17, 0), NEW_YORK)
        return close.astimezone(UTC)
    days = (6 - local.weekday()) % 7
    opening = datetime.combine(local.date() + timedelta(days=days), time(17, 0), NEW_YORK)
    if opening <= local:
        opening += timedelta(days=7)
    return opening.astimezone(UTC)


def _session_state(spec: SessionSpec, now: datetime) -> tuple[bool, datetime]:
    zone = ZoneInfo(spec.tz)
    local = now.astimezone(zone)
    today_open = datetime.combine(local.date(), spec.opens, zone)
    today_close = datetime.combine(local.date(), spec.closes, zone)
    in_hours = local.weekday() < 5 and today_open <= local < today_close
    week = fx_week_open(now)
    is_open = in_hours and week
    if is_open:
        nxt = min(today_close.astimezone(UTC), _fx_week_next_change(now))
        return True, nxt
    # Next local weekday open at or after now that falls inside the FX week.
    for days in range(0, 8):
        day = local.date() + timedelta(days=days)
        candidate = datetime.combine(day, spec.opens, zone)
        if candidate <= local or candidate.weekday() >= 5:
            continue
        if fx_week_open(candidate.astimezone(UTC)):
            return False, candidate.astimezone(UTC)
    return False, _fx_week_next_change(now)


def market_sessions(now: datetime | None = None) -> list[MarketSessionView]:
    stamp = (now or datetime.now(tz=UTC)).astimezone(UTC)
    out: list[MarketSessionView] = []
    week = fx_week_open(stamp)
    for spec in SESSIONS:
        is_open, nxt = _session_state(spec, stamp)
        local = stamp.astimezone(ZoneInfo(spec.tz))
        detail = (
            f"{spec.opens:%H:%M} to {spec.closes:%H:%M} local, weekdays"
            + ("" if week else "; the FX week is closed (Friday 17:00 to Sunday 17:00 New York)")
            + ". Conventional hours; public holidays are not modelled."
        )
        out.append(
            MarketSessionView(
                key=spec.key,
                label=spec.label,
                timezone=spec.tz,
                local_time=f"{local:%H:%M}",
                open=is_open,
                next_change_at=nxt,
                detail=detail,
            )
        )
    return out
