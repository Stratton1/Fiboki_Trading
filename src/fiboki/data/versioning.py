"""Content-addressed dataset versioning.

V1 had no answer to "which data produced this result". Results were stored
against an instrument and a timeframe, and the underlying parquet was rewritten
in place whenever anything was re-downloaded. Every stored backtest was
therefore attached to whatever the file happened to contain *now*, not to what
it contained when the backtest ran.

V2 gives every dataset a deterministic id derived from two things and nothing
else:

    version_id = H( content_checksum || canonical(lineage) )

Consequences that matter:

* identical content produced by an identical transformation chain always gets
  the same id, on any machine, in any process, at any time;
* different content, or the same content arrived at a different way, gets a
  different id;
* an experiment stores one short string and can always re-resolve the exact
  bytes it consumed;
* nothing about *when* a version was created enters its identity, so the id is
  reproducible rather than merely unique.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)

from fiboki.core.durable import install_sqlite_pragmas
from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.schema import DatasetKind, PriceBasis

VERSION_ID_PREFIX = "ds"
VERSION_ID_HEX_LENGTH = 24


class VersioningError(RuntimeError):
    pass


class VersionNotFound(VersioningError):
    def __init__(self, version_id: str) -> None:
        super().__init__(
            f"dataset version {version_id!r} is not in the catalogue. A result that "
            "references an unresolvable dataset version must be treated as "
            "unreproducible, not as approximately fine."
        )
        self.version_id = version_id


class VersionConflict(VersioningError):
    """Same id registered with materially different facts. Never overwrite."""


# ------------------------------------------------------------- lineage


@dataclass(frozen=True, slots=True)
class TransformationStep:
    """One link in the chain from a raw download to the bytes in hand.

    ``parameters`` must be JSON-serialisable and fully determine the step's
    behaviour: it is hashed into the version id, so an undeclared parameter is
    an invisible fork in the lineage.
    """

    operation: str
    parameters: dict[str, Any] = field(default_factory=dict)
    code_version: str = ""
    inputs: tuple[str, ...] = ()

    def canonical(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "parameters": _canonical_json_value(self.parameters),
            "code_version": self.code_version,
            "inputs": list(self.inputs),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "inputs": list(self.inputs)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TransformationStep:
        return cls(
            operation=raw["operation"],
            parameters=dict(raw.get("parameters", {})),
            code_version=raw.get("code_version", ""),
            inputs=tuple(raw.get("inputs", ())),
        )


def _canonical_json_value(value: Any) -> Any:
    """Make parameters hashable in a stable way across processes."""
    if isinstance(value, dict):
        return {str(k): _canonical_json_value(v) for k, v in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_canonical_json_value(v) for v in value]
    if isinstance(value, pd.Timestamp | datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value  # Enum
    return value


def compute_version_id(content_checksum: str, lineage: tuple[TransformationStep, ...]) -> str:
    """The deterministic id. Content plus how it was arrived at, nothing else."""
    payload = json.dumps(
        {
            "content_checksum": content_checksum,
            "lineage": [s.canonical() for s in lineage],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
    return f"{VERSION_ID_PREFIX}_{digest[:VERSION_ID_HEX_LENGTH]}"


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """An immutable, resolvable identity for a specific set of bytes."""

    content_checksum: str
    lineage: tuple[TransformationStep, ...]
    instrument: str
    timeframe: Timeframe
    price_basis: PriceBasis
    kind: DatasetKind
    quality: DataQuality
    row_count: int
    first_timestamp: pd.Timestamp | None
    last_timestamp: pd.Timestamp | None
    storage_path: str
    source: str
    parent_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    integrity_report: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def version_id(self) -> str:
        return compute_version_id(self.content_checksum, self.lineage)

    @property
    def short_id(self) -> str:
        return self.version_id.split("_", 1)[1][:12]

    def describe(self) -> str:
        return (
            f"{self.version_id} {self.instrument} {self.timeframe.value} "
            f"{self.kind.value}/{self.quality.value} rows={self.row_count} "
            f"basis={self.price_basis.value}"
        )

    def with_step(self, step: TransformationStep, **changes: Any) -> DatasetVersion:
        """A child version: same lineage plus one step, this version as parent."""
        base = {
            "content_checksum": self.content_checksum,
            "lineage": (*self.lineage, step),
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "price_basis": self.price_basis,
            "kind": self.kind,
            "quality": self.quality,
            "row_count": self.row_count,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "storage_path": self.storage_path,
            "source": self.source,
            "parent_ids": (self.version_id,),
            "metadata": dict(self.metadata),
            "integrity_report": self.integrity_report,
        }
        base.update(changes)
        return DatasetVersion(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------ ORM rows


class Base(DeclarativeBase):
    pass


class DatasetVersionRow(Base):
    __tablename__ = "dataset_version"

    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_checksum: Mapped[str] = mapped_column(String(64), index=True)
    instrument: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    price_basis: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(24), index=True)
    quality: Mapped[str] = mapped_column(String(24), index=True)
    row_count: Mapped[int] = mapped_column(Integer)
    first_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_path: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64), index=True)
    lineage_json: Mapped[list] = mapped_column(JSON)
    parent_ids_json: Mapped[list] = mapped_column(JSON)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    integrity_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LineageEdgeRow(Base):
    """parent -> child, so a chain can be walked without parsing JSON."""

    __tablename__ = "dataset_lineage_edge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[str] = mapped_column(String(64), index=True)
    child_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_version.version_id"), index=True
    )
    operation: Mapped[str] = mapped_column(String(64))


# --------------------------------------------------------------- diff


@dataclass(frozen=True, slots=True)
class VersionDiff:
    """What actually differs between two dataset versions."""

    left_id: str
    right_id: str
    same_content: bool
    same_lineage: bool
    row_count_delta: int
    first_timestamp_delta: pd.Timedelta | None
    last_timestamp_delta: pd.Timedelta | None
    lineage_diverges_at: int | None
    left_only_steps: tuple[str, ...]
    right_only_steps: tuple[str, ...]
    quality_changed: tuple[str, str] | None
    kind_changed: tuple[str, str] | None
    instrument_changed: tuple[str, str] | None
    price_basis_changed: tuple[str, str] | None

    @property
    def identical(self) -> bool:
        return self.left_id == self.right_id

    def summary(self) -> str:
        if self.identical:
            return f"{self.left_id}: identical"
        bits = []
        if not self.same_content:
            bits.append("content differs")
        if not self.same_lineage:
            at = self.lineage_diverges_at
            bits.append(f"lineage diverges at step {at}")
        if self.row_count_delta:
            bits.append(f"rows {self.row_count_delta:+d}")
        if self.quality_changed:
            bits.append("quality {} -> {}".format(*self.quality_changed))
        if self.price_basis_changed:
            bits.append("price_basis {} -> {}".format(*self.price_basis_changed))
        return f"{self.left_id} -> {self.right_id}: " + ("; ".join(bits) or "metadata only")

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_id": self.left_id,
            "right_id": self.right_id,
            "same_content": self.same_content,
            "same_lineage": self.same_lineage,
            "row_count_delta": self.row_count_delta,
            "first_timestamp_delta": (
                str(self.first_timestamp_delta) if self.first_timestamp_delta is not None else None
            ),
            "last_timestamp_delta": (
                str(self.last_timestamp_delta) if self.last_timestamp_delta is not None else None
            ),
            "lineage_diverges_at": self.lineage_diverges_at,
            "left_only_steps": list(self.left_only_steps),
            "right_only_steps": list(self.right_only_steps),
            "quality_changed": list(self.quality_changed) if self.quality_changed else None,
            "kind_changed": list(self.kind_changed) if self.kind_changed else None,
            "instrument_changed": (
                list(self.instrument_changed) if self.instrument_changed else None
            ),
            "price_basis_changed": (
                list(self.price_basis_changed) if self.price_basis_changed else None
            ),
        }


# ---------------------------------------------------------- catalogue


class DatasetCatalogue:
    """The SQLite-backed registry of dataset versions.

    Append-mostly: a version id is written once. Re-registering the same id with
    the same facts is a no-op (idempotent re-ingestion); re-registering it with
    *different* facts raises, because that would mean the content hash lied.
    """

    def __init__(self, db_path: str | Path, *, echo: bool = False) -> None:
        self.db_path = Path(db_path)
        if str(db_path) == ":memory:":
            url = "sqlite+pysqlite:///:memory:"
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite+pysqlite:///{self.db_path}"
        self._engine = create_engine(url, echo=echo, future=True)
        # WAL, busy_timeout and synchronous=FULL on EVERY connection (audit F
        # P2-14): the API, campaigns and the agents contend for this file.
        install_sqlite_pragmas(self._engine, synchronous="FULL")
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, future=True)

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> DatasetCatalogue:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- write -------------------------------------------------------

    def register(self, version: DatasetVersion) -> str:
        """Persist a version. Idempotent for identical facts, loud otherwise."""
        vid = version.version_id
        with self._session_factory() as session:
            existing = session.get(DatasetVersionRow, vid)
            if existing is not None:
                self._assert_compatible(existing, version)
                return vid
            row = DatasetVersionRow(
                version_id=vid,
                content_checksum=version.content_checksum,
                instrument=version.instrument,
                timeframe=version.timeframe.value,
                price_basis=version.price_basis.value,
                kind=version.kind.value,
                quality=version.quality.value,
                row_count=int(version.row_count),
                first_timestamp=(
                    version.first_timestamp.to_pydatetime()
                    if version.first_timestamp is not None
                    else None
                ),
                last_timestamp=(
                    version.last_timestamp.to_pydatetime()
                    if version.last_timestamp is not None
                    else None
                ),
                storage_path=str(version.storage_path),
                source=version.source,
                lineage_json=[s.to_dict() for s in version.lineage],
                parent_ids_json=list(version.parent_ids),
                metadata_json=_canonical_json_value(version.metadata),
                integrity_json=version.integrity_report,
                created_at=version.created_at,
            )
            session.add(row)
            for parent in version.parent_ids:
                session.add(
                    LineageEdgeRow(
                        parent_id=parent,
                        child_id=vid,
                        operation=version.lineage[-1].operation if version.lineage else "",
                    )
                )
            session.commit()
        return vid

    @staticmethod
    def _assert_compatible(row: DatasetVersionRow, version: DatasetVersion) -> None:
        mismatches = []
        if row.content_checksum != version.content_checksum:
            mismatches.append("content_checksum")
        if row.row_count != int(version.row_count):
            mismatches.append("row_count")
        if row.instrument != version.instrument:
            mismatches.append("instrument")
        if row.timeframe != version.timeframe.value:
            mismatches.append("timeframe")
        if mismatches:
            raise VersionConflict(
                f"version {row.version_id} already registered with different "
                f"{', '.join(mismatches)}. Content-addressed ids must never collide; "
                "this means the checksum function or the stored row has been corrupted."
            )

    # -- read --------------------------------------------------------

    def resolve(self, version_id: str) -> DatasetVersion:
        with self._session_factory() as session:
            row = session.get(DatasetVersionRow, version_id)
            if row is None:
                raise VersionNotFound(version_id)
            return _row_to_version(row)

    def exists(self, version_id: str) -> bool:
        with self._session_factory() as session:
            return session.get(DatasetVersionRow, version_id) is not None

    def list_versions(
        self,
        *,
        instrument: str | None = None,
        timeframe: Timeframe | str | None = None,
        kind: DatasetKind | str | None = None,
        quality: DataQuality | str | None = None,
        source: str | None = None,
        limit: int | None = None,
    ) -> list[DatasetVersion]:
        stmt = select(DatasetVersionRow)
        if instrument:
            stmt = stmt.where(DatasetVersionRow.instrument == instrument.upper())
        if timeframe:
            tf = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
            stmt = stmt.where(DatasetVersionRow.timeframe == tf)
        if kind:
            k = kind.value if isinstance(kind, DatasetKind) else str(kind)
            stmt = stmt.where(DatasetVersionRow.kind == k)
        if quality:
            q = quality.value if isinstance(quality, DataQuality) else str(quality)
            stmt = stmt.where(DatasetVersionRow.quality == q)
        if source:
            stmt = stmt.where(DatasetVersionRow.source == source)
        stmt = stmt.order_by(DatasetVersionRow.created_at.desc())
        if limit:
            stmt = stmt.limit(limit)
        with self._session_factory() as session:
            return [_row_to_version(r) for r in session.scalars(stmt).all()]

    def latest(
        self,
        instrument: str,
        timeframe: Timeframe | str,
        *,
        kind: DatasetKind | str = DatasetKind.VALIDATED,
    ) -> DatasetVersion | None:
        found = self.list_versions(
            instrument=instrument, timeframe=timeframe, kind=kind, limit=1
        )
        return found[0] if found else None

    def find_by_checksum(self, content_checksum: str) -> list[DatasetVersion]:
        stmt = select(DatasetVersionRow).where(
            DatasetVersionRow.content_checksum == content_checksum
        )
        with self._session_factory() as session:
            return [_row_to_version(r) for r in session.scalars(stmt).all()]

    # -- lineage -----------------------------------------------------

    def lineage_chain(self, version_id: str) -> list[DatasetVersion]:
        """Walk parents back to the root: [root, ..., this].

        The chain is raw -> validated -> resampled -> feature-engineered, and it
        is what lets a feature table be traced to the download it came from.
        """
        chain: list[DatasetVersion] = []
        seen: set[str] = set()
        current = self.resolve(version_id)
        while True:
            if current.version_id in seen:
                raise VersioningError(f"lineage cycle at {current.version_id}")
            seen.add(current.version_id)
            chain.append(current)
            if not current.parent_ids:
                break
            parent_id = current.parent_ids[0]
            if not self.exists(parent_id):
                break
            current = self.resolve(parent_id)
        return list(reversed(chain))

    def children(self, version_id: str) -> list[DatasetVersion]:
        stmt = select(LineageEdgeRow).where(LineageEdgeRow.parent_id == version_id)
        with self._session_factory() as session:
            child_ids = [e.child_id for e in session.scalars(stmt).all()]
        return [self.resolve(c) for c in child_ids]

    # -- diff --------------------------------------------------------

    def diff(self, left_id: str, right_id: str) -> VersionDiff:
        return diff_versions(self.resolve(left_id), self.resolve(right_id))


def diff_versions(left: DatasetVersion, right: DatasetVersion) -> VersionDiff:
    """Compare two versions without needing the catalogue."""
    lsteps = [s.canonical() for s in left.lineage]
    rsteps = [s.canonical() for s in right.lineage]
    diverge: int | None = None
    for i in range(max(len(lsteps), len(rsteps))):
        lv = lsteps[i] if i < len(lsteps) else None
        rv = rsteps[i] if i < len(rsteps) else None
        if lv != rv:
            diverge = i
            break

    def _delta(a: pd.Timestamp | None, b: pd.Timestamp | None) -> pd.Timedelta | None:
        if a is None or b is None:
            return None
        return b - a

    return VersionDiff(
        left_id=left.version_id,
        right_id=right.version_id,
        same_content=left.content_checksum == right.content_checksum,
        same_lineage=diverge is None,
        row_count_delta=int(right.row_count) - int(left.row_count),
        first_timestamp_delta=_delta(left.first_timestamp, right.first_timestamp),
        last_timestamp_delta=_delta(left.last_timestamp, right.last_timestamp),
        lineage_diverges_at=diverge,
        left_only_steps=tuple(
            s["operation"] for s in lsteps[diverge:] if diverge is not None
        ),
        right_only_steps=tuple(
            s["operation"] for s in rsteps[diverge:] if diverge is not None
        ),
        quality_changed=(
            (left.quality.value, right.quality.value)
            if left.quality != right.quality
            else None
        ),
        kind_changed=(
            (left.kind.value, right.kind.value) if left.kind != right.kind else None
        ),
        instrument_changed=(
            (left.instrument, right.instrument)
            if left.instrument != right.instrument
            else None
        ),
        price_basis_changed=(
            (left.price_basis.value, right.price_basis.value)
            if left.price_basis != right.price_basis
            else None
        ),
    )


def _row_to_version(row: DatasetVersionRow) -> DatasetVersion:
    def _ts(value: datetime | None) -> pd.Timestamp | None:
        if value is None:
            return None
        ts = pd.Timestamp(value)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)

    return DatasetVersion(
        content_checksum=row.content_checksum,
        lineage=tuple(TransformationStep.from_dict(s) for s in row.lineage_json),
        instrument=row.instrument,
        timeframe=Timeframe(row.timeframe),
        price_basis=PriceBasis(row.price_basis),
        kind=DatasetKind(row.kind),
        quality=DataQuality(row.quality),
        row_count=int(row.row_count),
        first_timestamp=_ts(row.first_timestamp),
        last_timestamp=_ts(row.last_timestamp),
        storage_path=row.storage_path,
        source=row.source,
        parent_ids=tuple(row.parent_ids_json or ()),
        metadata=dict(row.metadata_json or {}),
        integrity_report=row.integrity_json,
        created_at=created,
    )
