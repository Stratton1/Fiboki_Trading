"""The store: explicit root, immutable raw, partitioned reads, loud misses.

The first group of tests is about the V1 path-resolution bug. They assert that
every way of pointing the store at the wrong place fails immediately and
loudly, rather than succeeding and returning nothing.
"""
from __future__ import annotations

import stat
from pathlib import Path

import pandas as pd
import pytest

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.integrity import IntegrityConfig, validate
from fiboki.data.resample import ResampleSpec, resample
from fiboki.data.schema import DatasetKind, PriceBasis, content_checksum, describe_frame
from fiboki.data.store import (
    ROOT_MARKER,
    ChecksumMismatch,
    DataRootNotFound,
    DatasetNotFound,
    DataStore,
    resolve_root,
)
from fiboki.data.versioning import TransformationStep
from tests.data_fixtures import make_bars, make_metadata

# ============================================= V1 path-resolution bug


def test_no_root_configured_raises(monkeypatch):
    monkeypatch.delenv("FIBOKI_DATA_ROOT", raising=False)
    with pytest.raises(DataRootNotFound, match="never searches"):
        resolve_root(None)


def test_env_var_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("FIBOKI_DATA_ROOT", str(tmp_path))
    assert resolve_root(None) == tmp_path.resolve()


def test_missing_root_raises_instead_of_being_created(tmp_path):
    with pytest.raises(DataRootNotFound, match="does not exist"):
        DataStore(tmp_path / "not-here")


def test_an_unmarked_directory_is_rejected(tmp_path):
    """A plausible-looking directory that was never initialised is not a root.

    This is the exact V1 failure: the resolver found *a* directory, it happened
    to exist, reads returned nothing, and an entire research batch recorded
    no_data and checkpointed it as done.
    """
    plausible = tmp_path / "data" / "canonical"
    (plausible / "EURUSD" / "H1").mkdir(parents=True)
    with pytest.raises(DataRootNotFound, match=ROOT_MARKER):
        DataStore(plausible)


def test_the_store_never_walks_up_the_tree(tmp_path):
    """A real root one level up must NOT rescue a wrong path."""
    real = DataStore.initialise(tmp_path / "real")
    real.close()
    wrong = tmp_path / "real" / "raw"
    assert wrong.exists()  # it is a real directory inside a real root, just not a root
    with pytest.raises(DataRootNotFound):
        DataStore(wrong)


def test_a_file_is_not_a_root(tmp_path):
    path = tmp_path / "root"
    path.write_text("not a directory")
    with pytest.raises(DataRootNotFound, match="not a directory"):
        DataStore(path)


def test_missing_dataset_raises_with_the_paths_it_checked(store):
    with pytest.raises(DatasetNotFound) as exc:
        store.locate("EURUSD", Timeframe.H1)
    message = str(exc.value)
    assert "data root:" in message
    assert "expected under:" in message
    assert "No other location was searched" in message


def test_read_latest_of_an_absent_dataset_is_an_error_not_an_empty_frame(store):
    with pytest.raises(DatasetNotFound, match="do not record it as a completed"):
        store.read_latest("GBPUSD", Timeframe.H4)


# ============================================================ raw layer


def test_write_and_read_raw_roundtrip(store):
    frame = make_bars(periods=500)
    stored = store.write_raw(frame, make_metadata(frame))
    back = store.read(stored.version_id, verify_checksum=True)
    pd.testing.assert_frame_equal(frame, back)
    assert stored.version.kind is DatasetKind.RAW


def test_raw_files_are_written_read_only(store):
    frame = make_bars(periods=100)
    stored = store.write_raw(frame, make_metadata(frame))
    parquets = list(stored.path.rglob("*.parquet"))
    assert parquets
    for f in parquets:
        # Check the mode bits, not os.access: the test process may be root,
        # and root ignores permission bits.
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode & 0o222 == 0, f"{f} has write bits {oct(mode)}; RAW must be immutable"


def test_rewriting_identical_raw_content_is_a_verified_noop(store):
    frame = make_bars(periods=100)
    a = store.write_raw(frame, make_metadata(frame))
    b = store.write_raw(frame, make_metadata(frame))
    assert a.version_id == b.version_id
    assert store.verify_immutable(a.version_id)
    assert len(store.catalogue.list_versions(kind=DatasetKind.RAW)) == 1


def test_reingesting_different_content_creates_a_new_version_not_an_overwrite(store):
    first = make_bars(periods=100, seed=1)
    second = make_bars(periods=120, seed=1)  # extended history
    a = store.write_raw(first, make_metadata(first))
    b = store.write_raw(second, make_metadata(second))
    assert a.version_id != b.version_id
    # The original bytes are still resolvable.
    assert len(store.read(a.version_id)) == 100
    assert len(store.read(b.version_id)) == 120


def test_verify_immutable_detects_tampering(store):
    frame = make_bars(periods=100)
    stored = store.write_raw(frame, make_metadata(frame))
    parquet = next(stored.path.rglob("*.parquet"))
    parquet.chmod(0o644)
    tampered = frame.copy()
    tampered.iloc[0, tampered.columns.get_loc("close")] = 9.9
    import pyarrow.parquet as pq

    from fiboki.data.schema import frame_to_arrow

    table = frame_to_arrow(tampered)
    pq.write_table(table, parquet)
    with pytest.raises(Exception):  # noqa: B017 - schema or checksum, both are failures
        store.verify_immutable(stored.version_id)


def test_metadata_checksum_mismatch_is_refused(store):
    frame = make_bars(periods=50)
    meta = make_metadata(frame)
    other = make_bars(periods=50, seed=99)
    with pytest.raises(ChecksumMismatch):
        store.write_raw(other, meta)


# ====================================================== canonical layer


def test_canonical_is_derived_with_recorded_lineage(store):
    frame = make_bars(periods=300)
    raw = store.write_raw(frame, make_metadata(frame))
    report = validate(frame)
    meta = describe_frame(
        frame,
        source="test",
        source_identifier="synthetic",
        timezone_of_origin="UTC",
        quality=report.quality,
        kind=DatasetKind.VALIDATED,
    )
    step = TransformationStep(
        operation="validate",
        parameters={"integrity_config": IntegrityConfig().to_dict()},
        code_version="2.0.0",
        inputs=(raw.version_id,),
    )
    canonical = store.write_canonical(
        frame, meta, source_version=raw.version, transformation=step, integrity=report
    )
    assert canonical.version.parent_ids == (raw.version_id,)
    chain = store.catalogue.lineage_chain(canonical.version_id)
    assert [v.kind for v in chain] == [DatasetKind.RAW, DatasetKind.VALIDATED]
    assert (canonical.path / "_integrity.json").exists()


def test_resampled_dataset_extends_the_lineage(store):
    m1 = make_bars(timeframe=Timeframe.M1, periods=2880)
    raw = store.write_raw(m1, make_metadata(m1))

    h4 = resample(m1, Timeframe.H4)
    spec = ResampleSpec(source=Timeframe.M1, target=Timeframe.H4)
    meta = describe_frame(
        h4,
        source="test",
        source_identifier="synthetic",
        timezone_of_origin="UTC",
        quality=DataQuality.VALIDATED,
        kind=DatasetKind.RESAMPLED,
    )
    stored = store.write_canonical(
        h4,
        meta,
        source_version=raw.version,
        transformation=spec.to_step(),
        kind=DatasetKind.RESAMPLED,
    )
    chain = store.catalogue.lineage_chain(stored.version_id)
    assert [v.timeframe for v in chain] == [Timeframe.M1, Timeframe.H4]
    assert chain[-1].lineage[-1].parameters["target"] == "H4"

    # An experiment holding only this id can re-resolve the exact M1 bytes.
    source_frame = store.read(chain[0].version_id)
    assert content_checksum(source_frame) == chain[0].content_checksum


def test_canonical_write_refuses_to_forge_a_raw_dataset(store):
    frame = make_bars(periods=50)
    raw = store.write_raw(frame, make_metadata(frame))
    meta = make_metadata(frame, kind=DatasetKind.RAW)
    with pytest.raises(Exception, match="use write_raw"):
        store.write_canonical(
            frame,
            meta,
            source_version=raw.version,
            transformation=TransformationStep(operation="x"),
            kind=DatasetKind.RAW,
        )


# ====================================================== partitioned read


def _multi_year_h4(periods: int = 4400) -> pd.DataFrame:
    """H4 bars spanning 2019-2021, canonically shaped."""
    return make_bars(
        timeframe=Timeframe.H4, periods=periods, start="2019-06-01 00:00"
    )


def test_year_partitions_are_created(store):
    frame = _multi_year_h4()
    stored = store.write_raw(frame, make_metadata(frame))
    years = sorted(p.name for p in stored.path.glob("year=*"))
    assert years == ["year=2019", "year=2020", "year=2021"]


def test_date_range_read_returns_only_the_requested_window(store):
    frame = _multi_year_h4()
    stored = store.write_raw(frame, make_metadata(frame))

    window = store.read(
        stored.version_id, start="2020-01-01", end="2020-12-31 23:59"
    )
    assert window.index.min() >= pd.Timestamp("2020-01-01", tz="UTC")
    assert window.index.max() <= pd.Timestamp("2020-12-31 23:59", tz="UTC")
    expected = frame[(frame.index.year == 2020)]
    assert len(window) == len(expected)
    pd.testing.assert_frame_equal(window, expected)


def test_naive_range_bounds_are_interpreted_as_utc(store):
    frame = make_bars(periods=400)
    stored = store.write_raw(frame, make_metadata(frame))
    a = store.read(stored.version_id, start="2026-01-05 05:00")
    b = store.read(stored.version_id, start=pd.Timestamp("2026-01-05 05:00", tz="UTC"))
    pd.testing.assert_frame_equal(a, b)


def test_read_preserves_price_basis_through_parquet(store):
    frame = make_bars(periods=100, price_basis=PriceBasis.BID)
    stored = store.write_raw(frame, make_metadata(frame))
    back = store.read(stored.version_id)
    assert set(back["price_basis"]) == {"bid"}


# ============================================================ inventory


def test_inventory_lists_everything(store):
    for sym in ("EURUSD", "XAUUSD"):
        frame = make_bars(instrument=sym, periods=100)
        store.write_raw(frame, make_metadata(frame))
    inv = store.inventory()
    assert len(inv) == 2
    assert set(inv["instrument"]) == {"EURUSD", "XAUUSD"}
    assert set(inv["kind"]) == {"raw"}


def test_initialise_is_idempotent(tmp_path: Path):
    a = DataStore.initialise(tmp_path / "s")
    frame = make_bars(periods=50)
    stored = a.write_raw(frame, make_metadata(frame))
    a.close()
    b = DataStore.initialise(tmp_path / "s")
    assert b.catalogue.exists(stored.version_id)
    b.close()
