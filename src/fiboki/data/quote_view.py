"""Read-only views of the recorded quote log, for the operator's monitors.

The executable-price recorder (``fiboki quotes record``, :mod:`fiboki.data.
recorder`) appends one CRC-framed record per changed quote to
``<state>/quotes/seg-NNNNNN.ndjson``. The watchlist, Market Pulse and the price
alert evaluator need the RECENT part of that log (a day, not the year), many
times a minute. :class:`QuoteTail` reads it newest segment first, stops at the
first segment that starts before the window, and caches each parsed segment by
``(size, mtime)`` so a poll re-reads only what the recorder appended since.

Honest about damage, as the replay reader is: a torn final line (the recorder
was killed mid-write) is skipped and reported; a corrupt interior line is
skipped and COUNTED, and the count travels to the API as a caveat. Nothing here
writes, repairs or invents a quote. This module is a view; it is not the
research path (research reads bars from the dataset store, never these).
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fiboki.data.recorder import (
    SEGMENT_PREFIX,
    SEGMENT_SUFFIX,
    QuoteRecord,
    decode_record,
)

__all__ = ["QuoteTail", "QuoteWindow", "read_window"]


@dataclass(frozen=True, slots=True)
class QuoteWindow:
    """The quotes recorded in ``[since, until]``, per instrument, oldest first."""

    since: datetime
    until: datetime
    by_instrument: dict[str, list[QuoteRecord]]
    segments_read: int
    records_read: int
    torn_tail: bool
    corrupt_lines: int
    #: Modification time of the newest segment: when the recorder last wrote.
    newest_write: datetime | None
    #: ``True`` when the log directory does not exist (never recorded).
    absent: bool = False

    def latest(self, instrument: str) -> QuoteRecord | None:
        rows = self.by_instrument.get(instrument)
        return rows[-1] if rows else None

    @property
    def instruments(self) -> list[str]:
        return sorted(self.by_instrument)


@dataclass
class _Parsed:
    size: int
    mtime_ns: int
    offset: int
    records: list[QuoteRecord] = field(default_factory=list)
    corrupt: int = 0
    torn: bool = False


def _parse_lines(raw: bytes, *, final: bool) -> tuple[list[QuoteRecord], int, bool, int]:
    """Records, corrupt count, torn-tail flag and the bytes consumed."""
    out: list[QuoteRecord] = []
    corrupt = 0
    torn = False
    consumed = 0
    lines = raw.splitlines(keepends=True)
    for i, line in enumerate(lines):
        # An unterminated last line: the writer may still be mid-append.
        # Leave it unconsumed so the next read sees it whole.
        if i == len(lines) - 1 and not line.endswith(b"\n"):
            torn = final
            break
        try:
            out.append(QuoteRecord.from_payload(decode_record(line)))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            corrupt += 1
        consumed += len(line)
    return out, corrupt, torn, consumed


class QuoteTail:
    """Cached, incremental reader of the newest part of a quote log."""

    def __init__(self, directory: str | Path, *, max_cached_segments: int = 6) -> None:
        self.directory = Path(directory)
        self.max_cached_segments = max_cached_segments
        self._cache: dict[str, _Parsed] = {}
        self._lock = threading.Lock()

    def _segments(self) -> list[Path]:
        return sorted(self.directory.glob(f"{SEGMENT_PREFIX}-*{SEGMENT_SUFFIX}"))

    def _load(self, path: Path, *, newest: bool) -> _Parsed:
        stat = path.stat()
        key = str(path)
        hit = self._cache.get(key)
        if hit is not None and hit.size == stat.st_size and hit.mtime_ns == stat.st_mtime_ns:
            return hit
        if hit is not None and stat.st_size > hit.offset and hit.size <= stat.st_size:
            # Appended since the last read: parse only the new bytes.
            with path.open("rb") as fh:
                fh.seek(hit.offset)
                raw = fh.read()
            recs, corrupt, torn, consumed = _parse_lines(raw, final=not newest)
            hit.records.extend(recs)
            hit.corrupt += corrupt
            hit.torn = torn
            hit.offset += consumed
            hit.size = stat.st_size
            hit.mtime_ns = stat.st_mtime_ns
            return hit
        raw = path.read_bytes()
        recs, corrupt, torn, consumed = _parse_lines(raw, final=not newest)
        parsed = _Parsed(stat.st_size, stat.st_mtime_ns, consumed, recs, corrupt, torn)
        self._cache[key] = parsed
        if len(self._cache) > self.max_cached_segments:
            for old in sorted(self._cache)[: len(self._cache) - self.max_cached_segments]:
                self._cache.pop(old, None)
        return parsed

    def window(
        self,
        since: datetime,
        until: datetime,
        *,
        instruments: Iterable[str] | None = None,
    ) -> QuoteWindow:
        """Quotes with ``since <= timestamp <= until``, read-only."""
        if since.tzinfo is None or until.tzinfo is None:
            raise ValueError("since and until must be timezone-aware UTC")
        wanted = None if instruments is None else {s.upper() for s in instruments}
        if not self.directory.is_dir():
            return QuoteWindow(since, until, {}, 0, 0, False, 0, None, absent=True)
        with self._lock:
            segments = self._segments()
            by: dict[str, list[QuoteRecord]] = defaultdict(list)
            read = records = corrupt = 0
            torn = False
            newest_write: datetime | None = None
            for index, path in enumerate(reversed(segments)):
                try:
                    parsed = self._load(path, newest=index == 0)
                    if index == 0:
                        newest_write = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                except OSError:
                    continue
                read += 1
                corrupt += parsed.corrupt
                torn = torn or (index == 0 and parsed.torn)
                for rec in parsed.records:
                    ts = rec.timestamp if rec.timestamp.tzinfo else rec.timestamp.replace(tzinfo=UTC)
                    if ts < since or ts > until:
                        continue
                    if wanted is not None and rec.instrument.upper() not in wanted:
                        continue
                    by[rec.instrument.upper()].append(rec)
                    records += 1
                first = parsed.records[0].timestamp if parsed.records else None
                if first is not None and first <= since:
                    break
        for rows in by.values():
            rows.sort(key=lambda r: (r.timestamp, r.sequence))
        return QuoteWindow(since, until, dict(by), read, records, torn, corrupt, newest_write)


_TAILS: dict[str, QuoteTail] = {}
_TAILS_LOCK = threading.Lock()


def read_window(
    directory: str | Path,
    since: datetime,
    until: datetime,
    *,
    instruments: Iterable[str] | None = None,
) -> QuoteWindow:
    """One shared :class:`QuoteTail` per directory (the API process's cache)."""
    key = str(Path(directory))
    with _TAILS_LOCK:
        tail = _TAILS.get(key)
        if tail is None:
            tail = _TAILS[key] = QuoteTail(directory)
    return tail.window(since, until, instruments=instruments)
