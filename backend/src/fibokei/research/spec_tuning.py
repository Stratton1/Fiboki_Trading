"""Parameter tuning for Strategy Factory specs (declarative families).

The legacy ``variation.py`` / ``sensitivity.py`` tune by ``setattr`` on a
strategy *instance* — which works for the hand-coded bots but is a no-op for
factory families, whose parameters live inside the immutable ``StrategySpec``
(``RuleSpec.params`` + ``StopSpec`` / ``TargetSpec``). A compiled strategy reads
its params from the spec, not from attributes.

So factory tuning is **spec-level**: clone the spec, override the entry params
and/or stop/target multiples, recompile. Because the factory builds indicators
from each rule's params, changing a param (e.g. EMA fast 12 → 8) automatically
builds the right indicator — no extra wiring.

Tuning is parameter optimisation, which overfits if trusted blindly. This module
only *reports* scores per variant; promotion still requires OOS + Monte Carlo
(see ``promotion.py``). Use the OOS retention of the best variant as an
overfit guard before believing a tweak.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.factory.spec import StrategySpec

# Sensible per-primitive parameter grids for the primary entry trigger.
# Keys are primitive names; values map param → candidate values.
PRIMITIVE_PARAM_GRID: dict[str, dict[str, list]] = {
    "ema_cross": {"fast": [8, 12, 16], "slow": [21, 26, 34]},
    "sma_cross": {"fast": [5, 10, 15], "slow": [20, 30, 50]},
    "price_vs_ema": {"period": [20, 50, 100]},
    "price_vs_sma": {"period": [20, 50, 100]},
    "rsi_threshold": {"oversold": [20, 30, 35], "overbought": [65, 70, 80]},
    "stoch_threshold": {"oversold": [15, 20, 25], "overbought": [75, 80, 85]},
    "bb_revert": {"num_std": [1.5, 2.0, 2.5]},
    "bb_breakout": {"num_std": [1.5, 2.0, 2.5]},
    "donchian_breakout": {"period": [10, 20, 40]},
    "cci_threshold": {"level": [80, 100, 120]},
    "roc_threshold": {"level": [1.0, 2.0, 3.0]},
    "adx_filter": {"threshold": [20, 25, 30]},
    "atr_breakout": {"mult": [0.5, 1.0, 1.5]},
    "macd_zero": {},  # structural — vary stop/target instead
    "macd_cross": {},
    "psar_flip": {},
}

# Default stop / target multiples to sweep alongside entry params.
DEFAULT_STOP_MULTIPLES = [1.5, 2.0, 3.0]
DEFAULT_TARGET_MULTIPLES = [1.5, 2.0, 3.0]


@dataclass
class TuneResult:
    """One tuned variant's backtest outcome."""

    label: str
    is_baseline: bool
    entry_params: dict
    stop_multiple: float
    target_multiple: float
    total_trades: int = 0
    composite_score: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    spec_hash: str = ""
    status: str = "ok"


def mutate_spec(
    base: StrategySpec,
    *,
    entry_overrides: dict | None = None,
    stop_multiple: float | None = None,
    target_multiple: float | None = None,
    label: str = "",
) -> StrategySpec:
    """Return a new spec with overridden entry params / stop / target.

    ``entry_overrides`` is merged into the FIRST entry rule's params (the primary
    trigger). The result is a child spec: ``generation_method='parameter_mutation'``,
    ``parent_spec_id`` set, and a distinct ``spec_id`` (so the compiled
    strategy_id and content_hash differ from the baseline).
    """
    data = base.model_dump(mode="python", exclude={"created_at"})
    if entry_overrides and data["entry_rules"]:
        data["entry_rules"][0]["params"] = {
            **data["entry_rules"][0].get("params", {}),
            **entry_overrides,
        }
    if stop_multiple is not None:
        data["stop"]["multiple"] = stop_multiple
    if target_multiple is not None:
        data["target"]["multiple"] = target_multiple

    data["parent_spec_id"] = base.spec_id
    data["generation_method"] = "parameter_mutation"
    suffix = label or "tuned"
    data["spec_id"] = f"{base.spec_id}__{suffix}"
    return StrategySpec(**data)


def _score_spec(
    spec: StrategySpec,
    df: pd.DataFrame,
    instrument: str,
    timeframe: Timeframe,
    config: BacktestConfig,
    scoring: ScoringConfig,
) -> dict:
    strat = compile_spec(spec)
    bt_result = Backtester(strat, config).run(df, instrument, timeframe)
    metrics = compute_metrics(bt_result)
    metrics["equity_curve"] = bt_result.equity_curve
    metrics["initial_capital"] = config.initial_capital
    metrics["composite_score"] = compute_composite_score(metrics, scoring)
    return metrics


def tune_spec(
    base: StrategySpec,
    df: pd.DataFrame,
    instrument: str,
    timeframe: Timeframe,
    *,
    entry_grid: dict[str, list] | None = None,
    stop_multiples: list[float] | None = None,
    target_multiples: list[float] | None = None,
    config: BacktestConfig | None = None,
    scoring: ScoringConfig | None = None,
    max_variants: int = 60,
) -> list[TuneResult]:
    """Sweep entry-param + stop/target combinations for one spec on one dataset.

    Returns results sorted by composite score (desc), with the baseline included
    and flagged. Deterministic: same spec + data + grid → same ordering.
    """
    config = config or BacktestConfig()
    scoring = scoring or ScoringConfig()

    if entry_grid is None:
        prim = base.entry_rules[0].primitive
        entry_grid = PRIMITIVE_PARAM_GRID.get(prim, {})
    stop_multiples = stop_multiples or DEFAULT_STOP_MULTIPLES
    target_multiples = target_multiples or DEFAULT_TARGET_MULTIPLES

    # Baseline first
    results: list[TuneResult] = []
    base_m = _score_spec(base, df, instrument, timeframe, config, scoring)
    results.append(TuneResult(
        label="baseline", is_baseline=True,
        entry_params=dict(base.entry_rules[0].params),
        stop_multiple=base.stop.multiple, target_multiple=base.target.multiple,
        total_trades=base_m.get("total_trades", 0),
        composite_score=base_m["composite_score"],
        net_profit=base_m.get("total_net_profit", 0.0),
        profit_factor=base_m.get("profit_factor", 0.0) or 0.0,
        sharpe_ratio=base_m.get("sharpe_ratio", 0.0) or 0.0,
        max_drawdown_pct=base_m.get("max_drawdown_pct", 0.0),
        win_rate=base_m.get("win_rate", 0.0),
        spec_hash=base.content_hash,
    ))

    # Build entry-param combinations
    if entry_grid:
        names = sorted(entry_grid)
        entry_combos = [dict(zip(names, vals))
                        for vals in itertools.product(*[entry_grid[n] for n in names])]
    else:
        entry_combos = [{}]

    count = 0
    for entry in entry_combos:
        for sm in stop_multiples:
            for tm in target_multiples:
                # Skip the exact baseline combo (already recorded)
                if (entry == dict(base.entry_rules[0].params)
                        and sm == base.stop.multiple
                        and tm == base.target.multiple):
                    continue
                if count >= max_variants:
                    break
                count += 1
                label = ("_".join(f"{k}{v}" for k, v in sorted(entry.items()))
                         or "base") + f"_s{sm}_t{tm}"
                try:
                    spec = mutate_spec(base, entry_overrides=entry or None,
                                       stop_multiple=sm, target_multiple=tm,
                                       label=label)
                    m = _score_spec(spec, df, instrument, timeframe, config, scoring)
                    results.append(TuneResult(
                        label=label, is_baseline=False, entry_params=entry,
                        stop_multiple=sm, target_multiple=tm,
                        total_trades=m.get("total_trades", 0),
                        composite_score=m["composite_score"],
                        net_profit=m.get("total_net_profit", 0.0),
                        profit_factor=m.get("profit_factor", 0.0) or 0.0,
                        sharpe_ratio=m.get("sharpe_ratio", 0.0) or 0.0,
                        max_drawdown_pct=m.get("max_drawdown_pct", 0.0),
                        win_rate=m.get("win_rate", 0.0),
                        spec_hash=spec.content_hash,
                    ))
                except Exception as e:  # noqa: BLE001
                    results.append(TuneResult(
                        label=label, is_baseline=False, entry_params=entry,
                        stop_multiple=sm, target_multiple=tm, status=f"error: {e}"))

    results.sort(key=lambda r: r.composite_score, reverse=True)
    return results


__all__ = [
    "PRIMITIVE_PARAM_GRID",
    "TuneResult",
    "mutate_spec",
    "tune_spec",
]
