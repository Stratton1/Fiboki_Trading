"""Out-of-sample testing with configurable train/test split."""

from dataclasses import dataclass, field

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.strategies.registry import strategy_registry


@dataclass
class OOSSplitResult:
    """Result of an in-sample vs out-of-sample comparison."""

    strategy_id: str
    instrument: str
    timeframe: str
    split_ratio: float  # e.g. 0.7 = 70% train
    split_bar_index: int
    in_sample_bars: int
    out_of_sample_bars: int
    in_sample_start: str = ""
    in_sample_end: str = ""
    out_of_sample_start: str = ""
    out_of_sample_end: str = ""
    # In-sample metrics
    is_trades: int = 0
    is_net_profit: float = 0.0
    is_sharpe: float = 0.0
    is_win_rate: float = 0.0
    is_profit_factor: float = 0.0
    is_max_drawdown_pct: float = 0.0
    is_score: float = 0.0
    is_metrics: dict = field(default_factory=dict)
    # Out-of-sample metrics
    oos_trades: int = 0
    oos_net_profit: float = 0.0
    oos_sharpe: float = 0.0
    oos_win_rate: float = 0.0
    oos_profit_factor: float = 0.0
    oos_max_drawdown_pct: float = 0.0
    oos_score: float = 0.0
    oos_metrics: dict = field(default_factory=dict)
    # Comparison
    score_degradation: float = 0.0  # IS score - OOS score
    sharpe_degradation: float = 0.0
    robust: bool = False  # OOS score >= 50% of IS score
    status: str = "ok"


def run_oos_test(
    df: pd.DataFrame,
    strategy_id: str,
    instrument: str,
    timeframe: Timeframe,
    split_ratio: float = 0.7,
    config: BacktestConfig | None = None,
    scoring_config: ScoringConfig | None = None,
) -> OOSSplitResult:
    """Run out-of-sample test by splitting data at split_ratio.

    split_ratio=0.7 means 70% in-sample, 30% out-of-sample.
    """
    config = config or BacktestConfig()
    scoring_config = scoring_config or ScoringConfig()

    strategy = strategy_registry.get(strategy_id)
    total_bars = len(df)
    split_idx = int(total_bars * split_ratio)

    is_df = df.iloc[:split_idx].copy()
    oos_df = df.iloc[split_idx:].copy()

    result = OOSSplitResult(
        strategy_id=strategy_id,
        instrument=instrument,
        timeframe=timeframe.value,
        split_ratio=split_ratio,
        split_bar_index=split_idx,
        in_sample_bars=len(is_df),
        out_of_sample_bars=len(oos_df),
    )

    if len(is_df) > 0:
        result.in_sample_start = str(is_df.index[0])
        result.in_sample_end = str(is_df.index[-1])
    if len(oos_df) > 0:
        result.out_of_sample_start = str(oos_df.index[0])
        result.out_of_sample_end = str(oos_df.index[-1])

    # Run in-sample
    try:
        is_bt = Backtester(strategy, config)
        is_bt_result = is_bt.run(is_df, instrument, timeframe)
        is_metrics = compute_metrics(is_bt_result)
        is_metrics["equity_curve"] = is_bt_result.equity_curve
        is_metrics["initial_capital"] = config.initial_capital
        result.is_trades = is_metrics.get("total_trades", 0)
        result.is_net_profit = is_metrics.get("total_net_profit", 0.0)
        result.is_sharpe = is_metrics.get("sharpe_ratio", 0.0) or 0.0
        result.is_win_rate = is_metrics.get("win_rate", 0.0)
        result.is_profit_factor = is_metrics.get("profit_factor", 0.0) or 0.0
        result.is_max_drawdown_pct = is_metrics.get("max_drawdown_pct", 0.0)
        result.is_score = compute_composite_score(is_metrics, scoring_config)
        result.is_metrics = is_metrics
    except Exception as e:
        result.status = f"error (in-sample): {e}"
        return result

    # Run out-of-sample
    try:
        oos_bt = Backtester(strategy, config)
        oos_bt_result = oos_bt.run(oos_df, instrument, timeframe)
        oos_metrics = compute_metrics(oos_bt_result)
        oos_metrics["equity_curve"] = oos_bt_result.equity_curve
        oos_metrics["initial_capital"] = config.initial_capital
        result.oos_trades = oos_metrics.get("total_trades", 0)
        result.oos_net_profit = oos_metrics.get("total_net_profit", 0.0)
        result.oos_sharpe = oos_metrics.get("sharpe_ratio", 0.0) or 0.0
        result.oos_win_rate = oos_metrics.get("win_rate", 0.0)
        result.oos_profit_factor = oos_metrics.get("profit_factor", 0.0) or 0.0
        result.oos_max_drawdown_pct = oos_metrics.get("max_drawdown_pct", 0.0)
        result.oos_score = compute_composite_score(oos_metrics, scoring_config)
        result.oos_metrics = oos_metrics
    except Exception as e:
        result.status = f"error (out-of-sample): {e}"
        return result

    # Comparison
    result.score_degradation = round(result.is_score - result.oos_score, 4)
    result.sharpe_degradation = round(result.is_sharpe - result.oos_sharpe, 4)
    result.robust = result.oos_score >= (result.is_score * 0.5) if result.is_score > 0 else False

    return result
