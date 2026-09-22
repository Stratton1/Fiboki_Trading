"""Timeframe resampling with explicit session and DST semantics.

Two things make resampling wrong in practice, and V1 hit both:

1.  **Implicit origin.** Where does an H4 bar start? If you resample without
    saying, pandas anchors to the first timestamp in the frame, so the same
    instrument resampled from two different date ranges produces two different
    bar grids, and the "same" backtest on the "same" data disagrees with itself.
    Here the origin is always explicit: epoch-anchored UTC by default, or
    session-anchored in a named timezone when you ask for it.

2.  **DST.** A daily bar anchored at 17:00 New York is 23 hours long on one
    Sunday a year and 25 hours on another. Resampling in UTC with a fixed
    24-hour rule silently slides the day boundary for half the year. The
    session-anchored path converts into the venue timezone, buckets there, and
    converts back, so the tz database does the work.

Empty buckets are dropped, never forward-filled. A period with no bars is a
period with no bars; inventing one is exactly the silent repair this package
exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.data.schema import (
    COLUMN_ORDER,
    VOLUME_COLUMNS,
    validate_frame_shape,
)
from fiboki.data.versioning import TransformationStep

RESAMPLE_CODE_VERSION = "2.0.0"

# Volume columns use -1 as an explicit "the source did not provide this" marker,
# which must not be summed as if it were a quantity.
_ABSENT_VOLUME = -1


class ResampleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResampleSpec:
    """Everything that determines the output grid. Hashed into the lineage."""

    source: Timeframe
    target: Timeframe
    anchor_tz: str | None = None
    anchor_time: str | None = None  # "HH:MM" local, only with anchor_tz
    label: str = "left"
    closed: str = "left"

    def to_parameters(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "target": self.target.value,
            "anchor_tz": self.anchor_tz,
            "anchor_time": self.anchor_time,
            "label": self.label,
            "closed": self.closed,
        }

    def to_step(self) -> TransformationStep:
        return TransformationStep(
            operation="resample",
            parameters=self.to_parameters(),
            code_version=RESAMPLE_CODE_VERSION,
        )


def assert_nested(source: Timeframe, target: Timeframe) -> None:
    """Refuse a resample whose buckets do not nest exactly.

    Nesting is what makes M1 -> H1 -> H4 and M1 -> H4 agree. Without it, a
    two-step aggregation and a one-step aggregation are different numbers, and
    which one a result used becomes a matter of luck.
    """
    if target.minutes < source.minutes:
        raise ResampleError(
            f"cannot upsample {source.value} -> {target.value}: V2 does not "
            "manufacture bars that were never observed"
        )
    if target.minutes % source.minutes != 0:
        raise ResampleError(
            f"{source.value} does not nest inside {target.value} "
            f"({target.minutes} % {source.minutes} != 0); the two-step and one-step "
            "aggregations would disagree"
        )


def _aggregation_map(columns: list[str]) -> dict[str, Any]:
    agg: dict[str, Any] = {}
    for col in columns:
        if col in ("open", "bid_open", "ask_open"):
            agg[col] = "first"
        elif col in ("high", "bid_high", "ask_high"):
            agg[col] = "max"
        elif col in ("low", "bid_low", "ask_low"):
            agg[col] = "min"
        elif col in ("close", "bid_close", "ask_close"):
            agg[col] = "last"
        elif col in VOLUME_COLUMNS:
            agg[col] = "sum"
        elif col in ("instrument", "timeframe", "price_basis"):
            agg[col] = "first"
    return agg


def resample(
    frame: pd.DataFrame,
    target: Timeframe | str,
    *,
    source: Timeframe | str | None = None,
    anchor_tz: str | None = None,
    anchor_time: str | None = None,
) -> pd.DataFrame:
    """Aggregate bars to a coarser timeframe.

    Default (``anchor_tz=None``) is epoch-anchored UTC: H4 buckets begin at
    00:00, 04:00, 08:00, 12:00, 16:00 and 20:00 UTC regardless of what the
    frame's first timestamp happens to be. That is the grid every Fiboki
    research artefact is computed on.

    With ``anchor_tz`` (and optionally ``anchor_time``), bucketing happens in
    the venue's local time, so a daily bar can start at 17:00 New York and stay
    on the session boundary through both DST transitions.
    """
    validate_frame_shape(frame)
    tgt = Timeframe(target) if not isinstance(target, Timeframe) else target
    if len(frame) == 0:
        raise ResampleError("cannot resample an empty frame")

    src = (
        Timeframe(source)
        if source is not None and not isinstance(source, Timeframe)
        else source or Timeframe(str(frame["timeframe"].iloc[0]))
    )
    assert_nested(src, tgt)
    if src is tgt:
        return frame.copy()

    if anchor_time is not None and anchor_tz is None:
        raise ResampleError("anchor_time requires anchor_tz: a local time needs a locale")

    work = frame.sort_index(kind="stable").copy()

    # Volume columns use -1 for "the source had no volume". Sixty absent bars
    # are not a volume of -60, and they are not a volume of 0 either: they are
    # still absent. Aggregate them separately with min_count=1 so an all-absent
    # bucket comes back absent rather than as a fabricated zero.
    volume_cols = [c for c in VOLUME_COLUMNS if c in work.columns]
    volume_source: dict[str, pd.Series] = {}
    for col in volume_cols:
        absent = work[col] == _ABSENT_VOLUME
        volume_source[col] = work[col].astype("float64").where(~absent, np.nan)
        work = work.drop(columns=[col])

    agg = _aggregation_map(list(work.columns))
    freq = f"{tgt.minutes}min"

    if anchor_tz is None:
        resampler_kwargs: dict[str, Any] = {"origin": "epoch"}
        out = work.resample(freq, label="left", closed="left", **resampler_kwargs).agg(agg)
        relabel = None
    else:
        # Bucket on the venue's *wall clock*. A session day that runs 17:00 to
        # 17:00 local is 24 wall-clock hours, which is 23 or 25 UTC hours across
        # a DST change. Resampling a tz-aware index with a fixed-minute rule
        # would instead hold the UTC duration constant and slide the session
        # boundary, which is exactly the bug this path exists to avoid.
        naive = work.index.tz_convert(anchor_tz).tz_localize(None)
        work = work.set_axis(naive, axis=0)
        origin: Any = "epoch"
        if anchor_time is not None:
            hh, mm = (int(x) for x in anchor_time.split(":"))
            origin = naive[0].normalize() + pd.Timedelta(hours=hh, minutes=mm)
            if origin > naive[0]:
                origin = origin - pd.Timedelta(days=1)
        resampler_kwargs = {"origin": origin}
        out = work.resample(freq, label="left", closed="left", **resampler_kwargs).agg(agg)
        relabel = anchor_tz

    for col in volume_cols:
        # ``work`` may have been re-indexed onto wall-clock time above; the
        # volume series must follow it onto the same axis.
        series = volume_source[col].set_axis(work.index, axis=0)
        summed = series.resample(
            freq, label="left", closed="left", **resampler_kwargs
        ).sum(min_count=1)
        out[col] = summed.reindex(out.index)

    # Empty buckets: drop, never fill.
    out = out[out["open"].notna()]

    for col in volume_cols:
        out[col] = out[col].fillna(_ABSENT_VOLUME).round().astype("int64")

    if relabel is not None:
        localised = pd.DatetimeIndex(out.index).tz_localize(
            relabel, ambiguous=True, nonexistent="shift_forward"
        )
        out.index = localised.tz_convert("UTC")

    for col in ("instrument", "price_basis"):
        if col in out.columns:
            out[col] = out[col].astype("object")
    out["timeframe"] = tgt.value

    out.index = pd.DatetimeIndex(out.index).tz_convert("UTC").as_unit("us")
    out.index.freq = None
    out.index.name = "timestamp"
    ordered = [c for c in COLUMN_ORDER if c in out.columns]
    out = out[ordered]
    validate_frame_shape(out)
    return out


def resample_chain(
    frame: pd.DataFrame, path: list[Timeframe | str], **kwargs: Any
) -> pd.DataFrame:
    """Resample through intermediate timeframes, e.g. M1 -> H1 -> H4.

    Provided so the transitivity property can be exercised directly rather than
    assumed. ``tests/unit/test_data_resample.py`` asserts this agrees exactly
    with a single-step resample to the final timeframe.
    """
    out = frame
    for step in path:
        out = resample(out, step, **kwargs)
    return out


def session_bar_count(frame: pd.DataFrame, target: Timeframe | str) -> pd.Series:
    """Bars contributing to each output bucket. Used to spot thin buckets."""
    validate_frame_shape(frame)
    tgt = Timeframe(target) if not isinstance(target, Timeframe) else target
    counts = (
        frame.sort_index()
        .resample(f"{tgt.minutes}min", origin="epoch", label="left", closed="left")["close"]
        .count()
    )
    return counts[counts > 0]
