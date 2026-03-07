"""Market data endpoints."""

import math
from pathlib import Path

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
from fibokei.indicators.ichimoku import IchimokuCloud

# Resolve data directory: route file is at backend/src/fibokei/api/routes/
# We need to reach /Users/joseph/Projects/Fiboki_Trading/data/fixtures/
_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "data" / "fixtures"

router = APIRouter(tags=["market-data"])


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
):
    """Return OHLCV candles with precomputed Ichimoku Cloud data."""
    # Validate instrument
    try:
        get_instrument(instrument)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown instrument: {instrument}")

    # Validate timeframe
    try:
        tf_enum = Timeframe(timeframe.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    # Load CSV
    csv_path = _DATA_DIR / f"sample_{instrument.lower()}_{tf_enum.value.lower()}.csv"
    if not csv_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No data file for {instrument}/{tf_enum.value}",
        )

    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip().str.lower()

    # Normalize column names
    col_map = {"date": "timestamp", "datetime": "timestamp", "time": "timestamp"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    if "volume" not in df.columns:
        df["volume"] = 0.0

    # Build candle bars
    candles = []
    for _, row in df.iterrows():
        candles.append(
            CandleBar(
                timestamp=int(row["timestamp"].timestamp() * 1000),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row.get("volume", 0.0),
            )
        )

    # Compute Ichimoku
    ich_df = df[["timestamp", "open", "high", "low", "close"]].copy()
    ich_df = ich_df.set_index("timestamp")
    ich = IchimokuCloud()
    ich.compute(ich_df)
    ich_df = ich_df.reset_index()

    ichimoku = []
    for _, row in ich_df.iterrows():
        ichimoku.append(
            IchimokuSeries(
                timestamp=int(row["timestamp"].timestamp() * 1000),
                tenkan=_nan_to_none(row.get("tenkan_sen", float("nan"))),
                kijun=_nan_to_none(row.get("kijun_sen", float("nan"))),
                senkou_a=_nan_to_none(row.get("senkou_span_a", float("nan"))),
                senkou_b=_nan_to_none(row.get("senkou_span_b", float("nan"))),
                chikou=_nan_to_none(row.get("chikou_span", float("nan"))),
            )
        )

    return MarketDataResponse(
        instrument=instrument,
        timeframe=tf_enum.value,
        candles=candles,
        ichimoku=ichimoku,
    )
