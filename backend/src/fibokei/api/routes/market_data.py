"""Market data endpoints."""

import logging
import math
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.schemas.charts import (
    CandleBar,
    IchimokuSeries,
    MarketDataResponse,
)
from fibokei.core.instruments import get_instrument
from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical_cached
from fibokei.indicators.ichimoku import IchimokuCloud

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-data"])

MAX_BARS = 10_000
DEFAULT_BARS = 2_000


def _nan_to_none(val: float) -> float | None:
    """Convert NaN/inf to None for JSON serialization."""
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


@router.get("/market-data/{instrument}/{timeframe}", response_model=MarketDataResponse)
def get_market_data(
    instrument: str,
    timeframe: str,
    user: TokenData = Depends(get_current_user),
    limit: int = DEFAULT_BARS,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
):
    """Return OHLCV candles with precomputed Ichimoku Cloud data."""
    instrument = instrument.replace("_", "").replace("/", "").upper()

    try:
        get_instrument(instrument)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown instrument: {instrument}")

    try:
        tf_enum = Timeframe(timeframe.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    # Enforce server-side cap
    limit = min(max(limit, 1), MAX_BARS)

    # Load data via cached canonical loader
    try:
        df, source = load_canonical_cached(instrument, tf_enum.value)
    except Exception as e:
        logger.error("Failed to load data for %s/%s: %s", instrument, tf_enum.value, e)
        raise HTTPException(
            status_code=500, detail=f"Failed to load data for {instrument}/{tf_enum.value}: {e}"
        )

    if df is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data file for {instrument}/{tf_enum.value}",
        )

    # Reset index so timestamp is a column
    df = df.reset_index()

    total_bars = len(df)

    # Apply date range filtering
    if from_dt is not None:
        df = df[df["timestamp"] >= pd.Timestamp(from_dt, tz="UTC")]
    if to_dt is not None:
        df = df[df["timestamp"] <= pd.Timestamp(to_dt, tz="UTC")]

    # Apply limit (last N bars)
    if len(df) > limit:
        df = df.tail(limit)

    if "volume" not in df.columns:
        df["volume"] = 0.0

    # Vectorized candle serialization (replaces iterrows)
    candles = [
        CandleBar(
            timestamp=int(ts.timestamp() * 1000),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row.get("volume", 0.0),
        )
        for ts, row in zip(df["timestamp"], df.to_dict("records"))
    ]

    # Compute Ichimoku — graceful degradation
    ichimoku = []
    try:
        ich_df = df[["timestamp", "open", "high", "low", "close"]].copy()
        ich_df = ich_df.set_index("timestamp")
        ich = IchimokuCloud()
        ich.compute(ich_df)
        ich_df = ich_df.reset_index()

        ichimoku = [
            IchimokuSeries(
                timestamp=int(row["timestamp"].timestamp() * 1000),
                tenkan=_nan_to_none(row.get("tenkan_sen", float("nan"))),
                kijun=_nan_to_none(row.get("kijun_sen", float("nan"))),
                senkou_a=_nan_to_none(row.get("senkou_span_a", float("nan"))),
                senkou_b=_nan_to_none(row.get("senkou_span_b", float("nan"))),
                chikou=_nan_to_none(row.get("chikou_span", float("nan"))),
            )
            for row in ich_df.to_dict("records")
        ]
    except Exception as e:
        logger.error("Ichimoku computation failed for %s/%s: %s", instrument, tf_enum.value, e)

    # Response metadata
    from_date = str(df["timestamp"].iloc[0]) if len(df) > 0 else None
    to_date = str(df["timestamp"].iloc[-1]) if len(df) > 0 else None

    return MarketDataResponse(
        instrument=instrument,
        timeframe=tf_enum.value,
        candles=candles,
        ichimoku=ichimoku,
        total_bars=total_bars,
        from_date=from_date,
        to_date=to_date,
        source=source,
    )


@router.post("/market-data/refresh", response_model=dict)
def refresh_market_data(
    user: TokenData = Depends(get_current_user),
):
    """Trigger a refresh of market data for all instruments."""
    from fibokei.data.ingestion import refresh_all
    results = refresh_all(timeframe="H1")
    return {"refreshed": results, "total": sum(1 for v in results.values() if v > 0)}
