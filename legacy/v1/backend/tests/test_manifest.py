"""Tests for data manifest generation and loading."""

import json
import tempfile
from pathlib import Path

import pandas as pd

from fibokei.data.manifest import generate_manifest, load_manifest


def _create_test_parquet(path: Path, bars: int = 100):
    """Create a minimal parquet file for testing."""
    import numpy as np

    dates = pd.date_range("2024-01-01", periods=bars, freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": np.random.uniform(1.0, 1.1, bars),
            "high": np.random.uniform(1.1, 1.2, bars),
            "low": np.random.uniform(0.9, 1.0, bars),
            "close": np.random.uniform(1.0, 1.1, bars),
            "volume": np.zeros(bars),
        },
        index=pd.DatetimeIndex(dates, name="timestamp"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def test_generate_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical"
        _create_test_parquet(
            canonical / "histdata" / "eurusd" / "eurusd_h1.parquet", bars=200
        )
        _create_test_parquet(
            canonical / "histdata" / "gbpusd" / "gbpusd_m15.parquet", bars=500
        )

        manifest = generate_manifest(canonical)

        assert manifest["version"] == 1
        assert len(manifest["datasets"]) == 2

        eurusd = next(d for d in manifest["datasets"] if d["symbol"] == "EURUSD")
        assert eurusd["provider"] == "histdata"
        assert eurusd["timeframe"] == "H1"
        assert eurusd["bars"] == 200
        assert eurusd["path"] == "histdata/eurusd/eurusd_h1.parquet"
        assert "checksum" in eurusd
        assert "size_bytes" in eurusd
        assert "from_date" in eurusd
        assert "to_date" in eurusd


def test_generate_manifest_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical"
        _create_test_parquet(
            canonical / "histdata" / "eurusd" / "eurusd_h1.parquet"
        )

        manifest = generate_manifest(canonical)
        manifest_path = canonical / "manifest.json"
        assert manifest_path.exists()

        loaded = json.loads(manifest_path.read_text())
        assert loaded["version"] == 1
        assert len(loaded["datasets"]) == 1


def test_load_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical"
        _create_test_parquet(
            canonical / "histdata" / "eurusd" / "eurusd_h1.parquet"
        )
        generate_manifest(canonical)

        manifest = load_manifest(canonical)
        assert manifest is not None
        assert len(manifest["datasets"]) == 1


def test_load_manifest_missing():
    with tempfile.TemporaryDirectory() as tmp:
        assert load_manifest(Path(tmp) / "nonexistent") is None
