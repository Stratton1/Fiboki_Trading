"""Turn a campaign report into the per-instrument and per-rung breakdown K2 needs.

Usage::

    python research/analyse_campaign_k2.py \\
        --campaign research/reports/campaign_k2_multi_instrument/campaign_k2_multi_instrument_h4.json \\
        --ingest   research/reports/campaign_k2_multi_instrument/ingest_manifest.json \\
        --out      research/reports/campaign_k2_multi_instrument

Three questions this answers, and one it refuses to
---------------------------------------------------
1. Which rungs executed, and on how many cells. K1 never got past rung 2, so
   "rung 3 ran at all" is itself a result.
2. The full distribution of rejection reasons, by rung and by binding gate.
3. Whether any instrument looks systematically better. That pattern is usually
   a fact about the data -- bar count, session coverage, a trending sample --
   rather than about the strategy, so the table reports bars and trade counts
   beside the outcome and does not rank instruments by "quality".

It refuses to pick a winner. A cell that dies later than another cell has not
"nearly passed"; the ladder is not a score.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

RUNG_ORDER = [
    "RUNG 0 SANITY",
    "RUNG 1 IN_SAMPLE",
    "RUNG 2 WALK_FORWARD",
    "RUNG 3 PURGED_CV",
    "RUNG 4 ROBUSTNESS",
    "RUNG 5 DEFLATION",
    "RUNG 6 HOLDOUT",
]


def _rung_index(label: str) -> int:
    for i, name in enumerate(RUNG_ORDER):
        if label.startswith(name.split()[0] + " " + name.split()[1]):
            return i
    return -1


def _binding_gate(cell: dict[str, Any]) -> str:
    """The gate name a rejection actually turned on, or the rung's own reason."""
    binding = str(cell.get("binding_constraint") or "")
    if ":" in binding:
        return binding.split(":", 1)[0].strip()
    reason = str(cell.get("reason") or "")
    if ":" in reason and reason.split(":", 1)[0].strip().isupper() is False:
        return reason.split(":", 1)[0].strip()
    if "expectancy" in reason:
        return "expectancy_not_positive"
    return binding or reason[:60] or "unrecorded"


def analyse(report: dict[str, Any], ingest: dict[str, Any] | None) -> dict[str, Any]:
    attempted = list(report.get("attempted", ()))
    skipped = list(report.get("skipped", ()))

    bars_by_key: dict[str, int] = {}
    if ingest:
        for s in ingest.get("series", ()):
            bars_by_key[f"{s['instrument']}_{s['timeframe']}"] = int(s["bars_stored"])

    # -- how deep did the ladder actually get -------------------------------
    deepest_reached: Counter[str] = Counter()
    rung_executed: Counter[str] = Counter()
    for cell in attempted:
        died = str(cell.get("died_at_rung") or "")
        idx = _rung_index(died) if died else len(RUNG_ORDER) - 1
        if idx < 0:
            idx = 0
        deepest_reached[RUNG_ORDER[idx]] += 1
        # A cell that died at rung k executed rungs 0..k.
        for i in range(idx + 1):
            rung_executed[RUNG_ORDER[i]] += 1

    # -- rejection reasons ---------------------------------------------------
    by_rung: Counter[str] = Counter()
    by_gate: Counter[str] = Counter()
    by_rung_gate: dict[str, Counter[str]] = defaultdict(Counter)
    for cell in attempted:
        died = str(cell.get("died_at_rung") or "")
        if not died:
            continue
        gate = _binding_gate(cell)
        by_rung[died] += 1
        by_gate[gate] += 1
        by_rung_gate[died][gate] += 1

    # -- per instrument ------------------------------------------------------
    per_instrument: dict[str, dict[str, Any]] = {}
    for cell in attempted:
        inst = str(cell["instrument"])
        row = per_instrument.setdefault(
            inst,
            {
                "instrument": inst,
                "timeframe": str(cell.get("timeframe", "")),
                "bars": bars_by_key.get(f"{inst}_{cell.get('timeframe','')}"),
                "cells": 0,
                "survivors": 0,
                "cleared_rung_0": 0,
                "deepest_rung": "RUNG 0 SANITY",
                "trade_counts": [],
                "rejections": Counter(),
            },
        )
        row["cells"] += 1
        if cell.get("verdict") == "promote":
            row["survivors"] += 1
        died = str(cell.get("died_at_rung") or "")
        idx = _rung_index(died) if died else len(RUNG_ORDER) - 1
        if idx > 0:
            row["cleared_rung_0"] += 1
        if idx > _rung_index(row["deepest_rung"]):
            row["deepest_rung"] = RUNG_ORDER[max(idx, 0)]
        if cell.get("n_trades") is not None:
            row["trade_counts"].append(float(cell["n_trades"]))
        if died:
            row["rejections"][_binding_gate(cell)] += 1

    for row in per_instrument.values():
        tc = row.pop("trade_counts")
        row["n_cells_with_trade_count"] = len(tc)
        row["median_trades"] = round(statistics.median(tc), 1) if tc else None
        row["max_trades"] = round(max(tc), 1) if tc else None
        row["cells_at_or_above_400_trades"] = sum(1 for t in tc if t >= 400)
        row["rejections"] = dict(row["rejections"].most_common())

    # -- per strategy family -------------------------------------------------
    per_family: dict[str, dict[str, Any]] = {}
    for cell in attempted:
        root = str(cell["strategy_id"]).split("_m")[0]
        row = per_family.setdefault(
            root,
            {"family": root, "cells": 0, "survivors": 0, "cleared_rung_0": 0, "trade_counts": []},
        )
        row["cells"] += 1
        if cell.get("verdict") == "promote":
            row["survivors"] += 1
        died = str(cell.get("died_at_rung") or "")
        if _rung_index(died) > 0 or not died:
            row["cleared_rung_0"] += 1
        if cell.get("n_trades") is not None:
            row["trade_counts"].append(float(cell["n_trades"]))
    for row in per_family.values():
        tc = row.pop("trade_counts")
        row["median_trades"] = round(statistics.median(tc), 1) if tc else None

    # -- the trade-count question K1 left open --------------------------------
    trades = [
        (str(c["instrument"]), float(c["n_trades"]))
        for c in attempted
        if c.get("n_trades") is not None
    ]
    min_trades_rejections = sum(
        1 for c in attempted if _binding_gate(c) == "min_trades"
    )

    return {
        "campaign_id": report.get("campaign_id"),
        "gate_set_version": report.get("gate_set_version"),
        "true_trial_count": report.get("true_trial_count"),
        "trial_accounting": (report.get("lineage") or {}).get("trial_accounting", {}),
        "campaign_deflation_threshold": report.get("campaign_deflation_threshold"),
        "deflation_variance_used": report.get("deflation_variance_used"),
        "cells_attempted": len(attempted),
        "cells_skipped": len(skipped),
        "survivors": [
            {
                "strategy_id": c["strategy_id"],
                "instrument": c["instrument"],
                "timeframe": c["timeframe"],
                "key": c["key"],
                "n_trades": c.get("n_trades"),
                "selected_sharpe": c.get("selected_sharpe"),
                "deflated_sharpe_ratio": c.get("deflated_sharpe_ratio"),
                "deflation_threshold": c.get("deflation_threshold"),
                "n_trials_used_for_deflation": c.get("n_trials_used_for_deflation"),
                "report_path": c.get("report_path"),
                "dataset_version_id": c.get("dataset_version_id"),
            }
            for c in attempted
            if c.get("verdict") == "promote"
        ],
        "deepest_rung_reached": {
            k: deepest_reached.get(k, 0) for k in RUNG_ORDER if deepest_reached.get(k)
        },
        "rungs_executed_on_n_cells": {
            k: rung_executed.get(k, 0) for k in RUNG_ORDER if rung_executed.get(k)
        },
        "rejections_by_rung": dict(by_rung.most_common()),
        "rejections_by_binding_gate": dict(by_gate.most_common()),
        "rejections_by_rung_then_gate": {
            r: dict(g.most_common()) for r, g in by_rung_gate.items()
        },
        "min_trades_rejection_share": (
            round(min_trades_rejections / len(attempted), 4) if attempted else None
        ),
        "skips_by_kind": report.get("skips_by_kind", {}),
        "per_instrument": [
            per_instrument[k] for k in sorted(per_instrument, key=lambda i: -per_instrument[i]["cells"])
        ],
        "per_family": [per_family[k] for k in sorted(per_family)],
        "trade_count_extremes": {
            "max": max(trades, key=lambda t: t[1]) if trades else None,
            "min": min(trades, key=lambda t: t[1]) if trades else None,
            "median_across_all_cells": (
                round(statistics.median([t[1] for t in trades]), 1) if trades else None
            ),
        },
        "holdout": report.get("holdout", {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--ingest", default=None, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    report = json.loads(args.campaign.read_text(encoding="utf-8"))
    ingest = (
        json.loads(args.ingest.read_text(encoding="utf-8")) if args.ingest else None
    )
    analysis = analyse(report, ingest)

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "analysis.json"
    path.write_text(json.dumps(analysis, indent=2, default=str), encoding="utf-8")

    print(json.dumps({k: v for k, v in analysis.items() if k not in ("per_instrument", "per_family")}, indent=2, default=str))
    print()
    print(f"{'instrument':10s} {'bars':>7s} {'cells':>6s} {'passed r0':>10s} {'med trades':>11s} {'>=400':>6s} {'deepest':>20s}")
    for row in analysis["per_instrument"]:
        print(
            f"{row['instrument']:10s} {row['bars'] or 0:>7,d} {row['cells']:>6d} "
            f"{row['cleared_rung_0']:>10d} {row['median_trades']!s:>11s} "
            f"{row['cells_at_or_above_400_trades']:>6d} {row['deepest_rung']:>20s}"
        )
    print()
    print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
