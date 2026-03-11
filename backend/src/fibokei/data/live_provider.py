"""Live market data provider using IG REST API.

Fetches recent candles via IGClient.get_prices() and normalizes them
into the same DataFrame format used by the canonical data pipeline.
Includes a TTL cache to avoid excessive API calls.
"""

import logging
import time
from datetime import datetime, timezone

import pandas as pd

from fibokei.core.instruments import get_ig_epic
from fibokei.execution.ig_client import IGClient, IGClientError

logger = logging.getLogger(__name__)

# Map Fiboki timeframes to IG resolution strings
_TF_TO_IG_RESOLUTION: dict[str, str] = {
    "M1": "MINUTE",
    "M5": "MINUTE_5",
    "M15": "MINUTE_15",
    "M30": "MINUTE_30",
    "H1": "HOUR",
    "H4": "HOUR_4",
    "D": "DAY",
    "W": "WEEK",
}

# Cache TTL per timeframe (seconds) — shorter timeframes refresh faster
_CACHE_TTL: dict[str, int] = {
    "M1": 10,
    "M5": 15,
    "M15": 30,
    "M30": 60,
    "H1": 60,
    "H4": 120,
    "D": 300,
    "W": 600,
}

DEFAULT_CACHE_TTL = 60

# In-memory cache: key = (symbol, timeframe) -> (df, timestamp)
_live_cache: dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}

# Module-level client (lazy singleton)
_ig_client: IGClient | None = None


def _get_client() -> IGClient:
    global _ig_client
    if _ig_client is None:
        _ig_client = IGClient()
    return _ig_client


def is_live_available() -> bool:
    """Check if IG credentials are configured for live chart data."""
    import os
    return bool(
        os.environ.get("FIBOKEI_IG_API_KEY")
        and os.environ.get("FIBOKEI_IG_USERNAME")
        and os.environ.get("FIBOKEI_IG_PASSWORD")
    )


def get_supported_ig_resolution(timeframe: str) -> str | None:
    """Return IG resolution for a Fiboki timeframe, or None if unsupported."""
    return _TF_TO_IG_RESOLUTION.get(timeframe.upper())


def load_live(
    symbol: str,
    timeframe: str,
    limit: int = 200,
) -> tuple[pd.DataFrame, str]:
    """Fetch live candle data from IG demo API.

    Returns:
        (DataFrame with OHLCV columns and DatetimeIndex, source string)

    Raises:
        ValueError: If timeframe is not supported for live mode.
        RuntimeError: If IG API call fails.
    """
    tf_upper = timeframe.upper()
    resolution = get_supported_ig_resolution(tf_upper)
    if resolution is None:
        raise ValueError(
            f"Timeframe {timeframe} is not supported for live mode. "
            f"Supported: {', '.join(_TF_TO_IG_RESOLUTION.keys())}"
        )

    # Check cache
    cache_key = (symbol.upper(), tf_upper)
    ttl = _CACHE_TTL.get(tf_upper, DEFAULT_CACHE_TTL)
    cached = _live_cache.get(cache_key)
    if cached is not None:
        df, cached_at = cached
        if (time.time() - cached_at) < ttl:
            logger.debug("Live cache hit for %s/%s", symbol, tf_upper)
            return df, "live/ig_demo"

    # Resolve epic
    try:
        epic = get_ig_epic(symbol.upper())
    except KeyError:
        raise ValueError(f"No IG epic mapping for {symbol}")

    # Fetch from IG
    client = _get_client()
    num_points = min(limit, 200)  # IG max per request

    try:
        data = client.get_prices(epic, resolution, num_points)
    except IGClientError as e:
        raise RuntimeError(f"IG API error fetching prices for {symbol}/{tf_upper}: {e}")

    prices = data.get("prices", [])
    if not prices:
        raise RuntimeError(f"No price data returned from IG for {symbol}/{tf_upper}")

    # Normalize IG price format to DataFrame
    rows = []
    for p in prices:
        snapshot_time = p.get("snapshotTime", "")
        # IG returns times like "2026/03/10 14:00:00" or ISO format
        try:
            ts = pd.Timestamp(snapshot_time.replace("/", "-"), tz="UTC")
        except Exception:
            continue

        # IG returns bid/ask/last prices; use mid-point of bid for charting
        open_price = p.get("openPrice", {})
        high_price = p.get("highPrice", {})
        low_price = p.get("lowPrice", {})
        close_price = p.get("closePrice", {})

        # Use bid prices (standard for forex charting)
        rows.append({
            "timestamp": ts,
            "open": open_price.get("bid", open_price.get("lastTraded", 0)),
            "high": high_price.get("bid", high_price.get("lastTraded", 0)),
            "low": low_price.get("bid", low_price.get("lastTraded", 0)),
            "close": close_price.get("bid", close_price.get("lastTraded", 0)),
            "volume": p.get("lastTradedVolume", 0) or 0,
        })

    if not rows:
        raise RuntimeError(f"Could not parse any price data from IG for {symbol}/{tf_upper}")

    df = pd.DataFrame(rows)
    df = df.set_index("timestamp").sort_index()

    # Store in cache
    _live_cache[cache_key] = (df, time.time())

    logger.info(
        "Loaded %d live candles for %s/%s from IG demo",
        len(df), symbol, tf_upper,
    )
    return df, "live/ig_demo"


def clear_live_cache() -> None:
    """Clear the live data cache (for testing)."""
    _live_cache.clear()
