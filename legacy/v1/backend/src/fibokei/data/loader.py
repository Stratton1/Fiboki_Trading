"""OHLCV data loading from CSV files."""

from pathlib import Path

import pandas as pd

from fibokei.core.models import Timeframe


def load_ohlcv_csv(
    path: str | Path,
    instrument: str,
    timeframe: Timeframe,
) -> pd.DataFrame:
    """Load OHLCV data from CSV file.

    Supports common CSV formats: with/without header, various date formats,
    comma/semicolon delimiters.

    Returns DataFrame with DatetimeIndex and columns:
    open, high, low, close, volume, instrument, timeframe
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    # Try comma first, then semicolon
    for sep in [",", ";"]:
        try:
            df = pd.read_csv(path, sep=sep)
            if len(df.columns) >= 5:
                break
        except Exception:
            continue
    else:
        raise ValueError(f"Could not parse CSV file: {path}")

    # Normalize column names
    df.columns = df.columns.str.strip().str.lower()

    # Map common column name variants
    col_map = {
        "date": "timestamp",
        "datetime": "timestamp",
        "time": "timestamp",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "vol": "volume",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # If no header was detected (numeric column names), assign standard names
    if "timestamp" not in df.columns and "open" not in df.columns:
        if len(df.columns) == 6:
            df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
        elif len(df.columns) == 5:
            df.columns = ["timestamp", "open", "high", "low", "close"]
            df["volume"] = 0.0

    required = ["timestamp", "open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    if "volume" not in df.columns:
        df["volume"] = 0.0

    # Parse timestamps
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Sort by timestamp
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Set DatetimeIndex
    df = df.set_index("timestamp")

    # Keep only OHLCV columns + metadata
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df["instrument"] = instrument
    df["timeframe"] = timeframe.value

    # Ensure numeric types
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows where OHLC coerced to NaN (unparseable prices)
    ohlc_cols = ["open", "high", "low", "close"]
    nan_count = df[ohlc_cols].isna().any(axis=1).sum()
    if nan_count > 0:
        import warnings

        warnings.warn(
            f"Dropped {nan_count} rows with unparseable OHLC values from {path}",
            stacklevel=2,
        )
        df = df.dropna(subset=ohlc_cols)

    return df
