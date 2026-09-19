"""The live executable-price recorder.

Why this exists before a broker does
------------------------------------
A backtest fills at a bar price plus a static spread assumption. A live account
fills at whatever was actually quotable, after latency, with a spread that
widens at the exact moments a strategy most wants to trade. The gap between
those two numbers is the single largest unknown in the platform, and it cannot
be measured retrospectively: the quotes are gone.

So the recorder is built now and started now. It captures executable prices from
whatever source is available — today a deterministic simulator, tomorrow an IG
or OANDA stream — into an append-only log that a replay reader can turn back
into a frame. When a broker is finally connected, there is already a reference
series to compare against instead of a year of waiting.

Crash safety
------------
Recording runs unattended for months; the process *will* be killed mid-write.
The log format makes a torn write detectable rather than corrupting:

* one record per line: ``<crc32 hex> <payload length> <json>``;
* the reader verifies the CRC and the declared length of every line;
* a truncated or corrupt final line is reported and skipped, and every record
  before it is still readable;
* segments rotate on a size/record budget, and each closed segment gets a
  manifest written via ``tmp`` + ``os.replace`` (atomic on POSIX);
* ``fsync_every`` controls the durability/throughput trade-off explicitly
  rather than leaving it to the OS page cache.

A corrupt line *in the middle* of a segment is a different animal from a torn
tail — it means real corruption, not a crash — so it is reported separately and
the reader refuses to pretend it did not happen.
"""
from __future__ import annotations

import json
import os
import random
import zlib
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.data.calendars import SessionCalendar, calendar_for
from fiboki.data.schema import MarketState

RECORDER_FORMAT_VERSION = 1
SEGMENT_PREFIX = "seg"
SEGMENT_SUFFIX = ".ndjson"
MANIFEST_SUFFIX = ".manifest.json"


class RecorderError(RuntimeError):
    pass


class LogCorruption(RecorderError):
    """A corrupt record was found somewhere other than the very end of a segment."""


# ------------------------------------------------- append-only log core


@dataclass(frozen=True, slots=True)
class ReadReport:
    """What the reader saw. Never silently swallowed."""

    records_read: int
    segments_read: int
    truncated_tail: bool
    corrupt_interior_lines: tuple[tuple[str, int], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.truncated_tail and not self.corrupt_interior_lines


def encode_record(payload: dict[str, Any]) -> bytes:
    """``<crc32 hex> <len> <json>\\n`` — self-describing and self-verifying."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return f"{crc:08x} {len(body)} ".encode("ascii") + body + b"\n"


def decode_record(line: bytes) -> dict[str, Any]:
    """Decode and verify one framed record. Raises on any mismatch."""
    if not line.endswith(b"\n"):
        raise ValueError("record is not newline-terminated (torn write)")
    stripped = line[:-1]
    try:
        crc_hex, rest = stripped.split(b" ", 1)
        length_bytes, body = rest.split(b" ", 1)
        declared_len = int(length_bytes)
    except ValueError as exc:
        raise ValueError(f"malformed record frame: {exc}") from exc
    if len(body) != declared_len:
        raise ValueError(f"length mismatch: declared {declared_len}, got {len(body)}")
    if (zlib.crc32(body) & 0xFFFFFFFF) != int(crc_hex, 16):
        raise ValueError("crc32 mismatch")
    return json.loads(body)


class AppendOnlyLog:
    """Segmented, CRC-framed, append-only writer.

    Used by both the quote recorder and the execution telemetry store; the
    durability problem is identical and solving it twice would guarantee the two
    solutions drift.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        max_records_per_segment: int = 100_000,
        max_bytes_per_segment: int = 64 * 1024 * 1024,
        fsync_every: int = 1,
        stream_name: str = "stream",
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_records_per_segment = max_records_per_segment
        self.max_bytes_per_segment = max_bytes_per_segment
        self.fsync_every = max(0, int(fsync_every))
        self.stream_name = stream_name

        self._segment_index = self._next_segment_index()
        self._handle: Any = None
        self._records_in_segment = 0
        self._bytes_in_segment = 0
        self._since_fsync = 0
        self._total_records = 0
        self._closed = False
        self._open_segment()

    # -- segments ----------------------------------------------------

    def _next_segment_index(self) -> int:
        existing = sorted(self.directory.glob(f"{SEGMENT_PREFIX}-*{SEGMENT_SUFFIX}"))
        if not existing:
            return 0
        last = existing[-1].name
        # "seg-000007.ndjson" -> 7. Parse the stem, not the raw split, or the
        # suffix rides along with the number.
        return int(last[len(SEGMENT_PREFIX) + 1 : -len(SEGMENT_SUFFIX)]) + 1

    def _segment_path(self, index: int) -> Path:
        return self.directory / f"{SEGMENT_PREFIX}-{index:06d}{SEGMENT_SUFFIX}"

    def _open_segment(self) -> None:
        self.current_path = self._segment_path(self._segment_index)
        # Deliberately not a context manager: the handle is owned by this log
        # for the life of the segment and is closed by rotate()/close(). An
        # append-only recorder that reopened the file per record would fsync
        # itself to a standstill.
        self._handle = open(self.current_path, "ab", buffering=0)  # noqa: SIM115
        self._records_in_segment = 0
        self._bytes_in_segment = self.current_path.stat().st_size
        self._opened_at = datetime.now(tz=UTC)

    def _write_manifest(self) -> None:
        manifest = {
            "format_version": RECORDER_FORMAT_VERSION,
            "stream": self.stream_name,
            "segment": self._segment_index,
            "records": self._records_in_segment,
            "bytes": self._bytes_in_segment,
            "opened_at": self._opened_at.isoformat(),
            "closed_at": datetime.now(tz=UTC).isoformat(),
        }
        target = self.directory / f"{SEGMENT_PREFIX}-{self._segment_index:06d}{MANIFEST_SUFFIX}"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)  # atomic

    def rotate(self) -> None:
        """Close the current segment atomically and start the next."""
        if self._handle is not None:
            os.fsync(self._handle.fileno())
            self._handle.close()
            self._write_manifest()
        self._segment_index += 1
        self._open_segment()

    # -- writing -----------------------------------------------------

    def append(self, payload: dict[str, Any]) -> None:
        if self._closed:
            raise RecorderError("log is closed")
        blob = encode_record(payload)
        self._handle.write(blob)
        self._records_in_segment += 1
        self._bytes_in_segment += len(blob)
        self._total_records += 1
        self._since_fsync += 1
        if self.fsync_every and self._since_fsync >= self.fsync_every:
            os.fsync(self._handle.fileno())
            self._since_fsync = 0
        if (
            self._records_in_segment >= self.max_records_per_segment
            or self._bytes_in_segment >= self.max_bytes_per_segment
        ):
            self.rotate()

    def flush(self) -> None:
        if self._handle is not None:
            os.fsync(self._handle.fileno())
            self._since_fsync = 0

    def close(self) -> None:
        if self._closed:
            return
        if self._handle is not None:
            os.fsync(self._handle.fileno())
            self._handle.close()
            self._write_manifest()
            self._handle = None
        self._closed = True

    def __enter__(self) -> AppendOnlyLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def total_records(self) -> int:
        return self._total_records


class LogReader:
    """Reads segments in order, verifying every record.

    A torn final line (the crash case) is skipped and reported; a corrupt line
    with valid lines after it is real corruption and raises unless the caller
    explicitly opts into lossy reading.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        if not self.directory.exists():
            raise RecorderError(f"no such log directory: {self.directory}")

    def segments(self) -> list[Path]:
        return sorted(self.directory.glob(f"{SEGMENT_PREFIX}-*{SEGMENT_SUFFIX}"))

    def iter_records(
        self, *, tolerate_interior_corruption: bool = False
    ) -> Iterator[dict[str, Any]]:
        yield from self._iter_with_report(tolerate_interior_corruption)

    def read_all(
        self, *, tolerate_interior_corruption: bool = False
    ) -> tuple[list[dict[str, Any]], ReadReport]:
        report_holder: list[ReadReport] = []
        records = list(
            self._iter_with_report(tolerate_interior_corruption, report_holder)
        )
        report = report_holder[0] if report_holder else ReadReport(0, 0, False)
        return records, report

    def _iter_with_report(
        self,
        tolerate_interior_corruption: bool,
        report_holder: list[ReadReport] | None = None,
    ) -> Iterator[dict[str, Any]]:
        truncated_tail = False
        corrupt_interior: list[tuple[str, int]] = []
        count = 0
        segs = self.segments()
        for seg_no, seg in enumerate(segs):
            raw = seg.read_bytes()
            lines = raw.splitlines(keepends=True)
            for line_no, line in enumerate(lines):
                is_last_line_of_last_segment = (
                    seg_no == len(segs) - 1 and line_no == len(lines) - 1
                )
                try:
                    payload = decode_record(line)
                except (ValueError, json.JSONDecodeError):
                    if is_last_line_of_last_segment:
                        truncated_tail = True
                        continue
                    corrupt_interior.append((seg.name, line_no))
                    if tolerate_interior_corruption:
                        continue
                    raise LogCorruption(
                        f"corrupt record at {seg.name}:{line_no} with valid records "
                        "after it. This is data corruption, not a crash, and the "
                        "reader will not pretend otherwise."
                    ) from None
                count += 1
                yield payload
        if report_holder is not None:
            report_holder.append(
                ReadReport(
                    records_read=count,
                    segments_read=len(segs),
                    truncated_tail=truncated_tail,
                    corrupt_interior_lines=tuple(corrupt_interior),
                )
            )


# ------------------------------------------------------- quote records


@dataclass(frozen=True, slots=True)
class QuoteRecord:
    """One executable-price observation.

    ``mid`` and ``spread`` are derived from bid/ask rather than quoted, and say
    so via ``price_basis`` on the way into any bar built from these.
    """

    timestamp: datetime
    instrument: str
    bid: float
    ask: float
    provider: str
    market_state: MarketState = MarketState.UNKNOWN
    session_open: bool = True
    latency_ms: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    tick_volume: int | None = None
    trade_price: float | None = None
    trade_size: float | None = None
    depth: tuple[tuple[float, float, float, float], ...] = ()
    quote_id: str | None = None
    broker_status: str | None = None
    sequence: int = 0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    def spread_pips(self) -> float:
        try:
            pip = instrument_registry.get(self.instrument).pip_size
        except KeyError:
            return float("nan")
        return self.spread / pip

    def to_payload(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.astimezone(UTC).isoformat()
        d["market_state"] = self.market_state.value
        d["mid"] = self.mid
        d["spread"] = self.spread
        d["spread_pips"] = self.spread_pips()
        d["depth"] = [list(level) for level in self.depth]
        return d

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> QuoteRecord:
        return cls(
            timestamp=datetime.fromisoformat(raw["timestamp"]),
            instrument=raw["instrument"],
            bid=float(raw["bid"]),
            ask=float(raw["ask"]),
            provider=raw["provider"],
            market_state=MarketState(raw.get("market_state", "unknown")),
            session_open=bool(raw.get("session_open", True)),
            latency_ms=raw.get("latency_ms"),
            bid_size=raw.get("bid_size"),
            ask_size=raw.get("ask_size"),
            tick_volume=raw.get("tick_volume"),
            trade_price=raw.get("trade_price"),
            trade_size=raw.get("trade_size"),
            depth=tuple(tuple(level) for level in raw.get("depth", ())),  # type: ignore[misc]
            quote_id=raw.get("quote_id"),
            broker_status=raw.get("broker_status"),
            sequence=int(raw.get("sequence", 0)),
        )


class QuoteFeed(ABC):
    """A source of executable prices. Implemented per broker, later."""

    name: str = "abstract"

    @abstractmethod
    def stream(self, instruments: list[str], *, limit: int | None = None) -> Iterator[QuoteRecord]:
        """Yield quotes until ``limit`` records or the feed ends."""


class SimulatedQuoteFeed(QuoteFeed):
    """Deterministic simulated feed, so the recorder can run with no broker.

    Not a market model and not used for research. Its job is to exercise the
    recorder, the rotation logic and the replay reader on realistic-shaped
    input: a seeded random walk, a spread that widens outside the session and
    around the top of the hour, and occasional latency spikes.
    """

    name = "simulated"

    def __init__(
        self,
        *,
        seed: int = 7,
        start: datetime | None = None,
        interval_ms: int = 250,
        calendar: SessionCalendar | None = None,
    ) -> None:
        self.seed = seed
        self.interval_ms = interval_ms
        self.start = start or datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
        self._calendar = calendar

    def _calendar_for(self, instrument: str) -> SessionCalendar:
        if self._calendar is not None:
            return self._calendar
        try:
            return calendar_for(instrument)
        except KeyError:
            from fiboki.data.calendars import FX_CALENDAR

            return FX_CALENDAR

    def stream(
        self, instruments: list[str], *, limit: int | None = None
    ) -> Iterator[QuoteRecord]:
        rng = random.Random(self.seed)
        state: dict[str, float] = {}
        for sym in instruments:
            try:
                inst = instrument_registry.get(sym)
                state[sym] = 1.10 if inst.is_fx else 2000.0
            except KeyError:
                state[sym] = 1.0
        emitted = 0
        ts = self.start
        seq = 0
        while limit is None or emitted < limit:
            for sym in instruments:
                inst_pip = 0.0001
                base_spread_pips = 1.0
                try:
                    inst = instrument_registry.get(sym)
                    inst_pip = inst.pip_size
                    base_spread_pips = inst.typical_spread_pips
                except KeyError:
                    pass
                cal = self._calendar_for(sym)
                pd_ts = pd.Timestamp(ts)
                is_open = cal.is_open(pd_ts)

                drift = rng.gauss(0.0, 1.0) * inst_pip * 0.6
                state[sym] = max(state[sym] + drift, inst_pip * 10)
                mid = state[sym]

                widen = 1.0
                if not is_open:
                    widen *= 6.0
                if ts.minute == 0 and ts.second < 5:
                    widen *= 2.5
                spread = base_spread_pips * inst_pip * widen
                bid = mid - spread / 2.0
                ask = mid + spread / 2.0

                latency = 12.0 + abs(rng.gauss(0.0, 4.0))
                if rng.random() < 0.01:
                    latency += rng.uniform(80.0, 400.0)

                yield QuoteRecord(
                    timestamp=ts,
                    instrument=sym,
                    bid=round(bid, 7),
                    ask=round(ask, 7),
                    provider=self.name,
                    market_state=MarketState.OPEN if is_open else MarketState.CLOSED,
                    session_open=is_open,
                    latency_ms=round(latency, 3),
                    bid_size=round(rng.uniform(0.5, 5.0) * 1e6, 2),
                    ask_size=round(rng.uniform(0.5, 5.0) * 1e6, 2),
                    tick_volume=1,
                    quote_id=f"sim-{seq:012d}",
                    broker_status="ok",
                    sequence=seq,
                )
                seq += 1
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
            ts = ts + timedelta(milliseconds=self.interval_ms)


class QuoteRecorder:
    """Writes :class:`QuoteRecord` to a crash-safe append-only log."""

    def __init__(
        self,
        directory: str | Path,
        *,
        max_records_per_segment: int = 50_000,
        fsync_every: int = 1,
    ) -> None:
        self.log = AppendOnlyLog(
            directory,
            max_records_per_segment=max_records_per_segment,
            fsync_every=fsync_every,
            stream_name="quotes",
        )

    def record(self, quote: QuoteRecord) -> None:
        self.log.append(quote.to_payload())

    def record_many(self, quotes: Iterable[QuoteRecord]) -> int:
        n = 0
        for q in quotes:
            self.record(q)
            n += 1
        return n

    def run(
        self, feed: QuoteFeed, instruments: list[str], *, limit: int | None = None
    ) -> int:
        return self.record_many(feed.stream(instruments, limit=limit))

    def close(self) -> None:
        self.log.close()

    def __enter__(self) -> QuoteRecorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class QuoteReplayReader:
    """Reads a recorded quote log back, as records or as a frame."""

    def __init__(self, directory: str | Path) -> None:
        self.reader = LogReader(directory)

    def records(
        self,
        *,
        instrument: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        tolerate_interior_corruption: bool = False,
    ) -> tuple[list[QuoteRecord], ReadReport]:
        raw, report = self.reader.read_all(
            tolerate_interior_corruption=tolerate_interior_corruption
        )
        out: list[QuoteRecord] = []
        for payload in raw:
            q = QuoteRecord.from_payload(payload)
            if instrument and q.instrument != instrument:
                continue
            if start and q.timestamp < start:
                continue
            if end and q.timestamp > end:
                continue
            out.append(q)
        return out, report

    def frame(self, **kwargs: Any) -> tuple[pd.DataFrame, ReadReport]:
        records, report = self.records(**kwargs)
        if not records:
            empty = pd.DataFrame(
                columns=[
                    "instrument", "bid", "ask", "mid", "spread", "spread_pips",
                    "provider", "latency_ms", "market_state", "session_open",
                    "tick_volume", "quote_id", "broker_status", "sequence",
                ]
            )
            empty.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
            return empty, report
        rows = []
        for q in records:
            rows.append(
                {
                    "timestamp": pd.Timestamp(q.timestamp).tz_convert("UTC"),
                    "instrument": q.instrument,
                    "bid": q.bid,
                    "ask": q.ask,
                    "mid": q.mid,
                    "spread": q.spread,
                    "spread_pips": q.spread_pips(),
                    "provider": q.provider,
                    "latency_ms": q.latency_ms,
                    "market_state": q.market_state.value,
                    "session_open": q.session_open,
                    "tick_volume": q.tick_volume,
                    "quote_id": q.quote_id,
                    "broker_status": q.broker_status,
                    "sequence": q.sequence,
                }
            )
        df = pd.DataFrame(rows).set_index("timestamp").sort_index(kind="stable")
        return df, report


def spread_profile(frame: pd.DataFrame, *, by: str = "hour") -> pd.DataFrame:
    """Observed spread by hour (or weekday) — the input to realistic cost models.

    This is the number a backtest should eventually use instead of a single
    static ``typical_spread_pips``. Until enough has been recorded, the static
    assumption stands and is documented as an approximation.
    """
    if frame.empty:
        return pd.DataFrame(columns=["count", "median_pips", "p90_pips", "max_pips"])
    key = frame.index.hour if by == "hour" else frame.index.dayofweek
    grouped = frame.groupby(key)["spread_pips"]
    out = pd.DataFrame(
        {
            "count": grouped.count(),
            "median_pips": grouped.median(),
            "p90_pips": grouped.quantile(0.90),
            "max_pips": grouped.max(),
        }
    )
    out.index.name = by
    return out


def quotes_to_bars(
    frame: pd.DataFrame, *, timeframe_minutes: int = 1, side: str = "mid"
) -> pd.DataFrame:
    """Build OHLC bars from recorded quotes on a chosen side.

    ``side`` is explicit and flows into ``price_basis`` downstream: bars built
    from the bid are BID bars, and V2 will not let them be mistaken for mid.
    """
    if side not in ("bid", "ask", "mid"):
        raise ValueError(f"side must be bid/ask/mid, got {side!r}")
    if frame.empty:
        raise RecorderError("no quotes to bar")
    series = frame[side]
    rule = f"{timeframe_minutes}min"
    grouped = series.resample(rule, origin="epoch", label="left", closed="left")
    bars = pd.DataFrame(
        {
            "open": grouped.first(),
            "high": grouped.max(),
            "low": grouped.min(),
            "close": grouped.last(),
            "tick_volume": series.resample(
                rule, origin="epoch", label="left", closed="left"
            ).count(),
        }
    )
    bars = bars[bars["open"].notna()]
    bars.index.name = "timestamp"
    return bars
