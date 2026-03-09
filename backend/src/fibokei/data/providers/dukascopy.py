"""Dukascopy provider — validation-grade historical data source.

Dukascopy provides high-fidelity tick and candle data via their public
historical data feed.  This provider handles:

  - Importing pre-downloaded Dukascopy CSV exports
  - Normalising tick data to M1 candles
  - Normalising candle exports directly
  - Using Dukascopy datasets for validation backtests on shortlisted combos

Data format
-----------

**Dukascopy tick export (CSV):**
    ``Gmt time,Ask,Bid,AskVolume,BidVolume``
    Timestamp: ``DD.MM.YYYY HH:MM:SS.fff``

**Dukascopy candle export (CSV):**
    ``Gmt time,Open,High,Low,Close,Volume``
    Timestamp: ``DD.MM.YYYY HH:MM:SS``

**duka CLI tool output:**
    ``time,ask,bid,ask_volume,bid_volume``
    or candle format depending on flags used.

Typical workflow
----------------

1. User exports data from Dukascopy JForex or uses ``duka`` CLI tool
2. Places CSVs in ``data/raw/dukascopy/``
3. Runs ``fibokei data ingest --provider dukascopy --symbol EURUSD``
4. Provider reads, normalises to canonical M1, derives higher TFs
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pandas as pd

from fibokei.data.providers.base import (
    DataProvider,
    DatasetMetadata,
    DatasetStatus,
    ProviderID,
    SourcePrecision,
)
from fibokei.data.providers.resampler import derive_all_timeframes
from fibokei.data.providers.symbol_map import (
    list_mapped_symbols,
    to_provider_symbol,
)

_DEFAULT_RAW_DIR = Path(__file__).resolve().parents[5] / "data" / "raw" / "dukascopy"


class DukascopyProvider(DataProvider):
    """Import and normalise Dukascopy CSV exports."""

    def __init__(self, raw_dir: Path | None = None) -> None:
        self._raw_dir = raw_dir or _DEFAULT_RAW_DIR

    @property
    def provider_id(self) -> ProviderID:
        return ProviderID.DUKASCOPY

    # ------------------------------------------------------------------
    # Symbol catalogue
    # ------------------------------------------------------------------

    def list_symbols(self) -> list[str]:
        return list_mapped_symbols(ProviderID.DUKASCOPY)

    # ------------------------------------------------------------------
    # Data acquisition
    # ------------------------------------------------------------------

    def fetch_raw(
        self,
        symbol: str,
        timeframe: str = "M1",
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        output_dir: Path | None = None,
    ) -> pd.DataFrame:
        """Scan the raw directory for Dukascopy CSVs and load them."""
        try:
            provider_symbol = to_provider_symbol(symbol, ProviderID.DUKASCOPY)
        except KeyError:
            provider_symbol = symbol.upper()

        search_dir = self._raw_dir
        if not search_dir.exists():
            search_dir.mkdir(parents=True, exist_ok=True)
            raise FileNotFoundError(
                f"Raw Dukascopy directory created at {search_dir} — "
                f"place Dukascopy CSV exports there and re-run."
            )

        # Collect matching files
        frames: list[pd.DataFrame] = []
        search_terms = [
            provider_symbol.upper(),
            provider_symbol.lower(),
            symbol.upper(),
            symbol.lower(),
        ]
        seen: set[Path] = set()

        for path in sorted(search_dir.glob("*.csv")):
            if path in seen:
                continue
            name_upper = path.stem.upper()
            if any(term.upper() in name_upper for term in search_terms):
                seen.add(path)
                frames.append(self._read_csv(path))

        if not frames:
            raise FileNotFoundError(
                f"No Dukascopy files found for {symbol} "
                f"(searched {search_dir}). "
                f"Export data from Dukascopy and place CSVs in {search_dir}."
            )

        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])

        if start:
            df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]

        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    def normalise(
        self,
        raw: Path | pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> pd.DataFrame:
        if isinstance(raw, Path):
            raw = self._read_csv(raw)

        df = raw.copy()
        df.columns = [c.strip().lower() for c in df.columns]

        # Detect format: tick (has bid/ask) vs candle (has open/high/low/close)
        is_tick = "bid" in df.columns or "ask" in df.columns

        if is_tick:
            df = self._normalise_ticks(df)
        else:
            df = self._normalise_candles(df)

        df.index.name = "timestamp"
        return df

    # ------------------------------------------------------------------
    # Tick → M1 aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_ticks(df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate tick data into M1 candles using mid-price."""
        ts_col = next(
            (c for c in df.columns if c in ("gmt time", "time", "timestamp", "datetime")),
            df.columns[0],
        )
        df = df.rename(columns={ts_col: "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        # Use mid-price = (bid + ask) / 2
        if "bid" in df.columns and "ask" in df.columns:
            df["bid"] = pd.to_numeric(df["bid"], errors="coerce")
            df["ask"] = pd.to_numeric(df["ask"], errors="coerce")
            df["price"] = (df["bid"] + df["ask"]) / 2
        elif "bid" in df.columns:
            df["price"] = pd.to_numeric(df["bid"], errors="coerce")
        else:
            df["price"] = pd.to_numeric(df["ask"], errors="coerce")

        # Volume from bid+ask volumes
        vol_cols = [c for c in df.columns if "volume" in c]
        if vol_cols:
            df["volume"] = sum(pd.to_numeric(df[c], errors="coerce").fillna(0) for c in vol_cols)
        else:
            df["volume"] = 1.0  # tick count

        df = df.set_index("timestamp")
        df = df.dropna(subset=["price"])

        # Resample to M1
        m1 = df["price"].resample("1min").agg(
            open="first", high="max", low="min", close="last"
        )
        m1["volume"] = df["volume"].resample("1min").sum()
        m1 = m1.dropna(subset=["open"])

        return m1

    # ------------------------------------------------------------------
    # Candle normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_candles(df: pd.DataFrame) -> pd.DataFrame:
        """Normalise a Dukascopy candle export."""
        col_renames = {
            "gmt time": "timestamp",
            "datetime": "timestamp",
            "date": "timestamp",
            "time": "timestamp",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
            "vol": "volume",
        }
        df = df.rename(columns={k: v for k, v in col_renames.items() if k in df.columns})

        if "timestamp" not in df.columns:
            df = df.rename(columns={df.columns[0]: "timestamp"})

        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, dayfirst=True)

        if "volume" not in df.columns:
            df["volume"] = 0.0

        df = df.set_index("timestamp")
        df = df[["open", "high", "low", "close", "volume"]]

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        return df

    # ------------------------------------------------------------------
    # Higher timeframe derivation
    # ------------------------------------------------------------------

    def ingest_all_timeframes(
        self,
        symbol: str,
        data_dir: Path,
        *,
        timeframes: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, DatasetMetadata]:
        """Ingest Dukascopy data and derive all requested timeframes."""
        raw = self.fetch_raw(symbol, "M1", start=start, end=end)
        m1_df = self.normalise(raw, symbol, "M1")
        warnings = self.validate(m1_df)

        # Detect source precision
        is_tick = "bid" in raw.columns or "ask" in raw.columns
        precision = SourcePrecision.TICK if is_tick else SourcePrecision.M1

        derived = derive_all_timeframes(m1_df, timeframes)
        results: dict[str, DatasetMetadata] = {}

        for tf, df in derived.items():
            path, meta = self.save_canonical(df, symbol, tf, data_dir)
            meta.source_precision = precision
            if is_tick and tf == "M1":
                meta.resampled_from = "tick"
            elif tf != "M1":
                meta.resampled_from = "M1"
            if warnings:
                meta.extra["validation_warnings"] = warnings
            meta.status = DatasetStatus.VALIDATED
            results[tf] = meta

        return results

    # ------------------------------------------------------------------
    # CSV reading
    # ------------------------------------------------------------------

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        """Read a Dukascopy CSV export."""
        text = path.read_text(encoding="utf-8", errors="replace")
        first_line = text.split("\n", 1)[0]

        # Dukascopy always uses comma
        has_header = any(
            kw in first_line.lower()
            for kw in ("open", "high", "low", "close", "bid", "ask", "gmt", "time")
        )

        if has_header:
            df = pd.read_csv(io.StringIO(text))
        else:
            col_count = len(first_line.split(","))
            if col_count == 5:
                # Tick format: time, ask, bid, ask_volume, bid_volume
                names = ["timestamp", "ask", "bid", "ask_volume", "bid_volume"]
            elif col_count >= 6:
                names = ["timestamp", "open", "high", "low", "close", "volume"]
            else:
                names = None
            df = pd.read_csv(io.StringIO(text), names=names)

        df.columns = [c.strip().lower() for c in df.columns]

        # Parse timestamp
        ts_col = next(
            (c for c in df.columns if c in ("gmt time", "time", "timestamp", "datetime")),
            df.columns[0],
        )
        df = df.rename(columns={ts_col: "timestamp"})
        df["timestamp"] = df["timestamp"].astype(str).str.strip()

        try:
            # Dukascopy format: DD.MM.YYYY HH:MM:SS.fff
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, dayfirst=True)
        except Exception:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        return df
