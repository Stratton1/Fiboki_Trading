"""Data integrity: detect defects, never fix them.

V1 repaired silently. A bar with ``high < low`` got quietly clipped, a duplicate
timestamp got quietly dropped, a hole got quietly forward-filled, and the
research batch that consumed the result had no idea any of it happened. The
numbers looked fine. They were not fine.

V2 splits the two operations completely:

    validate(frame)                 -> IntegrityReport      (pure, no mutation)
    repair(frame, report, actions)  -> (new_frame, records)  (explicit, logged)

``validate`` is a pure function. It cannot modify the frame; it does not return
one. ``repair`` must be called deliberately, with a stated reason and actor, and
produces a *new dataset version* whose metadata carries the full repair log.
A dataset that has blocking defects and has not been repaired cannot be read as
clean — :mod:`fiboki.data.store` enforces that on the way out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.calendars import SessionCalendar, calendar_for
from fiboki.data.schema import (
    GapRecord,
    RepairRecord,
    validate_frame_shape,
)

#: Volume columns use -1 to mean "the source did not provide this". It is not a
#: quantity and must never be aggregated, summed or charted as one.
_ABSENT_VOLUME_MARKER = -1


class Severity(str, Enum):
    """How bad a defect is. ERROR and above block a clean read."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}[self.value]

    @property
    def blocks_clean_read(self) -> bool:
        return self.rank >= Severity.ERROR.rank


class DefectCode(str, Enum):
    """Every defect V2 knows how to look for."""

    IMPOSSIBLE_BAR = "impossible_bar"
    NON_POSITIVE_PRICE = "non_positive_price"
    NAN_PRICE = "nan_price"
    DUPLICATE_TIMESTAMP = "duplicate_timestamp"
    NON_MONOTONIC_INDEX = "non_monotonic_index"
    NAIVE_TIMESTAMP = "naive_timestamp"
    UNEXPECTED_GAP = "unexpected_gap"
    EXPECTED_GAP = "expected_gap"
    OFF_SESSION_BAR = "off_session_bar"
    STALE_RUN = "stale_run"
    RETURN_OUTLIER = "return_outlier"
    VOLUME_ALWAYS_ZERO = "volume_always_zero"
    VOLUME_ANOMALY = "volume_anomaly"
    NEGATIVE_VOLUME = "negative_volume"
    BID_ASK_CROSSED = "bid_ask_crossed"
    MISALIGNED_BAR_START = "misaligned_bar_start"


@dataclass(frozen=True, slots=True)
class Defect:
    """One class of problem, with enough detail to go and look at it."""

    code: DefectCode
    severity: Severity
    count: int
    message: str
    first_timestamp: pd.Timestamp | None = None
    last_timestamp: pd.Timestamp | None = None
    sample_timestamps: tuple[pd.Timestamp, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "count": int(self.count),
            "message": self.message,
            "first_timestamp": (
                self.first_timestamp.isoformat() if self.first_timestamp is not None else None
            ),
            "last_timestamp": (
                self.last_timestamp.isoformat() if self.last_timestamp is not None else None
            ),
            "sample_timestamps": [t.isoformat() for t in self.sample_timestamps],
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class IntegrityConfig:
    """Thresholds. Explicit and stored, so a report can be reproduced."""

    stale_run_length: int = 6
    outlier_z_threshold: float = 12.0
    volume_anomaly_z_threshold: float = 15.0
    max_gap_samples: int = 25
    max_defect_samples: int = 20
    check_session: bool = True
    check_outliers: bool = True
    check_bar_alignment: bool = True
    # A bar start must lie on the timeframe grid measured from this anchor.
    alignment_anchor_utc_minutes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stale_run_length": self.stale_run_length,
            "outlier_z_threshold": self.outlier_z_threshold,
            "volume_anomaly_z_threshold": self.volume_anomaly_z_threshold,
            "check_session": self.check_session,
            "check_outliers": self.check_outliers,
            "check_bar_alignment": self.check_bar_alignment,
            "alignment_anchor_utc_minutes": self.alignment_anchor_utc_minutes,
        }


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """The structured verdict on a dataset. Stored alongside it, forever."""

    instrument: str
    timeframe: Timeframe
    row_count: int
    first_timestamp: pd.Timestamp | None
    last_timestamp: pd.Timestamp | None
    defects: tuple[Defect, ...]
    gaps: tuple[GapRecord, ...]
    config: IntegrityConfig
    checks_run: tuple[str, ...]
    calendar_name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    # -- verdict ------------------------------------------------------

    @property
    def worst_severity(self) -> Severity:
        if not self.defects:
            return Severity.INFO
        return max((d.severity for d in self.defects), key=lambda s: s.rank)

    @property
    def blocking_defects(self) -> tuple[Defect, ...]:
        return tuple(d for d in self.defects if d.severity.blocks_clean_read)

    @property
    def is_clean(self) -> bool:
        """Clean means: nothing at ERROR or above. WARNINGs are still recorded."""
        return not self.blocking_defects

    @property
    def quality(self) -> DataQuality:
        worst = self.worst_severity
        if worst is Severity.CRITICAL:
            return DataQuality.REJECTED
        if worst is Severity.ERROR:
            return DataQuality.SUSPECT
        return DataQuality.VALIDATED

    def by_code(self, code: DefectCode) -> Defect | None:
        for d in self.defects:
            if d.code is code:
                return d
        return None

    def has(self, code: DefectCode) -> bool:
        return self.by_code(code) is not None

    def summary(self) -> str:
        if not self.defects:
            return (
                f"{self.instrument} {self.timeframe.value}: {self.row_count} rows, clean"
            )
        parts = [f"{d.code.value}x{d.count}({d.severity.value})" for d in self.defects]
        return (
            f"{self.instrument} {self.timeframe.value}: {self.row_count} rows, "
            f"quality={self.quality.value}, " + ", ".join(parts)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "timeframe": self.timeframe.value,
            "row_count": int(self.row_count),
            "first_timestamp": (
                self.first_timestamp.isoformat() if self.first_timestamp is not None else None
            ),
            "last_timestamp": (
                self.last_timestamp.isoformat() if self.last_timestamp is not None else None
            ),
            "quality": self.quality.value,
            "worst_severity": self.worst_severity.value,
            "is_clean": self.is_clean,
            "defects": [d.to_dict() for d in self.defects],
            "gaps": [g.to_dict() for g in self.gaps[: self.config.max_gap_samples]],
            "gap_count": len(self.gaps),
            "unexpected_gap_count": sum(1 for g in self.gaps if not g.expected),
            "config": self.config.to_dict(),
            "checks_run": list(self.checks_run),
            "calendar_name": self.calendar_name,
            "generated_at": self.generated_at.isoformat(),
        }


class DirtyDataError(RuntimeError):
    """Raised when blocking defects are present and no repair was authorised."""

    def __init__(self, report: IntegrityReport) -> None:
        self.report = report
        codes = ", ".join(
            f"{d.code.value}({d.count})" for d in report.blocking_defects
        )
        super().__init__(
            f"{report.instrument} {report.timeframe.value} has blocking defects: {codes}. "
            "V2 will not silently repair. Either call fiboki.data.integrity.repair() "
            "with an explicit action list and reason (which creates a NEW dataset "
            "version), or read with allow_suspect=True and accept the consequences."
        )


# ----------------------------------------------------------- validation


def _sample(index: pd.DatetimeIndex, n: int) -> tuple[pd.Timestamp, ...]:
    return tuple(index[:n])


def validate(
    frame: pd.DataFrame,
    *,
    calendar: SessionCalendar | None = None,
    config: IntegrityConfig | None = None,
) -> IntegrityReport:
    """Inspect ``frame`` and return a structured report. Never mutates it.

    The frame must already be canonically shaped (see
    :func:`fiboki.data.schema.canonical_frame`), which guarantees a tz-aware UTC
    index — so ``NAIVE_TIMESTAMP`` can only be reported for a frame handed in
    unshaped, which we still check for, defensively.
    """
    cfg = config or IntegrityConfig()
    defects: list[Defect] = []
    checks: list[str] = []

    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError("integrity.validate requires a DatetimeIndex")

    # -- timezone ---------------------------------------------------
    checks.append("timezone")
    if frame.index.tz is None:
        defects.append(
            Defect(
                code=DefectCode.NAIVE_TIMESTAMP,
                severity=Severity.CRITICAL,
                count=len(frame),
                message=(
                    "index is timezone-naive; the source convention is unknown and "
                    "must not be assumed to be UTC"
                ),
            )
        )
        # Everything below depends on a comparable index; stop here honestly.
        return IntegrityReport(
            instrument=str(frame["instrument"].iloc[0]) if len(frame) else "?",
            timeframe=Timeframe(str(frame["timeframe"].iloc[0])) if len(frame) else Timeframe.H1,
            row_count=len(frame),
            first_timestamp=None,
            last_timestamp=None,
            defects=tuple(defects),
            gaps=(),
            config=cfg,
            checks_run=tuple(checks),
            calendar_name="unknown",
        )

    validate_frame_shape(frame)
    instrument = str(frame["instrument"].iloc[0]) if len(frame) else "?"
    timeframe = Timeframe(str(frame["timeframe"].iloc[0])) if len(frame) else Timeframe.H1
    cal = calendar or _safe_calendar(instrument)

    idx = frame.index
    o = frame["open"].to_numpy(dtype="float64")
    h = frame["high"].to_numpy(dtype="float64")
    low = frame["low"].to_numpy(dtype="float64")
    c = frame["close"].to_numpy(dtype="float64")

    # -- NaNs -------------------------------------------------------
    checks.append("nan_prices")
    nan_mask = np.isnan(o) | np.isnan(h) | np.isnan(low) | np.isnan(c)
    if nan_mask.any():
        ts = idx[nan_mask]
        defects.append(
            Defect(
                code=DefectCode.NAN_PRICE,
                severity=Severity.ERROR,
                count=int(nan_mask.sum()),
                message="bars with a missing OHLC value",
                first_timestamp=ts.min(),
                last_timestamp=ts.max(),
                sample_timestamps=_sample(ts, cfg.max_defect_samples),
            )
        )

    # -- non-positive prices ---------------------------------------
    checks.append("non_positive_prices")
    finite = ~nan_mask
    nonpos = finite & ((o <= 0) | (h <= 0) | (low <= 0) | (c <= 0))
    if nonpos.any():
        ts = idx[nonpos]
        defects.append(
            Defect(
                code=DefectCode.NON_POSITIVE_PRICE,
                severity=Severity.CRITICAL,
                count=int(nonpos.sum()),
                message=(
                    "bars with a zero or negative price. These are sentinel values "
                    "from the source, not prices, and they destroy log returns."
                ),
                first_timestamp=ts.min(),
                last_timestamp=ts.max(),
                sample_timestamps=_sample(ts, cfg.max_defect_samples),
            )
        )

    # -- impossible OHLC geometry ----------------------------------
    checks.append("impossible_bars")
    impossible = finite & (
        (h < low) | (h < o) | (h < c) | (low > o) | (low > c)
    )
    if impossible.any():
        ts = idx[impossible]
        defects.append(
            Defect(
                code=DefectCode.IMPOSSIBLE_BAR,
                severity=Severity.ERROR,
                count=int(impossible.sum()),
                message="bars where high/low do not bracket open/close",
                first_timestamp=ts.min(),
                last_timestamp=ts.max(),
                sample_timestamps=_sample(ts, cfg.max_defect_samples),
            )
        )

    # -- bid/ask crossed -------------------------------------------
    if "bid_close" in frame.columns and "ask_close" in frame.columns:
        checks.append("bid_ask_crossed")
        bid = frame["bid_close"].to_numpy(dtype="float64")
        ask = frame["ask_close"].to_numpy(dtype="float64")
        crossed = np.isfinite(bid) & np.isfinite(ask) & (bid > ask)
        if crossed.any():
            ts = idx[crossed]
            defects.append(
                Defect(
                    code=DefectCode.BID_ASK_CROSSED,
                    severity=Severity.ERROR,
                    count=int(crossed.sum()),
                    message="bid above ask: a crossed or corrupt quote",
                    first_timestamp=ts.min(),
                    last_timestamp=ts.max(),
                    sample_timestamps=_sample(ts, cfg.max_defect_samples),
                )
            )

    # -- duplicates -------------------------------------------------
    checks.append("duplicate_timestamps")
    dup_mask = idx.duplicated(keep="first")
    if dup_mask.any():
        ts = idx[dup_mask]
        defects.append(
            Defect(
                code=DefectCode.DUPLICATE_TIMESTAMP,
                severity=Severity.ERROR,
                count=int(dup_mask.sum()),
                message="repeated bar timestamps",
                first_timestamp=ts.min(),
                last_timestamp=ts.max(),
                sample_timestamps=_sample(ts, cfg.max_defect_samples),
                detail={"unique_duplicated_values": int(pd.Index(ts).nunique())},
            )
        )

    # -- monotonicity ----------------------------------------------
    checks.append("monotonic_index")
    if not idx.is_monotonic_increasing:
        steps = idx.to_series().diff()
        back = steps < pd.Timedelta(0)
        ts = idx[back.to_numpy()]
        defects.append(
            Defect(
                code=DefectCode.NON_MONOTONIC_INDEX,
                severity=Severity.ERROR,
                count=int(back.sum()),
                message="index goes backwards; bar order is not time order",
                first_timestamp=ts.min() if len(ts) else None,
                last_timestamp=ts.max() if len(ts) else None,
                sample_timestamps=_sample(ts, cfg.max_defect_samples),
            )
        )

    # -- bar-start alignment ---------------------------------------
    if cfg.check_bar_alignment:
        checks.append("bar_alignment")
        step_ns = timeframe.minutes * 60 * 1_000_000_000
        anchor_ns = cfg.alignment_anchor_utc_minutes * 60 * 1_000_000_000
        # asi8 is in the index's own unit; force nanoseconds so the modulus is
        # measured in the same units as step_ns.
        idx_ns = pd.DatetimeIndex(idx).as_unit("ns").asi8
        offsets = (idx_ns - anchor_ns) % step_ns
        misaligned = offsets != 0
        if misaligned.any():
            ts = idx[misaligned]
            defects.append(
                Defect(
                    code=DefectCode.MISALIGNED_BAR_START,
                    severity=Severity.WARNING,
                    count=int(misaligned.sum()),
                    message=(
                        f"bar starts not on the {timeframe.value} grid; the source may "
                        "use a session-anchored origin rather than an epoch-anchored one"
                    ),
                    first_timestamp=ts.min(),
                    last_timestamp=ts.max(),
                    sample_timestamps=_sample(ts, cfg.max_defect_samples),
                )
            )

    # -- gaps against the session calendar -------------------------
    checks.append("gaps")
    gaps = _classify_gaps(idx, timeframe, cal)
    unexpected = [g for g in gaps if not g.expected]
    if unexpected:
        missing_total = sum(g.missing_bars for g in unexpected)
        defects.append(
            Defect(
                code=DefectCode.UNEXPECTED_GAP,
                severity=Severity.WARNING,
                count=len(unexpected),
                message=(
                    f"{len(unexpected)} gaps during expected trading hours, "
                    f"{missing_total} bars missing in total"
                ),
                first_timestamp=unexpected[0].start,
                last_timestamp=unexpected[-1].end,
                sample_timestamps=tuple(g.start for g in unexpected[: cfg.max_defect_samples]),
                detail={
                    "missing_bars_total": int(missing_total),
                    "largest_gap_bars": int(max(g.missing_bars for g in unexpected)),
                },
            )
        )
    expected_gaps = [g for g in gaps if g.expected]
    if expected_gaps:
        defects.append(
            Defect(
                code=DefectCode.EXPECTED_GAP,
                severity=Severity.INFO,
                count=len(expected_gaps),
                message="session-closed gaps (weekends, holidays, daily breaks)",
                first_timestamp=expected_gaps[0].start,
                last_timestamp=expected_gaps[-1].end,
            )
        )

    # -- off-session bars ------------------------------------------
    if cfg.check_session and not cal.continuous:
        checks.append("off_session_bars")
        open_mask = np.fromiter((cal.is_open(t) for t in idx), dtype=bool, count=len(idx))
        off = ~open_mask
        if off.any():
            ts = idx[off]
            defects.append(
                Defect(
                    code=DefectCode.OFF_SESSION_BAR,
                    severity=Severity.WARNING,
                    count=int(off.sum()),
                    message=(
                        f"bars timestamped outside the {cal.name} session. Either the "
                        "source timezone convention is wrong, or foreign data has been "
                        "stitched in."
                    ),
                    first_timestamp=ts.min(),
                    last_timestamp=ts.max(),
                    sample_timestamps=_sample(ts, cfg.max_defect_samples),
                    detail={"fraction": float(off.mean())},
                )
            )

    # -- stale runs -------------------------------------------------
    checks.append("stale_runs")
    stale_starts, longest = _stale_runs(c, idx, cfg.stale_run_length)
    if stale_starts:
        defects.append(
            Defect(
                code=DefectCode.STALE_RUN,
                severity=Severity.WARNING,
                count=len(stale_starts),
                message=(
                    f"{len(stale_starts)} runs of >= {cfg.stale_run_length} identical "
                    f"consecutive closes (longest {longest}); a frozen or interpolated feed"
                ),
                first_timestamp=stale_starts[0],
                last_timestamp=stale_starts[-1],
                sample_timestamps=tuple(stale_starts[: cfg.max_defect_samples]),
                detail={"longest_run": int(longest)},
            )
        )

    # -- return outliers -------------------------------------------
    if cfg.check_outliers:
        checks.append("return_outliers")
        out_ts, max_z = _return_outliers(c, idx, cfg.outlier_z_threshold)
        if len(out_ts):
            defects.append(
                Defect(
                    code=DefectCode.RETURN_OUTLIER,
                    severity=Severity.WARNING,
                    count=len(out_ts),
                    message=(
                        f"{len(out_ts)} log returns beyond {cfg.outlier_z_threshold} robust "
                        f"MAD z-scores (max |z| = {max_z:.1f})"
                    ),
                    first_timestamp=out_ts[0],
                    last_timestamp=out_ts[-1],
                    sample_timestamps=tuple(out_ts[: cfg.max_defect_samples]),
                    detail={"max_abs_z": float(max_z)},
                )
            )

    # -- volume -----------------------------------------------------
    checks.append("volume")
    defects.extend(_volume_defects(frame, idx, cfg))

    return IntegrityReport(
        instrument=instrument,
        timeframe=timeframe,
        row_count=len(frame),
        first_timestamp=idx.min() if len(idx) else None,
        last_timestamp=idx.max() if len(idx) else None,
        defects=tuple(defects),
        gaps=tuple(gaps),
        config=cfg,
        checks_run=tuple(checks),
        calendar_name=cal.name,
    )


def _safe_calendar(instrument: str) -> SessionCalendar:
    try:
        return calendar_for(instrument)
    except KeyError:
        from fiboki.data.calendars import FX_CALENDAR

        return FX_CALENDAR


def _classify_gaps(
    idx: pd.DatetimeIndex, timeframe: Timeframe, calendar: SessionCalendar
) -> list[GapRecord]:
    """Split every discontinuity into expected (session closed) and not."""
    if len(idx) < 2:
        return []
    step = pd.Timedelta(minutes=timeframe.minutes)
    ordered = idx.sort_values()
    deltas = ordered.to_series().diff()
    gap_positions = np.flatnonzero((deltas > step).to_numpy())
    gaps: list[GapRecord] = []
    for pos in gap_positions:
        start = ordered[pos - 1]
        end = ordered[pos]
        expected_starts = calendar.expected_bar_starts(start, end, timeframe)
        missing = len(expected_starts)
        if missing == 0:
            reason = "session_closed"
            if calendar.is_holiday(start) or calendar.is_holiday(end):
                reason = "holiday"
            gaps.append(
                GapRecord(start=start, end=end, missing_bars=0, expected=True, reason=reason)
            )
        else:
            gaps.append(
                GapRecord(
                    start=start,
                    end=end,
                    missing_bars=int(missing),
                    expected=False,
                    reason="missing_bars_during_session",
                )
            )
    return gaps


def _stale_runs(
    closes: np.ndarray, idx: pd.DatetimeIndex, min_length: int
) -> tuple[list[pd.Timestamp], int]:
    """Start timestamps of runs of >= ``min_length`` identical consecutive closes."""
    if len(closes) < min_length:
        return [], 0
    same = closes[1:] == closes[:-1]
    starts: list[pd.Timestamp] = []
    longest = 0
    run = 1
    run_start = 0
    for i, eq in enumerate(same, start=1):
        if eq:
            run += 1
        else:
            if run >= min_length:
                starts.append(idx[run_start])
                longest = max(longest, run)
            run = 1
            run_start = i
    if run >= min_length:
        starts.append(idx[run_start])
        longest = max(longest, run)
    return starts, longest


def _return_outliers(
    closes: np.ndarray, idx: pd.DatetimeIndex, threshold: float
) -> tuple[list[pd.Timestamp], float]:
    """Robust (median/MAD) z-score on log returns. Immune to the outliers it hunts."""
    valid = np.isfinite(closes) & (closes > 0)
    if valid.sum() < 32:
        return [], 0.0
    logc = np.full(closes.shape, np.nan)
    logc[valid] = np.log(closes[valid])
    rets = np.diff(logc)
    finite = np.isfinite(rets)
    if finite.sum() < 32:
        return [], 0.0
    sample = rets[finite]
    median = float(np.median(sample))
    mad = float(np.median(np.abs(sample - median)))
    if mad == 0.0:
        return [], 0.0
    scale = 1.4826 * mad  # MAD -> sigma for a normal
    z = np.zeros_like(rets)
    z[finite] = (rets[finite] - median) / scale
    hits = np.flatnonzero(finite & (np.abs(z) > threshold))
    if len(hits) == 0:
        return [], float(np.max(np.abs(z[finite])))
    return [idx[i + 1] for i in hits], float(np.max(np.abs(z[finite])))


def _volume_defects(
    frame: pd.DataFrame, idx: pd.DatetimeIndex, cfg: IntegrityConfig
) -> list[Defect]:
    """Volume checks.

    ``-1`` is the explicit "the source provided no volume" marker. A column that
    is entirely absent, or entirely zero, carries no information and must be
    flagged: a volume-dependent strategy run against it would produce a flat
    feature and a plausible-looking backtest built on nothing.
    """
    defects: list[Defect] = []
    for col in ("volume", "tick_volume"):
        if col not in frame.columns:
            continue
        v = frame[col].to_numpy(dtype="float64")
        if len(v) == 0:
            continue

        absent = v == _ABSENT_VOLUME_MARKER
        corrupt_neg = v < _ABSENT_VOLUME_MARKER
        if corrupt_neg.any():
            ts = idx[corrupt_neg]
            defects.append(
                Defect(
                    code=DefectCode.NEGATIVE_VOLUME,
                    severity=Severity.ERROR,
                    count=int(corrupt_neg.sum()),
                    message=f"{col} has negative values that are not the absent marker",
                    first_timestamp=ts.min(),
                    last_timestamp=ts.max(),
                    sample_timestamps=_sample(ts, cfg.max_defect_samples),
                )
            )

        observed = v[~absent & ~corrupt_neg]
        uninformative = len(observed) == 0 or bool(np.all(observed == 0))
        if uninformative:
            if absent.all():
                marker, detail_msg = "absent", "the source provided no volume at all"
            elif len(observed) == 0:
                marker, detail_msg = "absent", "every usable value is the absent marker"
            else:
                marker, detail_msg = "zero", "every value is literally zero"
            defects.append(
                Defect(
                    code=DefectCode.VOLUME_ALWAYS_ZERO,
                    severity=Severity.WARNING,
                    count=len(v),
                    message=(
                        f"{col} carries no information ({detail_msg}). Any "
                        "volume-dependent strategy run on this dataset would be "
                        "running blind, and must be blocked rather than silently "
                        "produce a flat feature."
                    ),
                    first_timestamp=idx.min(),
                    last_timestamp=idx.max(),
                    detail={"column": col, "marker": marker},
                )
            )
            continue

        if len(observed) >= 32:
            median = float(np.median(observed))
            mad = float(np.median(np.abs(observed - median)))
            if mad > 0:
                z = (observed - median) / (1.4826 * mad)
                hits = np.abs(z) > cfg.volume_anomaly_z_threshold
                if hits.any():
                    keep = ~absent & ~corrupt_neg
                    ts = idx[keep][hits]
                    defects.append(
                        Defect(
                            code=DefectCode.VOLUME_ANOMALY,
                            severity=Severity.INFO,
                            count=int(hits.sum()),
                            message=(
                                f"{col} spikes beyond {cfg.volume_anomaly_z_threshold} "
                                "robust z-scores"
                            ),
                            first_timestamp=ts.min(),
                            last_timestamp=ts.max(),
                            sample_timestamps=_sample(ts, cfg.max_defect_samples),
                            detail={"column": col, "max_abs_z": float(np.max(np.abs(z)))},
                        )
                    )
    return defects


# --------------------------------------------------------------- repair


class RepairAction(str, Enum):
    """The only repairs V2 will perform, each one a deliberate choice."""

    DROP_NON_POSITIVE = "drop_non_positive"
    DROP_IMPOSSIBLE_BARS = "drop_impossible_bars"
    DROP_NAN_PRICES = "drop_nan_prices"
    DROP_DUPLICATE_TIMESTAMPS = "drop_duplicate_timestamps"
    SORT_INDEX = "sort_index"
    DROP_OFF_SESSION_BARS = "drop_off_session_bars"


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """An authorised repair. Both ``reason`` and ``actor`` are mandatory."""

    actions: tuple[RepairAction, ...]
    reason: str
    actor: str

    def __post_init__(self) -> None:
        if not self.actions:
            raise ValueError("a RepairPlan with no actions is not a repair")
        if not self.reason.strip():
            raise ValueError(
                "a repair must state why. An unexplained repair is a silent repair."
            )
        if not self.actor.strip():
            raise ValueError("a repair must name who authorised it")


@dataclass(frozen=True, slots=True)
class RepairResult:
    """The repaired frame plus an audit trail of exactly what was removed."""

    frame: pd.DataFrame
    records: tuple[RepairRecord, ...]
    plan: RepairPlan

    @property
    def rows_removed(self) -> int:
        if not self.records:
            return 0
        return self.records[0].rows_before - self.records[-1].rows_after


def repair(
    frame: pd.DataFrame,
    plan: RepairPlan,
    *,
    calendar: SessionCalendar | None = None,
) -> RepairResult:
    """Apply an explicit repair plan, recording every row it removes.

    Returns a NEW frame; the input is never modified. The caller is expected to
    register the result as a new dataset version whose lineage points back at
    the unrepaired one, so the original bytes remain resolvable forever.
    """
    validate_frame_shape(frame)
    working = frame.copy()
    records: list[RepairRecord] = []
    instrument = str(working["instrument"].iloc[0]) if len(working) else "?"
    cal = calendar or _safe_calendar(instrument)

    for action in plan.actions:
        before = len(working)
        removed_ts: list[pd.Timestamp] = []

        if action is RepairAction.SORT_INDEX:
            working = working.sort_index(kind="stable")
        elif action is RepairAction.DROP_DUPLICATE_TIMESTAMPS:
            dup = working.index.duplicated(keep="first")
            removed_ts = list(working.index[dup])
            working = working[~dup]
        elif action is RepairAction.DROP_NAN_PRICES:
            mask = working[["open", "high", "low", "close"]].isna().any(axis=1)
            removed_ts = list(working.index[mask])
            working = working[~mask]
        elif action is RepairAction.DROP_NON_POSITIVE:
            mask = (working[["open", "high", "low", "close"]] <= 0).any(axis=1)
            removed_ts = list(working.index[mask])
            working = working[~mask]
        elif action is RepairAction.DROP_IMPOSSIBLE_BARS:
            o, h = working["open"], working["high"]
            lo, c = working["low"], working["close"]
            mask = (h < lo) | (h < o) | (h < c) | (lo > o) | (lo > c)
            mask = mask.fillna(False)
            removed_ts = list(working.index[mask])
            working = working[~mask]
        elif action is RepairAction.DROP_OFF_SESSION_BARS:
            open_mask = np.fromiter(
                (cal.is_open(t) for t in working.index), dtype=bool, count=len(working)
            )
            removed_ts = list(working.index[~open_mask])
            working = working[open_mask]
        else:  # pragma: no cover - exhaustive over the enum
            raise ValueError(f"unhandled repair action {action}")

        records.append(
            RepairRecord(
                applied_at=datetime.now(tz=UTC),
                action=action.value,
                reason=plan.reason,
                actor=plan.actor,
                rows_before=before,
                rows_after=len(working),
                affected_timestamps=tuple(
                    t.isoformat() for t in removed_ts[: 200]
                ),
                detail={
                    "rows_removed": before - len(working),
                    "affected_sample_truncated": len(removed_ts) > 200,
                },
            )
        )

    return RepairResult(frame=working, records=tuple(records), plan=plan)


def assert_clean(report: IntegrityReport) -> None:
    """Raise :class:`DirtyDataError` if the report has blocking defects."""
    if not report.is_clean:
        raise DirtyDataError(report)
