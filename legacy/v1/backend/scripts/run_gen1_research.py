"""Strategy Factory Gen-1 research runner (Phase 7).

Runs the research matrix across the registered strategies and the available
instrument/timeframe universe, ranks by composite score, applies the
min-trades qualification filter, and writes machine-readable + human-readable
results. Deterministic: same data + same specs -> same ranking.

Usage:
    python scripts/run_gen1_research.py \
        --strategies all --instruments EURUSD,GBPUSD,... \
        --timeframes H1,H4 --min-trades 80 --out-dir results/

Data is loaded via load_canonical (canonical store first, then starter,
then fixtures), so populate data/canonical/ before running for a full sweep.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from fibokei.backtester.config import BacktestConfig
from fibokei.core.models import Timeframe
from fibokei.research.filter import apply_minimum_trade_filter
from fibokei.research.matrix import ResearchMatrix
from fibokei.strategies.registry import classify_strategy, strategy_registry

FX_MAJORS = ["EURUSD", "GBPUSD", "USDCHF", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="all")
    ap.add_argument("--instruments", default=",".join(FX_MAJORS))
    ap.add_argument("--timeframes", default="H1,H4")
    ap.add_argument("--min-trades", type=int, default=80)
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    if args.strategies == "all":
        strategy_ids = [s["id"] for s in strategy_registry.list_available()]
    else:
        strategy_ids = [s.strip() for s in args.strategies.split(",")]
    instruments = [i.strip() for i in args.instruments.split(",")]
    timeframes = [Timeframe(t.strip()) for t in args.timeframes.split(",")]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = BacktestConfig(initial_capital=args.capital)
    matrix = ResearchMatrix(strategy_ids, instruments, timeframes, config)

    total = len(strategy_ids) * len(instruments) * len(timeframes)
    print(f"Running {total} combinations "
          f"({len(strategy_ids)} strategies x {len(instruments)} instruments "
          f"x {len(timeframes)} timeframes), min_trades={args.min_trades}")

    results = matrix.run(str(out_dir))  # data_dir only used for legacy fallback

    qualified, insufficient = apply_minimum_trade_filter(results, args.min_trades)
    qids = {(r.strategy_id, r.instrument, r.timeframe) for r in qualified}

    # CSV of every combination
    csv_path = out_dir / "gen1_research_results.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "rank", "strategy_id", "tier", "instrument", "timeframe",
            "total_trades", "qualified", "composite_score", "net_profit",
            "sharpe_ratio", "profit_factor", "max_drawdown_pct", "win_rate",
            "status",
        ])
        for r in results:
            w.writerow([
                r.rank, r.strategy_id, classify_strategy(r.strategy_id),
                r.instrument, r.timeframe, r.total_trades,
                int((r.strategy_id, r.instrument, r.timeframe) in qids),
                round(r.composite_score, 4), round(r.net_profit, 2),
                round(r.sharpe_ratio, 3), round(r.profit_factor, 3),
                round(r.max_drawdown_pct, 2), round(r.win_rate, 3), r.status,
            ])

    # Qualified leaderboard (the only ranking we trust — min trades met)
    qualified_sorted = sorted(
        qualified, key=lambda r: r.composite_score, reverse=True
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "combinations": len(results),
        "strategies": len(strategy_ids),
        "instruments": instruments,
        "timeframes": [t.value for t in timeframes],
        "min_trades": args.min_trades,
        "qualified": len(qualified),
        "insufficient": len(insufficient),
        "errors": sum(1 for r in results if r.status != "ok"),
        "top_qualified": [
            {
                "rank": i + 1,
                "strategy_id": r.strategy_id,
                "tier": classify_strategy(r.strategy_id),
                "instrument": r.instrument,
                "timeframe": r.timeframe,
                "trades": r.total_trades,
                "composite_score": round(r.composite_score, 4),
                "net_profit": round(r.net_profit, 2),
                "sharpe": round(r.sharpe_ratio, 3),
                "profit_factor": round(r.profit_factor, 3),
                "max_dd_pct": round(r.max_drawdown_pct, 2),
                "win_rate": round(r.win_rate, 3),
            }
            for i, r in enumerate(qualified_sorted[:40])
        ],
    }
    (out_dir / "gen1_research_summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\nDone. {len(results)} combos | qualified={len(qualified)} "
          f"| insufficient={len(insufficient)} | errors={summary['errors']}")
    print(f"CSV: {csv_path}")
    print("Top 10 qualified by composite score:")
    for row in summary["top_qualified"][:10]:
        print(f"  {row['rank']:>2}. {row['strategy_id']:<32} {row['instrument']} "
              f"{row['timeframe']:<3} trades={row['trades']:<4} "
              f"score={row['composite_score']:.3f} PF={row['profit_factor']:.2f} "
              f"DD={row['max_dd_pct']:.1f}%")


if __name__ == "__main__":
    main()
