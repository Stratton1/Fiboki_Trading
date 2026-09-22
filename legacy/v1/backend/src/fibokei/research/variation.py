"""Parameter variation engine for generating strategy variants."""

from __future__ import annotations

import inspect
import itertools
from dataclasses import dataclass, field

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.research.sensitivity import DEFAULT_PARAM_RANGES
from fibokei.risk.engine import RiskEngine
from fibokei.strategies.registry import strategy_registry


@dataclass
class VariantResult:
    """Result of running a parameter variant."""

    strategy_id: str
    name: str
    params: dict
    total_trades: int = 0
    net_profit: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    trade_entries: list[str] = field(default_factory=list)


def get_strategy_params(strategy_id: str) -> dict[str, type]:
    """Discover configurable parameters from a strategy's __init__ signature."""
    strategy_cls = strategy_registry._strategies.get(strategy_id)
    if not strategy_cls:
        return {}
    sig = inspect.signature(strategy_cls.__init__)
    params = {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        if param.default is not inspect.Parameter.empty:
            params[name] = type(param.default)
    return params


def get_param_ranges(strategy_id: str) -> dict[str, list[float]]:
    """Get parameter ranges for a strategy, combining family defaults
    with strategy-specific constructor parameters."""
    strategy = strategy_registry.get(strategy_id)
    family = strategy.strategy_family

    # Start with family defaults
    ranges = dict(DEFAULT_PARAM_RANGES.get(family, {}))

    # Add strategy-specific constructor params if not already covered
    constructor_params = get_strategy_params(strategy_id)
    for pname, ptype in constructor_params.items():
        if pname not in ranges:
            default_val = getattr(strategy, pname, None)
            if default_val is not None and isinstance(default_val, (int, float)):
                # Generate a range around the default
                if isinstance(default_val, int):
                    step = max(1, default_val // 3)
                    ranges[pname] = [
                        float(v) for v in range(
                            max(1, default_val - step * 2),
                            default_val + step * 2 + 1,
                            step,
                        )
                    ]
                else:
                    step = max(0.1, default_val * 0.25)
                    ranges[pname] = [
                        round(default_val + step * i, 2)
                        for i in range(-2, 3)
                        if default_val + step * i > 0
                    ]

    return ranges


def generate_variants(
    strategy_id: str,
    param_overrides: dict[str, list[float]] | None = None,
    max_variants: int = 50,
) -> list[dict[str, float]]:
    """Generate parameter combinations for a strategy.

    If param_overrides is provided, use those ranges. Otherwise, use
    auto-discovered ranges from get_param_ranges().

    Returns a list of param dicts, capped at max_variants.
    """
    ranges = param_overrides or get_param_ranges(strategy_id)
    if not ranges:
        return []

    # Generate cartesian product
    param_names = list(ranges.keys())
    param_values = [ranges[name] for name in param_names]

    combos = []
    for values in itertools.product(*param_values):
        combo = dict(zip(param_names, values))
        combos.append(combo)
        if len(combos) >= max_variants:
            break

    return combos


def run_variant(
    df: pd.DataFrame,
    strategy_id: str,
    instrument: str,
    timeframe: Timeframe,
    params: dict,
) -> VariantResult:
    """Run a single parameter variant and return results."""
    strategy = strategy_registry.get(strategy_id)

    # Apply parameter overrides via setattr
    for pname, pval in params.items():
        if hasattr(strategy, pname):
            # Preserve int type if original is int
            original = getattr(strategy, pname)
            if isinstance(original, int) and isinstance(pval, float) and pval == int(pval):
                setattr(strategy, pname, int(pval))
            else:
                setattr(strategy, pname, pval)

    config = BacktestConfig(
        initial_balance=1000.0,
        risk_per_trade_pct=1.0,
    )
    bt = Backtester(strategy, config)
    result = bt.run(df, instrument, timeframe)

    metrics = compute_metrics(result.trades) if result.trades else {}

    # Collect entry times for overlap comparison
    entries = [
        t.entry_time.isoformat() if t.entry_time else ""
        for t in result.trades
    ]

    name = "_".join(f"{k}{v}" for k, v in sorted(params.items()))

    return VariantResult(
        strategy_id=strategy_id,
        name=f"{strategy_id}_{name}" if name else strategy_id,
        params=params,
        total_trades=len(result.trades),
        net_profit=float(metrics.get("net_profit", 0.0)),
        sharpe_ratio=float(metrics.get("sharpe_ratio", 0.0)),
        win_rate=float(metrics.get("win_rate", 0.0)),
        trade_entries=entries,
    )


def check_overlap(
    new_entries: list[str],
    existing_entries_list: list[list[str]],
    threshold: float = 0.80,
) -> tuple[bool, float]:
    """Check if a new variant's trades overlap too much with existing variants.

    Returns (is_duplicate, max_overlap).
    """
    if not new_entries or not existing_entries_list:
        return False, 0.0

    max_overlap = 0.0
    for existing in existing_entries_list:
        overlap = RiskEngine.compute_trade_overlap(
            [(e, "") for e in new_entries],
            [(e, "") for e in existing],
        )
        max_overlap = max(max_overlap, overlap)

    return max_overlap >= threshold, max_overlap
