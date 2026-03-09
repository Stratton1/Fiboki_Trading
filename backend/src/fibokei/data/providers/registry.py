"""Provider registry — single place to obtain provider instances and load data."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fibokei.data.providers.base import DataProvider, ProviderID

# Default canonical data directory
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_CANONICAL_DIR = _PROJECT_ROOT / "data" / "canonical"
_DEFAULT_FIXTURES_DIR = _PROJECT_ROOT / "data" / "fixtures"


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
        1. Dukascopy (validation-grade)
        2. HistData (bulk research)
        3. Legacy fixtures directory (``data/fixtures/``)

    Returns None if no file is found.
    """
    canonical_dir = data_dir or _DEFAULT_CANONICAL_DIR

    if provider is not None:
        pid = ProviderID(provider) if isinstance(provider, str) else provider
        return _try_load(canonical_dir, pid, symbol, timeframe)

    # Priority search across providers
    for pid in (ProviderID.DUKASCOPY, ProviderID.HISTDATA):
        df = _try_load(canonical_dir, pid, symbol, timeframe)
        if df is not None:
            return df

    # Fall back to legacy fixtures
    return _try_load_fixture(symbol, timeframe)


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
            if ext == "parquet":
                df = pd.read_parquet(path)
            else:
                df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
            df.index = pd.to_datetime(df.index, utc=True)
            df.index.name = "timestamp"
            return df

    return None


def _try_load_fixture(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Try loading from the legacy data/fixtures/ directory."""
    patterns = [
        f"sample_{symbol.lower()}_{timeframe.lower()}.csv",
        f"{symbol.lower()}_{timeframe.lower()}.csv",
    ]
    for pattern in patterns:
        path = _DEFAULT_FIXTURES_DIR / pattern
        if path.exists():
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

    return None
