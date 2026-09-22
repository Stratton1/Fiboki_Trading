"""Migrating the real V1 HistData store.

These tests run against the actual V1 parquet files. They assert the two things
that matter about the migration: the timestamps come out corrected, and the
defects that are genuinely in the V1 data come out labelled rather than fixed.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.integrity import DefectCode, DirtyDataError
from fiboki.data.migrate_v1 import migrate
from fiboki.data.providers.histdata import (
    ABSENT_VOLUME,
    HistDataParquetProvider,
    detect_timestamp_convention,
)
from fiboki.data.resample import resample
from fiboki.data.schema import DatasetKind, PriceBasis
from fiboki.data.store import DataStore
from tests.data_fixtures import V1_HISTDATA_ROOT, requires_v1_data

pytestmark = requires_v1_data


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    root = tmp_path_factory.mktemp("v2store")
    store = DataStore.initialise(root)
    report = migrate(V1_HISTDATA_ROOT, root, store=store)
    yield report, store
    store.close()


def test_migration_finds_the_four_v1_datasets(migrated):
    report, _ = migrated
    assert len(report.entries) == 4
    assert not report.failed
    found = {(e.instrument, e.timeframe.value) for e in report.succeeded}
    assert found == {
        ("EURUSD", "H1"), ("EURUSD", "H4"), ("XAUUSD", "H1"), ("XAUUSD", "H4")
    }


def test_row_counts_match_the_source_files(migrated):
    report, _ = migrated
    counts = {(e.instrument, e.timeframe.value): e.row_count for e in report.succeeded}
    assert counts[("EURUSD", "H1")] == 155_808
    assert counts[("EURUSD", "H4")] == 40_541
    assert counts[("XAUUSD", "H1")] == 99_945
    assert counts[("XAUUSD", "H4")] == 26_837
    assert report.total_rows == 323_131


def test_timestamps_are_shifted_five_hours_into_true_utc(migrated):
    """The V1 file says 17:00 UTC; the bar actually happened at 22:00 UTC."""
    report, _ = migrated
    eur_h1 = next(
        e for e in report.succeeded
        if e.instrument == "EURUSD" and e.timeframe is Timeframe.H1
    )
    raw = pd.read_parquet(
        V1_HISTDATA_ROOT / "EURUSD" / "eurusd_h1.parquet"
    )
    assert raw.index.min() == pd.Timestamp("2000-05-30 17:00", tz="UTC")
    assert eur_h1.first_timestamp == pd.Timestamp("2000-05-30 22:00", tz="UTC")
    assert eur_h1.last_timestamp == pd.Timestamp("2025-12-31 21:00", tz="UTC")
    assert eur_h1.timestamp_shift_hours == 5


def test_the_v1_store_really_is_on_a_fixed_offset_clock():
    """Evidence, not assertion: one weekly-open hour means no DST."""
    raw = pd.read_parquet(V1_HISTDATA_ROOT / "XAUUSD" / "xauusd_h1.parquet")
    evidence = detect_timestamp_convention(raw.index)
    assert evidence.looks_fixed_offset
    assert evidence.distinct_open_hours == 1
    # Gold reopens at 18:00 EST, after the 17:00-18:00 settlement break, so the
    # dominant weekly-open label is 18 rather than FX's 17. What matters is that
    # there is exactly ONE such hour year-round, which only a fixed offset gives.
    assert evidence.dominant_open_hour == 18


def test_price_basis_is_recorded_as_bid_not_mid(migrated):
    _, store = migrated
    for sym, tf in [("EURUSD", Timeframe.H1), ("XAUUSD", Timeframe.H4)]:
        found = store.locate(sym, tf, kind=DatasetKind.VALIDATED)
        assert found.version.price_basis is PriceBasis.BID
        assert found.metadata.price_basis is PriceBasis.BID
        frame = store.read(found.version_id, allow_suspect=True)
        assert set(frame["price_basis"]) == {"bid"}


def test_the_timezone_adjustment_is_declared_in_the_metadata(migrated):
    _, store = migrated
    found = store.locate("EURUSD", Timeframe.H1, kind=DatasetKind.VALIDATED)
    kinds = {a.kind for a in found.metadata.adjustments}
    assert kinds == {"timezone_correction", "price_basis_declaration"}
    tz_adj = next(a for a in found.metadata.adjustments if a.kind == "timezone_correction")
    assert tz_adj.parameters["offset_applied_hours"] == 5
    assert "no DST" in found.metadata.timezone_of_origin
    assert "fixed-offset" in tz_adj.parameters["evidence"]


def test_absent_volume_is_stored_as_a_marker_and_flagged(migrated):
    report, store = migrated
    for entry in report.succeeded:
        assert any("absent marker" in w for w in entry.warnings)
        assert entry.report.has(DefectCode.VOLUME_ALWAYS_ZERO)
    found = store.locate("EURUSD", Timeframe.H1, kind=DatasetKind.VALIDATED)
    frame = store.read(found.version_id, allow_suspect=True)
    assert (frame["volume"] == ABSENT_VOLUME).all()


def test_the_negative_price_bar_is_found_not_fixed(migrated):
    """EURUSD 2001-09-11: OHLC all -0.0001 in the V1 'canonical' store."""
    report, store = migrated
    eur_h1 = next(
        e for e in report.succeeded
        if e.instrument == "EURUSD" and e.timeframe is Timeframe.H1
    )
    defect = eur_h1.report.by_code(DefectCode.NON_POSITIVE_PRICE)
    assert defect is not None
    assert defect.count == 1
    assert defect.first_timestamp == pd.Timestamp("2001-09-12 01:00", tz="UTC")
    assert eur_h1.quality is DataQuality.REJECTED

    # It is still in the stored bytes, exactly as the source had it.
    found = store.locate("EURUSD", Timeframe.H1, kind=DatasetKind.VALIDATED)
    frame = store.read(found.version_id, allow_suspect=True)
    bad = frame.loc[frame["close"] <= 0]
    assert len(bad) == 1
    assert bad["close"].iloc[0] == pytest.approx(-0.0001)


def test_eurusd_cannot_be_read_clean_but_xauusd_can(migrated):
    _, store = migrated
    with pytest.raises(DirtyDataError):
        store.read_latest("EURUSD", Timeframe.H1)
    frame, version = store.read_latest("XAUUSD", Timeframe.H1)
    assert len(frame) == 99_945
    assert version.quality is DataQuality.VALIDATED


def test_off_session_bars_are_reported_for_both_instruments(migrated):
    """HistData's fixed clock puts bars an hour outside the real session for
    the eight months of the year when New York observes DST."""
    report, _ = migrated
    for entry in report.succeeded:
        defect = entry.report.by_code(DefectCode.OFF_SESSION_BAR)
        assert defect is not None, f"{entry.instrument} {entry.timeframe.value}"
        # Metals are worse affected than FX because the settlement break is a
        # second boundary the fixed clock can land on the wrong side of: XAUUSD
        # H4 runs ~14%, EURUSD H1 ~1%.
        assert 0 < defect.detail["fraction"] < 0.20


def test_h4_bars_are_flagged_as_off_the_epoch_grid(migrated):
    """H4 bars anchored to 00:00 EST land at 05:00/09:00/... UTC, never on the
    epoch 4h grid. Silently resampling them onto it would misalign every bar."""
    report, _ = migrated
    for entry in report.succeeded:
        defect = entry.report.by_code(DefectCode.MISALIGNED_BAR_START)
        if entry.timeframe is Timeframe.H4:
            assert defect is not None
            assert defect.count == entry.row_count
        else:
            assert defect is None


def test_gaps_are_classified_against_a_session_calendar(migrated):
    report, _ = migrated
    for entry in report.succeeded:
        expected = [g for g in entry.report.gaps if g.expected]
        unexpected = [g for g in entry.report.gaps if not g.expected]
        assert expected, "weekends must be recognised as expected gaps"
        assert unexpected, "the V1 store genuinely has holes"
        # The weekend gaps outnumber nothing in particular, but they must exist
        # and must not be counted as missing bars.
        assert all(g.missing_bars == 0 for g in expected)
        assert all(g.missing_bars > 0 for g in unexpected)


def test_raw_and_canonical_are_separate_versions_with_lineage(migrated):
    report, store = migrated
    for entry in report.succeeded:
        assert entry.raw_version_id != entry.canonical_version_id
        chain = store.catalogue.lineage_chain(entry.canonical_version_id)
        assert [v.kind for v in chain] == [DatasetKind.RAW, DatasetKind.VALIDATED]
        assert chain[-1].lineage[-1].operation == "validate"
        assert chain[-1].lineage[-1].parameters["repairs_applied"] == []
        assert store.verify_immutable(entry.raw_version_id)


def test_the_integrity_report_is_persisted_next_to_the_data(migrated):
    _, store = migrated
    found = store.locate("EURUSD", Timeframe.H1, kind=DatasetKind.VALIDATED)
    stored = json.loads((found.path / "_integrity.json").read_text())
    assert stored["row_count"] == 155_808
    assert stored["calendar_name"] == "fx_24_5"
    assert stored["is_clean"] is False
    assert stored["unexpected_gap_count"] > 0


def test_migration_is_reproducible(tmp_path):
    """Same source, same code -> same version ids. That is the whole point."""
    a_root = tmp_path / "a"
    b_root = tmp_path / "b"
    a = migrate(
        V1_HISTDATA_ROOT, a_root, instruments=["XAUUSD"], timeframes=[Timeframe.H4]
    )
    b = migrate(
        V1_HISTDATA_ROOT, b_root, instruments=["XAUUSD"], timeframes=[Timeframe.H4]
    )
    assert [e.raw_version_id for e in a.succeeded] == [
        e.raw_version_id for e in b.succeeded
    ]
    assert [e.canonical_version_id for e in a.succeeded] == [
        e.canonical_version_id for e in b.succeeded
    ]


def test_migrated_bars_can_be_resampled_and_traced_back(migrated):
    _, store = migrated
    found = store.locate("XAUUSD", Timeframe.H1, kind=DatasetKind.VALIDATED)
    h1 = store.read(found.version_id)
    h4 = resample(h1, Timeframe.H4)
    assert len(h4) < len(h1)
    assert set(h4["price_basis"]) == {"bid"}, "resampling must not launder the basis"
    assert (h4["high"] >= h4["low"]).all()


def test_provider_lists_exactly_what_is_on_disk():
    provider = HistDataParquetProvider(V1_HISTDATA_ROOT)
    available = set(provider.available())
    assert available == {
        ("EURUSD", Timeframe.H1), ("EURUSD", Timeframe.H4),
        ("XAUUSD", Timeframe.H1), ("XAUUSD", Timeframe.H4),
    }


def test_render_produces_an_operator_readable_report(migrated):
    report, _ = migrated
    text = report.render()
    assert "EURUSD" in text and "XAUUSD" in text
    assert "CRITICAL non_positive_price" in text
    assert "quality=rejected" in text
