"""Resampling: aggregation correctness, transitivity, DST, no invented bars."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.data.resample import (
    ResampleError,
    assert_nested,
    resample,
    resample_chain,
    session_bar_count,
)
from fiboki.data.schema import PriceBasis, validate_frame_shape
from tests.data_fixtures import make_bars


def m1(periods: int = 2880, start: str = "2026-01-05 00:00", **kw) -> pd.DataFrame:
    return make_bars(timeframe=Timeframe.M1, periods=periods, start=start, **kw)


# ------------------------------------------------------ transitivity


def test_m1_to_h1_to_h4_equals_m1_to_h4_directly():
    """The property that makes two-step and one-step aggregation interchangeable."""
    bars = m1(periods=2880)  # two full days
    two_step = resample_chain(bars, [Timeframe.H1, Timeframe.H4])
    one_step = resample(bars, Timeframe.H4)
    pd.testing.assert_frame_equal(two_step, one_step)


def test_transitivity_survives_missing_bars():
    """Holes must not make the two paths disagree."""
    bars = m1(periods=2880)
    holed = bars.drop(index=bars.index[137:499])
    two_step = resample_chain(holed, [Timeframe.H1, Timeframe.H4])
    one_step = resample(holed, Timeframe.H4)
    pd.testing.assert_frame_equal(two_step, one_step)


def test_transitivity_m1_m5_m15_m30_h1():
    bars = m1(periods=1440)
    chained = resample_chain(
        bars, [Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1]
    )
    direct = resample(bars, Timeframe.H1)
    pd.testing.assert_frame_equal(chained, direct)


# ------------------------------------------------------- aggregation


def test_ohlc_aggregation_is_correct():
    bars = m1(periods=180)
    h1 = resample(bars, Timeframe.H1)
    first_hour = bars.iloc[:60]
    row = h1.iloc[0]
    assert row["open"] == first_hour["open"].iloc[0]
    assert row["close"] == first_hour["close"].iloc[-1]
    assert row["high"] == first_hour["high"].max()
    assert row["low"] == first_hour["low"].min()
    assert row["volume"] == first_hour["volume"].sum()


def test_identity_columns_are_carried_and_timeframe_updated():
    bars = m1(periods=240, price_basis=PriceBasis.BID, instrument="XAUUSD")
    h1 = resample(bars, Timeframe.H1)
    assert set(h1["timeframe"]) == {"H1"}
    assert set(h1["price_basis"]) == {"bid"}
    assert set(h1["instrument"]) == {"XAUUSD"}
    validate_frame_shape(h1)


def test_bid_and_ask_columns_are_aggregated_independently():
    bars = m1(periods=120, price_basis=PriceBasis.SYNTHETIC_MID)
    bars["bid_open"] = bars["open"] - 0.0001
    bars["bid_high"] = bars["high"] - 0.0001
    bars["bid_low"] = bars["low"] - 0.0001
    bars["bid_close"] = bars["close"] - 0.0001
    bars["ask_open"] = bars["open"] + 0.0001
    bars["ask_high"] = bars["high"] + 0.0001
    bars["ask_low"] = bars["low"] + 0.0001
    bars["ask_close"] = bars["close"] + 0.0001
    h1 = resample(bars, Timeframe.H1)
    assert h1["bid_high"].iloc[0] == pytest.approx(bars["bid_high"].iloc[:60].max())
    assert h1["ask_low"].iloc[0] == pytest.approx(bars["ask_low"].iloc[:60].min())
    assert (h1["bid_close"] < h1["ask_close"]).all()


def test_absent_volume_marker_is_not_summed_into_nonsense():
    """-1 means 'the source had no volume'. Sixty of them are not -60."""
    bars = m1(periods=120)
    bars["volume"] = -1
    h1 = resample(bars, Timeframe.H1)
    assert (h1["volume"] == -1).all()


def test_partially_absent_volume_sums_only_what_was_observed():
    bars = m1(periods=120)
    bars["volume"] = 10
    bars.iloc[0:30, bars.columns.get_loc("volume")] = -1
    h1 = resample(bars, Timeframe.H1)
    assert h1["volume"].iloc[0] == 300  # 30 real bars at 10


# ------------------------------------------------------- empty buckets


def test_empty_buckets_are_dropped_never_filled():
    """A missing hour stays missing. Forward-filling it is the silent repair."""
    bars = m1(periods=2880)
    drop = (bars.index >= pd.Timestamp("2026-01-05 03:00", tz="UTC")) & (
        bars.index < pd.Timestamp("2026-01-05 04:00", tz="UTC")
    )
    holed = bars[~drop]
    h1 = resample(holed, Timeframe.H1)
    assert pd.Timestamp("2026-01-05 03:00", tz="UTC") not in h1.index
    assert not h1.isna().any().any()


# ------------------------------------------------------------- origin


def test_origin_is_epoch_anchored_not_first_row_anchored():
    """Two ranges of the same series must land on the same H4 grid."""
    bars = m1(periods=2880)
    a = resample(bars, Timeframe.H4)
    b = resample(bars.iloc[137:], Timeframe.H4)
    shared = a.index.intersection(b.index)
    assert len(shared) >= 3
    # Every shared bucket starts on the epoch 4h grid.
    assert all(ts.hour % 4 == 0 and ts.minute == 0 for ts in a.index)
    assert all(ts.hour % 4 == 0 and ts.minute == 0 for ts in b.index)


def test_session_anchored_daily_bars_respect_dst():
    """A 17:00 New York day boundary is 21:00 UTC in summer, 22:00 in winter."""
    # Straddle the US autumn DST change (2026-11-01).
    idx = pd.date_range("2026-10-28 00:00", "2026-11-05 00:00", freq="h", tz="UTC")
    base = make_bars(timeframe=Timeframe.H1, periods=len(idx))
    bars = base.set_axis(idx.rename("timestamp"), axis=0)
    daily = resample(
        bars, Timeframe.D1, anchor_tz="America/New_York", anchor_time="17:00"
    )
    ny = daily.index.tz_convert("America/New_York")
    assert set(ny.hour) == {17}, f"day boundary drifted: {sorted(set(ny.hour))}"
    utc_hours = set(daily.index.hour)
    assert utc_hours == {21, 22}, (
        "a session-anchored daily bar must move in UTC across a DST change, "
        f"got {sorted(utc_hours)}"
    )


def test_utc_anchored_daily_bars_do_not_move():
    idx = pd.date_range("2026-10-28 00:00", "2026-11-05 00:00", freq="h", tz="UTC")
    base = make_bars(timeframe=Timeframe.H1, periods=len(idx))
    bars = base.set_axis(idx.rename("timestamp"), axis=0)
    daily = resample(bars, Timeframe.D1)
    assert set(daily.index.hour) == {0}


def test_anchor_time_without_anchor_tz_is_refused():
    with pytest.raises(ResampleError, match="needs a locale"):
        resample(m1(120), Timeframe.H1, anchor_time="17:00")


# ------------------------------------------------------------ refusals


def test_upsampling_is_refused():
    bars = make_bars(timeframe=Timeframe.H1, periods=100)
    with pytest.raises(ResampleError, match="does not manufacture"):
        resample(bars, Timeframe.M1)


class _NinetyMinutes:
    """Not a real Timeframe; used to exercise the non-nesting guard.

    Every pair in the real Timeframe enum happens to nest, so the guard can only
    be tested with a synthetic timeframe. It still matters: the moment someone
    adds an H2 or an H6, a non-nesting resample becomes reachable.
    """

    minutes = 90
    value = "M90"


def test_non_nesting_timeframes_are_refused():
    with pytest.raises(ResampleError, match="does not nest"):
        assert_nested(_NinetyMinutes(), Timeframe.H4)  # type: ignore[arg-type]


def test_every_real_timeframe_pair_nests():
    order = [
        Timeframe.M1, Timeframe.M5, Timeframe.M15,
        Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1,
    ]
    for i, src in enumerate(order):
        for tgt in order[i:]:
            assert_nested(src, tgt)


def test_same_timeframe_is_a_copy():
    bars = make_bars(timeframe=Timeframe.H1, periods=50)
    out = resample(bars, Timeframe.H1)
    pd.testing.assert_frame_equal(out, bars)
    assert out is not bars


def test_empty_frame_is_refused():
    bars = make_bars(periods=10)
    with pytest.raises(ResampleError, match="empty"):
        resample(bars.iloc[:0], Timeframe.H4)


def test_d1_nests_m1_h1_and_h4():
    assert_nested(Timeframe.M1, Timeframe.D1)
    assert_nested(Timeframe.H1, Timeframe.D1)
    assert_nested(Timeframe.H4, Timeframe.D1)


def test_session_bar_count_reports_thin_buckets():
    bars = m1(periods=180)
    counts = session_bar_count(bars, Timeframe.H1)
    assert counts.iloc[0] == 60
    assert int(counts.sum()) == 180


def test_high_low_bracket_survives_aggregation():
    bars = m1(periods=1440)
    for tf in (Timeframe.M5, Timeframe.M30, Timeframe.H1, Timeframe.H4):
        out = resample(bars, tf)
        assert (out["high"] >= out[["open", "close"]].max(axis=1) - 1e-12).all()
        assert (out["low"] <= out[["open", "close"]].min(axis=1) + 1e-12).all()
        assert (out["high"] >= out["low"]).all()


def test_aggregated_extremes_match_the_underlying_bars():
    bars = m1(periods=1440)
    h4 = resample(bars, Timeframe.H4)
    for ts, row in h4.iterrows():
        window = bars[(bars.index >= ts) & (bars.index < ts + pd.Timedelta(hours=4))]
        assert row["high"] == pytest.approx(window["high"].max())
        assert row["low"] == pytest.approx(window["low"].min())
        assert np.isclose(row["open"], window["open"].iloc[0])
        assert np.isclose(row["close"], window["close"].iloc[-1])
