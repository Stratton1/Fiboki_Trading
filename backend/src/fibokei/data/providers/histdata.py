"""HistData provider — primary bulk historical source for Fiboki.

HistData (histdata.com) distributes free M1 forex/commodity data as
downloadable CSV files.  This provider handles:

  - Importing already-downloaded HistData CSV/ZIP archives
  - Normalising the two common HistData CSV formats into Fiboki canonical
  - Deriving higher timeframes from M1 via resampling
  - Tracking provenance so we always know a dataset came from HistData

HistData CSV formats
--------------------

**ASCII format (most common):**
    ``DateTime;Open;High;Low;Close;Volume``
    Timestamp format: ``YYYYMMDD HHMMSS``
    Delimiter: semicolon
    No header row in some files, header in others.

**Generic ASCII format (tick):**
    ``DateTime,Bid,Ask,Volume``
    Timestamp format: ``YYYYMMDD HH:MM:SS.fff``

This provider focuses on the M1 bar format which is the most useful for
backtesting across all timeframes.

Typical workflow
----------------

1. User downloads ZIP files from histdata.com (e.g. ``DAT_ASCII_EURUSD_M1_2023.zip``)
2. Extracts to ``data/raw/histdata/``
3. Runs ``fibokei data ingest --provider histdata --symbol EURUSD``
4. Provider finds all matching CSVs, merges, normalises, validates, saves canonical
"""

from __future__ import annotations

import io
import zipfile
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

# Default location for raw HistData archives
_DEFAULT_RAW_DIR = Path(__file__).resolve().parents[5] / "data" / "raw" / "histdata"


class HistDataProvider(DataProvider):
    """Import and normalise HistData M1 CSV data."""

    def __init__(self, raw_dir: Path | None = None) -> None:
        self._raw_dir = raw_dir or _DEFAULT_RAW_DIR

    @property
    def provider_id(self) -> ProviderID:
        return ProviderID.HISTDATA

    # ------------------------------------------------------------------
    # Symbol catalogue
    # ------------------------------------------------------------------

    def list_symbols(self) -> list[str]:
        return list_mapped_symbols(ProviderID.HISTDATA)

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
        """Scan the raw directory for HistData CSVs/ZIPs and load them.

        HistData files are typically named like:
            DAT_ASCII_EURUSD_M1_2023.zip
            DAT_ASCII_EURUSD_M1_202301.csv
            EURUSD_M1_2023.csv

        We search for any file containing the provider symbol (case-insensitive).
        """
        try:
            provider_symbol = to_provider_symbol(symbol, ProviderID.HISTDATA)
        except KeyError:
            provider_symbol = symbol.upper()

        search_dir = self._raw_dir
        if not search_dir.exists():
            search_dir.mkdir(parents=True, exist_ok=True)
            raise FileNotFoundError(
                f"Raw HistData directory created at {search_dir} — "
                f"place HistData CSV/ZIP files there and re-run."
            )

        # Collect all matching files
        frames: list[pd.DataFrame] = []
        patterns = [f"*{provider_symbol}*", f"*{provider_symbol.lower()}*"]
        seen: set[Path] = set()

        for pattern in patterns:
            for path in sorted(search_dir.glob(pattern)):
                if path in seen:
                    continue
                seen.add(path)

                if path.suffix.lower() == ".zip":
                    frames.extend(self._read_zip(path))
                elif path.suffix.lower() == ".csv":
                    frames.append(self._read_csv(path))

        if not frames:
            raise FileNotFoundError(
                f"No HistData files found for {symbol} "
                f"(searched {search_dir} for {provider_symbol}). "
                f"Download M1 data from histdata.com and place it in {search_dir}."
            )

        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])

        # Apply date filters
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

        # Ensure standard column names
        df.columns = [c.strip().lower() for c in df.columns]
        col_renames = {
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

        # Parse timestamp to UTC
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        else:
            raise ValueError("Cannot find timestamp column in HistData CSV")

        # Ensure volume
        if "volume" not in df.columns:
            df["volume"] = 0.0

        # Set index
        df = df.set_index("timestamp")
        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()

        # Coerce to float
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Drop invalid rows
        df = df.dropna(subset=["open", "high", "low", "close"])

        # Remove duplicates
        df = df[~df.index.duplicated(keep="first")]

        df.index.name = "timestamp"
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
        """Ingest M1 data and derive all requested timeframes.

        Returns a dict mapping timeframe → DatasetMetadata for each
        saved canonical file.
        """
        raw = self.fetch_raw(symbol, "M1", start=start, end=end)
        m1_df = self.normalise(raw, symbol, "M1")
        warnings = self.validate(m1_df)

        derived = derive_all_timeframes(m1_df, timeframes)
        results: dict[str, DatasetMetadata] = {}

        for tf, df in derived.items():
            path, meta = self.save_canonical(df, symbol, tf, data_dir)
            meta.source_precision = SourcePrecision.M1
            meta.resampled_from = "M1" if tf != "M1" else None
            if warnings:
                meta.extra["validation_warnings"] = warnings
            meta.status = DatasetStatus.RESEARCH_READY
            results[tf] = meta

        return results

    # ------------------------------------------------------------------
    # Automated download
    # ------------------------------------------------------------------

    @property
    def raw_dir(self) -> Path:
        return self._raw_dir

    @raw_dir.setter
    def raw_dir(self, value: Path) -> None:
        self._raw_dir = value

    def download(
        self,
        symbol: str,
        *,
        years: list[int] | None = None,
        output_dir: Path | None = None,
    ) -> list[Path]:
        """Download M1 data from histdata.com via the `histdata` package.

        Returns list of downloaded ZIP file paths.
        """
        from histdata import download_hist_data

        try:
            native = to_provider_symbol(symbol, ProviderID.HISTDATA)
        except KeyError:
            native = symbol.upper()

        out = output_dir or self._raw_dir
        out.mkdir(parents=True, exist_ok=True)

        if years is None:
            from datetime import datetime as dt
            current_year = dt.now().year
            years = list(range(2019, current_year + 1))

        downloaded: list[Path] = []
        for year in years:
            try:
                result = download_hist_data(
                    year=str(year),
                    month=None,
                    pair=native.lower(),
                    output_directory=str(out),
                    verbose=False,
                )
                if result:
                    downloaded.append(Path(result))
            except Exception:
                pass  # year not available

        return downloaded

    # ------------------------------------------------------------------
    # CSV parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        """Read a single HistData CSV file.

        Handles both semicolon-delimited and comma-delimited formats,
        with or without headers.
        """
        text = path.read_text(encoding="utf-8", errors="replace")

        # Detect delimiter
        first_line = text.split("\n", 1)[0]
        delimiter = ";" if ";" in first_line else ","

        # Detect header
        has_header = any(
            kw in first_line.lower()
            for kw in ("open", "high", "low", "close", "date", "time")
        )

        if has_header:
            df = pd.read_csv(io.StringIO(text), delimiter=delimiter)
        else:
            # HistData ASCII M1 format: DateTime;Open;High;Low;Close;Volume
            col_count = len(first_line.split(delimiter))
            if col_count >= 6:
                names = ["timestamp", "open", "high", "low", "close", "volume"]
            elif col_count == 5:
                names = ["timestamp", "open", "high", "low", "close"]
            else:
                names = None
            df = pd.read_csv(io.StringIO(text), delimiter=delimiter, names=names)

        # HistData timestamps: "YYYYMMDD HHMMSS" or "YYYYMMDD HH:MM:SS"
        df.columns = [c.strip().lower() for c in df.columns]
        ts_col = next(
            (c for c in df.columns if c in ("datetime", "date", "timestamp", "time")),
            df.columns[0],
        )
        df = df.rename(columns={ts_col: "timestamp"})

        # Parse the HistData timestamp format
        df["timestamp"] = df["timestamp"].astype(str).str.strip()
        try:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        except Exception:
            # Try HistData's compact format: 20230101 120000
            df["timestamp"] = pd.to_datetime(
                df["timestamp"], format="%Y%m%d %H%M%S", utc=True
            )

        return df

    @staticmethod
    def _read_zip(path: Path) -> list[pd.DataFrame]:
        """Extract and read all CSV files inside a ZIP archive."""
        frames: list[pd.DataFrame] = []
        with zipfile.ZipFile(path, "r") as zf:
            for name in zf.namelist():
                if name.lower().endswith(".csv"):
                    csv_bytes = zf.read(name)
                    temp_path = path.parent / f"_tmp_{name}"
                    try:
                        temp_path.write_bytes(csv_bytes)
                        frames.append(HistDataProvider._read_csv(temp_path))
                    finally:
                        temp_path.unlink(missing_ok=True)
        return frames
