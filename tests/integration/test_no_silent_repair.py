"""The no-silent-repair guarantee, end to end.

The claim under test: a dirty dataset cannot be read as clean without an
explicit, logged repair step that produces a new dataset version. There is no
code path that quietly fixes anything, and a caller who wants dirty data has to
say so in code.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.integrity import (
    DefectCode,
    DirtyDataError,
    RepairAction,
    RepairPlan,
    repair,
    validate,
)
from fiboki.data.schema import DatasetKind, content_checksum, describe_frame
from fiboki.data.store import DataStore
from fiboki.data.versioning import TransformationStep
from tests.data_fixtures import make_bars, make_metadata


def _freeze(frame, start, stop):
    """Freeze a whole bar range to one price: a flat bar is still a valid bar.

    Freezing only the close would also create impossible geometry, which would
    make a staleness test secretly a geometry test.
    """
    price = frame["close"].iloc[start]
    for col in ("open", "high", "low", "close"):
        frame.iloc[start:stop, frame.columns.get_loc(col)] = price
    return frame


def dirty_bars() -> pd.DataFrame:
    """Synthetic bars with the exact defects the V1 store actually has.

    A sentinel negative-price bar (EURUSD 2001-09-11 in the real store), a
    frozen close run, and an impossible high/low.
    """
    frame = make_bars(periods=400, seed=7)
    for col in ("open", "high", "low", "close"):
        frame.iloc[100, frame.columns.get_loc(col)] = -0.0001
    frame.iloc[200, frame.columns.get_loc("high")] = frame["low"].iloc[200] - 0.01
    _freeze(frame, 300, 320)
    return frame


def _ingest(store: DataStore, frame: pd.DataFrame):
    """Write raw, validate, register canonical carrying the verdict."""
    raw = store.write_raw(frame, make_metadata(frame))
    report = validate(frame)
    meta = describe_frame(
        frame,
        source="test",
        source_identifier="dirty",
        timezone_of_origin="UTC",
        quality=report.quality,
        kind=DatasetKind.VALIDATED,
        gaps=report.gaps,
    )
    canonical = store.write_canonical(
        frame,
        meta,
        source_version=raw.version,
        transformation=TransformationStep(
            operation="validate", parameters={"repairs_applied": []}
        ),
        integrity=report,
    )
    return raw, canonical, report


# ------------------------------------------------ the core guarantee


def test_a_dirty_dataset_cannot_be_read_as_clean(store):
    frame = dirty_bars()
    _, canonical, report = _ingest(store, frame)

    assert not report.is_clean
    assert report.quality is DataQuality.REJECTED

    with pytest.raises(DirtyDataError) as exc:
        store.read(canonical.version_id)
    assert "will not silently repair" in str(exc.value)
    assert "explicit action list" in str(exc.value)


def test_read_latest_is_gated_too(store):
    frame = dirty_bars()
    _ingest(store, frame)
    with pytest.raises(DirtyDataError):
        store.read_latest("EURUSD", Timeframe.H1)


def test_dirty_data_can_be_read_only_by_saying_so(store):
    frame = dirty_bars()
    _, canonical, _ = _ingest(store, frame)
    got = store.read(canonical.version_id, allow_suspect=True)
    # It comes back exactly as stored — still dirty, nothing fixed on the way out.
    assert content_checksum(got) == content_checksum(frame)
    assert (got[["open", "high", "low", "close"]] <= 0).any(axis=1).sum() == 1


def test_validation_did_not_change_the_stored_bytes(store):
    frame = dirty_bars()
    before = content_checksum(frame)
    raw, canonical, _ = _ingest(store, frame)
    assert store.catalogue.resolve(raw.version_id).content_checksum == before
    assert store.catalogue.resolve(canonical.version_id).content_checksum == before


def test_the_stored_integrity_report_names_every_defect(store):
    frame = dirty_bars()
    _, canonical, _ = _ingest(store, frame)
    stored = json.loads((canonical.path / "_integrity.json").read_text())
    codes = {d["code"] for d in stored["defects"]}
    assert DefectCode.NON_POSITIVE_PRICE.value in codes
    assert DefectCode.IMPOSSIBLE_BAR.value in codes
    assert DefectCode.STALE_RUN.value in codes
    assert stored["is_clean"] is False
    assert stored["quality"] == "rejected"


# -------------------------------------------- the explicit repair path


def test_repair_creates_a_new_version_and_leaves_the_dirty_one_intact(store):
    frame = dirty_bars()
    raw, canonical, report = _ingest(store, frame)

    plan = RepairPlan(
        actions=(
            RepairAction.DROP_NON_POSITIVE,
            RepairAction.DROP_IMPOSSIBLE_BARS,
        ),
        reason=(
            "two sentinel bars confirmed against a second source; the stale run "
            "is real market behaviour and is deliberately NOT repaired"
        ),
        actor="joe",
    )
    result = repair(frame, plan)
    clean_report = validate(result.frame)
    assert clean_report.is_clean

    repaired_meta = describe_frame(
        result.frame,
        source="test",
        source_identifier="dirty",
        timezone_of_origin="UTC",
        quality=clean_report.quality,
        kind=DatasetKind.REPAIRED,
        repairs=result.records,
        gaps=clean_report.gaps,
    )
    repaired = store.write_canonical(
        result.frame,
        repaired_meta,
        source_version=canonical.version,
        transformation=TransformationStep(
            operation="repair",
            parameters={
                "actions": [a.value for a in plan.actions],
                "reason": plan.reason,
                "actor": plan.actor,
            },
            code_version="2.0.0",
            inputs=(canonical.version_id,),
        ),
        integrity=clean_report,
        kind=DatasetKind.REPAIRED,
    )

    # A different version, readable without allow_suspect.
    assert repaired.version_id != canonical.version_id
    clean = store.read(repaired.version_id)
    assert len(clean) == len(frame) - 2

    # The dirty version is still there, still refused, still resolvable.
    with pytest.raises(DirtyDataError):
        store.read(canonical.version_id)
    assert len(store.read(canonical.version_id, allow_suspect=True)) == len(frame)

    # The lineage records what was done and why.
    chain = store.catalogue.lineage_chain(repaired.version_id)
    assert [v.kind for v in chain] == [
        DatasetKind.RAW, DatasetKind.VALIDATED, DatasetKind.REPAIRED
    ]
    step = chain[-1].lineage[-1]
    assert step.operation == "repair"
    assert step.parameters["actor"] == "joe"
    assert "second source" in step.parameters["reason"]

    # And the metadata carries the row-level audit trail.
    stored_meta = json.loads((repaired.path / "_dataset.json").read_text())
    repairs = stored_meta["dataset"]["repairs"]
    assert [r["action"] for r in repairs] == [
        "drop_non_positive", "drop_impossible_bars"
    ]
    assert sum(r["rows_before"] - r["rows_after"] for r in repairs) == 2
    assert all(r["affected_timestamps"] for r in repairs)


def test_a_partial_repair_still_cannot_be_read_as_clean(store):
    """Fixing one defect does not launder the others."""
    frame = dirty_bars()
    _, canonical, _ = _ingest(store, frame)

    plan = RepairPlan(
        actions=(RepairAction.DROP_NON_POSITIVE,),
        reason="only the sentinel bar has been verified so far",
        actor="joe",
    )
    result = repair(frame, plan)
    report = validate(result.frame)
    assert not report.is_clean
    assert report.has(DefectCode.IMPOSSIBLE_BAR)

    meta = describe_frame(
        result.frame,
        source="test",
        source_identifier="dirty",
        timezone_of_origin="UTC",
        quality=report.quality,
        kind=DatasetKind.REPAIRED,
        repairs=result.records,
    )
    partial = store.write_canonical(
        result.frame,
        meta,
        source_version=canonical.version,
        transformation=TransformationStep(operation="repair"),
        integrity=report,
        kind=DatasetKind.REPAIRED,
    )
    with pytest.raises(DirtyDataError):
        store.read(partial.version_id)


def test_repairing_the_same_data_twice_is_deterministic(store):
    frame = dirty_bars()
    plan = RepairPlan(
        actions=(RepairAction.DROP_NON_POSITIVE, RepairAction.DROP_IMPOSSIBLE_BARS),
        reason="r",
        actor="a",
    )
    a = repair(frame, plan)
    b = repair(frame, plan)
    assert content_checksum(a.frame) == content_checksum(b.frame)


def test_clean_data_needs_no_ceremony(store):
    """The gate must not get in the way of data that is actually fine."""
    frame = make_bars(periods=300)
    _, canonical, report = _ingest(store, frame)
    assert report.is_clean
    back = store.read(canonical.version_id)
    pd.testing.assert_frame_equal(back, frame)


def test_warnings_alone_do_not_block_a_read(store):
    """A stale run is worth flagging; it is not grounds for refusing the data."""
    frame = _freeze(make_bars(periods=300), 50, 70)
    _, canonical, report = _ingest(store, frame)
    assert report.has(DefectCode.STALE_RUN)
    assert report.is_clean  # WARNING, not ERROR
    assert len(store.read(canonical.version_id)) == 300
