"""Fiboki V2 market-data platform.

Layering:

    providers/   fetch bytes from a source, declare what they actually are
    schema       the one canonical bar shape, price basis always explicit
    integrity    detect defects; NEVER fix them
    versioning   content-addressed dataset ids + lineage, persisted in SQLite
    store        raw (immutable) / canonical (derived) parquet, explicit root
    resample     timeframe aggregation with declared session/DST semantics
    recorder     executable-price capture, append-only and crash-safe
    telemetry    execution telemetry, append-only, backtest-vs-live divergence

Everything downstream (research, backtest, paper) references a
``DatasetVersion.version_id``, so any stored result can re-resolve the exact
bytes it was computed from. V1 could not answer "which data produced this",
and that is the failure this package exists to remove.
"""
from __future__ import annotations

from fiboki.data.schema import (
    BarDatasetMetadata,
    MarketState,
    PriceBasis,
    canonical_frame,
    validate_frame_shape,
)

__all__ = [
    "BarDatasetMetadata",
    "MarketState",
    "PriceBasis",
    "canonical_frame",
    "validate_frame_shape",
]
