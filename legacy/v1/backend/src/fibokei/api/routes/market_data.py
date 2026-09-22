"""Market data endpoints."""

import logging
import math
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

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


def _df_to_response(
    df: pd.DataFrame,
    instrument: str,
    timeframe: str,
    source: str,
    mode: str,
    limit: int,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> MarketDataResponse:
    """Convert a DataFrame to MarketDataResponse (shared by historical and live)."""
    # Reset index so timestamp is a column
    df = df.reset_index()

    total_bars = len(df)

    # Apply date range filtering (historical mode only — live always shows latest)
    if mode == "historical":
        if from_dt is not None:
            df = df[df["timestamp"] >= pd.Timestamp(from_dt, tz="UTC")]
        if to_dt is not None:
            df = df[df["timestamp"] <= pd.Timestamp(to_dt, tz="UTC")]

    # Apply limit (last N bars)
    if len(df) > limit:
        df = df.tail(limit)

    if "volume" not in df.columns:
        df["volume"] = 0.0

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
    ichimoku: list[IchimokuSeries] = []
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
        logger.error("Ichimoku computation failed for %s/%s: %s", instrument, timeframe, e)

    from_date = str(df["timestamp"].iloc[0]) if len(df) > 0 else None
    to_date = str(df["timestamp"].iloc[-1]) if len(df) > 0 else None

    return MarketDataResponse(
        instrument=instrument,
        timeframe=timeframe,
        candles=candles,
        ichimoku=ichimoku,
        total_bars=total_bars,
        from_date=from_date,
        to_date=to_date,
        source=source,
        mode=mode,
    )


@router.get("/market-data/sessions")
def get_sessions(user: TokenData = Depends(get_current_user)):
    """Return market session definitions for chart rendering."""
    from fibokei.data.sessions import get_sessions_metadata

    return {"sessions": get_sessions_metadata()}


@router.get("/market-data/live/status")
def live_chart_status(user: TokenData = Depends(get_current_user)):
    """Check whether live chart mode is available."""
    from fibokei.data.live_provider import is_live_available

    available = is_live_available()
    return {
        "available": available,
        "reason": None if available else "IG demo credentials not configured",
    }


@router.post("/market-data/refresh", response_model=dict)
def refresh_market_data(
    user: TokenData = Depends(get_current_user),
):
    """Trigger a refresh of market data for all instruments."""
    from fibokei.data.ingestion import refresh_all
    results = refresh_all(timeframe="H1")
    return {"refreshed": results, "total": sum(1 for v in results.values() if v > 0)}


@router.get("/market-data/{instrument}/{timeframe}", response_model=MarketDataResponse)
def get_market_data(
    instrument: str,
    timeframe: str,
    user: TokenData = Depends(get_current_user),
    limit: int = DEFAULT_BARS,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    mode: str = Query("historical", pattern="^(historical|live)$"),
):
    """Return OHLCV candles with precomputed Ichimoku Cloud data.

    Modes:
        - historical (default): Canonical dataset from disk
        - live: Recent candles from IG demo API (requires IG credentials)
    """
    instrument = instrument.replace("_", "").replace("/", "").upper()

    try:
        get_instrument(instrument)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown instrument: {instrument}")

    try:
        tf_enum = Timeframe(timeframe.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    limit = min(max(limit, 1), MAX_BARS)

    if mode == "live":
        return _handle_live(instrument, tf_enum.value, limit)

    return _handle_historical(instrument, tf_enum.value, limit, from_dt, to_dt)


def _handle_historical(
    instrument: str,
    timeframe: str,
    limit: int,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> MarketDataResponse:
    """Load canonical/historical data."""
    try:
        df, source = load_canonical_cached(instrument, timeframe)
    except Exception as e:
        logger.error("Failed to load data for %s/%s: %s", instrument, timeframe, e)
        raise HTTPException(
            status_code=500, detail=f"Failed to load data for {instrument}/{timeframe}: {e}"
        )

    if df is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data file for {instrument}/{timeframe}",
        )

    return _df_to_response(df, instrument, timeframe, source, "historical", limit, from_dt, to_dt)


def _handle_live(
    instrument: str,
    timeframe: str,
    limit: int,
) -> MarketDataResponse:
    """Load live data from IG demo API."""
    from fibokei.data.live_provider import (
        get_supported_ig_resolution,
        is_live_available,
        load_live,
    )

    if not is_live_available():
        raise HTTPException(
            status_code=503,
            detail="Live chart mode is not available. IG demo credentials are not configured.",
        )

    if get_supported_ig_resolution(timeframe) is None:
        raise HTTPException(
            status_code=400,
            detail=f"Timeframe {timeframe} is not supported for live mode.",
        )

    try:
        df, source = load_live(instrument, timeframe, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("Live data error for %s/%s: %s", instrument, timeframe, e)
        raise HTTPException(status_code=502, detail=str(e))

    return _df_to_response(df, instrument, timeframe, source, "live", limit)
