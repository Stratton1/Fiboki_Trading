"""The canonical bar schema and its dataset-level metadata.

V1 stored five float columns and a timestamp, and nothing else. That is how
HistData's *bid-only* bars, timestamped in EST-without-DST, ended up being fed
to strategies as if they were mid prices in UTC. The spread was therefore
double-counted on one side and ignored on the other, and every session filter
was up to an hour wrong for half the year.

V2 refuses to store a bar without saying, in the data itself, what the price
*is*. ``price_basis`` is a required column, not an optional annotation, and the
dataset metadata additionally records the timezone the source used, the exact
ingestion code version, a content checksum, and every adjustment and repair
ever applied.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa

from fiboki.core.enums import DataQuality, Timeframe

# Bumped whenever the ingestion/normalisation code changes in a way that could
# alter stored bytes. Part of every dataset's identity.
INGESTION_VERSION = "2.0.0"

TIMESTAMP_COLUMN = "timestamp"


class PriceBasis(str, Enum):
    """What the OHLC numbers in a bar actually are.

    Never infer this. A provider declares it; an importer records it; a consumer
    that needs a different basis must convert explicitly and say so.
    """

    BID = "bid"
    ASK = "ask"
    MID = "mid"
    LAST = "last"
    SYNTHETIC_MID = "synthetic_mid"  # (bid+ask)/2 computed by us, not quoted

    @property
    def is_executable_both_sides(self) -> bool:
        """True only if a buy and a sell can both be priced off this basis."""
        return self in (PriceBasis.MID, PriceBasis.SYNTHETIC_MID)


class MarketState(str, Enum):
    """Venue state at the moment a quote or bar was observed."""

    OPEN = "open"
    CLOSED = "closed"
    PRE_OPEN = "pre_open"
    HALTED = "halted"
    AUCTION = "auction"
    UNKNOWN = "unknown"


class DatasetKind(str, Enum):
    """Where a dataset sits in the lineage chain."""

    RAW = "raw"
    VALIDATED = "validated"
    REPAIRED = "repaired"
    RESAMPLED = "resampled"
    FEATURE = "feature"


# --------------------------------------------------------------- columns

IDENTITY_COLUMNS: tuple[str, ...] = ("instrument", "timeframe", "price_basis")
OHLC_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")
VOLUME_COLUMNS: tuple[str, ...] = ("volume", "tick_volume")
BID_COLUMNS: tuple[str, ...] = ("bid_open", "bid_high", "bid_low", "bid_close")
ASK_COLUMNS: tuple[str, ...] = ("ask_open", "ask_high", "ask_low", "ask_close")

REQUIRED_COLUMNS: tuple[str, ...] = IDENTITY_COLUMNS + OHLC_COLUMNS
OPTIONAL_COLUMNS: tuple[str, ...] = VOLUME_COLUMNS + BID_COLUMNS + ASK_COLUMNS
ALL_COLUMNS: tuple[str, ...] = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

_FLOAT_COLUMNS = frozenset(OHLC_COLUMNS + BID_COLUMNS + ASK_COLUMNS)
_INT_COLUMNS = frozenset(VOLUME_COLUMNS)
_STRING_COLUMNS = frozenset(IDENTITY_COLUMNS)

# Canonical column order. Stored frames are written in exactly this order so
# that the content checksum is a function of the data, not of dict iteration.
COLUMN_ORDER: tuple[str, ...] = ALL_COLUMNS


class SchemaError(ValueError):
    """The frame is not a canonical bar frame. Loudly, before anything reads it."""


def arrow_schema(columns: tuple[str, ...] | None = None) -> pa.Schema:
    """The pyarrow schema for a canonical bar table (timestamp as a column)."""
    cols = columns or ALL_COLUMNS
    fields = [pa.field(TIMESTAMP_COLUMN, pa.timestamp("us", tz="UTC"), nullable=False)]
    for name in COLUMN_ORDER:
        if name not in cols:
            continue
        if name in _STRING_COLUMNS:
            fields.append(pa.field(name, pa.string(), nullable=False))
        elif name in _INT_COLUMNS:
            fields.append(pa.field(name, pa.int64(), nullable=True))
        else:
            fields.append(pa.field(name, pa.float64(), nullable=True))
    return pa.schema(fields)


def canonical_frame(
    frame: pd.DataFrame,
    *,
    instrument: str,
    timeframe: Timeframe | str,
    price_basis: PriceBasis | str,
    source_timezone: str | None = None,
) -> pd.DataFrame:
    """Coerce ``frame`` into the canonical bar shape.

    This is a *shape* operation only: it reorders and types columns, stamps the
    identity columns, and enforces a tz-aware UTC monotonic index. It does not
    clean, repair, fill, dedupe or sort away problems — those are integrity
    concerns and must be explicit (see :mod:`fiboki.data.integrity`). The one
    thing it will do is raise.

    ``source_timezone`` is documentation only; the index must already be true
    UTC by the time it gets here. Converting a provider's local convention to
    UTC is the *provider's* job, because only the provider knows the convention.
    """
    tf = Timeframe(timeframe) if not isinstance(timeframe, Timeframe) else timeframe
    pb = PriceBasis(price_basis) if not isinstance(price_basis, PriceBasis) else price_basis

    out = frame.copy()

    # Accept a timestamp column or an index; normalise to an index.
    if TIMESTAMP_COLUMN in out.columns:
        out = out.set_index(TIMESTAMP_COLUMN)
    if not isinstance(out.index, pd.DatetimeIndex):
        raise SchemaError(
            f"bar frame index must be a DatetimeIndex, got {type(out.index).__name__}"
        )
    if out.index.tz is None:
        raise SchemaError(
            "bar frame index is timezone-naive. V2 never guesses a timezone: the "
            "provider must declare the source convention and convert to UTC. "
            "This is the HistData EST-without-DST bug."
        )
    out.index = out.index.tz_convert("UTC").as_unit("us")
    # Drop any inferred freq: a bar frame with real gaps does not have one, and
    # carrying it makes an arrow round-trip compare unequal for no reason.
    out.index.freq = None
    out.index.name = TIMESTAMP_COLUMN

    missing = [c for c in OHLC_COLUMNS if c not in out.columns]
    if missing:
        raise SchemaError(f"bar frame missing required OHLC columns: {missing}")

    out["instrument"] = str(instrument).upper()
    out["timeframe"] = tf.value
    out["price_basis"] = pb.value

    for col in list(out.columns):
        if col not in ALL_COLUMNS:
            out = out.drop(columns=[col])

    for col in out.columns:
        if col in _FLOAT_COLUMNS:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
        elif col in _INT_COLUMNS:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(-1).astype("int64")
        else:
            out[col] = out[col].astype("string").astype("object")

    ordered = [c for c in COLUMN_ORDER if c in out.columns]
    return out[ordered]


def validate_frame_shape(frame: pd.DataFrame) -> None:
    """Raise :class:`SchemaError` unless ``frame`` is canonically shaped.

    Shape only — says nothing about whether the *data* is any good.
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise SchemaError("index must be a DatetimeIndex")
    if frame.index.tz is None:
        raise SchemaError("index must be timezone-aware UTC")
    if str(frame.index.tz) not in ("UTC", "utc"):
        raise SchemaError(f"index must be UTC, got {frame.index.tz}")
    if frame.index.name != TIMESTAMP_COLUMN:
        raise SchemaError(f"index must be named {TIMESTAMP_COLUMN!r}")
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise SchemaError(f"missing required columns: {missing}")
    unknown = [c for c in frame.columns if c not in ALL_COLUMNS]
    if unknown:
        raise SchemaError(f"unknown columns: {unknown}")
    if len(frame):
        bases = set(frame["price_basis"].unique())
        if len(bases) != 1:
            raise SchemaError(f"frame mixes price bases: {sorted(bases)}")
        PriceBasis(next(iter(bases)))
        tfs = set(frame["timeframe"].unique())
        if len(tfs) != 1:
            raise SchemaError(f"frame mixes timeframes: {sorted(tfs)}")
        Timeframe(next(iter(tfs)))
        insts = set(frame["instrument"].unique())
        if len(insts) != 1:
            raise SchemaError(f"frame mixes instruments: {sorted(insts)}")


def frame_to_arrow(frame: pd.DataFrame) -> pa.Table:
    """Serialise a canonical frame to arrow with the exact canonical schema."""
    validate_frame_shape(frame)
    flat = frame.reset_index()
    cols = tuple(frame.columns)
    return pa.Table.from_pandas(
        flat, schema=arrow_schema(cols), preserve_index=False
    )


def frame_from_arrow(table: pa.Table) -> pd.DataFrame:
    """Read a canonical arrow table back into a canonical frame."""
    df = table.to_pandas()
    if TIMESTAMP_COLUMN in df.columns:
        df = df.set_index(TIMESTAMP_COLUMN)
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC").as_unit("us")
    df.index.name = TIMESTAMP_COLUMN
    for col in df.columns:
        if col in _STRING_COLUMNS:
            df[col] = df[col].astype("object")
    ordered = [c for c in COLUMN_ORDER if c in df.columns]
    df = df[ordered]
    validate_frame_shape(df)
    return df


def content_checksum(frame: pd.DataFrame) -> str:
    """A deterministic content hash of a canonical bar frame.

    Deterministic across processes and machines: it is built from the raw
    little-endian bytes of each column in a fixed order, with the index encoded
    as int64 nanoseconds. It deliberately does *not* include any timestamp of
    when the hash was taken, so identical content always hashes identically.
    That property is what makes dataset ids content-addressed.
    """
    validate_frame_shape(frame)
    h = hashlib.blake2b(digest_size=32)
    h.update(b"fiboki-bars-v1\x00")
    # Nanoseconds, always, regardless of the index's storage unit: the checksum
    # must not change because pandas stored microseconds instead of nanoseconds.
    idx = pd.DatetimeIndex(frame.index).tz_convert("UTC").as_unit("ns").asi8
    h.update(np.ascontiguousarray(np.asarray(idx, dtype="<i8")).tobytes())
    for col in COLUMN_ORDER:
        if col not in frame.columns:
            continue
        h.update(col.encode("utf-8"))
        h.update(b"\x00")
        series = frame[col]
        if col in _STRING_COLUMNS:
            for value in series.to_numpy(dtype=object):
                h.update(str(value).encode("utf-8"))
                h.update(b"\x1f")
        elif col in _INT_COLUMNS:
            h.update(np.ascontiguousarray(series.to_numpy(dtype="<i8")).tobytes())
        else:
            h.update(np.ascontiguousarray(series.to_numpy(dtype="<f8")).tobytes())
        h.update(b"\x1e")
    return h.hexdigest()


# ------------------------------------------------------- dataset metadata


@dataclass(frozen=True, slots=True)
class GapRecord:
    """One observed discontinuity in the bar index."""

    start: pd.Timestamp
    end: pd.Timestamp
    missing_bars: int
    expected: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "missing_bars": int(self.missing_bars),
            "expected": bool(self.expected),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GapRecord:
        return cls(
            start=pd.Timestamp(raw["start"]),
            end=pd.Timestamp(raw["end"]),
            missing_bars=int(raw["missing_bars"]),
            expected=bool(raw["expected"]),
            reason=str(raw["reason"]),
        )


@dataclass(frozen=True, slots=True)
class RepairRecord:
    """Exactly what a repair changed, and why. Written once, never edited."""

    applied_at: datetime
    action: str
    reason: str
    actor: str
    rows_before: int
    rows_after: int
    affected_timestamps: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["applied_at"] = self.applied_at.isoformat()
        d["affected_timestamps"] = list(self.affected_timestamps)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RepairRecord:
        return cls(
            applied_at=datetime.fromisoformat(raw["applied_at"]),
            action=raw["action"],
            reason=raw["reason"],
            actor=raw["actor"],
            rows_before=int(raw["rows_before"]),
            rows_after=int(raw["rows_after"]),
            affected_timestamps=tuple(raw.get("affected_timestamps", ())),
            detail=dict(raw.get("detail", {})),
        )


@dataclass(frozen=True, slots=True)
class Adjustment:
    """A declared, deliberate transformation of the source numbers.

    Timezone correction, price-basis conversion, split/dividend adjustment.
    Anything that makes the stored numbers differ from what the source shipped.
    """

    kind: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Adjustment:
        return cls(
            kind=raw["kind"],
            description=raw["description"],
            parameters=dict(raw.get("parameters", {})),
        )


@dataclass(frozen=True, slots=True)
class BarDatasetMetadata:
    """Everything needed to say what a stored bar dataset *is*.

    This travels with the parquet (as arrow key/value metadata) and into the
    version catalogue. If any field here is unknown, the honest value is
    recorded as unknown rather than guessed.
    """

    instrument: str
    timeframe: Timeframe
    price_basis: PriceBasis
    source: str
    source_identifier: str
    ingestion_version: str
    checksum: str
    row_count: int
    first_timestamp: pd.Timestamp | None
    last_timestamp: pd.Timestamp | None
    timezone_of_origin: str
    quality: DataQuality = DataQuality.RAW
    kind: DatasetKind = DatasetKind.RAW
    adjustments: tuple[Adjustment, ...] = ()
    gaps: tuple[GapRecord, ...] = ()
    repairs: tuple[RepairRecord, ...] = ()
    columns: tuple[str, ...] = ()
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_volume(self) -> bool:
        return "volume" in self.columns or "tick_volume" in self.columns

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "timeframe": self.timeframe.value,
            "price_basis": self.price_basis.value,
            "source": self.source,
            "source_identifier": self.source_identifier,
            "ingestion_version": self.ingestion_version,
            "checksum": self.checksum,
            "row_count": int(self.row_count),
            "first_timestamp": (
                self.first_timestamp.isoformat() if self.first_timestamp is not None else None
            ),
            "last_timestamp": (
                self.last_timestamp.isoformat() if self.last_timestamp is not None else None
            ),
            "timezone_of_origin": self.timezone_of_origin,
            "quality": self.quality.value,
            "kind": self.kind.value,
            "adjustments": [a.to_dict() for a in self.adjustments],
            "gaps": [g.to_dict() for g in self.gaps],
            "repairs": [r.to_dict() for r in self.repairs],
            "columns": list(self.columns),
            "created_at": self.created_at.isoformat(),
            "notes": self.notes,
            "extra": self.extra,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BarDatasetMetadata:
        return cls(
            instrument=raw["instrument"],
            timeframe=Timeframe(raw["timeframe"]),
            price_basis=PriceBasis(raw["price_basis"]),
            source=raw["source"],
            source_identifier=raw["source_identifier"],
            ingestion_version=raw["ingestion_version"],
            checksum=raw["checksum"],
            row_count=int(raw["row_count"]),
            first_timestamp=(
                pd.Timestamp(raw["first_timestamp"]) if raw.get("first_timestamp") else None
            ),
            last_timestamp=(
                pd.Timestamp(raw["last_timestamp"]) if raw.get("last_timestamp") else None
            ),
            timezone_of_origin=raw["timezone_of_origin"],
            quality=DataQuality(raw.get("quality", "raw")),
            kind=DatasetKind(raw.get("kind", "raw")),
            adjustments=tuple(Adjustment.from_dict(a) for a in raw.get("adjustments", [])),
            gaps=tuple(GapRecord.from_dict(g) for g in raw.get("gaps", [])),
            repairs=tuple(RepairRecord.from_dict(r) for r in raw.get("repairs", [])),
            columns=tuple(raw.get("columns", ())),
            created_at=datetime.fromisoformat(raw["created_at"])
            if raw.get("created_at")
            else datetime.now(tz=UTC),
            notes=raw.get("notes", ""),
            extra=dict(raw.get("extra", {})),
        )

    @classmethod
    def from_json(cls, blob: str | bytes) -> BarDatasetMetadata:
        if isinstance(blob, bytes):
            blob = blob.decode("utf-8")
        return cls.from_dict(json.loads(blob))


def describe_frame(
    frame: pd.DataFrame,
    *,
    source: str,
    source_identifier: str,
    timezone_of_origin: str,
    quality: DataQuality = DataQuality.RAW,
    kind: DatasetKind = DatasetKind.RAW,
    adjustments: tuple[Adjustment, ...] = (),
    gaps: tuple[GapRecord, ...] = (),
    repairs: tuple[RepairRecord, ...] = (),
    notes: str = "",
    extra: dict[str, Any] | None = None,
) -> BarDatasetMetadata:
    """Build :class:`BarDatasetMetadata` for an already-canonical frame."""
    validate_frame_shape(frame)
    if len(frame):
        instrument = str(frame["instrument"].iloc[0])
        timeframe = Timeframe(str(frame["timeframe"].iloc[0]))
        price_basis = PriceBasis(str(frame["price_basis"].iloc[0]))
        first_ts: pd.Timestamp | None = frame.index.min()
        last_ts: pd.Timestamp | None = frame.index.max()
    else:  # pragma: no cover - empty frames are refused upstream, kept honest here
        raise SchemaError("cannot describe an empty frame: identity would be unknown")

    return BarDatasetMetadata(
        instrument=instrument,
        timeframe=timeframe,
        price_basis=price_basis,
        source=source,
        source_identifier=source_identifier,
        ingestion_version=INGESTION_VERSION,
        checksum=content_checksum(frame),
        row_count=len(frame),
        first_timestamp=first_ts,
        last_timestamp=last_ts,
        timezone_of_origin=timezone_of_origin,
        quality=quality,
        kind=kind,
        adjustments=adjustments,
        gaps=gaps,
        repairs=repairs,
        columns=tuple(frame.columns),
        notes=notes,
        extra=dict(extra or {}),
    )
