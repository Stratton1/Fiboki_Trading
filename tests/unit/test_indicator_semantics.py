"""Indicator behaviour that is not about causality: the V1 traps.

Each test here corresponds to a specific defect that shipped in V1 and that a
purely causal implementation would still have.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv, zero_volume_ohlcv

from fiboki.indicators import (
    OBV,
    VWAP,
    Donchian,
    Fibonacci,
    Ichimoku,
    SwingDetector,
    VolumeUnavailableError,
    available,
    causality_suite,
    chikou_span_display,
    create,
    projected_cloud_display,
)
from fiboki.indicators.registry import UnknownIndicatorError


@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    return synthetic_ohlcv(300)


@pytest.fixture(scope="module")
def flat_volume() -> pd.DataFrame:
    return zero_volume_ohlcv(300)


# ------------------------------------------------- volume: no silent fallback


def test_obv_refuses_zero_volume(flat_volume: pd.DataFrame) -> None:
    """V1 computed OBV on FX volume that was identically zero; the strategy
    built on it could never trade and nothing said why."""
    with pytest.raises(VolumeUnavailableError, match="identically zero"):
        OBV().compute(flat_volume)


def test_obv_marks_instead_of_raising_when_asked(flat_volume: pd.DataFrame) -> None:
    out = OBV(on_unavailable="mark").compute(flat_volume)
    assert out["obv_unavailable"].eq(1.0).all()
    assert out["obv"].isna().all()


def test_obv_works_when_volume_is_real(ohlcv: pd.DataFrame) -> None:
    out = OBV().compute(ohlcv)
    assert out["obv_unavailable"].eq(0.0).all()
    assert out["obv"].iloc[1:].notna().all()
    expected = float(np.sign(ohlcv["close"].iloc[1] - ohlcv["close"].iloc[0]) * ohlcv["volume"].iloc[1])
    assert out["obv"].iloc[1] == pytest.approx(expected)


def test_vwap_refuses_a_frame_with_no_volume_column() -> None:
    df = synthetic_ohlcv(100, with_volume=False)
    with pytest.raises(ValueError, match="volume"):
        VWAP(period=5).compute(df)


def test_vwap_is_vwap_and_not_a_typical_price_mean(ohlcv: pd.DataFrame) -> None:
    """V1's VWAP fell back to a rolling mean of typical price and kept the name."""
    ind = VWAP(period=5)
    out = ind.compute(ohlcv)
    tp = (ohlcv["high"] + ohlcv["low"] + ohlcv["close"]) / 3.0
    vol = ohlcv["volume"]
    i = 50
    manual = float((tp.iloc[i - 4 : i + 1] * vol.iloc[i - 4 : i + 1]).sum() / vol.iloc[i - 4 : i + 1].sum())
    assert out[ind.name].iloc[i] == pytest.approx(manual, abs=1e-12)
    unweighted = float(tp.iloc[i - 4 : i + 1].mean())
    assert out[ind.name].iloc[i] != pytest.approx(unweighted, abs=1e-9)


def test_vwap_marks_zero_volume_windows(flat_volume: pd.DataFrame) -> None:
    ind = VWAP(period=5, on_unavailable="mark")
    out = ind.compute(flat_volume)
    assert out[f"{ind.name}_unavailable"].eq(1.0).all()
    assert out[ind.name].isna().all()


# ------------------------------------------------------ ichimoku quarantine


def test_chikou_is_absent_from_every_computed_column(ohlcv: pd.DataFrame) -> None:
    out = Ichimoku().compute(ohlcv)
    leaked = [c for c in out.columns if c.endswith("chikou_span")]
    assert leaked == []


def test_chikou_display_helper_is_explicitly_non_causal(ohlcv: pd.DataFrame) -> None:
    display = chikou_span_display(ohlcv, 26)
    assert display.iloc[0] == pytest.approx(ohlcv["close"].iloc[26])
    assert display.iloc[-26:].isna().all()
    assert "display" in display.name


def test_projected_cloud_lands_on_a_different_index(ohlcv: pd.DataFrame) -> None:
    """It cannot be joined onto a strategy frame by accident."""
    projected = projected_cloud_display(ohlcv, 3, 5, 7, senkou_shift=3)
    assert list(projected.columns) == ["bars_ahead", "senkou_a", "senkou_b"]
    assert list(projected["bars_ahead"]) == [1, 2, 3]
    assert not isinstance(projected.index, pd.DatetimeIndex)


def test_chikou_confirmation_is_a_backward_comparison(ohlcv: pd.DataFrame) -> None:
    ind = Ichimoku(chikou_shift=26)
    out = ind.compute(ohlcv)
    col = f"{ind.name}_chikou_above_price"
    i = 100
    expected = float(ohlcv["close"].iloc[i] > ohlcv["close"].iloc[i - 26])
    assert out[col].iloc[i] == expected
    assert out[col].iloc[:26].isna().all()


# --------------------------------------------------------------- structure


def test_swing_confirmation_never_appears_on_the_swing_bar(ohlcv: pd.DataFrame) -> None:
    lb = 4
    out = SwingDetector(lookback=lb).compute(ohlcv)
    confirmations = out["swing_high_confirmed"].notna().to_numpy().nonzero()[0]
    assert len(confirmations) > 3
    for i in confirmations:
        swing_bar = int(out["last_swing_high_bar"].iloc[i])
        assert swing_bar == i - lb
        assert out["swing_high_confirmed"].iloc[i] == pytest.approx(
            ohlcv["high"].iloc[swing_bar]
        )


def test_swings_exist_right_up_to_the_last_bar(ohlcv: pd.DataFrame) -> None:
    """V1's centred window meant the final `lookback` bars could never carry a
    swing, so the live bot saw a different world from the backtest."""
    out = SwingDetector(lookback=4).compute(ohlcv)
    assert out["last_swing_high"].iloc[-1] == out["last_swing_high"].iloc[-1]  # not NaN
    assert out["last_swing_low"].notna().iloc[-1]


def test_fibonacci_emits_levels_even_when_the_low_is_above_the_high() -> None:
    """The exact V1 silent-nothing case, now handled by time-ordered anchoring."""
    ind = Fibonacci(swing_lookback=2, retracements=(0.5,), extensions=(1.618,))
    out = ind.compute(synthetic_ohlcv(300))
    p = ind.name
    crossed = out[
        out[f"{p}_start"].notna()
        & out[f"{p}_end"].notna()
        & (out[f"{p}_dir"] == -1.0)
    ]
    assert len(crossed) > 0
    # In a down leg the anchor START (a swing high) is above the anchor END.
    assert (crossed[f"{p}_start"] > crossed[f"{p}_end"]).all()
    levels = crossed[f"{p}_ret_0500"]
    assert levels.notna().all()


def test_fibonacci_up_and_down_legs_both_occur(ohlcv: pd.DataFrame) -> None:
    ind = Fibonacci(swing_lookback=3)
    out = ind.compute(ohlcv)
    dirs = out[f"{ind.name}_dir"].dropna().unique()
    assert 1.0 in dirs and -1.0 in dirs


def test_donchian_prior_channel_excludes_the_current_bar(ohlcv: pd.DataFrame) -> None:
    """A breakout rule on the inclusive channel can never fire."""
    ind = Donchian(period=10)
    out = ind.compute(ohlcv)
    assert (out["close"] > out[f"{ind.name}_upper"]).sum() == 0
    assert (out["close"] > out[f"{ind.name}_upper_prior"]).sum() > 0


# ---------------------------------------------------------------- registry


def test_every_registered_indicator_declares_a_positive_warmup() -> None:
    for indicator in causality_suite():
        assert indicator.warmup_period >= 1, indicator.name
        assert indicator.output_columns, indicator.name
        assert indicator.params() is not None


def test_registry_rejects_unknown_keys_and_bad_params() -> None:
    assert len(available()) == 20
    with pytest.raises(UnknownIndicatorError):
        create("does_not_exist")
    with pytest.raises(TypeError, match="bad parameters"):
        create("sma", {"nope": 1})
    with pytest.raises(ValueError, match="period must be"):
        create("sma", {"period": 0})


def test_indicator_names_are_unique_across_the_suite() -> None:
    names = [i.name for i in causality_suite()]
    assert len(names) == len(set(names))


def test_declared_outputs_must_match_what_compute_produces(ohlcv: pd.DataFrame) -> None:
    for indicator in causality_suite():
        if indicator.requires_volume and "volume" not in ohlcv.columns:
            continue
        out = indicator.compute(ohlcv)
        assert set(indicator.output_columns).issubset(out.columns), indicator.name
