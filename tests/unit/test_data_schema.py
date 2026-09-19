"""Canonical schema: price basis is mandatory, naive timestamps are refused."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.data.schema import (
    ALL_COLUMNS,
    BarDatasetMetadata,
    PriceBasis,
    SchemaError,
    canonical_frame,
    content_checksum,
    describe_frame,
    frame_from_arrow,
    frame_to_arrow,
    validate_frame_shape,
)
from tests.data_fixtures import make_bars


def test_canonical_frame_stamps_identity_columns():
    frame = make_bars(instrument="eurusd", price_basis=PriceBasis.BID)
    assert set(frame["instrument"]) == {"EURUSD"}
    assert set(frame["timeframe"]) == {"H1"}
    assert set(frame["price_basis"]) == {"bid"}


def test_price_basis_is_not_optional():
    """A bar frame cannot exist without saying which side of the book it is."""
    frame = make_bars()
    stripped = frame.drop(columns=["price_basis"])
    with pytest.raises(SchemaError, match="missing required columns"):
        validate_frame_shape(stripped)


def test_naive_index_is_refused_not_assumed_utc():
    """The HistData bug, made impossible: no silent tz assumption."""
    idx = pd.date_range("2020-01-01", periods=10, freq="h")  # naive
    raw = pd.DataFrame(
        {"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}, index=idx
    )
    with pytest.raises(SchemaError, match="timezone-naive"):
        canonical_frame(
            raw, instrument="EURUSD", timeframe=Timeframe.H1, price_basis=PriceBasis.MID
        )


def test_non_utc_index_is_converted_to_utc():
    idx = pd.date_range("2020-06-01", periods=10, freq="h", tz="America/New_York")
    raw = pd.DataFrame(
        {"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}, index=idx
    )
    frame = canonical_frame(
        raw, instrument="EURUSD", timeframe=Timeframe.H1, price_basis=PriceBasis.MID
    )
    assert str(frame.index.tz) == "UTC"
    assert frame.index[0] == pd.Timestamp("2020-06-01 04:00", tz="UTC")


def test_mixed_price_basis_is_refused():
    frame = make_bars()
    mixed = frame.copy()
    mixed.loc[mixed.index[0], "price_basis"] = "bid"
    with pytest.raises(SchemaError, match="mixes price bases"):
        validate_frame_shape(mixed)


def test_arrow_roundtrip_preserves_everything():
    frame = make_bars(price_basis=PriceBasis.SYNTHETIC_MID)
    table = frame_to_arrow(frame)
    back = frame_from_arrow(table)
    pd.testing.assert_frame_equal(frame, back)
    assert content_checksum(frame) == content_checksum(back)


def test_checksum_is_deterministic_and_content_sensitive():
    a = make_bars(seed=1)
    b = make_bars(seed=1)
    assert content_checksum(a) == content_checksum(b)

    c = a.copy()
    c.iloc[0, c.columns.get_loc("close")] += 1e-9
    assert content_checksum(c) != content_checksum(a)


def test_checksum_ignores_index_storage_unit():
    """A checksum must not change because pandas stored ns instead of us."""
    frame = make_bars()
    other = frame.copy()
    other.index = other.index.as_unit("ns")
    assert content_checksum(frame) == content_checksum(other)


def test_checksum_is_sensitive_to_price_basis():
    """Bid bars and mid bars with identical numbers are different datasets."""
    bid = make_bars(price_basis=PriceBasis.BID)
    mid = bid.copy()
    mid["price_basis"] = PriceBasis.MID.value
    assert content_checksum(bid) != content_checksum(mid)


def test_metadata_json_roundtrip():
    frame = make_bars()
    meta = describe_frame(
        frame,
        source="histdata",
        source_identifier="/path/to/file.parquet",
        timezone_of_origin="EST-fixed (UTC-05:00, no DST)",
    )
    back = BarDatasetMetadata.from_json(meta.to_json())
    assert back.checksum == meta.checksum
    assert back.timezone_of_origin == meta.timezone_of_origin
    assert back.price_basis is meta.price_basis
    assert back.first_timestamp == meta.first_timestamp


def test_unknown_columns_are_dropped_not_smuggled_through():
    frame = make_bars()
    dirty = frame.copy()
    dirty["my_secret_feature"] = np.arange(len(dirty))
    cleaned = canonical_frame(
        dirty, instrument="EURUSD", timeframe=Timeframe.H1, price_basis=PriceBasis.MID
    )
    assert "my_secret_feature" not in cleaned.columns
    assert all(c in ALL_COLUMNS for c in cleaned.columns)
