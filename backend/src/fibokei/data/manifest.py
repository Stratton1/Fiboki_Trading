"""Data manifest generation and loading.

The manifest indexes all canonical datasets with metadata including
bar counts, date ranges, file sizes, and checksums.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_MANIFEST_VERSION = 1


def generate_manifest(canonical_dir: Path) -> dict:
    """Scan canonical_dir and generate manifest.json.

    Walks the directory tree looking for .parquet and .csv files,
    reads metadata from each, and writes manifest.json to canonical_dir.
    Returns the manifest dict.
    """
    datasets = []

    if not canonical_dir.exists():
        logger.warning("Canonical directory does not exist: %s", canonical_dir)
        manifest = _build_manifest(datasets)
        return manifest

    for provider_dir in sorted(canonical_dir.iterdir()):
        if not provider_dir.is_dir() or provider_dir.name.startswith("."):
            continue
        if provider_dir.name == "manifest.json":
            continue
        provider_name = provider_dir.name

        for symbol_dir in sorted(provider_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue
            symbol = symbol_dir.name.upper()

            for data_file in sorted(symbol_dir.iterdir()):
                if data_file.suffix not in (".parquet", ".csv"):
                    continue

                entry = _build_dataset_entry(
                    data_file, provider_name, symbol, canonical_dir
                )
                if entry:
                    datasets.append(entry)

    manifest = _build_manifest(datasets)

    # Write to disk
    manifest_path = canonical_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    logger.info(
        "Manifest generated: %d datasets written to %s",
        len(datasets),
        manifest_path,
    )

    return manifest


def _build_manifest(datasets: list[dict]) -> dict:
    return {
        "version": _MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "fibokei manifest generate",
        "datasets": datasets,
    }


def _build_dataset_entry(
    file_path: Path, provider: str, symbol: str, canonical_dir: Path
) -> dict | None:
    """Build a manifest entry for a single data file."""
    # Extract timeframe from filename: eurusd_h1.parquet -> H1
    parts = file_path.stem.split("_")
    if len(parts) < 2:
        return None
    timeframe = parts[-1].upper()

    try:
        if file_path.suffix == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            df = pd.read_csv(file_path, parse_dates=["timestamp"], index_col="timestamp")
    except Exception as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return None

    # Ensure we have a datetime index
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            idx = pd.DatetimeIndex(df["timestamp"])
        else:
            return None

    # Compute checksum
    file_bytes = file_path.read_bytes()
    checksum = hashlib.sha256(file_bytes).hexdigest()[:16]

    rel_path = file_path.relative_to(canonical_dir)

    return {
        "symbol": symbol,
        "provider": provider,
        "timeframe": timeframe,
        "bars": len(df),
        "from_date": str(idx.min().date()) if len(idx) > 0 else None,
        "to_date": str(idx.max().date()) if len(idx) > 0 else None,
        "size_bytes": file_path.stat().st_size,
        "path": str(rel_path),
        "modified_at": datetime.fromtimestamp(
            file_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
        "checksum": f"sha256:{checksum}",
    }


def load_manifest(canonical_dir: Path) -> dict | None:
    """Load manifest.json from canonical_dir. Returns None if missing."""
    manifest_path = canonical_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load manifest: %s", e)
        return None
