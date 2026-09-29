"""Business-day financing, the New York session calendar and per-class minimum stops.

Audit P2-7 (financing charged every calendar night, no triple day), P2-6 (a
fixed 22:00 UTC weekend that is right only in northern winter) and P2-9 (a flat
4-pip minimum stop that is $0.04 on gold). Dates are chosen so the weekday is
obvious: 2024-01-01 and 2024-07-01 are Mondays.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.backtest.position import financing_nights, nights_between
from fiboki.core.enums import AssetClass
from fiboki.core.instruments import get as get_instrument
from fiboki.sim.fills import FxSessionCalendar
from fiboki.sim.profiles import (
    IG_REALISTIC,
    OANDA_REALISTIC,
    SEVERE_STRESS,
    ExecutionProfile,
    MinStopRule,
)


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


# ------------------------------------------------------------- financing


@pytest.mark.parametrize(
    ("last", "now", "cls", "expected", "why"),
    [
        ("2024-01-01 12:00", "2024-01-08 12:00", AssetClass.FX_MAJOR, 7,
         "full week: Mon 1 + Tue 1 + Wed 3 + Thu 1 + Fri 1 = 7"),
        ("2024-01-05 12:00", "2024-01-08 12:00", AssetClass.FX_MAJOR, 1,
         "Fri to Mon: Fri 1, Sat 0, Sun 0 (calendar count said 3)"),
        ("2024-01-02 12:00", "2024-01-04 12:00", AssetClass.FX_CROSS, 4,
         "Tue to Thu: Tue 1 + Wed 3 (calendar count said 2)"),
        ("2024-01-03 12:00", "2024-01-04 12:00", AssetClass.METAL, 3,
         "Wed only, spot metal settles T+2 like FX"),
        ("2024-01-05 12:00", "2024-01-08 12:00", AssetClass.INDEX, 3,
         "index CFDs charge the weekend on Friday"),
        ("2024-01-03 12:00", "2024-01-04 12:00", AssetClass.ENERGY, 1,
         "Wednesday is an ordinary night for energy"),
        ("2024-01-05 12:00", "2024-01-08 12:00", AssetClass.CRYPTO, 3,
         "crypto: every calendar night, no triple"),
        ("2024-01-06 12:00", "2024-01-07 12:00", AssetClass.FX_MAJOR, 0,
         "a weekend-only hold pays nothing"),
        ("2024-01-02 21:00", "2024-01-02 22:00", AssetClass.FX_MAJOR, 0,
         "half-open: a crossing AT last is not in (last, now]"),
        ("2024-01-02 20:00", "2024-01-02 21:00", AssetClass.FX_MAJOR, 1,
         "half-open: a crossing AT now is"),
    ],
)
def test_financing_nights(last, now, cls, expected, why) -> None:
    assert financing_nights(T(last), T(now), 21, cls) == expected, why


def test_a_full_week_matches_the_calendar_count() -> None:
    """The old rule's WEEKLY total was right; only the days were wrong."""
    last, now = T("2024-01-01 12:00"), T("2024-01-08 12:00")
    assert nights_between(last, now, 21) == 7
    assert financing_nights(last, now, 21, AssetClass.FX_MAJOR) == 7
    assert financing_nights(last, now, 21, AssetClass.INDEX) == 7


def test_an_unknown_asset_class_raises_rather_than_charging_calendar_nights() -> None:
    with pytest.raises(KeyError, match="no financing-day rule"):
        financing_nights(T("2024-01-01 12:00"), T("2024-01-03 12:00"), 21, "bond")


# --------------------------------------------------------------- sessions


EURUSD = get_instrument("EURUSD")


@pytest.mark.parametrize(
    ("when", "is_open"),
    [
        # Winter (EST, UTC-5): 17:00 New York is 22:00 UTC.
        ("2024-01-05 21:59", True),
        ("2024-01-05 22:00", False),
        ("2024-01-07 21:59", False),
        ("2024-01-07 22:00", True),
        # Summer (EDT, UTC-4): 17:00 New York is 21:00 UTC. The old fixed
        # 22:00 UTC rule got all four of these wrong.
        ("2024-07-05 20:59", True),
        ("2024-07-05 21:00", False),
        ("2024-07-07 20:59", False),
        ("2024-07-07 21:00", True),
        ("2024-07-06 12:00", False),
    ],
)
def test_the_fx_week_is_anchored_to_new_york(when: str, is_open: bool) -> None:
    assert FxSessionCalendar().is_open(EURUSD, T(when)) is is_open


def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        FxSessionCalendar().is_open(EURUSD, pd.Timestamp("2024-07-05 21:00"))


# ------------------------------------------------------------ min stops


@pytest.mark.parametrize(
    ("symbol", "ig", "severe"),
    [
        # FX keeps its flat pip floor: 4 pips IG, 10 pips SEVERE.
        ("EURUSD", 4 * 0.0001, 10 * 0.0001),
        ("USDJPY", 4 * 0.01, 10 * 0.01),
        # Everything else is a multiple of the typical spread: 3x IG, 6x SEVERE.
        ("XAUUSD", 3 * 30.0 * 0.01, 6 * 30.0 * 0.01),   # $0.90 / $1.80 (was $0.04)
        ("XAGUSD", 3 * 25.0 * 0.001, 6 * 25.0 * 0.001),
        ("US500", 3 * 0.4 * 1.0, 6 * 0.4 * 1.0),        # 1.2 points (was 4)
        ("JP225", 3 * 7.0 * 1.0, 6 * 7.0 * 1.0),        # 21 points (was 4, below the spread)
        ("WTIUSD", 3 * 3.0 * 0.01, 6 * 3.0 * 0.01),
    ],
)
def test_minimum_stop_is_per_asset_class(symbol: str, ig: float, severe: float) -> None:
    instrument = get_instrument(symbol)
    assert IG_REALISTIC.min_stop_distance_price(instrument) == pytest.approx(ig, abs=1e-12)
    assert SEVERE_STRESS.min_stop_distance_price(instrument) == pytest.approx(severe, abs=1e-12)


def test_profiles_without_a_minimum_are_unchanged() -> None:
    assert OANDA_REALISTIC.min_stop_distance_price(get_instrument("XAUUSD")) == 0.0


def test_a_class_missing_from_the_table_raises() -> None:
    from dataclasses import replace

    partial: ExecutionProfile = replace(
        IG_REALISTIC, min_stop_by_asset_class=(("fx_major", MinStopRule(floor_pips=4.0)),)
    )
    with pytest.raises(KeyError, match="none for 'metal'"):
        partial.min_stop_distance_price(get_instrument("XAUUSD"))


def test_the_rule_is_in_the_profile_fingerprint() -> None:
    fp = IG_REALISTIC.fingerprint()
    assert fp["key_version"] == "profile_v2"
    assert ["metal", 0.0, 3.0] in fp["min_stop_by_asset_class"]
