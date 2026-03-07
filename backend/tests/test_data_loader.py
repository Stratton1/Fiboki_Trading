"""Tests for OHLCV data loader."""

import pandas as pd
import pytest

from fibokei.core.models import Timeframe
from fibokei.data.loader import load_ohlcv_csv


class TestLoadOHLCV:
    def test_load_sample_fixture(self, sample_eurusd_h1_path):
        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        assert len(df) >= 500

    def test_correct_columns(self, sample_eurusd_h1_path):
        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        assert "open" in df.columns
        assert "high" in df.columns
        assert "low" in df.columns
        assert "close" in df.columns
        assert "volume" in df.columns
        assert "instrument" in df.columns
        assert "timeframe" in df.columns

    def test_datetime_index(self, sample_eurusd_h1_path):
        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "timestamp"

    def test_sorted_ascending(self, sample_eurusd_h1_path):
        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        assert df.index.is_monotonic_increasing

    def test_values_in_plausible_range(self, sample_eurusd_h1_path):
        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        # EURUSD typically trades between 0.90 and 1.30
        assert df["close"].min() > 0.90
        assert df["close"].max() < 1.30

    def test_instrument_and_timeframe_set(self, sample_eurusd_h1_path):
        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        assert (df["instrument"] == "EURUSD").all()
        assert (df["timeframe"] == "H1").all()

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_ohlcv_csv("/nonexistent/path.csv", "EURUSD", Timeframe.H1)

    def test_numeric_types(self, sample_eurusd_h1_path):
        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        for col in ["open", "high", "low", "close", "volume"]:
            assert pd.api.types.is_numeric_dtype(df[col])
