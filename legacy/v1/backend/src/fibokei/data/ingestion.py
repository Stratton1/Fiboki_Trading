"""Market data ingestion from Yahoo Finance."""

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Map Fiboki symbols to Yahoo Finance tickers
SYMBOL_MAP = {
    # ── Forex Major ──────────────────────────────────────────────────────
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCHF": "USDCHF=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
    # ── Forex Cross ──────────────────────────────────────────────────────
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "AUDJPY": "AUDJPY=X",
    "EURAUD": "EURAUD=X",
    # ── Forex G10 Cross ──────────────────────────────────────────────────
    "AUDCAD": "AUDCAD=X",
    "AUDCHF": "AUDCHF=X",
    "AUDNZD": "AUDNZD=X",
    "CADCHF": "CADCHF=X",
    "CADJPY": "CADJPY=X",
    "CHFJPY": "CHFJPY=X",
    "EURCAD": "EURCAD=X",
    "EURCHF": "EURCHF=X",
    "EURNZD": "EURNZD=X",
    "GBPAUD": "GBPAUD=X",
    "GBPCAD": "GBPCAD=X",
    "GBPCHF": "GBPCHF=X",
    "GBPNZD": "GBPNZD=X",
    "NZDCAD": "NZDCAD=X",
    "NZDCHF": "NZDCHF=X",
    "NZDJPY": "NZDJPY=X",
    "SGDJPY": "SGDJPY=X",
    # ── Commodities ──────────────────────────────────────────────────────
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "BCOUSD": "BZ=F",
    "WTIUSD": "CL=F",
    "NATGAS": "NG=F",
    # legacy symbols kept for compat
    "XTIUSD": "CL=F",
    "XNGUSD": "NG=F",
    "XBRUSD": "BZ=F",
    # ── Indices ──────────────────────────────────────────────────────────
    "US500": "^GSPC",
    "US30": "^DJI",
    "US100": "^IXIC",
    "UK100": "^FTSE",
    "DE40": "^GDAXI",
    "JP225": "^N225",
    "AU200": "^AXJO",
    "CAC40": "^FCHI",
    "HK50": "^HSI",
    "DXY": "DX-Y.NYB",
    # ── Crypto ───────────────────────────────────────────────────────────
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "XRPUSD": "XRP-USD",
    "LTCUSD": "LTC-USD",
}

# Map Fiboki timeframes to yfinance intervals
TIMEFRAME_MAP = {
    "M1": "1m",
    "M2": "2m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
}

# yfinance period limits by interval
PERIOD_MAP = {
    "1m": "7d",
    "2m": "60d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "1h": "730d",
    "4h": "730d",
}


def fetch_ohlcv(symbol: str, timeframe: str = "H1") -> pd.DataFrame | None:
    """Fetch OHLCV data from Yahoo Finance.

    Args:
        symbol: Fiboki instrument symbol (e.g., 'EURUSD')
        timeframe: Fiboki timeframe (e.g., 'H1')

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
        or None if fetch fails.
    """
    yf_ticker = SYMBOL_MAP.get(symbol.upper())
    if not yf_ticker:
        logger.warning(f"No Yahoo Finance mapping for {symbol}")
        return None

    yf_interval = TIMEFRAME_MAP.get(timeframe.upper())
    if not yf_interval:
        logger.warning(f"Unsupported timeframe: {timeframe}")
        return None

    period = PERIOD_MAP.get(yf_interval, "730d")

    try:
        ticker = yf.Ticker(yf_ticker)
        df = ticker.history(period=period, interval=yf_interval)

        if df.empty:
            logger.warning(f"No data returned for {symbol} ({yf_ticker})")
            return None

        df = df.reset_index()
        # Normalize columns
        rename_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if col_lower in ("date", "datetime"):
                rename_map[col] = "timestamp"
            elif col_lower == "open":
                rename_map[col] = "open"
            elif col_lower == "high":
                rename_map[col] = "high"
            elif col_lower == "low":
                rename_map[col] = "low"
            elif col_lower == "close":
                rename_map[col] = "close"
            elif col_lower == "volume":
                rename_map[col] = "volume"

        df = df.rename(columns=rename_map)
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

        logger.info(f"Fetched {len(df)} bars for {symbol}/{timeframe}")
        return df

    except Exception as e:
        logger.error(f"Failed to fetch {symbol}: {e}")
        return None


def save_to_csv(df: pd.DataFrame, symbol: str, timeframe: str, data_dir: str | Path) -> Path:
    """Save DataFrame to CSV in the fixture format."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    filename = f"sample_{symbol.lower()}_{timeframe.lower()}.csv"
    path = data_dir / filename
    df.to_csv(path, index=False)
    logger.info(f"Saved {len(df)} bars to {path}")
    return path


def refresh_all(
    symbols: list[str] | None = None,
    timeframe: str = "H1",
    data_dir: str | Path | None = None,
) -> dict[str, int]:
    """Fetch and save data for multiple symbols.

    Returns dict mapping symbol to bar count (0 = failed).
    """
    if data_dir is None:
        data_dir = (
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "data"
            / "fixtures"
        )

    if symbols is None:
        symbols = list(SYMBOL_MAP.keys())

    results = {}
    for symbol in symbols:
        df = fetch_ohlcv(symbol, timeframe)
        if df is not None and not df.empty:
            save_to_csv(df, symbol, timeframe, data_dir)
            results[symbol] = len(df)
        else:
            results[symbol] = 0

    return results
