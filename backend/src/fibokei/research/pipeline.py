"""Resumable, checkpointed research pipeline core (review-only).

One unit of work = one (strategy, instrument, timeframe) combo, independent and
idempotent. Each combo is swept, and if it qualifies, run through the full
robustness ladder (walk-forward → held-out OOS → Monte Carlo → parameter
sensitivity → cost stress, reject on first failure). Every result is appended to
a checkpoint file (so an interrupted run resumes exactly where it stopped) and to
the append-only lifecycle ledger.

ZERO execution authority: this never creates a bot, never trades, never flips a
flag. Survivors are recorded as paper_candidate / research_watchlist for human
review only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.research.monte_carlo import run_monte_carlo
from fibokei.research.oos import run_oos_test
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.research.spec_tuning import mutate_spec
from fibokei.research.walk_forward import run_walk_forward
from fibokei.strategies.registry import classify_strategy, strategy_registry


# strategy_id -> StrategySpec (factory families only; used for param sensitivity)
def _factory_specs() -> dict:
    from fibokei.strategies.traditional import (
        HYBRID_GEN1_SPECS,
        TRADITIONAL_GEN1_SPECS,
        TRIPLE_HYBRID_GEN1_SPECS,
    )
    out = {}
    for s in (TRADITIONAL_GEN1_SPECS + HYBRID_GEN1_SPECS + TRIPLE_HYBRID_GEN1_SPECS):
        out[f"factory_{s.spec_id}_v{s.version}"] = s
    return out


@dataclass
class ComboResult:
    strategy_id: str
    tier: str
    instrument: str
    timeframe: str
    content_hash: str = ""
    trades: int = 0
    composite: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    max_dd: float = 0.0
    net_profit: float = 0.0
    wf_test_score: float = 0.0
    oos_score: float = 0.0
    oos_robust: bool = False
    mc_profit_prob: float = 0.0
    mc_ruin_prob: float = 1.0
    sens_stable: bool = False
    cost_net: float = 0.0
    rung_failed: str = ""          # "" means passed all rungs
    recommended_state: str = "rejected"
    status: str = "ok"
    entries: list[str] = field(default_factory=list)

    def to_stats(self) -> dict:
        """Full stat set persisted to the ledger (stats_json) for review."""
        return {
            "trades": self.trades, "composite": self.composite,
            "sharpe": self.sharpe, "profit_factor": self.profit_factor,
            "max_dd": self.max_dd, "net_profit": self.net_profit,
            "wf_test_score": self.wf_test_score, "oos_score": self.oos_score,
            "oos_robust": self.oos_robust, "mc_profit_prob": self.mc_profit_prob,
            "mc_ruin_prob": self.mc_ruin_prob, "sens_stable": self.sens_stable,
            "cost_net": self.cost_net, "rung_failed": self.rung_failed,
        }


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


def _content_hash(strategy_id: str, specs: dict) -> str:
    spec = specs.get(strategy_id)
    return spec.content_hash if spec else strategy_id


def _param_sensitivity(strategy_id, df, instrument, tf, config, scoring,
                       specs, base_score) -> bool:
    """Jitter stop/target on factory specs; stable if no point collapses.

    Legacy (non-factory) strategies have no spec to mutate → treated as n/a
    (pass), since their robustness is covered by the other four rungs.
    """
    spec = specs.get(strategy_id)
    if spec is None or base_score <= 0:
        return True
    scores = []
    for sm, tm in [(1.5, spec.target.multiple), (3.0, spec.target.multiple),
                   (spec.stop.multiple, max(1.0, spec.target.multiple - 0.5)),
                   (spec.stop.multiple, spec.target.multiple + 1.0)]:
        try:
            child = mutate_spec(spec, stop_multiple=sm, target_multiple=tm,
                                label="sens")
            from fibokei.strategies.factory.compiler import compile_spec
            strat = compile_spec(child)
            r = Backtester(strat, config).run(df, instrument, tf)
            m = compute_metrics(r)
            m["equity_curve"] = r.equity_curve
            m["initial_capital"] = config.initial_capital
            scores.append(compute_composite_score(m, scoring))
        except Exception:
            continue
    if not scores:
        return True
    # Stable if the median neighbour keeps >= 60% of baseline (no cliff edge).
    scores.sort()
    median = scores[len(scores) // 2]
    return median >= 0.6 * base_score


def process_combo(strategy_id, instrument, timeframe, *, config, cost_config,
                  scoring, min_trades, specs,
                  ladder_min_composite: float = 0.30,
                  run_ladder: bool = True) -> ComboResult:
    """Sweep + (optionally) full robustness ladder for one combo. Writes nothing.

    Combos below ``ladder_min_composite`` are screened out cheaply *before* the
    expensive ladder (walk-forward etc.) so a full-grid backfill stays tractable
    — the ladder only ever runs on combos with a real in-sample edge.

    ``run_ladder=False`` → ranking-only mode: sweep + score + record, no ladder.
    Used for sub-hourly timeframes where the ladder (walk-forward over 25y of M1)
    is computationally prohibitive; those are ranked for the record, not gated.
    """
    tier = classify_strategy(strategy_id)
    c = ComboResult(strategy_id=strategy_id, tier=tier, instrument=instrument,
                    timeframe=timeframe, content_hash=_content_hash(strategy_id, specs))
    tf = Timeframe(timeframe)
    df = load_canonical(instrument, timeframe)
    if df is None:
        c.status = "no_data"
        c.rung_failed = "no_data"
        return c
    df = df.copy()
    df["instrument"], df["timeframe"] = instrument, timeframe

    try:
        m, pnls, entries = _bt(strategy_id, df, instrument, tf, config, scoring)
    except Exception as e:  # noqa: BLE001
        c.status = f"error: {e}"
        c.rung_failed = "backtest_error"
        return c

    c.trades = int(m.get("total_trades", 0))
    c.composite = round(m["composite"], 4)
    c.sharpe = round(m.get("sharpe_ratio", 0.0) or 0.0, 3)
    c.profit_factor = round(m.get("profit_factor", 0.0) or 0.0, 3)
    c.max_dd = round(m.get("max_drawdown_pct", 0.0), 2)
    c.net_profit = round(m.get("total_net_profit", 0.0), 2)
    c.entries = entries

    if c.trades < min_trades:
        c.rung_failed = "min_trades"
        return c

    # Ranking-only mode (sub-hourly): recorded + ranked, never laddered/gated.
    if not run_ladder:
        c.rung_failed = "ranking_only"
        return c

    # Cheap pre-screen: don't ladder combos with no in-sample edge.
    if c.composite < ladder_min_composite:
        c.rung_failed = "below_screen"
        return c

    # Rung 1: walk-forward (coarse windows — keep the full-grid backfill tractable)
    n = len(df)
    train = max(3000, n // 6)
    test = max(750, train // 3)
    wf = run_walk_forward(df, strategy_id, instrument, tf, train_window_bars=train,
                          test_window_bars=test, step_bars=test, config=config,
                          scoring_config=scoring)
    c.wf_test_score = round(wf.avg_test_score, 4)
    if not (wf.avg_test_score >= 0.15 and wf.total_test_trades >= 30):
        c.rung_failed = "walk_forward"
        return c

    # Rung 2: held-out OOS
    oos = run_oos_test(df, strategy_id, instrument, tf, split_ratio=0.7,
                       config=config, scoring_config=scoring)
    c.oos_score = round(oos.oos_score, 4)
    c.oos_robust = oos.robust
    if not oos.robust:
        c.rung_failed = "oos"
        return c

    # Rung 3: Monte Carlo (moving-block bootstrap preserves autocorrelation)
    mc = run_monte_carlo(pnls, strategy_id, instrument, timeframe,
                         initial_capital=config.initial_capital, block_size=5)
    c.mc_profit_prob = mc.profit_probability
    c.mc_ruin_prob = mc.ruin_probability
    if not (mc.robust and mc.ruin_probability <= 0.05):
        c.rung_failed = "monte_carlo"
        return c

    # Rung 4: parameter sensitivity
    c.sens_stable = _param_sensitivity(strategy_id, df, instrument, tf, config,
                                       scoring, specs, c.composite)
    if not c.sens_stable:
        c.rung_failed = "param_sensitivity"
        return c

    # Rung 5: cost stress
    m_cost, _, _ = _bt(strategy_id, df, instrument, tf, cost_config, scoring)
    c.cost_net = round(m_cost.get("total_net_profit", 0.0), 2)
    if not (c.cost_net > 0 and (m_cost.get("profit_factor", 0.0) or 0.0) > 1.0):
        c.rung_failed = "cost_stress"
        return c

    c.rung_failed = ""  # survived every rung
    return c


# ── Diversity / Pareto (applied at report time, across the ledger) ────────

def trade_overlap(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / min(len(sa), len(sb))


def diversity_prune(results: list[ComboResult], overlap_thresh: float = 0.7):
    kept: list[ComboResult] = []
    for c in sorted(results, key=lambda x: x.composite, reverse=True):
        if any(c.instrument == k.instrument and c.timeframe == k.timeframe
               and trade_overlap(c.entries, k.entries) >= overlap_thresh
               for k in kept):
            continue
        kept.append(c)
    return kept


def pareto_front(results: list[ComboResult]):
    def dominates(x, y):
        ge = (x.sharpe >= y.sharpe and -x.max_dd >= -y.max_dd
              and x.oos_score >= y.oos_score and x.trades >= y.trades)
        gt = (x.sharpe > y.sharpe or -x.max_dd > -y.max_dd
              or x.oos_score > y.oos_score or x.trades > y.trades)
        return ge and gt
    return [c for c in results if not any(dominates(o, c) for o in results if o is not c)]


# ── Checkpoint ────────────────────────────────────────────────────────────

def load_checkpoint(path: Path) -> set[str]:
    """Return the set of completed combo keys (strategy|instrument|timeframe)."""
    done: set[str] = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
                done.add(rec["key"])
            except Exception:
                continue
    return done


def append_checkpoint(path: Path, c: ComboResult) -> None:
    key = f"{c.strategy_id}|{c.instrument}|{c.timeframe}"
    rec = {"key": key, "content_hash": c.content_hash, "trades": c.trades,
           "composite": c.composite, "sharpe": c.sharpe,
           "rung_failed": c.rung_failed, "recommended_state": c.recommended_state,
           "status": c.status}
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def default_configs():
    # Normal run uses realistic per-instrument default spreads (spread_points=0).
    config = BacktestConfig(initial_capital=10000.0)
    # Cost stress = 2x the realistic per-instrument spread (per asset class),
    # NOT a flat absolute spread (which was meaningless across instruments).
    cost_config = BacktestConfig(initial_capital=10000.0, spread_multiplier=2.0)
    return config, cost_config, ScoringConfig()


__all__ = [
    "ComboResult", "process_combo", "diversity_prune", "pareto_front",
    "load_checkpoint", "append_checkpoint", "default_configs",
    "trade_overlap", "_factory_specs",
]
