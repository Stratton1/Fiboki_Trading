"""Integrity: construct each defect deliberately, assert it is caught.

Every test here builds a specific kind of dirty data and asserts both that the
defect is detected and that ``validate`` did not quietly fix it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.calendars import CRYPTO_CALENDAR, FX_CALENDAR
from fiboki.data.integrity import (
    DefectCode,
    DirtyDataError,
    IntegrityConfig,
    RepairAction,
    RepairPlan,
    Severity,
    assert_clean,
    repair,
    validate,
)
from fiboki.data.schema import PriceBasis, content_checksum
from tests.data_fixtures import make_bars


def test_clean_data_is_clean():
    report = validate(make_bars())
    assert report.is_clean
    assert report.quality is DataQuality.VALIDATED
    assert not report.blocking_defects


# ------------------------------------------------------ impossible bars


@pytest.mark.parametrize(
    "column,delta",
    [("high", -0.05), ("low", 0.05)],
    ids=["high_below_body", "low_above_body"],
)
def test_impossible_bar_geometry_is_detected(column, delta):
    frame = make_bars()
    frame.iloc[10, frame.columns.get_loc(column)] += delta
    report = validate(frame)
    defect = report.by_code(DefectCode.IMPOSSIBLE_BAR)
    assert defect is not None
    assert defect.count == 1
    assert defect.severity is Severity.ERROR
    assert frame.index[10] in defect.sample_timestamps
    assert not report.is_clean


def test_high_below_low_is_detected():
    frame = make_bars()
    i = 5
    frame.iloc[i, frame.columns.get_loc("high")] = 1.0
    frame.iloc[i, frame.columns.get_loc("low")] = 2.0
    report = validate(frame)
    assert report.has(DefectCode.IMPOSSIBLE_BAR)


def test_non_positive_price_is_critical_and_rejects_the_dataset():
    """This is the literal EURUSD 2001-09-11 bar in the V1 store."""
    frame = make_bars()
    for col in ("open", "high", "low", "close"):
        frame.iloc[3, frame.columns.get_loc(col)] = -0.0001
    report = validate(frame)
    defect = report.by_code(DefectCode.NON_POSITIVE_PRICE)
    assert defect is not None
    assert defect.severity is Severity.CRITICAL
    assert report.quality is DataQuality.REJECTED
    assert not report.is_clean


def test_nan_price_is_detected():
    frame = make_bars()
    frame.iloc[7, frame.columns.get_loc("close")] = np.nan
    report = validate(frame)
    assert report.has(DefectCode.NAN_PRICE)


# ------------------------------------------------------------- index


def test_duplicate_timestamps_are_detected():
    frame = make_bars(periods=100)
    doubled = pd.concat([frame, frame.iloc[[20, 21]]]).sort_index(kind="stable")
    report = validate(doubled)
    defect = report.by_code(DefectCode.DUPLICATE_TIMESTAMP)
    assert defect is not None
    assert defect.count == 2
    assert defect.severity is Severity.ERROR


def test_non_monotonic_index_is_detected():
    frame = make_bars(periods=50)
    shuffled = frame.iloc[[0, 1, 5, 2, 3, 4, *range(6, 50)]]
    report = validate(shuffled)
    assert report.has(DefectCode.NON_MONOTONIC_INDEX)
    assert report.by_code(DefectCode.NON_MONOTONIC_INDEX).severity is Severity.ERROR


def test_timezone_naive_index_is_critical():
    frame = make_bars(periods=50)
    naive = frame.copy()
    naive.index = naive.index.tz_localize(None)
    report = validate(naive)
    assert report.has(DefectCode.NAIVE_TIMESTAMP)
    assert report.quality is DataQuality.REJECTED


def test_misaligned_bar_start_is_flagged():
    """H4 bars on a 17:00-EST grid do not sit on the epoch UTC grid."""
    frame = make_bars(timeframe=Timeframe.H4, periods=60, start="2026-01-05 01:00")
    report = validate(frame)
    defect = report.by_code(DefectCode.MISALIGNED_BAR_START)
    assert defect is not None
    assert defect.count == 60
    assert defect.severity is Severity.WARNING


# -------------------------------------------------------------- gaps


def test_weekend_gap_in_fx_is_expected_not_a_defect():
    """The whole point of a session calendar: Friday night is not a hole."""
    # Monday 00:00 through the following Monday, hourly, with the weekend cut.
    idx = pd.date_range("2026-01-05 00:00", "2026-01-12 00:00", freq="h", tz="UTC")
    keep = [ts for ts in idx if FX_CALENDAR.is_open(ts)]
    base = make_bars(periods=len(keep))
    frame = base.set_axis(pd.DatetimeIndex(keep, name="timestamp"), axis=0)
    report = validate(frame)
    unexpected = report.by_code(DefectCode.UNEXPECTED_GAP)
    assert unexpected is None, (
        f"weekend was reported as a hole: {unexpected.message if unexpected else ''}"
    )
    assert report.has(DefectCode.EXPECTED_GAP)
    assert all(g.expected for g in report.gaps)


def test_midweek_gap_is_unexpected():
    """A Tuesday hole is a hole."""
    frame = make_bars(periods=120, start="2026-01-05 00:00")
    # Remove Tuesday 10:00-14:00 UTC, squarely inside the session.
    drop = pd.date_range("2026-01-06 10:00", "2026-01-06 14:00", freq="h", tz="UTC")
    holed = frame.drop(index=drop)
    report = validate(holed)
    defect = report.by_code(DefectCode.UNEXPECTED_GAP)
    assert defect is not None
    assert defect.count == 1
    assert defect.detail["missing_bars_total"] == len(drop)
    unexpected = [g for g in report.gaps if not g.expected]
    assert len(unexpected) == 1
    assert unexpected[0].missing_bars == len(drop)


def test_crypto_calendar_has_no_expected_gaps():
    frame = make_bars(periods=200)
    holed = frame.drop(index=frame.index[50:55])
    report = validate(holed, calendar=CRYPTO_CALENDAR)
    assert report.has(DefectCode.UNEXPECTED_GAP)
    assert report.by_code(DefectCode.UNEXPECTED_GAP).detail["missing_bars_total"] == 5


def test_saturday_bars_are_off_session():
    """The V1 store has 127 Saturday EURUSD bars. FX does not trade Saturday."""
    idx = pd.date_range("2026-01-10 00:00", periods=12, freq="h", tz="UTC")  # Saturday
    frame = make_bars(periods=12).set_axis(idx.rename("timestamp"), axis=0)
    report = validate(frame)
    defect = report.by_code(DefectCode.OFF_SESSION_BAR)
    assert defect is not None
    assert defect.count == 12


# ------------------------------------------------------ stale & outliers


def _freeze(frame, start, stop):
    """Freeze a whole bar range to one price. A flat bar is still a valid bar;
    freezing only the close would make this secretly a geometry test."""
    out = frame.copy()
    price = out["close"].iloc[start]
    for col in ("open", "high", "low", "close"):
        out.iloc[start:stop, out.columns.get_loc(col)] = price
    return out


def test_stale_run_is_detected():
    frozen = _freeze(make_bars(periods=200), 50, 70)
    report = validate(frozen)
    assert report.is_clean, "a flat run is a WARNING, not a blocking defect"
    defect = report.by_code(DefectCode.STALE_RUN)
    assert defect is not None
    assert defect.detail["longest_run"] >= 20


def test_stale_run_threshold_is_configurable():
    frozen = _freeze(make_bars(periods=200), 10, 13)
    assert not validate(frozen).has(DefectCode.STALE_RUN)
    tight = validate(frozen, config=IntegrityConfig(stale_run_length=3))
    assert tight.has(DefectCode.STALE_RUN)


def test_return_outlier_is_detected_by_robust_zscore():
    frame = make_bars(periods=500)
    spiked = frame.copy()
    for col in ("open", "high", "low", "close"):
        spiked.iloc[300, spiked.columns.get_loc(col)] *= 1.9
    report = validate(spiked)
    defect = report.by_code(DefectCode.RETURN_OUTLIER)
    assert defect is not None
    assert defect.detail["max_abs_z"] > 12.0


def test_single_outlier_does_not_hide_itself():
    """A mean/std z-score would be inflated by the outlier; MAD is not."""
    frame = make_bars(periods=500)
    spiked = frame.copy()
    for col in ("open", "high", "low", "close"):
        spiked.iloc[100, spiked.columns.get_loc(col)] *= 3.0
    assert validate(spiked).has(DefectCode.RETURN_OUTLIER)


# ------------------------------------------------------------- volume


def test_identically_zero_volume_is_flagged_so_strategies_cannot_run_blind():
    """The HistData case: a volume column carrying no information at all."""
    frame = make_bars()
    frame["volume"] = 0
    report = validate(frame)
    defect = report.by_code(DefectCode.VOLUME_ALWAYS_ZERO)
    assert defect is not None
    assert defect.severity is Severity.WARNING
    assert "blind" in defect.message


def test_absent_volume_marker_is_also_flagged():
    frame = make_bars()
    frame["volume"] = -1
    report = validate(frame)
    assert report.has(DefectCode.VOLUME_ALWAYS_ZERO)


def test_negative_volume_is_an_error():
    frame = make_bars()
    frame.iloc[4, frame.columns.get_loc("volume")] = -500
    report = validate(frame)
    defect = report.by_code(DefectCode.NEGATIVE_VOLUME)
    assert defect is not None
    assert defect.severity is Severity.ERROR


def test_volume_spike_is_reported():
    frame = make_bars(periods=300)
    frame.iloc[100, frame.columns.get_loc("volume")] = 10_000_000
    report = validate(frame)
    assert report.has(DefectCode.VOLUME_ANOMALY)


def test_crossed_bid_ask_is_detected():
    frame = make_bars(periods=50, price_basis=PriceBasis.SYNTHETIC_MID)
    frame["bid_close"] = frame["close"] - 0.0001
    frame["ask_close"] = frame["close"] + 0.0001
    frame.iloc[5, frame.columns.get_loc("bid_close")] = frame["ask_close"].iloc[5] + 0.01
    report = validate(frame)
    assert report.has(DefectCode.BID_ASK_CROSSED)


# --------------------------------------------------- validate is pure


def test_validate_never_mutates_the_frame():
    frame = make_bars(periods=200)
    frame.iloc[10, frame.columns.get_loc("high")] = 0.0
    frame.iloc[20, frame.columns.get_loc("close")] = np.nan
    before = content_checksum(frame)
    report = validate(frame)
    assert not report.is_clean
    assert content_checksum(frame) == before, "validate() modified the input frame"


def test_assert_clean_raises_on_blocking_defects():
    frame = make_bars()
    frame.iloc[1, frame.columns.get_loc("low")] = 99.0
    report = validate(frame)
    with pytest.raises(DirtyDataError, match="will not silently repair"):
        assert_clean(report)


# ----------------------------------------------------- explicit repair


def test_repair_requires_a_reason_and_an_actor():
    with pytest.raises(ValueError, match="why"):
        RepairPlan(actions=(RepairAction.SORT_INDEX,), reason="  ", actor="joe")
    with pytest.raises(ValueError, match="who"):
        RepairPlan(actions=(RepairAction.SORT_INDEX,), reason="because", actor="")
    with pytest.raises(ValueError, match="no actions"):
        RepairPlan(actions=(), reason="because", actor="joe")


def test_repair_produces_a_new_frame_and_leaves_the_original_alone():
    frame = make_bars(periods=100)
    for col in ("open", "high", "low", "close"):
        frame.iloc[42, frame.columns.get_loc(col)] = -1.0
    before = content_checksum(frame)

    plan = RepairPlan(
        actions=(RepairAction.DROP_NON_POSITIVE,),
        reason="sentinel -0.0001 bar from HistData, verified against Dukascopy",
        actor="joe",
    )
    result = repair(frame, plan)

    assert content_checksum(frame) == before, "repair mutated its input"
    assert len(result.frame) == len(frame) - 1
    assert validate(result.frame).is_clean

    record = result.records[0]
    assert record.action == RepairAction.DROP_NON_POSITIVE.value
    assert record.rows_before - record.rows_after == 1
    assert record.actor == "joe"
    assert "HistData" in record.reason
    assert len(record.affected_timestamps) == 1


def test_repair_logs_every_action_in_order():
    frame = make_bars(periods=60)
    dirty = pd.concat([frame, frame.iloc[[5]]])
    dirty.iloc[10, dirty.columns.get_loc("high")] = 0.0001

    plan = RepairPlan(
        actions=(
            RepairAction.SORT_INDEX,
            RepairAction.DROP_DUPLICATE_TIMESTAMPS,
            RepairAction.DROP_IMPOSSIBLE_BARS,
        ),
        reason="migration cleanup",
        actor="tom",
    )
    result = repair(dirty, plan)
    assert [r.action for r in result.records] == [
        "sort_index", "drop_duplicate_timestamps", "drop_impossible_bars"
    ]
    assert result.rows_removed == 2
    assert result.frame.index.is_monotonic_increasing
    assert not result.frame.index.has_duplicates
