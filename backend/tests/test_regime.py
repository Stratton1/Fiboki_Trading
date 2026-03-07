"""Tests for MarketRegime indicator."""

import numpy as np
import pandas as pd
import pytest

from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime


def _make_trending_bullish_df(n: int = 200) -> pd.DataFrame:
    """Create data with a clear uptrend: price well above cloud, TK cross up."""
    timestamps = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    # Strong uptrend with consistent higher highs
    base = 1.10 + np.linspace(0, 0.05, n)  # steady rise from 1.10 to 1.15
    noise = np.random.default_rng(42).normal(0, 0.0005, n)
    close = base + noise
    high = close + abs(np.random.default_rng(43).normal(0.001, 0.0003, n))
    low = close - abs(np.random.default_rng(44).normal(0.001, 0.0003, n))
    open_price = close + np.random.default_rng(45).normal(0, 0.0002, n)
    high = np.maximum(high, np.maximum(open_price, close))
    low = np.minimum(low, np.minimum(open_price, close))

    df = pd.DataFrame({
        "open": open_price, "high": high, "low": low, "close": close, "volume": 1000,
    }, index=timestamps)
    return df


def _make_consolidation_df(n: int = 200) -> pd.DataFrame:
    """Create data that oscillates in a tight range — cloud should envelope price."""
    timestamps = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    # Flat with small oscillations
    close = 1.10 + np.sin(np.linspace(0, 20, n)) * 0.001
    noise = np.random.default_rng(42).normal(0, 0.0003, n)
    close = close + noise
    high = close + 0.0010
    low = close - 0.0010
    open_price = close + np.random.default_rng(45).normal(0, 0.0002, n)
    high = np.maximum(high, np.maximum(open_price, close))
    low = np.minimum(low, np.minimum(open_price, close))

    df = pd.DataFrame({
        "open": open_price, "high": high, "low": low, "close": close, "volume": 1000,
    }, index=timestamps)
    return df


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add Ichimoku and ATR columns required by MarketRegime."""
    df = IchimokuCloud().compute(df)
    df = ATR().compute(df)
    return df


class TestMarketRegime:
    def test_name(self):
        mr = MarketRegime()
        assert mr.name == "market_regime"

    def test_output_column(self):
        df = _prepare_df(_make_trending_bullish_df())
        mr = MarketRegime()
        result = mr.compute(df)
        assert "regime" in result.columns
        assert len(result) == len(df)

    def test_trending_bullish_scenario(self):
        df = _prepare_df(_make_trending_bullish_df())
        mr = MarketRegime()
        result = mr.compute(df)
        # After full warmup, majority should be trending_bullish or volatility_expansion
        valid = result.iloc[mr.warmup_period:]
        regimes = valid["regime"].value_counts()
        bullish_count = regimes.get("trending_bullish", 0)
        # In a strong uptrend, trending_bullish should be the most common regime
        assert bullish_count > 0, f"Expected trending_bullish but got: {regimes.to_dict()}"

    def test_consolidation_scenario(self):
        df = _prepare_df(_make_consolidation_df())
        mr = MarketRegime()
        result = mr.compute(df)
        valid = result.iloc[mr.warmup_period:]
        regimes = valid["regime"].value_counts()
        # In consolidation, should see consolidation regime
        consolidation_count = regimes.get("consolidation", 0)
        assert consolidation_count > 0, (
            f"Expected consolidation but got: {regimes.to_dict()}"
        )

    def test_no_trade_before_warmup(self):
        df = _prepare_df(_make_trending_bullish_df())
        mr = MarketRegime()
        result = mr.compute(df)
        # Before warmup, all should be "no_trade"
        early = result["regime"].iloc[:mr.warmup_period]
        assert (early == "no_trade").all()

    def test_all_regimes_are_valid_labels(self):
        valid_labels = {
            "trending_bullish", "trending_bearish",
            "pullback_bullish", "pullback_bearish",
            "consolidation", "breakout_candidate",
            "volatility_expansion", "reversal_candidate",
            "no_trade",
        }
        df = _prepare_df(_make_trending_bullish_df())
        result = MarketRegime().compute(df)
        unique_regimes = set(result["regime"].unique())
        assert unique_regimes.issubset(valid_labels), (
            f"Unexpected regimes: {unique_regimes - valid_labels}"
        )
