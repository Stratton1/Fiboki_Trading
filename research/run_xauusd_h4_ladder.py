"""Run the validation ladder against the REAL engine on REAL XAUUSD H4 bars.

Usage::

    python research/run_xauusd_h4_ladder.py \\
        --histdata /path/to/canonical/histdata \\
        --data-root /path/to/a/fiboki/data/root \\
        --out research/reports

What this script is for
-----------------------
It is the end-to-end proof that the validation ladder can be run on real data:
the bars are ingested through the data layer (so the +5h HistData correction and
the BID price basis are declared rather than assumed), a dataset version is
minted, a holdout segment is reserved from that version's date range, and each
seed strategy is swept, fitted, transferred and -- if it gets that far -- shown
the holdout exactly once.

Two gate sets, and the difference matters
-----------------------------------------
``--gates production`` uses ``GATE_SET_V2``, whose ``min_trades`` is 400. One
instrument on one timeframe over sixteen years does not produce 400 trades from
a trend system, so this run rejects at rung 0. That is the correct answer and
the honest one: the promotion bar was written for a campaign across 60
instruments, and a single cell of that campaign cannot clear it alone.

``--gates diagnostic`` mints a NEW, explicitly named gate set with a lower
``min_trades`` so that every rung actually executes against real data. Its
reports carry that version string, so nothing produced under it can ever be
mistaken for a promotion decision. Nothing else is changed: no threshold is
moved to make a strategy pass, and the rejection each strategy earns is
reported as it comes.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.core.money import IdentityFxSource
from fiboki.data.integrity import validate as validate_integrity
from fiboki.data.providers.histdata import HistDataParquetProvider
from fiboki.data.schema import BarDatasetMetadata, DatasetKind
from fiboki.data.store import DataStore
from fiboki.data.versioning import TransformationStep
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.gates import GATE_SET_V2, GateSet
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig
from fiboki.validation.report import ValidationReport
from fiboki.validation.run import run_validation

SEED_DIR = Path("research/strategies")
INSTRUMENT = "XAUUSD"
TIMEFRAME = Timeframe.H4

#: XAUUSD is quoted in USD. The account is run in USD for this campaign so that
#: no exchange rate is invented: the production account is GBP, and a GBP figure
#: would need a real GBPUSD series, which this dataset does not contain. Every
#: monetary number below is therefore USD, and is labelled USD.
ACCOUNT_CCY = "USD"

#: Loosened ONLY in ``--gates diagnostic``, and only so that the rungs after 0
#: execute on real data at all. 25 trades is not a promotion bar and this script
#: never treats it as one.
DIAGNOSTIC_MIN_TRADES = 25


@dataclass(frozen=True)
class Ingested:
    frame: pd.DataFrame
    version_id: str
    quality: str
    defects: list[dict[str, Any]]


def ingest(histdata_root: Path, data_root: Path) -> Ingested:
    """Provider -> RAW -> integrity -> CANONICAL, and hand back a real version id."""
    store = DataStore.initialise(data_root)
    provider = HistDataParquetProvider(histdata_root)
    batch = provider.fetch_bars(INSTRUMENT, TIMEFRAME)

    raw = store.write_raw(batch.frame, batch.metadata)
    report = validate_integrity(batch.frame)
    quality = DataQuality.VALIDATED if report.is_clean else DataQuality.SUSPECT
    canonical_meta = BarDatasetMetadata.from_dict(
        {**batch.metadata.to_dict(), "quality": quality.value, "kind": DatasetKind.VALIDATED.value}
    )
    store.write_canonical(
        batch.frame,
        canonical_meta,
        source_version=raw.version,
        transformation=TransformationStep(
            operation="integrity_validated",
            parameters={"checks_run": list(report.checks_run), "repaired": False},
            code_version="fiboki-v2",
        ),
        integrity=report,
    )
    frame, version = store.read_latest(INSTRUMENT, TIMEFRAME, kind=DatasetKind.VALIDATED)
    store.close()
    return Ingested(
        frame=frame,
        version_id=version.version_id,
        quality=version.quality.value,
        defects=[
            {"code": d.code.value, "severity": d.severity.value, "count": d.count}
            for d in report.defects
        ],
    )


def gate_set_for(mode: str) -> GateSet:
    if mode == "production":
        return GATE_SET_V2
    return GATE_SET_V2.with_overrides(
        f"diagnostic-single-instrument-min{DIAGNOSTIC_MIN_TRADES}",
        min_trades=float(DIAGNOSTIC_MIN_TRADES),
    )


def describe(report: ValidationReport) -> dict[str, Any]:
    failing = report.first_failing_rung()
    rungs = []
    for index in range(7):
        rung = report.rung(index)
        if rung is None:
            continue
        rungs.append(
            {
                "index": index,
                "name": rung.name,
                "outcome": rung.outcome.value,
                "reason": rung.reason,
            }
        )
    return {
        "strategy_id": report.strategy_id,
        "strategy_content_hash": report.strategy_content_hash[:16],
        "gate_set": report.gate_set_version,
        "verdict": report.verdict.value,
        "died_at": failing.label if failing is not None else None,
        "reason": failing.reason if failing is not None else "",
        "binding_constraint": report.binding_constraint.describe(),
        "gate_values": report.gate_values(),
        "n_evaluations": report.windows.get("n_evaluations"),
        "rungs": rungs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--histdata", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--out", default=Path("research/reports"), type=Path)
    parser.add_argument("--cache", default=None, type=Path)
    parser.add_argument("--gates", choices=("production", "diagnostic"), default="production")
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["donchian_breakout_atr", "ichimoku_kumo_trend"],
    )
    parser.add_argument("--max-grid-points", type=int, default=16)
    parser.add_argument(
        "--sweep-parameters",
        nargs="*",
        default=None,
        help=(
            "Restrict the sweep to these declared parameters. Grid capping never "
            "DROPS an axis (that would make the report describe a sweep that did "
            "not happen), so a 7-parameter document has a 128-point floor and a "
            "demonstration run has to say out loud which axes it swept. The "
            "unswept ones take their declared defaults and are recorded in every "
            "binding."
        ),
    )
    parser.add_argument("--folds", type=int, default=4)
    args = parser.parse_args(argv)

    data = ingest(args.histdata, args.data_root)
    print(
        f"dataset {data.version_id[:16]} quality={data.quality} "
        f"bars={len(data.frame)} {data.frame.index[0].date()}..{data.frame.index[-1].date()}"
    )
    for defect in data.defects:
        print(f"  integrity {defect['severity']:8s} {defect['code']:24s} x{defect['count']}")

    gates = gate_set_for(args.gates)
    print(f"gate set: {gates.version} (min_trades={gates.by_name('min_trades').threshold:g})")

    args.out.mkdir(parents=True, exist_ok=True)
    registry = HoldoutRegistry(args.out / "holdout.sqlite")

    results = []
    for strategy_id in args.strategies:
        document = StrategyDocument.from_json(
            (SEED_DIR / f"{strategy_id}.json").read_text()
        )
        run = run_validation(
            document=document,
            bars=data.frame,
            dataset_version_id=data.version_id,
            instrument=INSTRUMENT,
            timeframe=TIMEFRAME,
            registry=registry,
            account_ccy=ACCOUNT_CCY,
            fx=IdentityFxSource(),
            fx_label=f"identity(USD=={ACCOUNT_CCY})",
            gate_set=gates,
            ladder_config=LadderConfig(
                min_trades=int(gates.by_name("min_trades").threshold),
                selection_metric="sharpe",
                walk_forward_folds=args.folds,
                stress_samples=100,
                spa_bootstraps=400,
            ),
            max_grid_points=args.max_grid_points,
            max_values_per_axis=3,
            sweep_parameters=args.sweep_parameters,
            cache_dir=args.cache,
            actor="script:run_xauusd_h4_ladder",
            notes=(
                f"XAUUSD H4 single-instrument cell, {args.gates} gate set. "
                "USD account, IG_REALISTIC profile, no FX conversion performed."
            ),
        )
        summary = describe(run.report)
        summary["grid"] = run.candidate.grid.to_dict()
        summary["declared_parameters"] = sorted(document.parameters)
        summary["swept_parameters"] = list(run.candidate.grid.names)
        summary["engine_runs"] = run.evaluator.n_engine_runs
        summary["cache"] = run.evaluator.cache.stats() if run.evaluator.cache else {}
        results.append(summary)
        path = args.out / f"{strategy_id}__{args.gates}__{data.version_id[:12]}.json"
        run.report.save(path)
        print(json.dumps(summary, indent=2, default=str))
        print(f"  -> {path}")

    print(json.dumps({"dataset": data.version_id, "results": results}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
