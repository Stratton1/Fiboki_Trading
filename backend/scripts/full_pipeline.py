"""End-to-end research pipeline — sweep → robustness ladder → gate → ledger.

Stages (review-only; ZERO execution authority — no bot is created, nothing
touches a broker, no flag is flipped):

  A. Broad sweep: backtest every (strategy x instrument x timeframe), keep
     combos with >= min_trades, rank by composite score.
  B. Shortlist: Pareto-front + correlation/diversity prune so the ladder isn't
     run on 40 near-identical strategies.
  C. Robustness ladder (reject on first failure, cheap checks first):
       walk-forward -> held-out OOS -> Monte Carlo -> cost stress.
  D. Promotion gate (research/promotion.py) -> recommended lifecycle state.
  E. Ledger: append-only bot_lifecycle_events for every shortlisted /
     validated / rejected / candidate decision (approval_status='pending').
  F. Report: CSV + JSON + markdown of paper_candidate / research_watchlist.

Nothing here promotes to paper execution — survivors are recorded for HUMAN
review only. The operator-only kill switch and live gating are untouched.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.db.database import (
    get_engine,
    get_session_factory,
    init_db,
    resolve_app_db_url,
)
from fibokei.db.ledger_repository import create_agent_run, create_lifecycle_event
from fibokei.research.monte_carlo import run_monte_carlo
from fibokei.research.oos import run_oos_test
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.research.walk_forward import run_walk_forward
from fibokei.strategies.registry import classify_strategy, strategy_registry


def _resolve_ledger_url(spec: str) -> str:
    if spec == "app":
        return resolve_app_db_url()
    if "://" in spec:
        return spec
    return f"sqlite:///{spec}"


@dataclass
class Combo:
    strategy_id: str
    tier: str
    instrument: str
    timeframe: str
    trades: int = 0
    composite: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    max_dd: float = 0.0
    net_profit: float = 0.0
    # ladder outputs
    wf_test_score: float = 0.0
    wf_pass: bool = False
    oos_score: float = 0.0
    oos_robust: bool = False
    mc_profit_prob: float = 0.0
    mc_ruin_prob: float = 1.0
    mc_pass: bool = False
    cost_net: float = 0.0
    cost_pass: bool = False
    rung_failed: str = ""
    recommended_state: str = "rejected"
    entries: list[str] = field(default_factory=list)


def _bt(strategy_id, df, instrument, tf, config, scoring):
    strat = strategy_registry.get(strategy_id)
    res = Backtester(strat, config).run(df, instrument, tf)
    m = compute_metrics(res)
    m["equity_curve"] = res.equity_curve
    m["initial_capital"] = config.initial_capital
    m["composite"] = compute_composite_score(m, scoring)
    pnls = [float(getattr(t, "pnl", 0.0)) for t in res.trades]
    entries = [t.entry_time.isoformat() for t in res.trades
               if getattr(t, "entry_time", None)]
    return m, pnls, entries


def stage_a_sweep(strategies, symbols, timeframes, min_trades, config, scoring):
    combos: list[Combo] = []
    total = len(strategies) * len(symbols) * len(timeframes)
    n = 0
    for sid in strategies:
        tier = classify_strategy(sid)
        for sym in symbols:
            for tf_str in timeframes:
                n += 1
                tf = Timeframe(tf_str)
                df = load_canonical(sym, tf_str)
                if df is None:
                    continue
                df = df.copy()
                df["instrument"], df["timeframe"] = sym, tf_str
                try:
                    m, _, entries = _bt(sid, df, sym, tf, config, scoring)
                except Exception:
                    continue
                tr = int(m.get("total_trades", 0))
                if tr < min_trades:
                    continue
                combos.append(Combo(
                    strategy_id=sid, tier=tier, instrument=sym, timeframe=tf_str,
                    trades=tr, composite=round(m["composite"], 4),
                    sharpe=round(m.get("sharpe_ratio", 0.0) or 0.0, 3),
                    profit_factor=round(m.get("profit_factor", 0.0) or 0.0, 3),
                    max_dd=round(m.get("max_drawdown_pct", 0.0), 2),
                    net_profit=round(m.get("total_net_profit", 0.0), 2),
                    entries=entries,
                ))
                if n % 200 == 0:
                    print(f"  swept {n}/{total} | qualified so far {len(combos)}",
                          flush=True)
    return combos


def _trade_overlap(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / min(len(sa), len(sb))


def diversity_prune(combos: list[Combo], cap: int, overlap_thresh: float = 0.7):
    """Keep best-composite combos, dropping near-duplicates by trade overlap."""
    ranked = sorted(combos, key=lambda c: c.composite, reverse=True)
    kept: list[Combo] = []
    for c in ranked:
        dup = any(c.instrument == k.instrument and c.timeframe == k.timeframe
                  and _trade_overlap(c.entries, k.entries) >= overlap_thresh
                  for k in kept)
        if not dup:
            kept.append(c)
        if len(kept) >= cap:
            break
    return kept


def pareto_front(combos: list[Combo]) -> list[Combo]:
    """Non-dominated set over (sharpe↑, -max_dd↑, oos_score↑, trades↑)."""
    def dominates(x: Combo, y: Combo) -> bool:
        ge = (x.sharpe >= y.sharpe and -x.max_dd >= -y.max_dd
              and x.oos_score >= y.oos_score and x.trades >= y.trades)
        gt = (x.sharpe > y.sharpe or -x.max_dd > -y.max_dd
              or x.oos_score > y.oos_score or x.trades > y.trades)
        return ge and gt
    return [c for c in combos if not any(dominates(o, c) for o in combos if o is not c)]


def run_ladder(c: Combo, config, scoring, cost_config) -> Combo:
    tf = Timeframe(c.timeframe)
    df = load_canonical(c.instrument, c.timeframe)
    if df is None:
        c.rung_failed = "no_data"
        return c
    df = df.copy()
    df["instrument"], df["timeframe"] = c.instrument, c.timeframe
    n = len(df)

    # Rung 1: walk-forward (windows scaled to data depth)
    train = max(2000, n // 12)
    test = max(500, train // 4)
    wf = run_walk_forward(df, c.strategy_id, c.instrument, tf,
                          train_window_bars=train, test_window_bars=test,
                          step_bars=test, config=config, scoring_config=scoring)
    c.wf_test_score = round(wf.avg_test_score, 4)
    c.wf_pass = wf.avg_test_score >= 0.15 and wf.total_test_trades >= 30
    if not c.wf_pass:
        c.rung_failed = "walk_forward"
        return c

    # Rung 2: held-out OOS (70/30)
    oos = run_oos_test(df, c.strategy_id, c.instrument, tf, split_ratio=0.7,
                       config=config, scoring_config=scoring)
    c.oos_score = round(oos.oos_score, 4)
    c.oos_robust = oos.robust
    if not oos.robust:
        c.rung_failed = "oos"
        return c

    # Rung 3: Monte Carlo on trade pnls
    _, pnls, _ = _bt(c.strategy_id, df, c.instrument, tf, config, scoring)
    mc = run_monte_carlo(pnls, c.strategy_id, c.instrument, c.timeframe,
                         initial_capital=config.initial_capital)
    c.mc_profit_prob = mc.profit_probability
    c.mc_ruin_prob = mc.ruin_probability
    c.mc_pass = mc.robust and mc.ruin_probability <= 0.05
    if not c.mc_pass:
        c.rung_failed = "monte_carlo"
        return c

    # Rung 4: cost stress (spread + slippage)
    m_cost, _, _ = _bt(c.strategy_id, df, c.instrument, tf, cost_config, scoring)
    c.cost_net = round(m_cost.get("total_net_profit", 0.0), 2)
    c.cost_pass = c.cost_net > 0 and (m_cost.get("profit_factor", 0.0) or 0.0) > 1.0
    if not c.cost_pass:
        c.rung_failed = "cost_stress"
        return c

    c.rung_failed = ""
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="all")
    ap.add_argument("--instruments", default="all")
    ap.add_argument("--timeframes", default="H4")
    ap.add_argument("--min-trades", type=int, default=80)
    ap.add_argument("--ladder-cap", type=int, default=40)
    ap.add_argument("--out-dir", default="results/pipeline")
    ap.add_argument("--ledger-db", default="results/pipeline/ledger.db")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    if args.strategies == "all":
        strategies = [s["id"] for s in strategy_registry.list_available()]
    else:
        strategies = [s.strip() for s in args.strategies.split(",")]
    import os
    canon = Path("../data/canonical/histdata")
    all_syms = sorted(d.upper() for d in os.listdir(canon)) if canon.exists() else []
    instruments = all_syms if args.instruments == "all" \
        else [s.strip().upper() for s in args.instruments.split(",")]
    timeframes = [t.strip() for t in args.timeframes.split(",")]

    config = BacktestConfig(initial_capital=10000.0)
    cost_config = BacktestConfig(initial_capital=10000.0, spread_points=2.0,
                                 slippage_points=1.0)
    scoring = ScoringConfig()

    # Ledger
    Path(args.ledger_db).parent.mkdir(parents=True, exist_ok=True)
    engine = get_engine(_resolve_ledger_url(args.ledger_db))
    init_db(engine)
    Session = get_session_factory(engine)
    run_id = f"pipeline_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    with Session() as s:
        create_agent_run(s, {"run_id": run_id, "lane": "operator", "actor": "agent",
                             "agent_type": "full_pipeline",
                             "summary": f"sweep {len(strategies)}x{len(instruments)}"
                             f"x{timeframes} min_trades={args.min_trades}"})

    print(f"[{run_id}] sweep: {len(strategies)} strategies x {len(instruments)} "
          f"instruments x {timeframes} (min_trades={args.min_trades})", flush=True)

    combos = stage_a_sweep(strategies, instruments, timeframes,
                           args.min_trades, config, scoring)
    print(f"Stage A: {len(combos)} qualified combos", flush=True)
    _write_sweep_csv(out / "sweep.csv", combos)

    shortlist = diversity_prune(combos, args.ladder_cap)
    print(f"Stage B: {len(shortlist)} shortlisted (diversity-pruned)", flush=True)

    survivors: list[Combo] = []
    rung_counts = {"walk_forward": 0, "oos": 0, "monte_carlo": 0,
                   "cost_stress": 0, "passed": 0}
    for i, c in enumerate(shortlist, 1):
        run_ladder(c, config, scoring, cost_config)
        with Session() as s:
            create_lifecycle_event(s, {
                "event_type": "validated" if not c.rung_failed else "rejected",
                "actor": "agent", "strategy_id": c.strategy_id,
                "instrument": c.instrument, "timeframe": c.timeframe,
                "research_run_id": run_id, "approval_status": "pending",
                "reason": c.rung_failed or "passed all robustness rungs",
                "stats_json": {
                    "composite": c.composite, "sharpe": c.sharpe,
                    "profit_factor": c.profit_factor, "max_dd": c.max_dd,
                    "net_profit": c.net_profit, "trades": c.trades,
                    "wf_test_score": c.wf_test_score, "oos_score": c.oos_score,
                    "oos_robust": c.oos_robust, "mc_profit_prob": c.mc_profit_prob,
                    "mc_ruin_prob": c.mc_ruin_prob, "cost_net": c.cost_net}})
        if c.rung_failed:
            rung_counts[c.rung_failed] = rung_counts.get(c.rung_failed, 0) + 1
        else:
            rung_counts["passed"] += 1
            survivors.append(c)
        print(f"  ladder {i}/{len(shortlist)} {c.strategy_id} {c.instrument} "
              f"{c.timeframe}: {'PASS' if not c.rung_failed else 'fail@'+c.rung_failed}",
              flush=True)

    # Pareto + promotion gate on survivors
    front = pareto_front(survivors) if survivors else []
    from fibokei.research.promotion import evaluate_promotion
    promoted = []
    for c in front:
        d = evaluate_promotion(
            strategy_id=c.strategy_id, instrument=c.instrument, timeframe=c.timeframe,
            metrics={"total_trades": c.trades, "profit_factor": c.profit_factor,
                     "max_drawdown_pct": c.max_dd, "total_net_profit": c.net_profit},
            composite_score=c.composite, oos_robust=c.oos_robust,
            mc_profit_probability=c.mc_profit_prob,
            mc_ruin_probability=c.mc_ruin_prob)
        c.recommended_state = d.recommended_state
        with Session() as s:
            create_lifecycle_event(s, {
                "event_type": "shortlisted", "actor": "agent",
                "strategy_id": c.strategy_id, "instrument": c.instrument,
                "timeframe": c.timeframe, "research_run_id": run_id,
                "approval_status": "pending", "risk_decision": d.recommended_state,
                "reason": "pareto front; " + "; ".join(d.notes[:3])})
        promoted.append(c)

    summary = {
        "run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"strategies": len(strategies), "instruments": len(instruments),
                  "timeframes": timeframes, "min_trades": args.min_trades},
        "qualified": len(combos), "shortlisted": len(shortlist),
        "ladder_funnel": rung_counts, "pareto_front": len(front),
        "paper_candidates": sum(1 for c in promoted
                                if c.recommended_state == "paper_candidate"),
        "research_watchlist": sum(1 for c in promoted
                                  if c.recommended_state == "research_watchlist"),
        "survivors": [asdict(c) for c in sorted(
            promoted, key=lambda x: x.sharpe, reverse=True)],
    }
    for c in summary["survivors"]:
        c.pop("entries", None)
    (out / "pipeline_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDONE. qualified={len(combos)} shortlisted={len(shortlist)} "
          f"funnel={rung_counts} pareto={len(front)} "
          f"paper_candidates={summary['paper_candidates']} "
          f"watchlist={summary['research_watchlist']}", flush=True)
    print(f"Ledger: {args.ledger_db} | report: {out}/pipeline_summary.json", flush=True)


def _write_sweep_csv(path: Path, combos: list[Combo]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["strategy_id", "tier", "instrument", "timeframe", "trades",
                    "composite", "sharpe", "profit_factor", "max_dd", "net_profit"])
        for c in sorted(combos, key=lambda x: x.composite, reverse=True):
            w.writerow([c.strategy_id, c.tier, c.instrument, c.timeframe, c.trades,
                        c.composite, c.sharpe, c.profit_factor, c.max_dd, c.net_profit])


if __name__ == "__main__":
    main()
