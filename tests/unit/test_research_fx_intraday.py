"""Daily research FX rates derived from intraday bars, and the USD fallback wiring.

The V2 store holds H4 and H1 for the GBP crosses but no D1, so
``build_research_fx_source`` reduces H4 (else H1) bars to one rate per UTC day:
the last bar to close that day, stamped at that bar's CLOSE. The property that
matters is that no rate is ever readable before the bar that produced it has
closed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.core.money import daily_rates_from_intraday_closes
from fiboki.validation.run import FxSourceUnavailable, build_research_fx_source


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


class _Version:
    def __init__(self, version_id: str) -> None:
        self.version_id = version_id


class TimeframeStore:
    """``read_latest`` keyed on (symbol, timeframe), unlike the D1-only fake."""

    def __init__(self, frames: dict[tuple[str, Timeframe], pd.DataFrame]) -> None:
        self.frames = frames

    def read_latest(self, symbol, timeframe, *, kind=None):
        key = (symbol, Timeframe(timeframe))
        if key not in self.frames:
            raise LookupError(key)
        return self.frames[key], _Version(f"ds_{symbol}_{key[1].value}")


def h4(start: str, periods: int, *, seed: int = 3, level: float = 1.25) -> pd.DataFrame:
    """H4 bars on HistData's 21:00-UTC grid (01:00, 05:00, ... 21:00)."""
    idx = pd.date_range(start, periods=periods, freq="4h", tz="UTC", name="timestamp")
    close = level + np.cumsum(np.random.default_rng(seed).normal(0.0, 0.001, periods))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=idx)


# --------------------------------------------------------- the derivation


def test_each_daily_rate_is_the_last_bar_to_close_that_day_stamped_at_its_close() -> None:
    frame = h4("2024-01-01 01:00", 9)
    frame["close"] = np.arange(9, dtype=float)
    out = daily_rates_from_intraday_closes(frame, 240)
    # Day 1: bars open 01,05,09,13,17 close 05..21; the 21:00 bar closes on day 2.
    # Day 2: the 21:00 bar (closes 01:00) and bars open 01,05,09 (last closes 13:00).
    assert list(out.index) == [T("2024-01-01 21:00"), T("2024-01-02 13:00")]
    assert list(out) == [4.0, 8.0]


def test_a_bar_closing_exactly_at_midnight_belongs_to_the_day_it_ends() -> None:
    frame = h4("2024-01-01 16:00", 2)  # opens 16:00 and 20:00; the second closes at 00:00
    frame["close"] = [1.0, 2.0]
    out = daily_rates_from_intraday_closes(frame, 240)
    assert list(out.index) == [T("2024-01-02 00:00")]
    assert list(out) == [2.0]


def test_a_derived_rate_is_never_available_before_the_bar_that_produced_it_closes() -> None:
    """Look-ahead: for every derived rate, one second before its producing bar
    closes the source must answer with an EARLIER rate (or refuse), never it."""
    frame = h4("2024-01-01 01:00", 6 * 40, seed=11)
    store = TimeframeStore({("GBPUSD", Timeframe.H4): frame})
    fx, _ = build_research_fx_source(store, quote_currencies=("USD",))
    derived = fx.series["GBPUSD"]
    assert len(derived) >= 39
    closes = frame.index + pd.Timedelta(hours=4)
    for stamp, value in derived.items():
        producing = frame.index[closes == stamp]
        assert len(producing) == 1, "every rate is stamped at a real bar's close"
        assert frame.loc[producing[0], "close"] == value
        assert fx.rate("USD", "GBP", stamp) == pytest.approx(1 / value)
        before = stamp - pd.Timedelta(seconds=1)
        try:
            earlier = fx.rate("USD", "GBP", before)
        except KeyError:
            continue  # nothing known yet: a refusal, not a peek
        assert earlier == pytest.approx(1 / derived[derived.index < stamp].iloc[-1])


# --------------------------------------------------- build_research_fx_source


def test_d1_is_preferred_and_h4_is_used_only_when_d1_is_absent() -> None:
    d1 = pd.DataFrame(
        {"open": 1.3, "high": 1.3, "low": 1.3, "close": 1.3},
        index=pd.date_range("2024-01-01", periods=20, freq="1D", tz="UTC"),
    )
    both = TimeframeStore({("GBPUSD", Timeframe.D1): d1, ("GBPUSD", Timeframe.H4): h4("2024-01-01 01:00", 60)})
    fx, label = build_research_fx_source(both, quote_currencies=("USD",))
    assert fx.lineage["GBPUSD"]["source_timeframe"] == "D1"
    assert "GBPUSD@ds_GBPUSD_D1" in label and "[derived:" not in label

    only_h4 = TimeframeStore({("GBPUSD", Timeframe.H4): h4("2024-01-01 01:00", 60)})
    fx, label = build_research_fx_source(only_h4, quote_currencies=("USD",))
    assert fx.lineage["GBPUSD"]["source_timeframe"] == "H4"
    assert "derived from H4 closes" in fx.lineage["GBPUSD"]["derivation"]
    assert "GBPUSD@ds_GBPUSD_H4[derived:H4 last close per UTC day]" in label


def test_h1_is_used_when_neither_d1_nor_h4_exists() -> None:
    idx = pd.date_range("2024-01-01 00:00", periods=24 * 10, freq="1h", tz="UTC")
    frame = pd.DataFrame({"open": 1.25, "high": 1.25, "low": 1.25, "close": 1.25}, index=idx)
    fx, label = build_research_fx_source(
        TimeframeStore({("GBPUSD", Timeframe.H1): frame}), quote_currencies=("USD",)
    )
    assert fx.lineage["GBPUSD"]["source_timeframe"] == "H1"
    assert "[derived:H1 last close per UTC day]" in label
    assert fx.series["GBPUSD"].index[0] == T("2024-01-02 00:00")  # the 23:00 bar's close


def test_a_pair_with_no_bars_at_any_timeframe_is_still_refused() -> None:
    with pytest.raises(FxSourceUnavailable) as exc:
        build_research_fx_source(TimeframeStore({}), quote_currencies=("JPY",))
    assert exc.value.missing == ("GBPJPY",)
    assert "H4 or H1 bars of the same pair are accepted" in str(exc.value)


def test_the_usd_legs_are_loaded_and_answer_before_a_late_cross_starts() -> None:
    store = TimeframeStore(
        {
            ("GBPJPY", Timeframe.H4): h4("2024-02-01 01:00", 120, level=190.0),
            ("USDJPY", Timeframe.H4): h4("2024-01-01 01:00", 400, level=150.0),
            ("GBPUSD", Timeframe.H4): h4("2024-01-01 01:00", 400, level=1.25),
        }
    )
    fx, label = build_research_fx_source(store, quote_currencies=("JPY",))
    assert fx.fallback_via_pivot is True
    assert fx.lineage["_fallback"]["JPY"] == {
        "route": "via_usd",
        "legs": ["USDJPY", "GBPUSD"],
        "available": True,
        "missing_legs": [],
    }
    assert "fallback via_usd where the direct cross has no fresh rate for JPY" in label

    when = T("2024-01-15 12:00")
    rate, route = fx.rate_with_route("JPY", "GBP", when)
    usdjpy = fx.series["USDJPY"][fx.series["USDJPY"].index <= when].iloc[-1]
    gbpusd = fx.series["GBPUSD"][fx.series["GBPUSD"].index <= when].iloc[-1]
    assert route == "via_usd"
    assert rate == pytest.approx((1 / usdjpy) * (1 / gbpusd), rel=1e-12)
    assert fx.rate_with_route("JPY", "GBP", T("2024-02-10 12:00"))[1] == "inverse"


def test_a_missing_usd_leg_leaves_the_direct_cross_as_the_only_route() -> None:
    store = TimeframeStore({("GBPJPY", Timeframe.H4): h4("2024-02-01 01:00", 60, level=190.0)})
    fx, label = build_research_fx_source(store, quote_currencies=("JPY",))
    assert fx.lineage["_fallback"]["JPY"]["available"] is False
    assert sorted(fx.lineage["_fallback"]["JPY"]["missing_legs"]) == ["GBPUSD", "USDJPY"]
    assert label.endswith("; no via_usd fallback)")
    with pytest.raises(KeyError, match="GBPJPY has no observation"):
        fx.rate("JPY", "GBP", T("2024-01-15 12:00"))
