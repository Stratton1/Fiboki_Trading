"""Session calendars: DST-correct session boundaries, per asset class."""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.data.calendars import (
    CRYPTO_CALENDAR,
    FX_CALENDAR,
    METALS_CALENDAR,
    SessionCalendar,
    calendar_for,
    observed_holidays,
)


def utc(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


# --------------------------------------------------------------- FX


def test_fx_week_opens_at_2200_utc_in_winter():
    """17:00 New York = 22:00 UTC while EST is in force."""
    assert not FX_CALENDAR.is_open(utc("2026-01-04 21:59"))  # Sunday
    assert FX_CALENDAR.is_open(utc("2026-01-04 22:00"))


def test_fx_week_opens_at_2100_utc_in_summer():
    """The same 17:00 New York is 21:00 UTC under EDT. A fixed offset gets
    this wrong for eight months of the year."""
    assert not FX_CALENDAR.is_open(utc("2026-07-05 20:59"))  # Sunday
    assert FX_CALENDAR.is_open(utc("2026-07-05 21:00"))


def test_fx_week_closes_friday_1700_new_york():
    assert FX_CALENDAR.is_open(utc("2026-01-09 21:59"))  # Friday 16:59 NY
    assert not FX_CALENDAR.is_open(utc("2026-01-09 22:00"))


def test_fx_is_closed_all_saturday():
    for hour in range(24):
        assert not FX_CALENDAR.is_open(utc(f"2026-01-10 {hour:02d}:00"))


def test_fx_is_open_midweek():
    assert FX_CALENDAR.is_open(utc("2026-01-07 12:00"))  # Wednesday


def test_fixed_date_holidays_are_closed():
    assert not FX_CALENDAR.is_open(utc("2026-12-25 12:00"))
    assert not FX_CALENDAR.is_open(utc("2026-01-01 12:00"))
    assert FX_CALENDAR.is_holiday(utc("2026-12-26 12:00"))


def test_naive_timestamp_is_refused():
    with pytest.raises(ValueError, match="tz-aware"):
        FX_CALENDAR.is_open(pd.Timestamp("2026-01-07 12:00"))


# ------------------------------------------------------------ metals


def test_metals_have_a_daily_settlement_break():
    # 17:00-18:00 New York, i.e. 22:00-23:00 UTC in winter.
    assert not METALS_CALENDAR.is_open(utc("2026-01-07 22:30"))
    assert METALS_CALENDAR.is_open(utc("2026-01-07 23:30"))


def test_fx_has_no_daily_break():
    assert FX_CALENDAR.is_open(utc("2026-01-07 22:30"))


# ------------------------------------------------------------ crypto


def test_crypto_never_closes():
    for ts in ("2026-01-10 03:00", "2026-12-25 12:00", "2026-01-04 21:00"):
        assert CRYPTO_CALENDAR.is_open(utc(ts))


# ---------------------------------------------------- expected bars


def test_weekend_contains_no_expected_bars():
    start = utc("2026-01-09 21:00")  # Friday before close
    end = utc("2026-01-12 00:00")  # Monday
    assert len(FX_CALENDAR.expected_bar_starts(start, end, Timeframe.H1)) > 0
    # From the close itself, nothing is expected until Sunday 22:00.
    close = utc("2026-01-09 22:00")
    sunday_open = utc("2026-01-11 22:00")
    assert len(FX_CALENDAR.expected_bar_starts(close, sunday_open, Timeframe.H1)) == 0


def test_midweek_hole_has_expected_bars():
    start = utc("2026-01-07 10:00")
    end = utc("2026-01-07 15:00")
    expected = FX_CALENDAR.expected_bar_starts(start, end, Timeframe.H1)
    assert len(expected) == 4  # 11,12,13,14


def test_crypto_expects_bars_across_a_weekend():
    start = utc("2026-01-10 00:00")
    end = utc("2026-01-10 05:00")
    assert len(CRYPTO_CALENDAR.expected_bar_starts(start, end, Timeframe.H1)) == 4


# ---------------------------------------------------------- lookup


def test_calendar_for_dispatches_on_asset_class():
    assert calendar_for("EURUSD") is FX_CALENDAR
    assert calendar_for("GBPJPY") is FX_CALENDAR
    assert calendar_for("XAUUSD") is METALS_CALENDAR


def test_calendar_for_refuses_an_unregistered_symbol():
    with pytest.raises(KeyError, match="Unknown instrument"):
        calendar_for("NOTREAL")


def test_observed_holidays_lists_what_is_modelled():
    days = observed_holidays(2026, FX_CALENDAR)
    assert len(days) == 3
    assert all(d.year == 2026 for d in days)


def test_a_custom_calendar_can_be_declared():
    """A cash session needs a daily break that wraps midnight."""
    tokyo = SessionCalendar(
        name="tokyo_cash",
        tz="Asia/Tokyo",
        open_weekday=0,
        open_hour=9,
        close_weekday=4,
        close_hour=15,
        daily_break=(15, 9),  # closed 15:00 -> 09:00 next day
        holidays=frozenset(),
    )
    assert tokyo.is_open(pd.Timestamp("2026-01-07 10:00", tz="Asia/Tokyo"))
    assert not tokyo.is_open(pd.Timestamp("2026-01-07 16:00", tz="Asia/Tokyo"))
    assert not tokyo.is_open(pd.Timestamp("2026-01-07 03:00", tz="Asia/Tokyo"))
    assert not tokyo.is_open(pd.Timestamp("2026-01-10 10:00", tz="Asia/Tokyo"))  # Sat


def test_a_weekly_session_without_a_daily_break_stays_open_overnight():
    """FX really is open at 03:00 on a Wednesday; that is not a modelling slip."""
    assert FX_CALENDAR.is_open(pd.Timestamp("2026-01-07 03:00", tz="UTC"))
