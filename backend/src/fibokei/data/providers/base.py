"""Provider abstraction for historical market data sources.

Every data provider implements the same contract so Fiboki's backtesting,
research, and charting layers are completely decoupled from individual vendors.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd


class ProviderID(str, Enum):
    """Canonical identifiers for data providers."""

    HISTDATA = "histdata"
    DUKASCOPY = "dukascopy"
    YAHOO = "yahoo"
    SAMPLE = "sample"  # deterministic fixture data


class SourcePrecision(str, Enum):
    """Granularity of the original source data."""

    TICK = "tick"
    M1 = "M1"
    DERIVED = "derived"  # resampled from a finer granularity


class DatasetStatus(str, Enum):
    """Processing stage of a dataset."""

    RAW = "raw"
    CLEANED = "cleaned"
    RESAMPLED = "resampled"
    VALIDATED = "validated"
    RESEARCH_READY = "research_ready"


@dataclass
class DatasetMetadata:
    """Rich provenance record for every canonical dataset.

    Stored alongside the Parquet/CSV file and persisted to the database
    so we always know *exactly* where data came from and what happened to it.
    """

    provider: ProviderID
    symbol: str
    timeframe: str

    # File paths
    raw_source_path: str | None = None
    canonical_path: str | None = None

    # Coverage
    date_start: datetime | None = None
    date_end: datetime | None = None
    row_count: int = 0

    # Quality metrics (populated by validation pass)
    missing_values: int = 0
    duplicate_timestamps: int = 0
    suspicious_gaps: int = 0
    timezone_normalised: bool = True

    # Provenance
    source_precision: SourcePrecision = SourcePrecision.M1
    status: DatasetStatus = DatasetStatus.RAW
    ingest_version: str = "1.0.0"
    processing_version: str = "1.0.0"
    resampled_from: str | None = None  # e.g. "M1" if H1 was derived from M1

    # Arbitrary provider-specific metadata
    extra: dict[str, Any] = field(default_factory=dict)


class DataProvider(abc.ABC):
    """Contract that every historical data provider must implement.

    Lifecycle:
        1. list_symbols()       — what instruments does this provider offer?
        2. fetch_raw()          — download / import raw data
        3. normalise()          — convert to Fiboki canonical DataFrame
        4. validate()           — run quality checks, return warnings
        5. save_canonical()     — persist to the canonical data store
    """

    @property
    @abc.abstractmethod
    def provider_id(self) -> ProviderID:
        """Unique identifier for this provider."""

    # ------------------------------------------------------------------
    # Symbol catalogue
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_symbols(self) -> list[str]:
        """Return provider-native symbols that this source can supply."""

    # ------------------------------------------------------------------
    # Data acquisition
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def fetch_raw(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        output_dir: Path | None = None,
    ) -> Path | pd.DataFrame:
        """Fetch or import raw data for *symbol* at *timeframe*.

        May return a Path to the downloaded file or a DataFrame already
        in memory, depending on the provider's natural delivery format.
        """

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def normalise(
        self,
        raw: Path | pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> pd.DataFrame:
        """Convert raw provider data into the Fiboki canonical format.

        The returned DataFrame MUST have:
            - DatetimeIndex named ``timestamp`` in UTC
            - Columns: open, high, low, close, volume
            - Sorted ascending by timestamp
            - No duplicate timestamps
        """

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, df: pd.DataFrame) -> list[str]:
        """Run quality checks on a canonical DataFrame.

        Returns a list of human-readable warnings.  An empty list means
        the data passed all checks.

        The default implementation delegates to the existing
        ``fibokei.data.validator.validate_ohlcv`` function.
        """
        from fibokei.data.validator import validate_ohlcv

        return validate_ohlcv(df)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_canonical(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        data_dir: Path,
        *,
        fmt: str = "parquet",
    ) -> tuple[Path, DatasetMetadata]:
        """Write a canonical dataset and return its metadata.

        Parameters
        ----------
        df : pd.DataFrame
            Canonical OHLCV DataFrame (must already be normalised).
        symbol : str
            Fiboki instrument symbol (e.g. ``EURUSD``).
        timeframe : str
            Candle period (e.g. ``M1``, ``H1``).
        data_dir : pathlib.Path
            Root directory for canonical data files.
        fmt : str
            ``"parquet"`` (default) or ``"csv"``.
        """
        provider_dir = data_dir / self.provider_id.value / symbol.lower()
        provider_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{symbol.lower()}_{timeframe.lower()}.{fmt}"
        out_path = provider_dir / filename

        if fmt == "parquet":
            df.to_parquet(out_path, index=True)
        else:
            df.to_csv(out_path, index=True)

        meta = DatasetMetadata(
            provider=self.provider_id,
            symbol=symbol,
            timeframe=timeframe,
            canonical_path=str(out_path),
            date_start=df.index.min().to_pydatetime() if len(df) else None,
            date_end=df.index.max().to_pydatetime() if len(df) else None,
            row_count=len(df),
            timezone_normalised=True,
            status=DatasetStatus.CLEANED,
        )
        return out_path, meta

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def ingest(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        data_dir: Path | None = None,
    ) -> tuple[pd.DataFrame, DatasetMetadata]:
        """Full pipeline: fetch → normalise → validate → save.

        Returns the canonical DataFrame and its metadata.
        """
        from fibokei.data.providers.symbol_map import to_fiboki_symbol

        raw = self.fetch_raw(symbol, timeframe, start=start, end=end)
        fiboki_symbol = to_fiboki_symbol(symbol, self.provider_id)
        df = self.normalise(raw, fiboki_symbol, timeframe)
        warnings = self.validate(df)

        meta_kwargs: dict[str, Any] = {}
        if warnings:
            meta_kwargs["extra"] = {"validation_warnings": warnings}

        if data_dir is not None:
            path, meta = self.save_canonical(df, fiboki_symbol, timeframe, data_dir)
            meta.extra.update(meta_kwargs.get("extra", {}))
            return df, meta

        # No persistence requested — build metadata anyway
        meta = DatasetMetadata(
            provider=self.provider_id,
            symbol=fiboki_symbol,
            timeframe=timeframe,
            date_start=df.index.min().to_pydatetime() if len(df) else None,
            date_end=df.index.max().to_pydatetime() if len(df) else None,
            row_count=len(df),
            status=DatasetStatus.CLEANED,
            **meta_kwargs,
        )
        return df, meta
