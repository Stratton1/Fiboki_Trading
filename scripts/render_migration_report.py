#!/usr/bin/env python
"""Render the per-instrument migration table from the ingest manifests.

Reads every ``ingest_manifest.json`` under a directory and writes one markdown
table per timeframe: rows, date range, quality verdict, and the integrity
findings by severity. Defects are reported, not summarised away -- a series with
a WARNING-severity defect is a usable series with a stated defect, and the
column says which.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SEVERITY_ORDER = ("critical", "error", "warning", "info")


def _findings(row: dict) -> str:
    before = row.get("integrity_before") or {}
    counts = Counter()
    detail: list[str] = []
    for defect in before.get("defects") or []:
        counts[defect["severity"]] += 1
        detail.append(f"{defect['code']} x {defect['count']:,}")
    detail.sort(key=lambda t: t)
    prefix = " ".join(
        f"{s[:4].upper()}:{counts[s]}" for s in SEVERITY_ORDER if counts[s]
    )
    return f"{prefix} — {', '.join(detail)}" if detail else "none"


def render(manifest_path: Path) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest["series"]
    timeframe = rows[0]["timeframe"] if rows else "?"
    lines = [
        f"### {timeframe} — {len(rows)} series, "
        f"{manifest['total_bars_stored']:,} bars stored, "
        f"{manifest['total_rows_removed_by_repair']} row(s) removed by authorised repair",
        "",
        "| Instrument | Rows | First bar | Last bar | Quality | Readable | Repair | Integrity findings (pre-repair) |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda r: r["instrument"]):
        repair = "none"
        if row["repaired"]:
            removed = [
                str(t)
                for rec in row["repair_records"]
                for t in rec["removed_timestamps"]
            ]
            repair = (
                f"**drop_non_positive** -{row['rows_removed']} row(s) at "
                f"{', '.join(removed)}"
            )
        lines.append(
            f"| {row['instrument']} | {row['bars_stored']:,} | "
            f"{str(row['first_bar'])[:10]} | {str(row['last_bar'])[:10]} | "
            f"{row['quality']} | {'yes' if row['readable_by_campaign'] else '**NO**'} | "
            f"{repair} | {_findings(row)} |"
        )
    lines.append("")
    if manifest["series_excluded"]:
        lines.append("**Excluded series:**")
        for item in manifest["series_excluded"]:
            lines.append(f"- {item['instrument']} {item['timeframe']}: {item['reason']}")
    else:
        lines.append(
            "No series was excluded: every blocking defect found was the "
            "non-positive-price sentinel, which is the one repair this ingest is "
            "authorised to perform."
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reports", type=Path, default=REPO / "research/reports/v2_store_migration"
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out or (args.reports / "PER_INSTRUMENT.md")

    manifests = sorted(args.reports.glob("*/ingest_manifest.json"))
    if not manifests:
        print(f"no ingest_manifest.json under {args.reports}")
        return 2
    body = ["# V1 -> V2 migration: per-instrument results", ""]
    for path in manifests:
        body.append(render(path))
    out.write_text("\n".join(body), encoding="utf-8")
    print(f"-> {out}  ({len(manifests)} manifest(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
