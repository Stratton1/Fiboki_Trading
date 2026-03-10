"""Provider registry — single place to obtain provider instances and load data."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from fibokei.data.cache import DataFrameCache
from fibokei.data.paths import get_canonical_dir, get_fixtures_dir, get_starter_dir
from fibokei.data.providers.base import DataProvider, ProviderID

logger = logging.getLogger(__name__)

_df_cache = DataFrameCache(max_size=50, ttl_seconds=300)


def get_provider(provider_id: ProviderID | str) -> DataProvider:
    """Return an instantiated provider by ID.

    Accepts either a ``ProviderID`` enum or a string like ``"histdata"``.
    """
    if isinstance(provider_id, str):
        provider_id = ProviderID(provider_id.lower())

    if provider_id == ProviderID.HISTDATA:
        from fibokei.data.providers.histdata import HistDataProvider
        return HistDataProvider()

    if provider_id == ProviderID.DUKASCOPY:
        from fibokei.data.providers.dukascopy import DukascopyProvider
        return DukascopyProvider()

    raise ValueError(f"No provider implementation for {provider_id!r}")


def list_providers() -> list[ProviderID]:
    """Return provider IDs that have implementations."""
    return [ProviderID.HISTDATA, ProviderID.DUKASCOPY]


def load_canonical(
    symbol: str,
    timeframe: str,
    provider: ProviderID | str | None = None,
    *,
    data_dir: Path | None = None,
) -> pd.DataFrame | None:
    """Load a canonical dataset from the provider data store.

    Search order when *provider* is None:
        1. Canonical: Dukascopy (validation-grade)
        2. Canonical: HistData (bulk research)
        3. Starter: HistData (production starter subset)
        4. Legacy fixtures directory (``data/fixtures/``)

    Returns None if no file is found.
    """
    canonical_dir = data_dir or get_canonical_dir()

    if provider is not None:
        pid = ProviderID(provider) if isinstance(provider, str) else provider
        df = _try_load(canonical_dir, pid, symbol, timeframe)
        if df is not None:
            return df
        # Also check starter for the specified provider
        return _try_load(get_starter_dir(), pid, symbol, timeframe)

    # Priority search across providers in canonical store
    for pid in (ProviderID.DUKASCOPY, ProviderID.HISTDATA):
        df = _try_load(canonical_dir, pid, symbol, timeframe)
        if df is not None:
            return df

    logger.info("No canonical data for %s/%s, checking starter", symbol, timeframe)

    # Search starter dataset
    for pid in (ProviderID.HISTDATA,):
        df = _try_load(get_starter_dir(), pid, symbol, timeframe)
        if df is not None:
            return df

    logger.info("No starter data for %s/%s, checking fixtures", symbol, timeframe)

    # Fall back to legacy fixtures
    df = _try_load_fixture(symbol, timeframe)
    if df is None:
        logger.warning(
            "No data found for %s/%s. Searched: canonical=%s, starter=%s, fixtures=%s",
            symbol,
            timeframe,
            canonical_dir,
            get_starter_dir(),
            get_fixtures_dir(),
        )
    return df


def load_canonical_cached(
    symbol: str,
    timeframe: str,
    data_dir: str | Path | None = None,
    provider: "ProviderID | str | None" = None,
) -> tuple[pd.DataFrame | None, str]:
    """Load canonical data with caching. Returns (df, source_label)."""
    if data_dir is None and provider is None:
        cached = _df_cache.get(symbol, timeframe)
        if cached is not None:
            return cached, "cache"

    df = load_canonical(symbol, timeframe, data_dir=data_dir, provider=provider)
    if df is not None and data_dir is None and provider is None:
        _df_cache.put(symbol, timeframe, df)

    source = _determine_source(symbol, timeframe)
    return df, source


def _determine_source(symbol: str, timeframe: str) -> str:
    """Determine which data source was used (for observability)."""
    canonical = get_canonical_dir()
    starter = get_starter_dir()
    sym_lower = symbol.lower()
    tf_lower = timeframe.lower()

    for pid in ("dukascopy", "histdata"):
        p = canonical / pid / sym_lower / f"{sym_lower}_{tf_lower}.parquet"
        if p.exists():
            return f"canonical/{pid}"
        p = p.with_suffix(".csv")
        if p.exists():
            return f"canonical/{pid}"

    for pid in ("histdata",):
        p = starter / pid / sym_lower / f"{sym_lower}_{tf_lower}.parquet"
        if p.exists():
            return "starter"

    return "fixtures"


def _try_load(
    canonical_dir: Path,
    provider: ProviderID,
    symbol: str,
    timeframe: str,
) -> pd.DataFrame | None:
    """Try loading a canonical file (parquet first, then CSV)."""
    base = canonical_dir / provider.value / symbol.lower()
    stem = f"{symbol.lower()}_{timeframe.lower()}"

    for ext in ("parquet", "csv"):
        path = base / f"{stem}.{ext}"
        if path.exists():
            logger.debug("Loading data from %s", path)
            try:
                if ext == "parquet":
                    df = pd.read_parquet(path)
                else:
                    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
                df.index = pd.to_datetime(df.index, utc=True)
                df.index.name = "timestamp"
                return df
            except Exception as e:
                logger.error("Failed to read %s: %s", path, e)
                continue

    return None


def _try_load_fixture(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Try loading from the legacy data/fixtures/ directory."""
    fixtures_dir = get_fixtures_dir()
    patterns = [
        f"sample_{symbol.lower()}_{timeframe.lower()}.csv",
        f"{symbol.lower()}_{timeframe.lower()}.csv",
    ]
    for pattern in patterns:
        path = fixtures_dir / pattern
        if path.exists():
            try:
                df = pd.read_csv(path)
                df.columns = df.columns.str.strip().str.lower()
                col_map = {"date": "timestamp", "datetime": "timestamp", "time": "timestamp"}
                df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df = df.set_index("timestamp").sort_index()
                if "volume" not in df.columns:
                    df["volume"] = 0.0
                df = df[["open", "high", "low", "close", "volume"]]
                return df
            except Exception as e:
                logger.error("Failed to read fixture %s: %s", path, e)
                continue

    return None
