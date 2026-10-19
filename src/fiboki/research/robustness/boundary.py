"""The holdout boundary every robustness diagnostic stops at.

A diagnostic is a LOOK. A look at the reserved holdout before rung 6 is the
exact leak the holdout registry exists to refuse (``validation/holdout.py``),
and a robustness panel that quietly read the tail of the data would turn every
later holdout verdict on that dataset into an in-sample number. So every
diagnostic in this package is fenced by one :class:`HoldoutBoundary`:

* **Ledger diagnostics** (bands, breakdowns, consistency, fill detail, objective
  ranking, optimisation profile) EXCLUDE every trade that exited at or after the
  boundary and every equity point at or after it, and say how many they dropped.
  A trade that exits inside the holdout was marked on holdout bars.
* **Re-run diagnostics** (warm-up, look-ahead, execution stress, parameter
  perturbation, random benchmark, price noise) load bars strictly BEFORE the
  boundary (:func:`load_end`) and refuse a window that reaches it
  (:func:`assert_clear`), raising :class:`~fiboki.validation.holdout.HoldoutLeak`.

Where the boundary comes from
-----------------------------
``registry``
    The holdout registry has a segment for the run's dataset version (the
    ladder defined it): the boundary is its ``holdout_start``, exactly.
``provisional``
    No segment is defined yet. The boundary is then the start of the final
    :data:`~fiboki.validation.holdout.DEFAULT_HOLDOUT_FRACTION` of the RUN's own
    bar span: the fraction the ladder WOULD reserve. This is deliberately more
    conservative than necessary when the run covers only part of its dataset
    (the true tail may lie later), and never less: a diagnostic run now must not
    have looked at bars a later ladder run reserves.

The registry is read with a read-only SQLite connection (``mode=ro``); this
module never defines, claims or writes a segment.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.validation.holdout import DEFAULT_HOLDOUT_FRACTION, HoldoutLeak

__all__ = [
    "HoldoutBoundary",
    "ResearchSlice",
    "assert_clear",
    "load_end",
    "read_segment_start",
    "research_slice",
    "resolve_boundary",
    "run_span",
]


def _utc(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(str(value))
    except (TypeError, ValueError):
        return None
    if ts is pd.NaT:
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


@dataclass(frozen=True, slots=True)
class HoldoutBoundary:
    """The first reserved instant. Everything at or after ``start`` is off limits."""

    start: pd.Timestamp
    #: ``registry`` (a defined segment) or ``provisional`` (the would-be segment).
    basis: str
    dataset_version_ids: tuple[str, ...]
    detail: str

    def reserved(self, ts: pd.Timestamp | None) -> bool:
        """True when ``ts`` is inside the holdout (or unknown, which is treated as inside)."""
        return ts is None or ts >= self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "basis": self.basis,
            "dataset_version_ids": list(self.dataset_version_ids),
            "detail": self.detail,
        }


def read_segment_start(registry_path: Path | None, dataset_version_id: str) -> pd.Timestamp | None:
    """``holdout_start`` of a DEFINED segment, read-only; ``None`` when there is none."""
    if registry_path is None or not dataset_version_id:
        return None
    path = Path(registry_path)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT holdout_start FROM holdout_segment WHERE dataset_version_id = ?",
            (str(dataset_version_id),),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return _utc(row[0]) if row else None


def run_span(record: Mapping[str, Any]) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """The first and last bar a stored run read, from its data fingerprint.

    Falls back to the equity curve's first and last point for a record filed
    without a fingerprint.
    """
    firsts: list[pd.Timestamp] = []
    lasts: list[pd.Timestamp] = []
    for entry in (record.get("data_fingerprint") or {}).values():
        if isinstance(entry, Mapping):
            f, la = _utc(entry.get("first")), _utc(entry.get("last"))
            if f is not None:
                firsts.append(f)
            if la is not None:
                lasts.append(la)
    if firsts and lasts:
        return min(firsts), max(lasts)
    curve = record.get("equity_curve") or []
    times = [t for t in (_utc(p.get("timestamp")) for p in curve if isinstance(p, Mapping)) if t is not None]
    if times:
        return min(times), max(times)
    return None, None


def resolve_boundary(
    *,
    dataset_version_ids: Sequence[str],
    first: pd.Timestamp | None,
    last: pd.Timestamp | None,
    registry_path: Path | None,
) -> HoldoutBoundary:
    """The boundary for a run over ``[first, last]`` on these dataset versions.

    Several versions (a pooled run): the EARLIEST boundary of any of them, so no
    sleeve reads another sleeve's holdout. Raises ``ValueError`` when neither a
    segment nor the run's own span is known: a diagnostic that cannot locate the
    holdout cannot promise to avoid it.
    """
    ids = tuple(str(v) for v in dataset_version_ids if v)
    defined = [(v, read_segment_start(registry_path, v)) for v in ids]
    starts = [s for _, s in defined if s is not None]
    if starts:
        start = min(starts)
        named = ", ".join(v for v, s in defined if s is not None)
        return HoldoutBoundary(
            start=start, basis="registry", dataset_version_ids=ids,
            detail=(f"Holdout segment defined in the registry for {named}: every bar from "
                    f"{start.isoformat()} is reserved and no diagnostic reads it."),
        )
    if first is None or last is None or last <= first:
        raise ValueError(
            "the holdout boundary cannot be located: no registry segment for "
            f"{list(ids) or 'an unrecorded dataset'} and no recorded bar span. A diagnostic "
            "that cannot locate the holdout cannot promise to avoid it."
        )
    span = (last - first).total_seconds()
    start = first + pd.Timedelta(seconds=span * (1.0 - DEFAULT_HOLDOUT_FRACTION))
    return HoldoutBoundary(
        start=start, basis="provisional", dataset_version_ids=ids,
        detail=(f"No holdout segment is defined for {list(ids) or 'this run'} yet, so the final "
                f"{DEFAULT_HOLDOUT_FRACTION:.0%} of the run's own bar span (from "
                f"{start.isoformat()}) is treated as reserved: the fraction the ladder would "
                "reserve. Conservative when the run covers only part of its dataset."),
    )


def assert_clear(boundary: HoldoutBoundary, *, start: pd.Timestamp, end: pd.Timestamp) -> None:
    """Refuse a half-open window ``[start, end)`` that reaches the boundary."""
    if end > boundary.start:
        raise HoldoutLeak(
            f"diagnostic window [{start.isoformat()}, {end.isoformat()}) reaches the holdout "
            f"boundary {boundary.start.isoformat()} ({boundary.basis}). Robustness diagnostics "
            "run strictly before it."
        )


def load_end(boundary: HoldoutBoundary) -> str:
    """The inclusive ``end`` to hand a bar source so that no reserved bar is read.

    Bar sources slice ``[:end]`` inclusively, so the boundary itself is not
    passed: one microsecond before it is (bars are stored at microsecond
    resolution, ``tests/integration/test_time_units.py``).
    """
    end = boundary.start - pd.Timedelta(microseconds=1)
    return end.tz_convert("UTC").tz_localize(None).isoformat()


@dataclass(frozen=True, slots=True)
class ResearchSlice:
    """A stored run with everything at or after the boundary removed."""

    trades: tuple[dict[str, Any], ...]
    equity_curve: tuple[dict[str, Any], ...]
    boundary: HoldoutBoundary
    trades_excluded: int
    points_excluded: int

    @property
    def note(self) -> str:
        if not self.trades_excluded and not self.points_excluded:
            return (f"Holdout boundary {self.boundary.start.isoformat()} ({self.boundary.basis}): "
                    "nothing in this run reaches it.")
        return (f"Holdout boundary {self.boundary.start.isoformat()} ({self.boundary.basis}): "
                f"{self.trades_excluded} trade(s) that exited at or after it and "
                f"{self.points_excluded} equity point(s) are EXCLUDED from this diagnostic.")


def research_slice(record: Mapping[str, Any], boundary: HoldoutBoundary) -> ResearchSlice:
    """The record's trades and equity curve strictly before the boundary."""
    trades_in: Iterable[Any] = record.get("trades") or []
    trades: list[dict[str, Any]] = []
    excluded_t = 0
    for t in trades_in:
        if not isinstance(t, Mapping):
            continue
        if boundary.reserved(_utc(t.get("exit_time"))):
            excluded_t += 1
            continue
        trades.append(dict(t))
    points: list[dict[str, Any]] = []
    excluded_p = 0
    for p in record.get("equity_curve") or []:
        if not isinstance(p, Mapping):
            continue
        if boundary.reserved(_utc(p.get("timestamp"))):
            excluded_p += 1
            continue
        points.append(dict(p))
    return ResearchSlice(tuple(trades), tuple(points), boundary, excluded_t, excluded_p)
