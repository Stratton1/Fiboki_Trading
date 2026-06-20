"""Evolve a Strategy Factory family on one instrument/timeframe.

Runs the genetic search (param + structural mutation + crossover) from a seed
spec and prints the best evolved candidates ranked by overfit-aware fitness
(min of in-sample and out-of-sample composite score). Shows how each winner
grew structurally (traditional → hybrid → triple).

Usage:
    python scripts/evolve_family.py --seed factory_trad_sma_crossover_v1 \
        --instrument EURUSD --timeframe H4 --generations 5 --population 16
"""

from __future__ import annotations

import argparse

from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.research.evolution import EvoConfig, evolve
from fibokei.strategies.traditional import (
    HYBRID_GEN1_SPECS,
    TRADITIONAL_GEN1_SPECS,
    TRIPLE_HYBRID_GEN1_SPECS,
)

ALL = {f"factory_{s.spec_id}_v{s.version}": s for s in (
    TRADITIONAL_GEN1_SPECS + HYBRID_GEN1_SPECS + TRIPLE_HYBRID_GEN1_SPECS)}


def _shape(spec) -> str:
    extra = len(spec.confirmation_rules) + len(spec.filters)
    kind = {0: "traditional", 1: "hybrid", 2: "triple"}.get(extra, f"{extra+1}-ind")
    prims = [r.primitive for r in spec.entry_rules]
    prims += [r.primitive for r in spec.confirmation_rules + spec.filters]
    return f"{kind}: {' + '.join(prims)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--instrument", required=True)
    ap.add_argument("--timeframe", default="H4")
    ap.add_argument("--generations", type=int, default=5)
    ap.add_argument("--population", type=int, default=16)
    ap.add_argument("--max-extra", type=int, default=2)  # 2 = allow triple
    args = ap.parse_args()

    seed = ALL.get(args.seed)
    if seed is None:
        raise SystemExit(f"Unknown seed {args.seed}")
    tf = Timeframe(args.timeframe)
    df = load_canonical(args.instrument, args.timeframe)
    if df is None:
        raise SystemExit(f"No data for {args.instrument} {args.timeframe}")

    cfg = EvoConfig(population=args.population, generations=args.generations,
                    elite=4, seed=42, max_extra_rules=args.max_extra)
    res = evolve(seed, df, args.instrument, tf, cfg)

    span = f"{str(df.index.min())[:10]}..{str(df.index.max())[:10]} ({len(df)} bars)"
    print(f"\n=== Evolve {args.seed} on {args.instrument} {args.timeframe} | {span} ===")
    print(f"generations={res.generations_run} evaluated={res.evaluated} genomes")
    print(f"seed shape: {_shape(seed)}")
    print("\nBest evolved candidates (overfit-aware fitness = min(IS,OOS)*trade_gate):")
    for i, g in enumerate(res.best[:8], 1):
        print(f"  {i}. fitness={g.fitness:.3f} IS={g.is_score:.3f} OOS={g.oos_score:.3f} "
              f"trades={g.trades} PF={g.profit_factor:.2f} DD={g.max_drawdown_pct:.1f}%")
        print(f"     {_shape(g.spec)}")
        print(f"     stop={g.spec.stop.multiple} target={g.spec.target.multiple} "
              f"method={g.spec.generation_method}")


if __name__ == "__main__":
    main()
