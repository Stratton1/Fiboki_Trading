"""Migrate the V1 HistData store into V2 raw + canonical with real provenance.

What the migration does per (instrument, timeframe):

1.  Read the V1 parquet through
    :class:`~fiboki.data.providers.histdata.HistDataParquetProvider`, which
    corrects the EST-no-DST timestamps to true UTC and stamps
    ``price_basis = BID``.
2.  Write that as an **immutable RAW** dataset, with the timezone correction and
    the basis declaration recorded as declared adjustments.
3.  Run integrity validation against a real session calendar. Nothing is
    repaired; the report is stored next to the data and attached to the version.
4.  Register a **VALIDATED** canonical dataset derived from RAW by a recorded
    transformation, carrying the integrity verdict as its quality.

What it deliberately does not do: fix anything. The V1 store contains genuinely
broken bars, and the point of this exercise is that they arrive in V2 labelled
as broken rather than quietly cleaned up. Repair, if wanted, is a separate
explicit step that produces yet another version.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.calendars import calendar_for
from fiboki.data.integrity import IntegrityConfig, IntegrityReport, validate
from fiboki.data.providers.histdata import HistDataParquetProvider
from fiboki.data.schema import BarDatasetMetadata, DatasetKind
from fiboki.data.store import DataStore
from fiboki.data.versioning import TransformationStep

MIGRATION_CODE_VERSION = "2.0.0"


@dataclass(slots=True)
class MigrationEntry:
    """Outcome for one (instrument, timeframe)."""

    instrument: str
    timeframe: Timeframe
    source_path: str
    raw_version_id: str | None = None
    canonical_version_id: str | None = None
    row_count: int = 0
    first_timestamp: pd.Timestamp | None = None
    last_timestamp: pd.Timestamp | None = None
    timestamp_shift_hours: int = 5
    quality: DataQuality = DataQuality.RAW
    report: IntegrityReport | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "timeframe": self.timeframe.value,
            "source_path": self.source_path,
            "raw_version_id": self.raw_version_id,
            "canonical_version_id": self.canonical_version_id,
            "row_count": self.row_count,
            "first_timestamp": (
                self.first_timestamp.isoformat() if self.first_timestamp is not None else None
            ),
            "last_timestamp": (
                self.last_timestamp.isoformat() if self.last_timestamp is not None else None
            ),
            "timestamp_shift_hours": self.timestamp_shift_hours,
            "quality": self.quality.value,
            "warnings": list(self.warnings),
            "error": self.error,
            "integrity": self.report.to_dict() if self.report else None,
        }


@dataclass(slots=True)
class MigrationReport:
    v1_root: str
    v2_root: str
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    finished_at: datetime | None = None
    entries: list[MigrationEntry] = field(default_factory=list)

    @property
    def succeeded(self) -> list[MigrationEntry]:
        return [e for e in self.entries if e.ok]

    @property
    def failed(self) -> list[MigrationEntry]:
        return [e for e in self.entries if not e.ok]

    @property
    def total_rows(self) -> int:
        return sum(e.row_count for e in self.succeeded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "v1_root": self.v1_root,
            "v2_root": self.v2_root,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "datasets": len(self.entries),
            "succeeded": len(self.succeeded),
            "failed": len(self.failed),
            "total_rows": self.total_rows,
            "entries": [e.to_dict() for e in self.entries],
        }

    def render(self) -> str:
        lines = [
            f"V1 -> V2 migration  {self.v1_root}  ->  {self.v2_root}",
            f"  datasets: {len(self.entries)}  ok: {len(self.succeeded)}  "
            f"failed: {len(self.failed)}  rows: {self.total_rows:,}",
            "",
        ]
        for e in self.entries:
            if not e.ok:
                lines.append(f"  [FAIL] {e.instrument} {e.timeframe.value}: {e.error}")
                continue
            lines.append(
                f"  {e.instrument:<8} {e.timeframe.value:<3} "
                f"rows={e.row_count:>8,}  "
                f"{e.first_timestamp}  ->  {e.last_timestamp}  "
                f"quality={e.quality.value}"
            )
            lines.append(f"      raw       {e.raw_version_id}")
            lines.append(f"      canonical {e.canonical_version_id}")
            if e.report:
                for d in e.report.defects:
                    lines.append(
                        f"      - {d.severity.value.upper():<8} {d.code.value}: "
                        f"{d.count} :: {d.message}"
                    )
            for w in e.warnings:
                lines.append(f"      ! {w}")
            lines.append("")
        return "\n".join(lines)


def migrate(
    v1_root: str | Path,
    v2_root: str | Path,
    *,
    instruments: list[str] | None = None,
    timeframes: list[Timeframe] | None = None,
    integrity_config: IntegrityConfig | None = None,
    store: DataStore | None = None,
) -> MigrationReport:
    """Run the migration. Never repairs; records everything it finds."""
    provider = HistDataParquetProvider(v1_root)
    owns_store = store is None
    store = store or DataStore.initialise(v2_root)
    report = MigrationReport(v1_root=str(provider.root), v2_root=str(store.root))

    wanted = provider.available()
    if instruments:
        upper = {s.upper() for s in instruments}
        wanted = [(i, tf) for i, tf in wanted if i in upper]
    if timeframes:
        wanted = [(i, tf) for i, tf in wanted if tf in set(timeframes)]

    try:
        for instrument, timeframe in wanted:
            entry = MigrationEntry(
                instrument=instrument,
                timeframe=timeframe,
                source_path=str(provider.path_for(instrument, timeframe)),
            )
            try:
                _migrate_one(provider, store, instrument, timeframe, entry, integrity_config)
            except Exception as exc:
                entry.error = f"{type(exc).__name__}: {exc}"
            report.entries.append(entry)
    finally:
        report.finished_at = datetime.now(tz=UTC)
        if owns_store:
            pass  # caller decides when to close; the catalogue is on disk already

    return report


def _migrate_one(
    provider: HistDataParquetProvider,
    store: DataStore,
    instrument: str,
    timeframe: Timeframe,
    entry: MigrationEntry,
    integrity_config: IntegrityConfig | None,
) -> None:
    batch = provider.fetch_bars(instrument, timeframe)
    frame = batch.frame
    entry.warnings = batch.warnings
    entry.row_count = len(frame)
    entry.first_timestamp = frame.index.min()
    entry.last_timestamp = frame.index.max()

    raw = store.write_raw(frame, batch.metadata)
    entry.raw_version_id = raw.version_id

    try:
        calendar = calendar_for(instrument)
    except KeyError:
        calendar = None

    cfg = integrity_config or IntegrityConfig()
    integrity = validate(frame, calendar=calendar, config=cfg)
    entry.report = integrity
    entry.quality = integrity.quality

    canonical_meta = BarDatasetMetadata(
        instrument=batch.metadata.instrument,
        timeframe=batch.metadata.timeframe,
        price_basis=batch.metadata.price_basis,
        source=batch.metadata.source,
        source_identifier=batch.metadata.source_identifier,
        ingestion_version=batch.metadata.ingestion_version,
        checksum=batch.metadata.checksum,
        row_count=batch.metadata.row_count,
        first_timestamp=batch.metadata.first_timestamp,
        last_timestamp=batch.metadata.last_timestamp,
        timezone_of_origin=batch.metadata.timezone_of_origin,
        quality=integrity.quality,
        kind=DatasetKind.VALIDATED,
        adjustments=batch.metadata.adjustments,
        gaps=integrity.gaps,
        repairs=(),
        columns=batch.metadata.columns,
        notes=batch.metadata.notes,
        extra={**batch.metadata.extra, "migrated_from_v1": True},
    )

    step = TransformationStep(
        operation="validate",
        parameters={
            "calendar": calendar.name if calendar else "none",
            "integrity_config": cfg.to_dict(),
            "repairs_applied": [],
        },
        code_version=MIGRATION_CODE_VERSION,
        inputs=(raw.version_id,),
    )
    canonical = store.write_canonical(
        frame,
        canonical_meta,
        source_version=raw.version,
        transformation=step,
        integrity=integrity,
        kind=DatasetKind.VALIDATED,
    )
    entry.canonical_version_id = canonical.version_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate the V1 HistData store to V2")
    parser.add_argument("v1_root", help="V1 canonical histdata root")
    parser.add_argument("v2_root", help="V2 data root (created if absent)")
    parser.add_argument("--instrument", action="append", dest="instruments")
    parser.add_argument("--timeframe", action="append", dest="timeframes")
    parser.add_argument("--json", dest="json_out", help="write the full report as JSON")
    args = parser.parse_args(argv)

    tfs = [Timeframe(t.upper()) for t in (args.timeframes or [])] or None
    report = migrate(
        args.v1_root, args.v2_root, instruments=args.instruments, timeframes=tfs
    )
    print(report.render())
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8"
        )
    return 0 if not report.failed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
