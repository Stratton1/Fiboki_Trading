"""Phase 2 — systematic expansion: one new/evolved strat at a time (review-only).

Gated: refuses to run until Phase 1 backfill is complete (phase1_complete.json
exists). Then it introduces ONE new candidate — an evolved mutation of a current
champion — and runs it across the full instrument × timeframe grid through the
robustness ladder, recording results in the append-only ledger. Loops forever,
one strat per invocation (stateless cron).

ZERO execution authority: no bot creation, no broker, no flags. Output is
paper_candidate / research_watchlist for human review only.

Usage:
  python scripts/phase2_systematic.py --timeframes H4,H1
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.db.database import (
    get_engine,
    get_session_factory,
    init_db,
    resolve_app_db_url,
)
from fibokei.db.ledger_repository import (
    create_agent_run,
    create_lifecycle_event,
    create_strategy_lineage,
)
from fibokei.research.evolution import EvoConfig, evolve
from fibokei.research.pipeline import (
    _factory_specs,
    append_checkpoint,
    default_configs,
    load_checkpoint,
    process_combo,
)
from fibokei.research.promotion import evaluate_promotion
from fibokei.strategies.factory.compiler import CompiledStrategy
from fibokei.strategies.registry import strategy_registry
from fibokei.strategies.traditional import TRADITIONAL_GEN1_SPECS


def _resolve_ledger_url(spec: str) -> str:
    if spec == "app":
        return resolve_app_db_url()
    if "://" in spec:
        return spec
    return f"sqlite:///{spec}"


def _pick_champion_seed():
    """Choose a seed family to evolve. Placeholder policy: a robust trend family.

    A later version reads the ledger for the highest-fitness survivor; for now we
    seed from a sensible default so the loop is deterministic and safe.
    """
    return next(s for s in TRADITIONAL_GEN1_SPECS if s.spec_id == "trad_macd_cross")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", default="H4,H1")
    ap.add_argument("--instruments", default="all")
    ap.add_argument("--min-trades", type=int, default=80)
    ap.add_argument("--evolve-instrument", default="EURUSD")
    ap.add_argument("--evolve-timeframe", default="H4")
    ap.add_argument("--phase1-marker", default="results/phase1/phase1_complete.json")
    ap.add_argument("--out-dir", default="results/phase2")
    ap.add_argument("--ledger-db", default="app",
                    help="'app' = application DB, a sqlite path, or a URL")
    args = ap.parse_args()

    if not Path(args.phase1_marker).exists():
        raise SystemExit(
            f"Phase 1 not complete ({args.phase1_marker} missing). "
            "Phase 2 must not start until the baseline backfill is ledgered.")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ckpt = out / "checkpoint.jsonl"

    import os
    canon = Path("../data/canonical/histdata")
    instruments = (sorted(d.upper() for d in os.listdir(canon))
                   if args.instruments == "all" and canon.exists()
                   else [s.strip().upper() for s in args.instruments.split(",")])
    timeframes = [t.strip() for t in args.timeframes.split(",")]
    config, cost_config, scoring = default_configs()

    # 1) Evolve ONE new candidate from a champion seed.
    seed = _pick_champion_seed()
    edf = load_canonical(args.evolve_instrument, args.evolve_timeframe)
    if edf is None:
        raise SystemExit("No data to evolve against")
    res = evolve(seed, edf, args.evolve_instrument,
                 Timeframe(args.evolve_timeframe),
                 EvoConfig(population=12, generations=3, elite=3, seed=42,
                           max_extra_rules=2))
    best = res.best[0].spec
    cand_id = f"factory_{best.spec_id}_v{best.version}"

    # Register it transiently so the ladder (registry-based) can resolve it.
    class _Cand(CompiledStrategy):
        def __init__(self) -> None:
            super().__init__(best)
    strategy_registry.register(_Cand)

    engine = get_engine(_resolve_ledger_url(args.ledger_db))
    init_db(engine)
    Session = get_session_factory(engine)
    run_id = f"phase2_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    with Session() as s:
        create_agent_run(s, {"run_id": run_id, "lane": "operator", "actor": "agent",
                             "agent_type": "phase2_systematic",
                             "summary": f"new candidate {cand_id} from {seed.spec_id}"})
        create_strategy_lineage(s, {"strategy_id": cand_id,
                                    "parent_strategy_id": f"factory_{seed.spec_id}_v1",
                                    "origin": "mutated", "actor": "agent",
                                    "agent_run_id": run_id})
        create_lifecycle_event(s, {"event_type": "generated", "actor": "agent",
                                   "strategy_id": cand_id, "research_run_id": run_id,
                                   "source_strategy_id": f"factory_{seed.spec_id}_v1",
                                   "reason": "phase2 evolved candidate"})

    print(f"Phase 2 candidate {cand_id} (parent {seed.spec_id}); "
          f"running across {len(instruments)} instruments x {timeframes}", flush=True)

    specs = _factory_specs()
    specs[cand_id] = best
    done = load_checkpoint(ckpt)
    survivors = 0
    for inst in instruments:
        for tf in timeframes:
            key = f"{cand_id}|{inst}|{tf}"
            if key in done:
                continue
            c = process_combo(cand_id, inst, tf, config=config,
                              cost_config=cost_config, scoring=scoring,
                              min_trades=args.min_trades, specs=specs)
            if not c.rung_failed:
                d = evaluate_promotion(
                    strategy_id=cand_id, instrument=inst, timeframe=tf,
                    metrics={"total_trades": c.trades,
                             "profit_factor": c.profit_factor,
                             "max_drawdown_pct": c.max_dd,
                             "total_net_profit": c.net_profit},
                    composite_score=c.composite, oos_robust=c.oos_robust,
                    mc_profit_probability=c.mc_profit_prob,
                    mc_ruin_probability=c.mc_ruin_prob)
                c.recommended_state = d.recommended_state
                survivors += 1
            if c.status == "ok" and c.rung_failed != "min_trades":
                with Session() as s:
                    create_lifecycle_event(s, {
                        "event_type": "validated" if not c.rung_failed else "rejected",
                        "actor": "agent", "strategy_id": cand_id, "instrument": inst,
                        "timeframe": tf, "research_run_id": run_id,
                        "approval_status": "pending",
                        "risk_decision": c.recommended_state,
                        "reason": c.rung_failed or "passed all robustness rungs",
                        "stats_json": {"composite": c.composite, "sharpe": c.sharpe}})
            append_checkpoint(ckpt, c)
    print(f"Phase 2 candidate done: survivors={survivors}", flush=True)


if __name__ == "__main__":
    main()
