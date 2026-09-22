"""Resample M1 candles into higher timeframes with provenance tracking.

All derived timeframes are built from M1 data using standard OHLCV
aggregation rules.  The output preserves the fact that it was resampled
so downstream consumers can distinguish directly-sourced from derived data.
"""

from __future__ import annotations

import pandas as pd

# Pandas offset aliases for each Fiboki timeframe
_TF_OFFSETS: dict[str, str] = {
    "M1": "1min",
    "M2": "2min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
}


def resample_ohlcv(
    df: pd.DataFrame,
    target_timeframe: str,
) -> pd.DataFrame:
    """Resample a canonical OHLCV DataFrame to a coarser timeframe.

    Parameters
    ----------
    df : pd.DataFrame
        Must have a DatetimeIndex named ``timestamp`` and columns
        ``open``, ``high``, ``low``, ``close``, ``volume``.
    target_timeframe : str
        One of M1, M2, M5, M15, M30, H1, H4.

    Returns
    -------
    pd.DataFrame
        Resampled DataFrame in canonical format.

    Raises
    ------
    ValueError
        If the target timeframe is not recognised.
    """
    offset = _TF_OFFSETS.get(target_timeframe.upper())
    if offset is None:
        raise ValueError(
            f"Unsupported target timeframe {target_timeframe!r}. "
            f"Supported: {sorted(_TF_OFFSETS)}"
        )

    resampled = (
        df.resample(offset)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna(subset=["open"])  # drop periods with no data
    )

    resampled.index.name = "timestamp"
    return resampled


def derive_all_timeframes(
    m1_df: pd.DataFrame,
    timeframes: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Derive multiple timeframes from M1 data.

    Parameters
    ----------
    m1_df : pd.DataFrame
        M1 canonical OHLCV DataFrame.
    timeframes : list[str] | None
        Timeframes to derive.  Defaults to M5, M15, M30, H1, H4.

    Returns
    -------
    dict[str, pd.DataFrame]
        Mapping from timeframe label to resampled DataFrame.
        Always includes ``M1`` pointing to the original data.
    """
    if timeframes is None:
        timeframes = ["M5", "M15", "M30", "H1", "H4"]

    result: dict[str, pd.DataFrame] = {"M1": m1_df}
    for tf in timeframes:
        tf_upper = tf.upper()
        if tf_upper == "M1":
            continue
        result[tf_upper] = resample_ohlcv(m1_df, tf_upper)

    return result
