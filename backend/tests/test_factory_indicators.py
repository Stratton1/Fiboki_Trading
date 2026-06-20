"""Sanity + no-look-ahead tests for the Strategy Factory Gen-1 indicators."""

import numpy as np
import pandas as pd
import pytest

from fibokei.indicators.channels import (
    BollingerBands,
    DonchianChannels,
    KeltnerChannels,
)
from fibokei.indicators.oscillators import CCI, MACD, ROC, Stochastic
from fibokei.indicators.pivots import PivotPoints
from fibokei.indicators.registry import registry
from fibokei.indicators.trend import ADX, ParabolicSAR
from fibokei.indicators.volume import OBV, VWAP, VolumeMA


def _df(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    # Gentle uptrend + noise, valid OHLC, positive volume.
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.uniform(100, 1000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol}
    )


def test_macd_columns():
    out = MACD().compute(_df())
    for c in ("macd_line", "macd_signal", "macd_hist"):
        assert c in out.columns
    assert not np.isnan(out["macd_hist"].iloc[-1])
    # hist = line - signal
    assert abs(out["macd_hist"].iloc[-1]
               - (out["macd_line"].iloc[-1] - out["macd_signal"].iloc[-1])) < 1e-9


def test_stochastic_range():
    out = Stochastic().compute(_df())
    k = out["stoch_k"].dropna()
    assert (k >= -0.01).all() and (k <= 100.01).all()


def test_cci_and_roc():
    out = ROC(period=10).compute(CCI(period=20).compute(_df()))
    assert "cci_20" in out.columns and "roc_10" in out.columns
    assert not np.isnan(out["roc_10"].iloc[-1])


def test_bollinger_ordering():
    out = BollingerBands().compute(_df())
    assert out["bb_upper"].iloc[-1] >= out["bb_mid"].iloc[-1] >= out["bb_lower"].iloc[-1]


def test_donchian_and_keltner_ordering():
    d = DonchianChannels().compute(_df())
    assert d["donchian_upper"].iloc[-1] >= d["donchian_lower"].iloc[-1]
    k = KeltnerChannels().compute(_df())
    assert k["kc_upper"].iloc[-1] >= k["kc_mid"].iloc[-1] >= k["kc_lower"].iloc[-1]


def test_adx_range():
    out = ADX().compute(_df())
    adx = out["adx_14"].dropna()
    assert (adx >= -0.01).all() and (adx <= 100.01).all()
    assert "plus_di" in out.columns and "minus_di" in out.columns


def test_psar_trend_values():
    out = ParabolicSAR().compute(_df())
    assert set(out["psar_trend"].iloc[1:].unique()).issubset({-1, 1})
    assert not np.isnan(out["psar"].iloc[-1])


def test_volume_indicators():
    out = OBV().compute(VolumeMA().compute(VWAP().compute(_df())))
    for c in ("vwap_20", "vol_ma_20", "obv"):
        assert c in out.columns
    assert not np.isnan(out["vwap_20"].iloc[-1])


def test_vwap_degrades_without_volume():
    df = _df()
    df["volume"] = 0.0
    out = VWAP().compute(df)
    # Falls back to rolling typical-price mean instead of NaN/divide-by-zero.
    assert not np.isnan(out["vwap_20"].iloc[-1])


def test_pivots():
    out = PivotPoints().compute(_df())
    last = out.iloc[-1]
    assert last["pivot_r1"] >= last["pivot"] >= last["pivot_s1"]


def test_registry_has_new_indicators():
    names = registry.list_available()
    for n in ("macd_12_26_9", "stoch_14_3_3", "bb_20_2.0", "adx_14",
              "psar", "vwap_20", "obv", "pivot", "sma_20", "ema_20", "rsi_14"):
        assert n in names, f"{n} not registered"


@pytest.mark.parametrize("indicator,col", [
    (MACD(), "macd_line"),
    (BollingerBands(), "bb_mid"),
    (ParabolicSAR(), "psar"),
    (ADX(), "adx_14"),
])
def test_no_lookahead(indicator, col):
    """A value at index i must not change when future bars (>i) are mutated."""
    df = _df()
    base = indicator.compute(df.copy())[col].iloc[100]
    mutated = df.copy()
    mutated.loc[150:, ["open", "high", "low", "close"]] *= 1.5
    after = indicator.compute(mutated)[col].iloc[100]
    if not np.isnan(base):
        assert abs(base - after) < 1e-9
