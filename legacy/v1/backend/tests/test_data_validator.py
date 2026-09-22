"""Tests for OHLCV data validator."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from fibokei.data.validator import validate_ohlcv


def _make_df(bars: list[dict], freq: str = "h") -> pd.DataFrame:
    """Helper to create a DataFrame with DatetimeIndex."""
    df = pd.DataFrame(bars)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp")
    return df


class TestValidateOHLCV:
    def test_valid_data_no_warnings(self):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = [
            {
                "timestamp": base + timedelta(hours=i),
                "open": 1.10 + i * 0.001,
                "high": 1.11 + i * 0.001,
                "low": 1.09 + i * 0.001,
                "close": 1.105 + i * 0.001,
                "volume": 1000,
            }
            for i in range(10)
        ]
        df = _make_df(bars)
        warnings = validate_ohlcv(df)
        assert warnings == []

    def test_detects_high_less_than_low(self):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = [
            {"timestamp": base, "open": 1.10, "high": 1.08, "low": 1.09, "close": 1.10, "volume": 100},
        ]
        df = _make_df(bars)
        warnings = validate_ohlcv(df)
        assert any("high < low" in w for w in warnings)

    def test_detects_duplicate_timestamps(self):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = [
            {"timestamp": base, "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 100},
            {"timestamp": base, "open": 1.11, "high": 1.13, "low": 1.10, "close": 1.12, "volume": 100},
        ]
        df = _make_df(bars)
        warnings = validate_ohlcv(df)
        assert any("duplicate" in w.lower() for w in warnings)

    def test_detects_out_of_order_timestamps(self):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = [
            {"timestamp": base + timedelta(hours=2), "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 100},
            {"timestamp": base, "open": 1.11, "high": 1.13, "low": 1.10, "close": 1.12, "volume": 100},
        ]
        df = _make_df(bars)
        warnings = validate_ohlcv(df)
        assert any("ascending" in w.lower() or "order" in w.lower() for w in warnings)

    def test_detects_negative_prices(self):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = [
            {"timestamp": base, "open": -1.10, "high": 1.12, "low": -1.10, "close": 1.11, "volume": 100},
        ]
        df = _make_df(bars)
        warnings = validate_ohlcv(df)
        assert any("negative" in w.lower() for w in warnings)

    def test_detects_suspicious_gaps(self):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = [
            {"timestamp": base + timedelta(hours=i), "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 100}
            for i in range(5)
        ]
        # Add a bar with a huge gap
        bars.append({
            "timestamp": base + timedelta(hours=100),
            "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 100,
        })
        df = _make_df(bars)
        warnings = validate_ohlcv(df)
        assert any("gap" in w.lower() for w in warnings)

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        warnings = validate_ohlcv(df)
        assert any("empty" in w.lower() for w in warnings)

    def test_validates_sample_fixture(self, sample_eurusd_h1_path):
        from fibokei.core.models import Timeframe
        from fibokei.data.loader import load_ohlcv_csv

        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        warnings = validate_ohlcv(df)
        # Sample data should have gaps (weekends skipped) but no fatal issues
        fatal = [w for w in warnings if "high < low" in w or "negative" in w or "duplicate" in w]
        assert fatal == [], f"Fatal validation issues in sample data: {fatal}"
