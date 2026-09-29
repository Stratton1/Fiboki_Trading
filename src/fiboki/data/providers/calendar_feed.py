"""Dated-event files from SECONDARY calendar sources, and a diff against the official one.

The official scheduled-events calendar is
``marketstate/fixtures/scheduled_events_official.json``: dates taken from the
publishers themselves, committed, never fetched at run time. Nothing here
replaces it. Vendor and aggregator calendars (Finnhub, the opt-in
ForexFactory feed) are written as separate, dated snapshot files so they can
be compared with it; the useful output is :func:`calendar_diff`, which lists
events one side has and the other does not. An event only the feed has is a
candidate omission in the official fixture (worth checking at the publisher);
an event only the official file has is a feed gap.

File shape: the object form ``marketstate.calendar.load_events_json``
already reads, ``{"schema_version", "source", "retrieved_at", ..., "events":
[...]}``, each event in the shape of ``EconomicEvent.to_dict``. This module
sits in ``data`` (rank 10) and may not import ``marketstate`` (rank 85), so
the shape is written out here and a test loads every file this module writes
through ``marketstate.calendar.load_events_json``.

Rules every writer keeps:

* ``event_time`` is tz-aware UTC ISO 8601; a vendor time without a declared
  zone is either established from evidence (and the evidence stated) or not
  used. Never guessed silently.
* Scheduled times only. ``actual``, ``forecast`` and ``previous`` are written
  as null: a snapshot taken after a release carries the realised figure and
  the LAST consensus, and a backtest reading them at the event bar would be
  reading a later snapshot. Values need their own point-in-time store.
* Write-once: a snapshot file that exists is never overwritten, and the
  official fixture's file name is refused outright.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.data.providers.base import ProviderError

__all__ = [
    "DATED_EVENTS_SCHEMA_VERSION",
    "IMPACT_ORDER",
    "OFFICIAL_FIXTURE_NAME",
    "CalendarDiff",
    "DatedEventsFile",
    "calendar_diff",
    "dated_event",
    "load_events_file",
    "snapshot_path",
    "write_events_file",
]

DATED_EVENTS_SCHEMA_VERSION = "1.0.0"
OFFICIAL_FIXTURE_NAME = "scheduled_events_official.json"
IMPACT_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


class CalendarFileError(ProviderError):
    pass


def _utc(value: Any, what: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise CalendarFileError(f"{what}: not a timestamp: {value!r}")
    if ts.tzinfo is None:
        raise CalendarFileError(f"{what}: timezone-naive {value!r}; a calendar time must be UTC-aware")
    return ts.tz_convert("UTC")


def dated_event(
    *,
    event_time: pd.Timestamp,
    currency: str,
    name: str,
    impact: str,
    source: str,
    source_url: str,
    retrieved_at: str,
    time_known: bool = True,
    window_end: pd.Timestamp | None = None,
    tags: Iterable[str] = (),
    recurring_key: str | None = None,
) -> dict[str, Any]:
    """One event dict in the ``EconomicEvent.to_dict`` shape, validated."""
    t = _utc(event_time, name)
    ccy = str(currency).strip().upper()
    if len(ccy) != 3 or not ccy.isalpha():
        raise CalendarFileError(f"{name}: currency {currency!r} is not an ISO code")
    imp = str(impact).strip().lower()
    if imp not in IMPACT_ORDER:
        raise CalendarFileError(f"{name}: impact {impact!r} is not low/medium/high")
    end = None if window_end is None else _utc(window_end, f"{name} window_end")
    if end is not None and end < t:
        raise CalendarFileError(f"{name}: window_end before event_time")
    return {
        "event_id": f"{source}:{ccy}:{name}:{t.isoformat()}",
        "event_time": t.isoformat(),
        "currency": ccy,
        "name": name,
        "impact": imp,
        "actual": None,
        "forecast": None,
        "previous": None,
        "recurring_key": recurring_key,
        "source": source,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "time_known": bool(time_known),
        "window_end": None if end is None else end.isoformat(),
        "tags": list(tags),
    }


def snapshot_path(root: str | Path, source: str, retrieved_at: pd.Timestamp) -> Path:
    """``<root>/calendar/<source>/<YYYYMMDDTHHMMSSZ>.json``."""
    stamp = _utc(retrieved_at, "retrieved_at").strftime("%Y%m%dT%H%M%SZ")
    return Path(root).expanduser() / "calendar" / source / f"{stamp}.json"


def write_events_file(
    path: str | Path,
    events: Sequence[Mapping[str, Any]],
    *,
    source: str,
    retrieved_at: str,
    manifest: Mapping[str, Any] | None = None,
) -> Path:
    """Write a dated-events snapshot once. Refuses to overwrite, and refuses
    the official fixture's name wherever it is asked to write it."""
    p = Path(path)
    if p.name == OFFICIAL_FIXTURE_NAME:
        raise CalendarFileError(
            f"refusing to write {p}: that is the official calendar's file name, and a "
            "secondary source must never replace it"
        )
    if p.exists():
        raise CalendarFileError(f"{p} exists; dated-event snapshots are write-once")
    for e in events:
        if e.get("source") != source:
            raise CalendarFileError(f"event {e.get('name')!r} is tagged {e.get('source')!r}, not {source!r}")
        if any(e.get(k) is not None for k in ("actual", "forecast", "previous")):
            raise CalendarFileError(f"event {e.get('name')!r} carries values; scheduled times only")
    body = {
        "schema_version": DATED_EVENTS_SCHEMA_VERSION,
        "source": source,
        "retrieved_at": retrieved_at,
        **dict(manifest or {}),
        "events": sorted(events, key=lambda e: (e["event_time"], e["currency"], e["name"])),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


@dataclass(frozen=True, slots=True)
class DatedEventsFile:
    manifest: dict[str, Any]
    events: tuple[dict[str, Any], ...]


def load_events_file(path: str | Path) -> DatedEventsFile:
    """Read a dated-events file (a list, or an object with ``events``), validating each event.

    Returns plain dicts so the data layer stays below ``marketstate``; the
    same file loads unchanged through ``marketstate.calendar.load_events_json``.
    """
    p = Path(path)
    if not p.exists():
        raise CalendarFileError(f"dated-events file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    manifest: dict[str, Any] = {}
    if isinstance(raw, dict):
        manifest = {k: v for k, v in raw.items() if k != "events"}
        raw = raw.get("events")
    if not isinstance(raw, list):
        raise CalendarFileError(f"{p}: expected a list of events")
    out = []
    for e in raw:
        _utc(e["event_time"], str(e.get("name")))
        if str(e.get("impact", "")).lower() not in IMPACT_ORDER:
            raise CalendarFileError(f"{p}: {e.get('name')!r} has impact {e.get('impact')!r}")
        if e.get("window_end") not in (None, ""):
            _utc(e["window_end"], f"{e.get('name')} window_end")
        out.append(dict(e))
    return DatedEventsFile(manifest=manifest, events=tuple(out))


# --------------------------------------------------------------------- diff


def _as_dict(e: Any) -> dict[str, Any]:
    to_dict = getattr(e, "to_dict", None)
    return dict(to_dict()) if callable(to_dict) else dict(e)


@dataclass(frozen=True, slots=True)
class _Ev:
    raw: dict[str, Any]
    start: pd.Timestamp
    end: pd.Timestamp
    currency: str
    impact: int


def _prep(events: Iterable[Any]) -> list[_Ev]:
    out = []
    for e in events:
        d = _as_dict(e)
        start = _utc(d["event_time"], str(d.get("name")))
        we = d.get("window_end")
        end = start if we in (None, "") else _utc(we, f"{d.get('name')} window_end")
        out.append(_Ev(d, start, end, str(d["currency"]).upper(),
                       IMPACT_ORDER.get(str(d.get("impact", "")).lower(), -1)))
    return out


@dataclass(frozen=True, slots=True)
class CalendarDiff:
    """What each side has that the other does not, inside ``window``."""

    window: tuple[str, str]
    currencies: tuple[str, ...]
    min_impact: str
    tolerance_s: float
    matched: tuple[tuple[dict[str, Any], dict[str, Any]], ...]
    only_in_official: tuple[dict[str, Any], ...]
    only_in_feed: tuple[dict[str, Any], ...]
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        def brief(d: Mapping[str, Any]) -> dict[str, Any]:
            return {k: d.get(k) for k in ("event_time", "currency", "name", "impact", "source")}

        return {
            "window": list(self.window),
            "currencies": list(self.currencies),
            "min_impact": self.min_impact,
            "tolerance_s": self.tolerance_s,
            "matched": len(self.matched),
            "only_in_official": [brief(d) for d in self.only_in_official],
            "only_in_feed": [brief(d) for d in self.only_in_feed],
            "notes": list(self.notes),
        }


def calendar_diff(
    official: Iterable[Any],
    feed: Iterable[Any],
    *,
    tolerance: timedelta = timedelta(minutes=5),
    currencies: Iterable[str] | None = None,
    min_impact: str = "high",
    start: Any = None,
    end: Any = None,
) -> CalendarDiff:
    """Events present in one calendar and not the other.

    Matching is by currency and time, never by name (publishers and
    aggregators name the same release differently): a feed event matches an
    official one of the same currency whose span ``[event_time, window_end]``,
    widened by ``tolerance``, contains the feed time. Each event matches at
    most once, nearest first, so two releases at the same instant need two
    events on each side.

    Scope, so the diff is not drowned in events nobody claimed to cover:

    * ``window`` defaults to the FEED's span (a weekly feed is compared on its
      week), narrowed by ``start``/``end`` when given;
    * ``currencies`` defaults to those present on BOTH sides;
    * both sides are filtered to ``impact >= min_impact``. An official event
      is kept whatever its stored impact when ``min_impact`` is "low".

    Accepts dicts (as written by this module) or objects with ``to_dict``
    (``marketstate.calendar.EconomicEvent``).
    """
    if min_impact not in IMPACT_ORDER:
        raise ValueError(f"min_impact must be one of {sorted(IMPACT_ORDER)}")
    off = _prep(official)
    fd = _prep(feed)
    notes: list[str] = []
    if fd:
        w0 = min(e.start for e in fd)
        w1 = max(e.end for e in fd)
    elif off:
        w0 = min(e.start for e in off)
        w1 = max(e.end for e in off)
        notes.append("the feed is empty: every official event in scope is reported as missing from it")
    else:
        w0 = w1 = pd.Timestamp("1970-01-01", tz="UTC")
    if start is not None:
        w0 = max(w0, _utc(start, "start"))
    if end is not None:
        w1 = min(w1, _utc(end, "end"))
    if currencies is None:
        ccys = sorted({e.currency for e in off} & {e.currency for e in fd})
    else:
        ccys = sorted({c.upper() for c in currencies})
    floor = IMPACT_ORDER[min_impact]

    def scope(evs: list[_Ev]) -> list[_Ev]:
        return [e for e in evs if e.currency in ccys and e.impact >= floor
                and e.end >= w0 and e.start <= w1]

    off_s = sorted(scope(off), key=lambda e: (e.start, e.currency))
    fd_s = sorted(scope(fd), key=lambda e: (e.start, e.currency))
    tol = pd.Timedelta(tolerance)
    used: set[int] = set()
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    missing_in_feed: list[dict[str, Any]] = []
    for o in off_s:
        best: tuple[pd.Timedelta, int] | None = None
        for j, f in enumerate(fd_s):
            if j in used or f.currency != o.currency:
                continue
            if o.start - tol <= f.start <= o.end + tol:
                gap = abs(f.start - o.start)
                if best is None or gap < best[0]:
                    best = (gap, j)
        if best is None:
            missing_in_feed.append(o.raw)
        else:
            used.add(best[1])
            matched.append((o.raw, fd_s[best[1]].raw))
    only_feed = [f.raw for j, f in enumerate(fd_s) if j not in used]
    return CalendarDiff(
        window=(w0.isoformat(), w1.isoformat()),
        currencies=tuple(ccys),
        min_impact=min_impact,
        tolerance_s=tol.total_seconds(),
        matched=tuple(matched),
        only_in_official=tuple(missing_in_feed),
        only_in_feed=tuple(only_feed),
        notes=tuple(notes),
    )
