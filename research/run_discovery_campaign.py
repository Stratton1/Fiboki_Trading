"""Run a real Phase K discovery campaign against the real engine on real bars.

Usage::

    python research/run_discovery_campaign.py \\
        --data-root /path/to/a/fiboki/data/root \\
        --out research/reports/campaign_k1 \\
        --cache /tmp/ladder-cache \\
        --gates production

What this script is
-------------------
The end-to-end proof that the discovery loop runs on real data: seeds are read
from ``research/strategies``, hypotheses from ``research/hypotheses``, mutations
are proposed and validated, novelty is checked against the experiment ledger
BEFORE compute is spent, the true campaign-wide trial count is fixed before the
first evaluation, and every cell goes through the full validation ladder with
that count supplied as ``external_trial_count``.

What it is NOT
--------------
It is not a search for something that passes. Nothing here is tuned to produce a
survivor, and the expected outcome on a single instrument and a single timeframe
is that nothing does. A campaign that honestly rejects everything is a
successful campaign.

Two gate sets, and the difference matters
-----------------------------------------
``--gates production`` uses ``GATE_SET_V2``, whose ``min_trades`` is 400.
``--gates diagnostic`` mints a NEW, explicitly named gate set with a lower
``min_trades`` so that the rungs after 0 execute on real data at all. Its
reports carry that version string, so nothing produced under it can ever be
mistaken for a promotion decision. No other threshold is moved, ever.

Resume
------
``--out`` holds ``checkpoint.json``. Re-running the same command resumes: cells
that produced a report are replayed from the checkpoint rather than recomputed,
and cells that returned no data are retried, because a missing feed is a gap in
the search rather than a result about a strategy.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import SeriesFxSource
from fiboki.data.schema import DatasetKind
from fiboki.data.store import DataStore
from fiboki.discovery.campaign import (
    BarSet,
    CampaignCheckpoint,
    CampaignRunner,
    CampaignSpec,
    seed_documents,
)
from fiboki.discovery.hypothesis import (
    HypothesisLedger,
    HypothesisStatus,
    load_hypotheses,
)
from fiboki.research.experiment import (
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    Outcome,
)
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.gates import GATE_SET_V2, GateSet
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.report import ValidationReport

SEED_DIR = Path("research/strategies")
HYPOTHESIS_DIR = Path("research/hypotheses")

#: The account currency for the campaign. Every monetary figure is in it.
ACCOUNT_CCY = "USD"

#: Quote currency -> the HistData series that converts it into USD.
#:
#: Nine of sixteen H4 series are quoted in something other than USD, and
#: ``run_validation`` refuses to run those in a USD account without a real rate
#: rather than applying a 1.0 that would mis-state every monetary figure by the
#: exchange rate. These are the pairs that supply the rate.
#: :class:`~fiboki.core.money.SeriesFxSource` derives the inverse itself, so
#: ``USDJPY`` serves ``JPY -> USD`` as ``1 / USDJPY`` and no rate is inverted by
#: hand. The rates are bid closes from the same HistData source as the bars, and
#: the conversion is therefore a bid-to-bid conversion, not a mid one -- stated
#: rather than corrected, because correcting it would need an ask series that
#: does not exist.
FX_SERIES_FOR: dict[str, str] = {
    "JPY": "USDJPY",
    "CHF": "USDCHF",
    "CAD": "USDCAD",
    "GBP": "GBPUSD",
    "EUR": "EURUSD",
}

#: Loosened ONLY under ``--gates diagnostic``, and only so that the rungs after
#: 0 execute on real data. 25 trades is not a promotion bar.
DIAGNOSTIC_MIN_TRADES = 25

#: Which declared axes each seed sweeps. Grid capping never DROPS an axis, so a
#: seven-parameter document has a 128-point floor; a campaign that wants an
#: eight-point grid per cell has to say out loud which three axes it swept. The
#: unswept parameters take their declared defaults and are recorded in every
#: binding. Mutants inherit their root seed's entry.
SWEEP_AXES: dict[str, tuple[str, ...]] = {
    "donchian_breakout_atr": ("channel_period", "stop_atr_multiple", "trail_atr_multiple"),
    "ichimoku_kumo_trend": ("tenkan_period", "kijun_period", "stop_buffer_atr"),
    "macd_ema_trend_hybrid": ("macd_fast", "macd_slow", "stop_atr_multiple"),
    "fib_golden_pocket_pullback": ("swing_lookback", "pocket_near", "stop_buffer_atr"),
    "rsi_band_mean_reversion": ("rsi_period", "rsi_floor", "bb_num_std"),
}


def gate_set_for(mode: str) -> GateSet:
    if mode == "production":
        return GATE_SET_V2
    return GATE_SET_V2.with_overrides(
        f"diagnostic-single-instrument-min{DIAGNOSTIC_MIN_TRADES}",
        min_trades=float(DIAGNOSTIC_MIN_TRADES),
    )


def build_fx_source(
    store: DataStore, instruments: Sequence[str], timeframe: Timeframe
) -> tuple[SeriesFxSource | None, str, dict[str, Any]]:
    """An FX source over the SAME ingested bars, or an honest refusal.

    Returns ``(source, label, coverage)``. ``coverage`` names which pairs were
    loaded, which quote currencies each instrument needs, and -- the part that
    matters -- any instrument whose first bar predates the first observation of
    the series that has to convert it, because ``SeriesFxSource`` looks up
    as-of BACKWARD and will raise rather than extrapolate. Such an instrument is
    reported here so it can be dropped from the universe deliberately instead of
    erroring cell by cell halfway through a campaign.
    """
    needed: dict[str, list[str]] = {}
    for symbol in instruments:
        quote = get_instrument(symbol.upper()).quote.upper()
        if quote != ACCOUNT_CCY:
            needed.setdefault(quote, []).append(symbol.upper())
    if not needed:
        return None, "", {"pairs_loaded": [], "needed_by_quote_ccy": {}}

    series: dict[str, pd.Series] = {}
    loaded: dict[str, dict[str, Any]] = {}
    missing: dict[str, str] = {}
    for quote in sorted(needed):
        pair = FX_SERIES_FOR.get(quote)
        if pair is None:
            missing[quote] = f"no series declared in FX_SERIES_FOR for {quote}"
            continue
        try:
            frame, version = store.read_latest(
                pair, timeframe, kind=DatasetKind.VALIDATED
            )
        except Exception as exc:
            missing[quote] = f"{pair} {timeframe.value} not readable: {exc}"
            continue
        close = frame["close"].astype(float)
        series[pair.upper()] = close
        loaded[pair.upper()] = {
            "quote_ccy_served": quote,
            "dataset_version_id": str(version.version_id),
            "observations": int(len(close)),
            "first": str(close.index[0]),
            "last": str(close.index[-1]),
        }

    # Coverage: a rate must exist at or before every bar it has to convert.
    uncovered: list[dict[str, Any]] = []
    for quote, symbols in sorted(needed.items()):
        pair = FX_SERIES_FOR.get(quote)
        if pair is None or pair.upper() not in series:
            for symbol in symbols:
                uncovered.append(
                    {"instrument": symbol, "quote_ccy": quote, "reason": missing.get(quote, "")}
                )
            continue
        fx_first = series[pair.upper()].index[0]
        for symbol in symbols:
            try:
                bars, _ = store.read_latest(symbol, timeframe, kind=DatasetKind.VALIDATED)
            except Exception as exc:
                uncovered.append(
                    {"instrument": symbol, "quote_ccy": quote, "reason": f"bars unreadable: {exc}"}
                )
                continue
            if len(bars) and bars.index[0] < fx_first:
                uncovered.append(
                    {
                        "instrument": symbol,
                        "quote_ccy": quote,
                        "reason": (
                            f"first bar {bars.index[0]} predates the first {pair} "
                            f"observation {fx_first}; an as-of backward lookup has "
                            "nothing to read there and would raise"
                        ),
                    }
                )

    label = (
        "SeriesFxSource(" + ", ".join(f"{k}->{v['quote_ccy_served']}" for k, v in sorted(loaded.items()))
        + f"; bid closes, HistData {timeframe.value}, as-of backward)"
    )
    coverage = {
        "pairs_loaded": loaded,
        "needed_by_quote_ccy": {k: sorted(v) for k, v in sorted(needed.items())},
        "missing_series": missing,
        "instruments_without_usable_coverage": uncovered,
        "label": label,
    }
    return (SeriesFxSource(series=series) if series else None), label, coverage


def bars_from_store(store: DataStore):
    """A :class:`~fiboki.discovery.campaign.BarSource` over a data root."""

    def source(instrument: str, timeframe: Timeframe) -> BarSet | None:
        try:
            frame, version = store.read_latest(
                instrument, timeframe, kind=DatasetKind.VALIDATED
            )
        except Exception as exc:
            print(f"  no bars for {instrument} {timeframe.value}: {exc}")
            return None
        if frame is None or len(frame) == 0:
            return None
        return BarSet(
            instrument=instrument.upper(),
            timeframe=timeframe,
            frame=frame,
            dataset_version_id=str(version.version_id),
        )

    return source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--out", default=Path("research/reports/campaign"), type=Path)
    parser.add_argument("--cache", default=None, type=Path)
    parser.add_argument("--gates", choices=("production", "diagnostic"), default="production")
    parser.add_argument("--campaign-id", default="k1_xauusd_h4")
    parser.add_argument("--instruments", nargs="+", default=["XAUUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["H4"])
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--max-evaluations", type=int, default=400)
    parser.add_argument("--max-grid-points", type=int, default=8)
    parser.add_argument("--max-values-per-axis", type=int, default=2)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--actor", default="script:run_discovery_campaign")
    parser.add_argument(
        "--backfill-reports",
        default=None,
        type=Path,
        help=(
            "Directory of ValidationReport JSON produced by EARLIER runs on the "
            "same bars. Each one is filed in the campaign's experiment ledger "
            "before planning, so its trials are counted in the true trial count "
            "and its strategies are visible to the novelty check. Those trials "
            "were part of the search whatever directory they were written to."
        ),
    )
    parser.add_argument(
        "--external-prior-trials",
        type=int,
        default=0,
        help=(
            "Trials already spent on THESE BARS that this ledger cannot see. A "
            "dataset version id hashes content together with lineage, so a fresh "
            "ingest of identical bytes mints a new id and the ledger reports zero "
            "prior trials for a series that has been searched hundreds of times. "
            "This adds them back. It can only raise the trial count, and it is "
            "refused without --external-prior-trials-reason."
        ),
    )
    parser.add_argument(
        "--external-prior-trials-reason",
        default="",
        help="Where those trials were spent and how you know they were on these bars.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the plan and the trial accounting, and run nothing.",
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    store = DataStore(args.data_root)
    gates = gate_set_for(args.gates)
    seeds = seed_documents(SEED_DIR)
    hypotheses = load_hypotheses(HYPOTHESIS_DIR)

    instruments = [s.upper() for s in args.instruments]
    timeframes = [Timeframe(t) for t in args.timeframes]
    fx, fx_label, fx_coverage = build_fx_source(store, instruments, timeframes[0])
    (args.out / "fx_coverage.json").write_text(
        json.dumps(fx_coverage, indent=2, default=str), encoding="utf-8"
    )
    if fx is None:
        print("fx: none needed -- every instrument is quoted in the account currency")
    else:
        print(f"fx: {fx_label}")
        for pair, detail in sorted(fx_coverage["pairs_loaded"].items()):
            print(
                f"     {pair} serves {detail['quote_ccy_served']}->{ACCOUNT_CCY}, "
                f"{detail['observations']:,} obs {detail['first'][:10]}..{detail['last'][:10]}"
            )
    for row in fx_coverage.get("instruments_without_usable_coverage", ()):
        print(f"     NO FX COVERAGE {row['instrument']}: {row['reason']}")

    spec = CampaignSpec(
        campaign_id=args.campaign_id,
        universe=tuple(instruments),
        timeframes=tuple(timeframes),
        hypotheses=hypotheses,
        actor=args.actor,
        actor_kind=ActorKind.AGENT,
        account_ccy=ACCOUNT_CCY,
        gate_set=gates,
        max_evaluations=args.max_evaluations,
        max_grid_points=args.max_grid_points,
        max_values_per_axis=args.max_values_per_axis,
        sweep_parameters=SWEEP_AXES,
        walk_forward_folds=args.folds,
        max_generations=args.generations,
        external_prior_trials=args.external_prior_trials,
        external_prior_trials_reason=args.external_prior_trials_reason,
        fx_label=fx_label,
        notes=(
            f"Phase K campaign {args.campaign_id} on "
            f"{len(instruments)} instrument(s) {', '.join(instruments)} "
            f"{', '.join(args.timeframes)}, {args.gates} gate set, "
            f"{ACCOUNT_CCY} account, IG_REALISTIC profile, "
            + (
                f"quote currencies converted by {fx_label}. "
                if fx is not None
                else "no FX conversion performed. "
            )
            + "Every cell is deflated against the campaign's true trial count, "
            "not against its own parameter sweep."
        ),
    )

    ledger = ExperimentLedger(args.out / "experiments.sqlite")
    registry = HoldoutRegistry(args.out / "holdout.sqlite")
    hypothesis_store = HypothesisLedger(ledger)
    for hypothesis in hypotheses:
        if hypothesis_store.get(hypothesis.hypothesis_id) is None:
            hypothesis_store.record(
                hypothesis,
                actor_name=args.actor,
                reason=f"campaign {args.campaign_id}: pre-registering the claim",
                campaign_id=args.campaign_id,
            )

    if args.backfill_reports is not None:
        filed = backfill(ledger, args.backfill_reports, actor=args.actor)
        print(f"backfilled {filed} prior experiment(s) from {args.backfill_reports}")

    runner = CampaignRunner(
        spec,
        bars=bars_from_store(store),
        ledger=ledger,
        registry=registry,
        checkpoint=CampaignCheckpoint(args.out / "checkpoint.json"),
        report_dir=args.out,
        cache_dir=args.cache,
        fx=fx,
    )

    print(f"gate set: {gates.version} (min_trades={gates.by_name('min_trades').threshold:g})")
    print(f"seeds: {[d.strategy_id for d in seeds]}")
    print(f"hypotheses: {[h.hypothesis_id for h in hypotheses]}")

    started = time.time()
    plan = runner.plan(list(seeds))
    print()
    print(plan.describe())
    print()
    for skip in plan.skipped:
        print(f"  SKIP {skip.describe()[:200]}")
    print()
    print(
        f"TRUE TRIAL COUNT {plan.true_trial_count} "
        f"(planned {plan.planned_trial_count} "
        f"+ ledger prior {plan.prior_trial_count} "
        f"+ declared prior {plan.external_prior_trial_count})"
    )
    if plan.external_prior_trial_count:
        print(f"  declared prior trials because: {plan.external_prior_trials_reason}")
    if args.plan_only:
        store.close()
        ledger.close()
        return 0

    report = runner.run(plan)
    elapsed = time.time() - started

    print()
    print(report.summary())
    print()
    for cell in report.attempted:
        print(f"  {cell.describe()[:220]}")
    print()
    print(json.dumps(_digest(report, elapsed), indent=2, default=str))
    print()
    print(report.honest_statement())
    print()
    print(f"  -> {args.out / f'campaign_{spec.campaign_id}.json'}")

    _record_hypothesis_outcomes(hypothesis_store, report, spec, args.actor)
    store.close()
    ledger.close()
    return 0


def backfill(ledger: ExperimentLedger, directory: Path, *, actor: str) -> int:
    """File earlier ValidationReports as experiments, once each.

    Why this exists: ``research/reports/xauusd_h4/`` holds real ladder runs on
    the very bars this campaign uses, and those runs swept real parameter grids.
    They were written to a directory rather than to a ledger, so a campaign that
    ignored them would under-count its own search -- which is the exact defect
    Phase K exists to fix, in a smaller form.

    The seed DOCUMENT is attached where it can be found, so the ledger holds the
    structural fingerprint and the novelty check can see the prior work. Note
    that a report's own ``strategy_content_hash`` is the hash of the BOUND
    defaults while the document's is the template's; both are recorded, and the
    template hash is the one the ledger keys on because that is what a campaign
    proposes.
    """
    existing = {
        str(e.outputs.get("backfilled_report_hash"))
        for e in ledger.list()
        if e.outputs.get("backfilled_report_hash")
    }
    filed = 0
    for path in sorted(Path(directory).glob("*.json")):
        try:
            report = ValidationReport.load(path)
        except Exception:
            continue
        digest = report.content_hash()
        if digest in existing:
            continue
        document = None
        seed_path = SEED_DIR / f"{report.strategy_id}.json"
        if seed_path.exists():
            document = StrategyDocument.from_json(seed_path.read_text(encoding="utf-8"))
        ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.SCHEDULE,
                actor_name=actor,
                reason=(
                    f"backfilled prior ladder run from {path.name}: it swept "
                    f"{report.raw_trial_count} parameterisation(s) on dataset "
                    f"{report.dataset_version_id}, and those trials are part of the "
                    "search this campaign's results must be corrected for"
                ),
                strategy_id=report.strategy_id,
                strategy_document=document,
                dataset_version_id=report.dataset_version_id,
                validation_report=report,
                outcome=Outcome.REJECTED if not report.verdict.promotable else Outcome.PROMOTED,
                conclusion=report.binding_constraint.describe(),
                outputs={
                    "backfilled_from": str(path),
                    "backfilled_report_hash": digest,
                    "report_strategy_content_hash": report.strategy_content_hash,
                    "raw_trial_count": report.raw_trial_count,
                },
                tags=("backfill", f"gates:{report.gate_set_version}"),
            )
        )
        existing.add(digest)
        filed += 1
    return filed


def _digest(report: Any, elapsed: float) -> dict[str, Any]:
    return {
        "campaign_id": report.campaign_id,
        "gate_set": report.gate_set_version,
        "datasets": report.dataset_versions,
        "planned_trials": report.planned_trial_count,
        "prior_trials": report.prior_trial_count,
        "trial_accounting": report.lineage.get("trial_accounting", {}),
        "true_trial_count": report.true_trial_count,
        "ladder_evaluations": report.n_ladder_evaluations,
        "engine_backtests_run": report.n_engine_evaluations,
        "cells_attempted": len(report.attempted),
        "cells_skipped": len(report.skipped),
        "skips_by_kind": report.skips_by_kind(),
        "rejections_by_rung": report.rejections_by_rung(),
        "mutations_refused": len(report.rejected_mutations),
        "survivors": [c.strategy_id for c in report.survivors],
        "campaign_deflation_threshold": report.campaign_deflation_threshold,
        "deflation_variance_used": report.deflation_variance_used,
        "holdout_unconsumed": report.holdout.get("unconsumed"),
        "wall_clock_seconds": round(elapsed, 1),
    }


def _record_hypothesis_outcomes(
    store: HypothesisLedger, report: Any, spec: CampaignSpec, actor: str
) -> None:
    """File what the campaign did to each claim. A refutation is a result."""
    from fiboki.discovery.hypothesis import Evidence

    by_hypothesis: dict[str, list[Any]] = {}
    for cell in report.attempted:
        by_hypothesis.setdefault(cell.hypothesis_id, []).append(cell)

    for hypothesis in spec.hypotheses:
        cells = by_hypothesis.get(hypothesis.hypothesis_id, [])
        current = store.get(hypothesis.hypothesis_id) or hypothesis
        if not cells:
            continue
        survivors = [c for c in cells if c.survived]
        if survivors:
            store.set_status(
                current,
                HypothesisStatus.TESTING,
                reason=(
                    f"{len(survivors)} of {len(cells)} candidate(s) cleared every gate "
                    f"on {report.dataset_versions}. One cell is not evidence of an "
                    "edge; the claim stays under test."
                ),
                actor_name=actor,
                campaign_id=spec.campaign_id,
            )
            continue
        rungs = sorted({c.died_at_rung for c in cells if c.died_at_rung})
        store.set_status(
            current,
            HypothesisStatus.REFUTED,
            reason=(
                f"all {len(cells)} candidate(s) expressing this claim on "
                f"{report.dataset_versions} were rejected (died at: "
                f"{', '.join(rungs) or 'unrecorded'}). This refutes the claim ON THIS "
                "CELL, under this cost model and this gate set. It does not refute "
                "the mechanism in general."
            ),
            actor_name=actor,
            evidence=Evidence(
                direction="against",
                claim=(
                    f"campaign {spec.campaign_id}: every candidate was rejected; "
                    f"rungs reached: {', '.join(rungs) or 'unrecorded'}"
                ),
                source=f"campaign_{spec.campaign_id}.json",
                strength="moderate",
            ),
            campaign_id=spec.campaign_id,
        )


if __name__ == "__main__":
    sys.exit(main())
