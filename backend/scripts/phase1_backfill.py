"""Phase 1 — batched, resumable full-grid backfill (review-only).

Runs EVERY strategy × EVERY instrument × EVERY timeframe through the robustness
ladder, in resumable batches. A checkpoint file records each completed combo, so
re-invocation skips finished work (dedup) — a stateless cron can chip away at the
grid across many short runs until it's complete, then writes a completion marker.

Survivors are gated by the promotion gate and recorded in the append-only ledger
as paper_candidate / research_watchlist for HUMAN review. ZERO execution: no bot,
no broker, no flag. Phase 2 must not start until phase1_complete.json exists.

Usage:
  python scripts/phase1_backfill.py --timeframes H4,H1 --batch-size 300
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fibokei.db.database import (
    get_engine,
    get_session_factory,
    init_db,
    resolve_app_db_url,
)
from fibokei.db.ledger_repository import create_agent_run, create_lifecycle_event
from fibokei.research.pipeline import (
    _factory_specs,
    append_checkpoint,
    default_configs,
    load_checkpoint,
    process_combo,
)
from fibokei.research.promotion import evaluate_promotion
from fibokei.strategies.registry import strategy_registry


def _resolve_ledger_url(spec: str) -> str:
    """'app' → application DB URL; '...://...' → URL as-is; else sqlite path."""
    if spec == "app":
        return resolve_app_db_url()
    if "://" in spec:
        return spec
    return f"sqlite:///{spec}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="all")
    ap.add_argument("--instruments", default="all")
    ap.add_argument("--timeframes", default="H4,H1")
    ap.add_argument("--ladder-timeframes", default="H1,H4",
                    help="timeframes that get the full ladder; others are "
                         "ranking-only (sub-hourly is too slow to ladder)")
    ap.add_argument("--min-trades", type=int, default=80)
    ap.add_argument("--batch-size", type=int, default=300,
                    help="max combos to process this invocation (0 = unlimited)")
    ap.add_argument("--out-dir", default="results/phase1")
    ap.add_argument("--ledger-db", default="app",
                    help="'app' = the application DB (where the API reads), a "
                         "sqlite path, or a full SQLAlchemy URL")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ckpt = out / "checkpoint.jsonl"
    marker = out / "phase1_complete.json"

    strategies = ([s["id"] for s in strategy_registry.list_available()]
                  if args.strategies == "all"
                  else [s.strip() for s in args.strategies.split(",")])
    # Factory families first (fast + the focus); slow legacy bots last.
    strategies.sort(key=lambda s: (0, s) if s.startswith("factory_") else (1, s))
    canon = Path("../data/canonical/histdata")
    instruments = (sorted(d.upper() for d in os.listdir(canon))
                   if args.instruments == "all" and canon.exists()
                   else [s.strip().upper() for s in args.instruments.split(",")])
    timeframes = [t.strip() for t in args.timeframes.split(",")]
    ladder_tfs = {t.strip() for t in args.ladder_timeframes.split(",")}

    # Full grid, deterministic order
    grid = [(s, i, tf) for s in strategies for i in instruments for tf in timeframes]
    done = load_checkpoint(ckpt)
    todo = [(s, i, tf) for (s, i, tf) in grid
            if f"{s}|{i}|{tf}" not in done]

    print(f"Phase 1 grid: {len(grid)} combos | done {len(done)} | todo {len(todo)}",
          flush=True)
    if not todo:
        marker.write_text(json.dumps({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "total_combos": len(grid)}))
        print("Phase 1 COMPLETE — marker written.", flush=True)
        return

    config, cost_config, scoring = default_configs()
    specs = _factory_specs()

    engine = get_engine(_resolve_ledger_url(args.ledger_db))
    init_db(engine)
    Session = get_session_factory(engine)
    run_id = f"phase1_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    with Session() as s:
        create_agent_run(s, {"run_id": run_id, "lane": "operator", "actor": "agent",
                             "agent_type": "phase1_backfill",
                             "summary": f"backfill batch todo={len(todo)}"})

    limit = args.batch_size if args.batch_size > 0 else len(todo)
    processed = survivors = 0
    for (sid, inst, tf) in todo[:limit]:
        run_ladder = tf in ladder_tfs
        c = process_combo(sid, inst, tf, config=config, cost_config=cost_config,
                          scoring=scoring, min_trades=args.min_trades, specs=specs,
                          run_ladder=run_ladder)
        if not c.rung_failed:  # passed all rungs → promotion gate
            d = evaluate_promotion(
                strategy_id=sid, instrument=inst, timeframe=tf,
                metrics={"total_trades": c.trades, "profit_factor": c.profit_factor,
                         "max_drawdown_pct": c.max_dd, "total_net_profit": c.net_profit},
                composite_score=c.composite, oos_robust=c.oos_robust,
                mc_profit_probability=c.mc_profit_prob,
                mc_ruin_probability=c.mc_ruin_prob)
            c.recommended_state = d.recommended_state
            survivors += 1
        # Ledger qualified combos. Ranking-only (sub-hourly) → 'backtested';
        # laddered → 'validated' (passed all rungs) or 'rejected'.
        if c.status == "ok" and c.rung_failed != "min_trades":
            if c.rung_failed == "ranking_only":
                event_type = "backtested"
            elif not c.rung_failed:
                event_type = "validated"
            else:
                event_type = "rejected"
            with Session() as s:
                create_lifecycle_event(s, {
                    "event_type": event_type,
                    "actor": "agent", "strategy_id": sid, "instrument": inst,
                    "timeframe": tf, "research_run_id": run_id,
                    "approval_status": "pending",
                    "risk_decision": c.recommended_state,
                    "reason": c.rung_failed or "passed all robustness rungs",
                    "stats_json": c.to_stats()})
        append_checkpoint(ckpt, c)
        processed += 1
        if processed % 25 == 0:
            print(f"  processed {processed}/{min(limit, len(todo))} "
                  f"survivors={survivors}", flush=True)

    remaining = len(todo) - processed
    print(f"\nBatch done: processed={processed} survivors={survivors} "
          f"remaining={remaining}", flush=True)
    if remaining <= 0:
        marker.write_text(json.dumps({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "total_combos": len(grid)}))
        print("Phase 1 COMPLETE — marker written. Phase 2 may begin.", flush=True)
    else:
        print(f"Phase 1 in progress — re-run to continue ({remaining} combos left).",
              flush=True)


if __name__ == "__main__":
    main()
