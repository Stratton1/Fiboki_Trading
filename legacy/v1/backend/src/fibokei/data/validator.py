"""OHLCV data quality validation."""

import pandas as pd


def validate_ohlcv(df: pd.DataFrame) -> list[str]:
    """Validate OHLCV DataFrame for quality issues.

    Returns list of warning strings. Empty list means data is valid.
    """
    warnings: list[str] = []

    if df.empty:
        warnings.append("DataFrame is empty")
        return warnings

    # Check required columns
    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        warnings.append(f"Missing columns: {missing}")
        return warnings

    # Missing/null values in OHLC
    for col in required:
        null_count = df[col].isna().sum()
        if null_count > 0:
            warnings.append(f"{null_count} null values in '{col}'")

    # high < low violations
    violations = df[df["high"] < df["low"]]
    if len(violations) > 0:
        warnings.append(f"{len(violations)} bars where high < low")

    # open/close outside [low, high]
    valid = df.dropna(subset=required)
    out_of_range = valid[
        (valid["open"] < valid["low"])
        | (valid["open"] > valid["high"])
        | (valid["close"] < valid["low"])
        | (valid["close"] > valid["high"])
    ]
    if len(out_of_range) > 0:
        warnings.append(f"{len(out_of_range)} bars where open/close outside [low, high]")

    # Negative prices
    for col in required:
        neg = (df[col] < 0).sum()
        if neg > 0:
            warnings.append(f"{neg} negative values in '{col}'")

    # Timestamp checks (only if DatetimeIndex)
    if isinstance(df.index, pd.DatetimeIndex):
        # Duplicate timestamps
        dupes = df.index.duplicated().sum()
        if dupes > 0:
            warnings.append(f"{dupes} duplicate timestamps")

        # Out-of-order timestamps
        if not df.index.is_monotonic_increasing:
            warnings.append("Timestamps are not in ascending order")

        # Suspicious gaps
        if len(df) > 1:
            gaps = df.index.to_series().diff().dropna()
            median_gap = gaps.median()
            if median_gap.total_seconds() > 0:
                large_gaps = gaps[gaps > median_gap * 3]
                if len(large_gaps) > 0:
                    warnings.append(
                        f"{len(large_gaps)} suspicious gaps (>3x median interval)"
                    )

    return warnings
