"""Ingest every HistData series in a root through the V2 data layer.

Usage::

    python research/ingest_histdata_universe.py \\
        --histdata data/canonical/histdata \\
        --data-root /path/to/a/fiboki/data/root \\
        --out research/reports/campaign_k2_multi_instrument

What this does, and why it is not a loop around ``pd.read_parquet``
------------------------------------------------------------------
Every series goes provider -> RAW (immutable, checksummed) -> integrity ->
CANONICAL, so the +5h EST-no-DST correction and the ``price_basis=BID``
declaration are recorded in dataset metadata rather than assumed by whoever
reads the file next, and every downstream result can name the dataset version
it was computed from.

The integrity verdict is obeyed, not narrated around
----------------------------------------------------
``validate()`` classifies defects by severity. Anything at ERROR or above makes
the dataset SUSPECT, and ``DataStore.read_latest`` refuses to serve a SUSPECT
dataset unless the caller opts in. That refusal is the point: a campaign must
not silently consume a series with a negative price in it.

One repair is authorised here and only one
------------------------------------------
``DROP_NON_POSITIVE``. HistData's EURUSD files carry a sentinel bar with a
negative price -- the same defect already known in its H1 file. A negative price
poisons every return computed across it, so the bar is dropped under an explicit
:class:`~fiboki.data.integrity.RepairPlan` that names the reason and the actor,
the removed timestamps are printed and written to the manifest, and the repaired
frame is stored as a CANONICAL dataset whose lineage points back at the
unrepaired RAW bytes. Nothing else is repaired: gaps, off-session bars, stale
runs and return outliers are reported and left alone, because they are
properties of the market and the calendar rather than corruption.

A series whose blocking defects are NOT exactly the non-positive-price defect is
written as SUSPECT and excluded from the campaign. That exclusion is a finding,
and it is printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.integrity import (
    DefectCode,
    IntegrityReport,
    RepairAction,
    RepairPlan,
)
from fiboki.data.integrity import repair as repair_frame
from fiboki.data.integrity import validate as validate_integrity
from fiboki.data.providers.histdata import HistDataParquetProvider
from fiboki.data.schema import BarDatasetMetadata, DatasetKind
from fiboki.data.store import DataStore
from fiboki.data.versioning import TransformationStep

#: The only defect this script is allowed to repair, and the only action it may
#: use to do it. Widening either of these is a decision a person has to make.
REPAIRABLE = {DefectCode.NON_POSITIVE_PRICE}

ACTOR = "script:ingest_histdata_universe"


def _defect_rows(report: IntegrityReport) -> list[dict[str, Any]]:
    return [
        {
            "code": d.code.value,
            "severity": d.severity.value,
            "count": int(d.count),
            "message": d.message,
            "sample_timestamps": [str(s) for s in d.sample_timestamps][:5],
        }
        for d in report.defects
    ]


def ingest_one(
    store: DataStore,
    provider: HistDataParquetProvider,
    instrument: str,
    timeframe: Timeframe,
) -> dict[str, Any]:
    batch = provider.fetch_bars(instrument, timeframe)
    raw = store.write_raw(batch.frame, batch.metadata)
    report = validate_integrity(batch.frame)

    row: dict[str, Any] = {
        "instrument": instrument,
        "timeframe": timeframe.value,
        "raw_version_id": raw.version.version_id,
        "bars_ingested": int(len(batch.frame)),
        "first_bar": str(batch.frame.index[0]) if len(batch.frame) else None,
        "last_bar": str(batch.frame.index[-1]) if len(batch.frame) else None,
        "price_basis": batch.metadata.price_basis.value,
        "timezone_of_origin": batch.metadata.timezone_of_origin,
        "adjustments": [a.to_dict() for a in batch.metadata.adjustments],
        "integrity_before": {
            "quality": report.quality.value,
            "is_clean": bool(report.is_clean),
            "worst_severity": report.worst_severity.value,
            "gap_count": len(report.gaps),
            "unexpected_gap_count": sum(1 for g in report.gaps if not g.expected),
            "defects": _defect_rows(report),
        },
        "repaired": False,
        "repair_records": [],
        "rows_removed": 0,
        "excluded": False,
        "exclusion_reason": "",
    }

    frame = batch.frame
    final_report = report
    transformation = TransformationStep(
        operation="integrity_validated",
        parameters={"checks_run": list(report.checks_run), "repaired": False},
        code_version="fiboki-v2",
    )

    blocking = {d.code for d in report.blocking_defects}
    if blocking and blocking <= REPAIRABLE:
        plan = RepairPlan(
            actions=(RepairAction.DROP_NON_POSITIVE,),
            reason=(
                f"{instrument} {timeframe.value}: HistData ships sentinel bar(s) with a "
                "non-positive price. A negative price makes every return computed across "
                "it meaningless, so the bar is dropped rather than carried into a "
                "backtest. No other defect is repaired."
            ),
            actor=ACTOR,
        )
        result = repair_frame(frame, plan)
        frame = result.frame
        row["repaired"] = True
        row["rows_removed"] = int(result.rows_removed)
        row["repair_records"] = [
            {
                "action": r.action,
                "rows_before": int(r.rows_before),
                "rows_after": int(r.rows_after),
                "removed_timestamps": list(r.affected_timestamps),
                "reason": r.reason,
                "actor": r.actor,
            }
            for r in result.records
        ]
        final_report = validate_integrity(frame)
        row["integrity_after"] = {
            "quality": final_report.quality.value,
            "is_clean": bool(final_report.is_clean),
            "worst_severity": final_report.worst_severity.value,
            "defects": _defect_rows(final_report),
        }
        transformation = TransformationStep(
            operation="integrity_repaired",
            parameters={
                "checks_run": list(final_report.checks_run),
                "repaired": True,
                "actions": [a.value for a in plan.actions],
                "reason": plan.reason,
                "actor": plan.actor,
                "rows_removed": int(result.rows_removed),
                "removed_timestamps": list(result.records[0].affected_timestamps),
            },
            code_version="fiboki-v2",
        )
    elif blocking:
        row["excluded"] = True
        row["exclusion_reason"] = (
            f"blocking defect(s) {sorted(c.value for c in blocking)} are outside the one "
            "repair this script is authorised to perform (drop_non_positive); the dataset "
            "is stored SUSPECT and the campaign will not read it"
        )

    quality = (
        DataQuality.VALIDATED if final_report.is_clean else final_report.quality
    )
    canonical_meta = BarDatasetMetadata.from_dict(
        {
            **batch.metadata.to_dict(),
            "quality": quality.value,
            "kind": DatasetKind.VALIDATED.value,
            "checksum": "",
        }
    )
    stored = store.write_canonical(
        frame,
        canonical_meta,
        source_version=raw.version,
        transformation=transformation,
        integrity=final_report,
    )
    row["canonical_version_id"] = stored.version.version_id
    row["bars_stored"] = int(len(frame))
    row["quality"] = quality.value
    row["readable_by_campaign"] = bool(final_report.is_clean)
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--histdata", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--timeframes",
        nargs="*",
        default=None,
        help="Restrict to these timeframes. Default: everything the root holds.",
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    store = DataStore.initialise(args.data_root)
    provider = HistDataParquetProvider(args.histdata)

    wanted = {Timeframe(t) for t in args.timeframes} if args.timeframes else None
    series = [
        (sym, tf) for sym, tf in provider.available() if wanted is None or tf in wanted
    ]
    series.sort()

    rows: list[dict[str, Any]] = []
    for instrument, timeframe in series:
        row = ingest_one(store, provider, instrument, timeframe)
        rows.append(row)
        flag = "EXCLUDED" if not row["readable_by_campaign"] else "ok      "
        print(
            f"{flag} {instrument:7s} {timeframe.value:3s} "
            f"{row['bars_stored']:>7,d} bars  {str(row['first_bar'])[:10]}..{str(row['last_bar'])[:10]}  "
            f"quality={row['quality']:9s} ds={row['canonical_version_id'][:14]}"
        )
        for d in row["integrity_before"]["defects"]:
            print(f"           {d['severity']:8s} {d['code']:22s} x{d['count']}")
        if row["repaired"]:
            for r in row["repair_records"]:
                print(
                    f"           REPAIR   {r['action']:22s} removed {r['rows_before'] - r['rows_after']} "
                    f"row(s) at {r['removed_timestamps']}"
                )
        if row["excluded"]:
            print(f"           EXCLUDE  {row['exclusion_reason']}")

    store.close()

    usable = [r for r in rows if r["readable_by_campaign"]]
    manifest = {
        "histdata_root": str(args.histdata.resolve()),
        "data_root": str(args.data_root.resolve()),
        "series_found": len(rows),
        "series_usable": len(usable),
        "series_excluded": [
            {"instrument": r["instrument"], "timeframe": r["timeframe"], "reason": r["exclusion_reason"]}
            for r in rows
            if not r["readable_by_campaign"]
        ],
        "total_bars_ingested": int(sum(r["bars_ingested"] for r in rows)),
        "total_bars_stored": int(sum(r["bars_stored"] for r in rows)),
        "total_rows_removed_by_repair": int(sum(r["rows_removed"] for r in rows)),
        "dataset_versions": {
            f"{r['instrument']}_{r['timeframe']}": r["canonical_version_id"] for r in rows
        },
        "series": rows,
    }
    path = args.out / "ingest_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    print()
    print(
        f"{len(rows)} series ingested, {len(usable)} usable, "
        f"{manifest['total_bars_stored']:,} bars stored "
        f"({manifest['total_rows_removed_by_repair']} row(s) removed by authorised repair)"
    )
    print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
