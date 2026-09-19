"""Version determinism, lineage and diff.

The one property everything else rests on: same content + same lineage always
yields the same id, and anything different yields a different id.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.schema import DatasetKind, PriceBasis, content_checksum
from fiboki.data.versioning import (
    DatasetCatalogue,
    DatasetVersion,
    TransformationStep,
    VersionConflict,
    VersionNotFound,
    compute_version_id,
    diff_versions,
)
from tests.data_fixtures import make_bars

RAW_STEP = TransformationStep(
    operation="ingest_raw",
    parameters={"source": "histdata", "price_basis": "bid"},
    code_version="2.0.0",
)


def _version(frame: pd.DataFrame, *, lineage=(RAW_STEP,), **overrides) -> DatasetVersion:
    defaults = dict(
        content_checksum=content_checksum(frame),
        lineage=tuple(lineage),
        instrument=str(frame["instrument"].iloc[0]),
        timeframe=Timeframe(str(frame["timeframe"].iloc[0])),
        price_basis=PriceBasis(str(frame["price_basis"].iloc[0])),
        kind=DatasetKind.RAW,
        quality=DataQuality.RAW,
        row_count=len(frame),
        first_timestamp=frame.index.min(),
        last_timestamp=frame.index.max(),
        storage_path="/tmp/does-not-matter",
        source="histdata",
    )
    defaults.update(overrides)
    return DatasetVersion(**defaults)  # type: ignore[arg-type]


# ------------------------------------------------------- determinism


def test_same_content_same_lineage_gives_the_same_id():
    a = _version(make_bars(seed=3))
    b = _version(make_bars(seed=3))
    assert a.version_id == b.version_id


def test_id_does_not_depend_on_creation_time():
    frame = make_bars()
    early = _version(frame, created_at=pd.Timestamp("2020-01-01", tz="UTC").to_pydatetime())
    late = _version(frame, created_at=pd.Timestamp("2030-01-01", tz="UTC").to_pydatetime())
    assert early.version_id == late.version_id


def test_different_content_gives_a_different_id():
    a = _version(make_bars(seed=3))
    b = _version(make_bars(seed=4))
    assert a.version_id != b.version_id


def test_one_changed_price_changes_the_id():
    frame = make_bars()
    tweaked = frame.copy()
    tweaked.iloc[10, tweaked.columns.get_loc("close")] += 1e-8
    assert _version(frame).version_id != _version(tweaked).version_id


def test_same_content_different_lineage_gives_a_different_id():
    """Bytes alone are not identity: how you got them is part of the answer."""
    frame = make_bars()
    via_a = _version(frame, lineage=(RAW_STEP,))
    other = TransformationStep(
        operation="ingest_raw",
        parameters={"source": "dukascopy", "price_basis": "bid"},
        code_version="2.0.0",
    )
    via_b = _version(frame, lineage=(other,))
    assert via_a.content_checksum == via_b.content_checksum
    assert via_a.version_id != via_b.version_id


def test_parameter_order_does_not_affect_the_id():
    frame = make_bars()
    a = TransformationStep(operation="x", parameters={"a": 1, "b": 2})
    b = TransformationStep(operation="x", parameters={"b": 2, "a": 1})
    assert _version(frame, lineage=(a,)).version_id == _version(frame, lineage=(b,)).version_id


def test_compute_version_id_is_pure():
    assert compute_version_id("abc", (RAW_STEP,)) == compute_version_id("abc", (RAW_STEP,))
    assert compute_version_id("abc", (RAW_STEP,)) != compute_version_id("abd", (RAW_STEP,))


# --------------------------------------------------------- catalogue


@pytest.fixture
def catalogue(tmp_path):
    cat = DatasetCatalogue(tmp_path / "catalogue.db")
    yield cat
    cat.close()


def test_register_and_resolve(catalogue):
    version = _version(make_bars())
    vid = catalogue.register(version)
    back = catalogue.resolve(vid)
    assert back.version_id == vid
    assert back.content_checksum == version.content_checksum
    assert back.price_basis is version.price_basis
    assert back.lineage == version.lineage


def test_resolve_unknown_id_raises_rather_than_returning_none(catalogue):
    with pytest.raises(VersionNotFound, match="unreproducible"):
        catalogue.resolve("ds_nope")


def test_register_is_idempotent(catalogue):
    version = _version(make_bars())
    assert catalogue.register(version) == catalogue.register(version)
    assert len(catalogue.list_versions()) == 1


def test_registering_a_conflicting_id_raises(catalogue):
    version = _version(make_bars())
    catalogue.register(version)
    # Forge a row with the same id but a different row count.
    liar = DatasetVersion(
        **{
            **{
                f: getattr(version, f)
                for f in (
                    "content_checksum", "lineage", "instrument", "timeframe",
                    "price_basis", "kind", "quality", "first_timestamp",
                    "last_timestamp", "storage_path", "source", "parent_ids",
                    "metadata", "integrity_report",
                )
            },
            "row_count": version.row_count + 1,
        }
    )
    assert liar.version_id == version.version_id
    with pytest.raises(VersionConflict):
        catalogue.register(liar)


def test_list_versions_filters(catalogue):
    eur = _version(make_bars(instrument="EURUSD"))
    xau = _version(make_bars(instrument="XAUUSD"), instrument="XAUUSD")
    catalogue.register(eur)
    catalogue.register(xau)
    assert len(catalogue.list_versions()) == 2
    assert len(catalogue.list_versions(instrument="XAUUSD")) == 1
    assert len(catalogue.list_versions(kind=DatasetKind.VALIDATED)) == 0


def test_find_by_checksum_shows_the_same_bytes_under_two_lineages(catalogue):
    frame = make_bars()
    a = _version(frame, lineage=(RAW_STEP,))
    b = _version(
        frame, lineage=(TransformationStep(operation="ingest_raw", parameters={"x": 1}),)
    )
    catalogue.register(a)
    catalogue.register(b)
    found = catalogue.find_by_checksum(content_checksum(frame))
    assert len(found) == 2


# ----------------------------------------------------------- lineage


def test_lineage_chain_walks_raw_to_features(catalogue):
    frame = make_bars()
    raw = _version(frame)
    catalogue.register(raw)

    validated = raw.with_step(
        TransformationStep(operation="validate", parameters={"calendar": "fx_24_5"}),
        kind=DatasetKind.VALIDATED,
        quality=DataQuality.VALIDATED,
    )
    catalogue.register(validated)

    h4 = make_bars(timeframe=Timeframe.H4, periods=100)
    resampled = DatasetVersion(
        content_checksum=content_checksum(h4),
        lineage=validated.lineage
        + (TransformationStep(operation="resample", parameters={"target": "H4"}),),
        instrument="EURUSD",
        timeframe=Timeframe.H4,
        price_basis=PriceBasis.MID,
        kind=DatasetKind.RESAMPLED,
        quality=DataQuality.VALIDATED,
        row_count=len(h4),
        first_timestamp=h4.index.min(),
        last_timestamp=h4.index.max(),
        storage_path="/tmp/x",
        source="histdata",
        parent_ids=(validated.version_id,),
    )
    catalogue.register(resampled)

    features = resampled.with_step(
        TransformationStep(operation="features", parameters={"set": "ichimoku"}),
        kind=DatasetKind.FEATURE,
    )
    catalogue.register(features)

    chain = catalogue.lineage_chain(features.version_id)
    assert [v.kind for v in chain] == [
        DatasetKind.RAW,
        DatasetKind.VALIDATED,
        DatasetKind.RESAMPLED,
        DatasetKind.FEATURE,
    ]
    assert chain[0].version_id == raw.version_id
    # An experiment holding only the last id can still reach the raw bytes.
    assert chain[0].storage_path


def test_children_edges(catalogue):
    raw = _version(make_bars())
    catalogue.register(raw)
    child = raw.with_step(
        TransformationStep(operation="validate"), kind=DatasetKind.VALIDATED
    )
    catalogue.register(child)
    kids = catalogue.children(raw.version_id)
    assert [k.version_id for k in kids] == [child.version_id]


# -------------------------------------------------------------- diff


def test_diff_identical_versions():
    v = _version(make_bars())
    d = diff_versions(v, v)
    assert d.identical
    assert d.same_content and d.same_lineage
    assert d.row_count_delta == 0


def test_diff_reports_content_and_row_changes():
    a = _version(make_bars(periods=400))
    b = _version(make_bars(periods=380, seed=99))
    d = diff_versions(a, b)
    assert not d.same_content
    assert d.row_count_delta == -20
    assert "content differs" in d.summary()


def test_diff_reports_lineage_divergence_point():
    frame = make_bars()
    base = _version(frame)
    left = base.with_step(TransformationStep(operation="validate"))
    right = base.with_step(TransformationStep(operation="repair"))
    d = diff_versions(left, right)
    assert not d.same_lineage
    assert d.lineage_diverges_at == 1
    assert d.left_only_steps == ("validate",)
    assert d.right_only_steps == ("repair",)


def test_diff_reports_price_basis_change():
    frame = make_bars()
    a = _version(frame)
    b = _version(frame, price_basis=PriceBasis.SYNTHETIC_MID)
    d = diff_versions(a, b)
    assert d.price_basis_changed == ("mid", "synthetic_mid")


def test_catalogue_diff_by_id(catalogue):
    a = _version(make_bars(seed=1))
    b = _version(make_bars(seed=2))
    catalogue.register(a)
    catalogue.register(b)
    d = catalogue.diff(a.version_id, b.version_id)
    assert not d.same_content
    assert d.to_dict()["left_id"] == a.version_id
