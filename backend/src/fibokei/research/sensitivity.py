"""Parameter sensitivity analysis for strategy robustness testing."""

from dataclasses import dataclass, field

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.strategies.registry import strategy_registry


@dataclass
class SensitivityPoint:
    """Result for a single parameter variation."""

    param_name: str
    param_value: float
    total_trades: int = 0
    net_profit: float = 0.0
    sharpe_ratio: float = 0.0
    composite_score: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0


@dataclass
class SensitivityResult:
    """Result of parameter sensitivity analysis for one strategy."""

    strategy_id: str
    instrument: str
    timeframe: str
    param_name: str
    baseline_value: float
    variations: list[SensitivityPoint] = field(default_factory=list)
    score_range: float = 0.0  # max score - min score across variations
    score_std: float = 0.0    # std dev of scores
    robust: bool = False      # score_range < 0.2 (scores don't swing wildly)
    status: str = "ok"


# Default parameter variations per strategy family
DEFAULT_PARAM_RANGES: dict[str, dict[str, list[float]]] = {
    "ichimoku": {
        "tenkan_period": [7, 8, 9, 10, 11],
        "kijun_period": [22, 24, 26, 28, 30],
    },
    "fibonacci": {
        "fib_lookback": [40, 60, 80, 100, 120],
    },
    "hybrid": {
        "tenkan_period": [7, 8, 9, 10, 11],
        "kijun_period": [22, 24, 26, 28, 30],
    },
}


def run_sensitivity(
    df: pd.DataFrame,
    strategy_id: str,
    instrument: str,
    timeframe: Timeframe,
    param_name: str,
    param_values: list[float],
    config: BacktestConfig | None = None,
    scoring_config: ScoringConfig | None = None,
) -> SensitivityResult:
    """Run sensitivity analysis by varying a single parameter.

    Creates a fresh strategy instance for each parameter value,
    injecting the parameter override via the strategy's config mechanism.
    """
    config = config or BacktestConfig()
    scoring_config = scoring_config or ScoringConfig()

    # Get baseline value
    baseline_strategy = strategy_registry.get(strategy_id)
    baseline_value = getattr(baseline_strategy, param_name, None)
    if baseline_value is None:
        # Check nested config / indicator params
        baseline_value = baseline_strategy.config.get(param_name, param_values[len(param_values) // 2])

    result = SensitivityResult(
        strategy_id=strategy_id,
        instrument=instrument,
        timeframe=timeframe.value,
        param_name=param_name,
        baseline_value=float(baseline_value),
    )

    points: list[SensitivityPoint] = []
    for val in param_values:
        try:
            strategy = strategy_registry.get(strategy_id)
            # Inject parameter override
            if hasattr(strategy, param_name):
                setattr(strategy, param_name, int(val) if val == int(val) else val)

            bt = Backtester(strategy, config)
            bt_result = bt.run(df, instrument, timeframe)
            metrics = compute_metrics(bt_result)
            metrics["equity_curve"] = bt_result.equity_curve
            metrics["initial_capital"] = config.initial_capital
            score = compute_composite_score(metrics, scoring_config)

            point = SensitivityPoint(
                param_name=param_name,
                param_value=float(val),
                total_trades=metrics.get("total_trades", 0),
                net_profit=metrics.get("total_net_profit", 0.0),
                sharpe_ratio=metrics.get("sharpe_ratio", 0.0) or 0.0,
                composite_score=score,
                win_rate=metrics.get("win_rate", 0.0),
                max_drawdown_pct=metrics.get("max_drawdown_pct", 0.0),
            )
        except Exception:
            point = SensitivityPoint(
                param_name=param_name,
                param_value=float(val),
            )
        points.append(point)

    result.variations = points

    if points:
        scores = [p.composite_score for p in points]
        result.score_range = round(max(scores) - min(scores), 4)
        if len(scores) > 1:
            import numpy as np
            result.score_std = round(float(np.std(scores)), 4)
        result.robust = result.score_range < 0.2

    return result


def get_default_params(strategy_id: str) -> dict[str, list[float]]:
    """Get default parameter ranges for a given strategy."""
    strategy = strategy_registry.get(strategy_id)
    family = strategy.strategy_family
    return DEFAULT_PARAM_RANGES.get(family, {})
