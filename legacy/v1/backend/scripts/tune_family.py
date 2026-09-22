"""Tune a Strategy Factory family's parameters on one instrument/timeframe.

Sweeps entry-param + stop/target combinations, ranks by composite score, shows
the baseline vs the best tweaks, and runs an out-of-sample check on the best
variant as an overfit guard. Tuning overfits if trusted blindly — a tweak only
matters if it also holds out-of-sample.

Usage:
    python scripts/tune_family.py --strategy factory_trad_rsi_meanrev_v1 \
        --instrument EURUSD --timeframe H4
"""

from __future__ import annotations

import argparse

from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.research.oos import run_oos_test
from fibokei.research.spec_tuning import mutate_spec, tune_spec
from fibokei.strategies.traditional import HYBRID_GEN1_SPECS, TRADITIONAL_GEN1_SPECS

ALL = {f"factory_{s.spec_id}_v{s.version}": s
       for s in (TRADITIONAL_GEN1_SPECS + HYBRID_GEN1_SPECS)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--instrument", required=True)
    ap.add_argument("--timeframe", default="H4")
    ap.add_argument("--max-variants", type=int, default=36)
    args = ap.parse_args()

    spec = ALL.get(args.strategy)
    if spec is None:
        raise SystemExit(f"Unknown factory strategy {args.strategy}")
    tf = Timeframe(args.timeframe)

    df = load_canonical(args.instrument, args.timeframe)
    if df is None:
        raise SystemExit(f"No data for {args.instrument} {args.timeframe}")
    df = df.copy()
    df["instrument"] = args.instrument
    df["timeframe"] = args.timeframe

    results = tune_spec(spec, df, args.instrument, tf, max_variants=args.max_variants)
    base = next(r for r in results if r.is_baseline)
    ok = [r for r in results if r.status == "ok"]

    span = f"{str(df.index.min())[:10]}..{str(df.index.max())[:10]} ({len(df)} bars)"
    print(f"\n=== {args.strategy} on {args.instrument} {args.timeframe} | {span} ===")
    print(f"variants tested: {len(ok)} (incl. baseline)")
    print(f"\nBASELINE: score={base.composite_score:.3f} trades={base.total_trades} "
          f"PF={base.profit_factor:.2f} DD={base.max_drawdown_pct:.1f}% "
          f"entry={base.entry_params} stop={base.stop_multiple} tgt={base.target_multiple}")

    print("\nTOP 5 variants by composite score:")
    for r in ok[:5]:
        tag = " (BASELINE)" if r.is_baseline else ""
        delta = r.composite_score - base.composite_score
        print(f"  score={r.composite_score:.3f} ({delta:+.3f}) trades={r.total_trades:<4} "
              f"PF={r.profit_factor:.2f} DD={r.max_drawdown_pct:.1f}% "
              f"entry={r.entry_params} s={r.stop_multiple} t={r.target_multiple}{tag}")

    best = ok[0]
    if best.is_baseline:
        print("\nNo tweak beat the baseline.")
        return

    # Overfit guard: OOS check on the best variant.
    child = mutate_spec(spec, entry_overrides=best.entry_params or None,
                        stop_multiple=best.stop_multiple,
                        target_multiple=best.target_multiple, label="best")
    # Register transiently so OOS (which uses the registry) can resolve it.
    from fibokei.strategies.factory.compiler import CompiledStrategy
    from fibokei.strategies.registry import strategy_registry

    class _Tuned(CompiledStrategy):
        def __init__(self) -> None:
            super().__init__(child)
    strategy_registry.register(_Tuned)

    oos = run_oos_test(df, child_strategy_id(child), args.instrument, tf, split_ratio=0.7)
    print(f"\nOOS check on best variant (70/30 split): IS score={oos.is_score:.3f} "
          f"OOS score={oos.oos_score:.3f} degradation={oos.score_degradation:+.3f} "
          f"robust={oos.robust} (OOS trades={oos.oos_trades})")
    verdict = ("KEEP — beats baseline and holds OOS"
               if oos.robust and best.composite_score > base.composite_score
               else "TREAT WITH CAUTION — likely in-sample overfit")
    print(f"VERDICT: {verdict}")


def child_strategy_id(child) -> str:
    return f"factory_{child.spec_id}_v{child.version}"


if __name__ == "__main__":
    main()
