"""The layered parquet store: RAW is immutable, CANONICAL is derived.

The V1 bug this module exists to make impossible
-----------------------------------------------
V1 resolved its data root by starting at the module's own location and walking
*up* the tree until it found a directory that looked like a data store. On the
research box it found a nearly-empty staging directory two levels above the real
one. Every read succeeded. Every read returned nothing. 99% of a research batch
recorded ``no_data`` — and, because ``no_data`` was treated as a completed
outcome, those combinations were written into the checkpoint as DONE and were
never retried. The batch looked finished. It had tested almost nothing.

Three rules follow, and they are enforced here rather than documented:

1.  The root is **explicit**. It comes from an argument or from
    ``FIBOKI_DATA_ROOT``. There is no search, no fallback, no walking up.
2.  A root must be **marked**. It must contain a ``.fiboki-data-root`` marker
    written by :meth:`DataStore.initialise`. A plausible-looking directory that
    was never initialised is rejected, so pointing at the wrong place fails
    immediately instead of quietly returning emptiness.
3.  A miss is **loud**. Asking for an instrument/timeframe that is not there
    raises :class:`DatasetNotFound`. There is no empty-DataFrame return path,
    because "empty" and "absent" are different facts and V1 conflated them.

Layout
------
::

    <root>/.fiboki-data-root
    <root>/catalogue.db
    <root>/raw/<INSTRUMENT>/<TF>/<version_id>/year=YYYY/part-0.parquet
    <root>/raw/<INSTRUMENT>/<TF>/<version_id>/_dataset.json
    <root>/canonical/<INSTRUMENT>/<TF>/<version_id>/...
    <root>/features/<INSTRUMENT>/<TF>/<version_id>/...

Partitioning by year gives cheap date-range reads: a 2019-2020 request touches
two directories out of twenty-six, not the whole 25-year file.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.integrity import (
    DirtyDataError,
    IntegrityConfig,
    IntegrityReport,
    validate,
)
from fiboki.data.schema import (
    BarDatasetMetadata,
    DatasetKind,
    content_checksum,
    frame_from_arrow,
    frame_to_arrow,
    validate_frame_shape,
)
from fiboki.data.versioning import (
    DatasetCatalogue,
    DatasetVersion,
    TransformationStep,
)

ROOT_MARKER = ".fiboki-data-root"
CATALOGUE_FILENAME = "catalogue.db"
DATASET_METADATA_FILENAME = "_dataset.json"
INTEGRITY_FILENAME = "_integrity.json"
ENV_ROOT = "FIBOKI_DATA_ROOT"

_LAYER_DIRS: dict[DatasetKind, str] = {
    DatasetKind.RAW: "raw",
    DatasetKind.VALIDATED: "canonical",
    DatasetKind.REPAIRED: "canonical",
    DatasetKind.RESAMPLED: "canonical",
    DatasetKind.FEATURE: "features",
}


class StoreError(RuntimeError):
    pass


class DataRootNotFound(StoreError):
    """The configured root does not exist or is not a marked Fiboki data root."""


class DatasetNotFound(StoreError):
    """Asked-for data is absent. Never silently an empty frame."""


class RawImmutabilityError(StoreError):
    """Something tried to modify RAW. RAW is written once and never again."""


class ChecksumMismatch(StoreError):
    """Stored bytes no longer hash to the registered checksum."""


def resolve_root(explicit: str | Path | None = None, *, env_var: str = ENV_ROOT) -> Path:
    """Resolve the data root explicitly. Never searches the filesystem.

    Order: the argument, then ``$FIBOKI_DATA_ROOT``. If neither is set, raise —
    do not guess, do not use the current working directory, do not walk up.
    """
    candidate = explicit if explicit is not None else os.environ.get(env_var)
    if not candidate:
        raise DataRootNotFound(
            f"No data root configured. Pass one explicitly or set {env_var}. "
            "V2 never searches for a plausible data directory: V1 did, found the "
            "wrong one, and silently recorded an entire research batch as no_data."
        )
    return Path(candidate).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class StoredDataset:
    """A resolved dataset: its version, its bytes' location, its verdict."""

    version: DatasetVersion
    path: Path
    metadata: BarDatasetMetadata
    integrity: dict[str, Any] | None

    @property
    def version_id(self) -> str:
        return self.version.version_id


class DataStore:
    """Explicit, layered, versioned parquet storage."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        catalogue: DatasetCatalogue | None = None,
        require_marker: bool = True,
    ) -> None:
        self.root = resolve_root(root)
        if not self.root.exists():
            raise DataRootNotFound(
                f"Data root {self.root} does not exist. Create it with "
                "DataStore.initialise(root) — V2 will not invent one, and will not "
                "look somewhere else that happens to exist."
            )
        if not self.root.is_dir():
            raise DataRootNotFound(f"Data root {self.root} is not a directory")
        if require_marker and not (self.root / ROOT_MARKER).exists():
            raise DataRootNotFound(
                f"{self.root} exists but is not a Fiboki data root: no {ROOT_MARKER} "
                "marker. This check is the whole point — it turns 'you pointed at the "
                "wrong directory' from a silent empty result into an immediate error."
            )
        self.catalogue = catalogue or DatasetCatalogue(self.root / CATALOGUE_FILENAME)

    # -- lifecycle ---------------------------------------------------

    @classmethod
    def initialise(cls, root: str | Path, *, exist_ok: bool = True) -> DataStore:
        """Create and mark a data root, then open it."""
        path = Path(root).expanduser().resolve()
        if path.exists() and not exist_ok and (path / ROOT_MARKER).exists():
            raise StoreError(f"{path} is already a Fiboki data root")
        path.mkdir(parents=True, exist_ok=True)
        marker = path / ROOT_MARKER
        if not marker.exists():
            marker.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now(tz=UTC).isoformat(),
                        "layout_version": 1,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        for layer in ("raw", "canonical", "features"):
            (path / layer).mkdir(exist_ok=True)
        return cls(path)

    def close(self) -> None:
        self.catalogue.close()

    def __enter__(self) -> DataStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- paths -------------------------------------------------------

    def layer_root(self, kind: DatasetKind) -> Path:
        return self.root / _LAYER_DIRS[kind]

    def dataset_path(
        self,
        kind: DatasetKind,
        instrument: str,
        timeframe: Timeframe | str,
        version_id: str,
    ) -> Path:
        tf = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
        return self.layer_root(kind) / instrument.upper() / tf / version_id

    # -- write -------------------------------------------------------

    def write_raw(
        self,
        frame: pd.DataFrame,
        metadata: BarDatasetMetadata,
        *,
        lineage: tuple[TransformationStep, ...] | None = None,
    ) -> StoredDataset:
        """Write an immutable RAW dataset.

        Raw bytes are what the provider gave us, after nothing but timezone
        correction and schema shaping (both declared as lineage steps). If the
        same content arrives again it resolves to the same version id and the
        write is a verified no-op. Re-ingestion that produces *different* bytes
        produces a *new* version; it never overwrites the old one.
        """
        validate_frame_shape(frame)
        steps = lineage or (
            TransformationStep(
                operation="ingest_raw",
                parameters={
                    "source": metadata.source,
                    "source_identifier": metadata.source_identifier,
                    "timezone_of_origin": metadata.timezone_of_origin,
                    "price_basis": metadata.price_basis.value,
                    "adjustments": [a.to_dict() for a in metadata.adjustments],
                },
                code_version=metadata.ingestion_version,
            ),
        )
        return self._write(
            frame,
            metadata,
            kind=DatasetKind.RAW,
            lineage=steps,
            parent_ids=(),
            integrity=None,
            immutable=True,
        )

    def write_canonical(
        self,
        frame: pd.DataFrame,
        metadata: BarDatasetMetadata,
        *,
        source_version: DatasetVersion,
        transformation: TransformationStep,
        integrity: IntegrityReport | None = None,
        kind: DatasetKind = DatasetKind.VALIDATED,
    ) -> StoredDataset:
        """Write a derived dataset, recording exactly how it was derived."""
        validate_frame_shape(frame)
        if kind is DatasetKind.RAW:
            raise StoreError("write_canonical cannot produce a RAW dataset; use write_raw")
        lineage = (*source_version.lineage, transformation)
        return self._write(
            frame,
            metadata,
            kind=kind,
            lineage=lineage,
            parent_ids=(source_version.version_id,),
            integrity=integrity,
            immutable=False,
        )

    def _write(
        self,
        frame: pd.DataFrame,
        metadata: BarDatasetMetadata,
        *,
        kind: DatasetKind,
        lineage: tuple[TransformationStep, ...],
        parent_ids: tuple[str, ...],
        integrity: IntegrityReport | None,
        immutable: bool,
    ) -> StoredDataset:
        checksum = content_checksum(frame)
        if metadata.checksum and metadata.checksum != checksum:
            raise ChecksumMismatch(
                "metadata checksum does not match the frame being written: "
                f"{metadata.checksum} vs {checksum}"
            )
        version = DatasetVersion(
            content_checksum=checksum,
            lineage=lineage,
            instrument=metadata.instrument,
            timeframe=metadata.timeframe,
            price_basis=metadata.price_basis,
            kind=kind,
            quality=metadata.quality,
            row_count=len(frame),
            first_timestamp=metadata.first_timestamp,
            last_timestamp=metadata.last_timestamp,
            storage_path="",
            source=metadata.source,
            parent_ids=parent_ids,
            metadata=metadata.to_dict(),
            integrity_report=integrity.to_dict() if integrity else None,
        )
        target = self.dataset_path(kind, metadata.instrument, metadata.timeframe,
                                   version.version_id)

        if target.exists():
            if immutable:
                # Same id means same content and same lineage. Verify, do not rewrite.
                stored = self._read_frame(target)
                if content_checksum(stored) != checksum:
                    raise RawImmutabilityError(
                        f"RAW dataset {version.version_id} exists at {target} but its "
                        "bytes no longer match its content hash. RAW must never be "
                        "modified after it is written."
                    )
            else:
                _rmtree_readonly(target)

        if not target.exists():
            self._write_partitioned(frame, target)
            (target / DATASET_METADATA_FILENAME).write_text(
                json.dumps(
                    {
                        "version_id": version.version_id,
                        "dataset": metadata.to_dict(),
                        "lineage": [s.to_dict() for s in lineage],
                        "parent_ids": list(parent_ids),
                        "kind": kind.value,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            if integrity is not None:
                (target / INTEGRITY_FILENAME).write_text(
                    json.dumps(integrity.to_dict(), indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            if immutable:
                _make_readonly(target)

        version = DatasetVersion(
            **{**_version_fields(version), "storage_path": str(target)}
        )
        self.catalogue.register(version)
        return StoredDataset(
            version=version,
            path=target,
            metadata=metadata,
            integrity=integrity.to_dict() if integrity else None,
        )

    @staticmethod
    def _write_partitioned(frame: pd.DataFrame, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        table = frame_to_arrow(frame)
        years = pa.array(frame.index.year.astype("int32"), type=pa.int32())
        table = table.append_column("year", years)
        pq.write_to_dataset(
            table,
            root_path=str(target),
            partition_cols=["year"],
            compression="zstd",
            existing_data_behavior="error",
        )

    # -- read --------------------------------------------------------

    def read(
        self,
        version_id: str,
        *,
        start: pd.Timestamp | str | None = None,
        end_inclusive: pd.Timestamp | str | None = None,
        allow_suspect: bool = False,
        verify_checksum: bool = False,
        end: pd.Timestamp | str | None = None,
    ) -> pd.DataFrame:
        """Read a dataset by version id, bars in ``[start, end_inclusive]``.

        The end is INCLUSIVE -- the bar stamped exactly ``end_inclusive`` is
        returned -- which is the opposite of :class:`~fiboki.validation.
        evaluation.DateWindow`'s half-open ``[start, end)``. The argument is
        named for that, so a caller building a window cannot forget it and put
        the boundary bar in both train and test. ``end`` is the old name, kept
        as an alias with identical (inclusive) behaviour so existing callers
        keep working; passing both is refused.

        ``allow_suspect=False`` (the default) refuses to hand back a dataset
        whose stored integrity report contains blocking defects. That refusal is
        the no-silent-repair guarantee at the point it matters: a caller cannot
        get dirty data back without having said, in code, that it wants dirty
        data.
        """
        end = _inclusive_end(end_inclusive, end)
        version = self.catalogue.resolve(version_id)
        path = Path(version.storage_path)
        if not path.exists():
            raise DatasetNotFound(
                f"version {version_id} is registered with storage_path {path}, but "
                "that path does not exist. The catalogue and the store disagree; "
                "do not proceed with a partial read."
            )
        if not allow_suspect:
            self._assert_readable_clean(version)
        frame = self._read_frame(path, start=start, end_inclusive=end)
        # A checksum only means anything against the whole dataset; a windowed
        # read is a different set of bytes by construction.
        if verify_checksum and start is None and end is None:
            actual = content_checksum(frame)
            if actual != version.content_checksum:
                raise ChecksumMismatch(
                    f"{version_id}: stored bytes hash to {actual}, catalogue says "
                    f"{version.content_checksum}"
                )
        return frame

    @staticmethod
    def _assert_readable_clean(version: DatasetVersion) -> None:
        if version.quality in (DataQuality.SUSPECT, DataQuality.REJECTED):
            raise DirtyDataError(_report_from_dict(version))
        report = version.integrity_report
        if report and not report.get("is_clean", True):
            raise DirtyDataError(_report_from_dict(version))

    def read_latest(
        self,
        instrument: str,
        timeframe: Timeframe | str,
        *,
        kind: DatasetKind = DatasetKind.VALIDATED,
        start: pd.Timestamp | str | None = None,
        end_inclusive: pd.Timestamp | str | None = None,
        allow_suspect: bool = False,
        end: pd.Timestamp | str | None = None,
    ) -> tuple[pd.DataFrame, DatasetVersion]:
        """Read the most recent dataset of a kind. Raises if there is none.

        Returning the version alongside the frame is deliberate: a caller that
        computes anything from these bars is expected to store the version id
        next to the result. The end bound is INCLUSIVE; see :meth:`read`.
        """
        end = _inclusive_end(end_inclusive, end)
        version = self.catalogue.latest(instrument, timeframe, kind=kind)
        if version is None:
            tf = timeframe.value if isinstance(timeframe, Timeframe) else timeframe
            raise DatasetNotFound(
                f"no {kind.value} dataset for {instrument} {tf} in {self.root}. "
                "This is an absence, not an empty result: do not record it as a "
                "completed no-data outcome and do not checkpoint it as done."
            )
        frame = self.read(
            version.version_id,
            start=start,
            end_inclusive=end,
            allow_suspect=allow_suspect,
        )
        return frame, version

    @staticmethod
    def _read_frame(
        path: Path,
        *,
        start: pd.Timestamp | str | None = None,
        end_inclusive: pd.Timestamp | str | None = None,
    ) -> pd.DataFrame:
        dataset = pads.dataset(str(path), format="parquet", partitioning="hive")
        filt = None
        start_ts = _as_utc(start)
        end_ts = _as_utc(end_inclusive)
        field = pads.field("timestamp")
        if start_ts is not None:
            filt = field >= pa.scalar(start_ts.to_pydatetime())
        if end_ts is not None:
            clause = field <= pa.scalar(end_ts.to_pydatetime())
            filt = clause if filt is None else (filt & clause)

        # Prune whole year partitions before touching any row group.
        if start_ts is not None or end_ts is not None:
            year_field = pads.field("year")
            if start_ts is not None:
                filt = filt & (year_field >= start_ts.year)
            if end_ts is not None:
                filt = filt & (year_field <= end_ts.year)

        table = dataset.to_table(filter=filt)
        if "year" in table.column_names:
            table = table.drop_columns(["year"])
        frame = frame_from_arrow(table)
        return frame.sort_index(kind="stable")

    # -- discovery ---------------------------------------------------

    def locate(
        self,
        instrument: str,
        timeframe: Timeframe | str,
        *,
        kind: DatasetKind = DatasetKind.VALIDATED,
    ) -> StoredDataset:
        """Find a dataset, or say precisely where it looked and did not find it."""
        version = self.catalogue.latest(instrument, timeframe, kind=kind)
        tf = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
        expected_dir = self.layer_root(kind) / instrument.upper() / tf
        if version is None:
            raise DatasetNotFound(
                f"no {kind.value} dataset registered for {instrument} {tf}.\n"
                f"  data root:      {self.root}\n"
                f"  expected under: {expected_dir}\n"
                f"  root exists:    {self.root.exists()}\n"
                f"  dir exists:     {expected_dir.exists()}\n"
                "No other location was searched, by design."
            )
        path = Path(version.storage_path)
        meta = self.read_metadata(path)
        integrity_path = path / INTEGRITY_FILENAME
        integrity = (
            json.loads(integrity_path.read_text(encoding="utf-8"))
            if integrity_path.exists()
            else None
        )
        return StoredDataset(version=version, path=path, metadata=meta, integrity=integrity)

    @staticmethod
    def read_metadata(path: Path) -> BarDatasetMetadata:
        blob = (path / DATASET_METADATA_FILENAME).read_text(encoding="utf-8")
        return BarDatasetMetadata.from_dict(json.loads(blob)["dataset"])

    def inventory(self) -> pd.DataFrame:
        """Everything in the store, as a frame. For operator eyeballs."""
        rows = []
        for v in self.catalogue.list_versions():
            rows.append(
                {
                    "version_id": v.version_id,
                    "instrument": v.instrument,
                    "timeframe": v.timeframe.value,
                    "kind": v.kind.value,
                    "quality": v.quality.value,
                    "price_basis": v.price_basis.value,
                    "rows": v.row_count,
                    "first": v.first_timestamp,
                    "last": v.last_timestamp,
                    "source": v.source,
                    "path": v.storage_path,
                }
            )
        return pd.DataFrame(rows)

    def verify_immutable(self, version_id: str) -> bool:
        """Re-hash a RAW dataset's stored bytes against its registered checksum."""
        version = self.catalogue.resolve(version_id)
        if version.kind is not DatasetKind.RAW:
            raise StoreError(f"{version_id} is {version.kind.value}, not raw")
        frame = self._read_frame(Path(version.storage_path))
        actual = content_checksum(frame)
        if actual != version.content_checksum:
            raise RawImmutabilityError(
                f"RAW {version_id} has been modified: {actual} != {version.content_checksum}"
            )
        return True

    def validate_dataset(
        self, version_id: str, *, config: IntegrityConfig | None = None
    ) -> IntegrityReport:
        """Run integrity checks against stored bytes, without repairing anything."""
        frame = self.read(version_id, allow_suspect=True)
        return validate(frame, config=config)


# ------------------------------------------------------------- helpers


def _version_fields(version: DatasetVersion) -> dict[str, Any]:
    return {
        "content_checksum": version.content_checksum,
        "lineage": version.lineage,
        "instrument": version.instrument,
        "timeframe": version.timeframe,
        "price_basis": version.price_basis,
        "kind": version.kind,
        "quality": version.quality,
        "row_count": version.row_count,
        "first_timestamp": version.first_timestamp,
        "last_timestamp": version.last_timestamp,
        "storage_path": version.storage_path,
        "source": version.source,
        "parent_ids": version.parent_ids,
        "metadata": version.metadata,
        "integrity_report": version.integrity_report,
        "created_at": version.created_at,
    }


def _report_from_dict(version: DatasetVersion) -> IntegrityReport:
    """Rebuild just enough of a report to raise a useful DirtyDataError."""
    from fiboki.data.integrity import Defect, DefectCode, Severity

    raw = version.integrity_report or {}
    defects = []
    for d in raw.get("defects", []):
        try:
            defects.append(
                Defect(
                    code=DefectCode(d["code"]),
                    severity=Severity(d["severity"]),
                    count=int(d["count"]),
                    message=d.get("message", ""),
                )
            )
        except ValueError:  # pragma: no cover - forward compatibility
            continue
    if not defects and version.quality in (DataQuality.SUSPECT, DataQuality.REJECTED):
        defects.append(
            Defect(
                code=DefectCode.IMPOSSIBLE_BAR,
                severity=Severity.ERROR,
                count=0,
                message=f"dataset quality is {version.quality.value}",
            )
        )
    return IntegrityReport(
        instrument=version.instrument,
        timeframe=version.timeframe,
        row_count=version.row_count,
        first_timestamp=version.first_timestamp,
        last_timestamp=version.last_timestamp,
        defects=tuple(defects),
        gaps=(),
        config=IntegrityConfig(),
        checks_run=("stored",),
        calendar_name=str(raw.get("calendar_name", "unknown")),
    )


def _inclusive_end(
    end_inclusive: pd.Timestamp | str | None, end: pd.Timestamp | str | None
) -> pd.Timestamp | str | None:
    """Resolve the renamed argument: ``end`` is an alias of ``end_inclusive``."""
    if end_inclusive is not None and end is not None:
        raise TypeError(
            "pass end_inclusive or its legacy alias end, not both; both are "
            "INCLUSIVE bounds"
        )
    return end_inclusive if end_inclusive is not None else end


def _as_utc(value: pd.Timestamp | str | None) -> pd.Timestamp | None:
    """Interpret a bound as UTC. A naive bound is UTC, and says so here once."""
    if value is None:
        return None
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _make_readonly(path: Path) -> None:
    for item in sorted(path.rglob("*"), reverse=True):
        if item.is_file():
            item.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _rmtree_readonly(path: Path) -> None:
    def _onerror(func, target, _exc):  # pragma: no cover - platform dependent
        os.chmod(target, stat.S_IWUSR | stat.S_IRUSR)
        func(target)

    shutil.rmtree(path, onerror=_onerror)
