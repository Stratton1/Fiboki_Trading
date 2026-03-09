"""Walk-forward analysis engine with configurable rolling train/test windows."""

from dataclasses import dataclass, field

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.strategies.registry import strategy_registry


@dataclass
class WalkForwardWindow:
    """Result for a single walk-forward window."""

    window_index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_bars: int
    test_bars: int
    train_trades: int = 0
    test_trades: int = 0
    train_score: float = 0.0
    test_score: float = 0.0
    train_net_profit: float = 0.0
    test_net_profit: float = 0.0
    train_sharpe: float = 0.0
    test_sharpe: float = 0.0
    test_metrics: dict = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    """Aggregate result of a walk-forward analysis."""

    strategy_id: str
    instrument: str
    timeframe: str
    train_window_bars: int
    test_window_bars: int
    step_bars: int
    total_windows: int
    windows: list[WalkForwardWindow] = field(default_factory=list)
    avg_test_score: float = 0.0
    avg_test_sharpe: float = 0.0
    avg_test_net_profit: float = 0.0
    total_test_trades: int = 0
    score_degradation: float = 0.0  # avg train score - avg test score
    status: str = "ok"


def run_walk_forward(
    df: pd.DataFrame,
    strategy_id: str,
    instrument: str,
    timeframe: Timeframe,
    train_window_bars: int = 2000,
    test_window_bars: int = 500,
    step_bars: int = 500,
    config: BacktestConfig | None = None,
    scoring_config: ScoringConfig | None = None,
) -> WalkForwardResult:
    """Run walk-forward analysis with rolling train/test windows.

    Slides a window across the data:
    - Train on [i, i + train_window_bars)
    - Test on [i + train_window_bars, i + train_window_bars + test_window_bars)
    - Step forward by step_bars each iteration

    Returns aggregate and per-window results.
    """
    config = config or BacktestConfig()
    scoring_config = scoring_config or ScoringConfig()

    strategy = strategy_registry.get(strategy_id)
    total_bars = len(df)

    windows: list[WalkForwardWindow] = []
    window_idx = 0
    start = 0

    while start + train_window_bars + test_window_bars <= total_bars:
        train_end = start + train_window_bars
        test_end = train_end + test_window_bars

        train_df = df.iloc[start:train_end].copy()
        test_df = df.iloc[train_end:test_end].copy()

        # Run backtest on train window
        train_score, train_trades, train_net, train_sharpe = _run_window(
            strategy, train_df, instrument, timeframe, config, scoring_config
        )

        # Run backtest on test window
        test_score, test_trades, test_net, test_sharpe, test_metrics = _run_window(
            strategy, test_df, instrument, timeframe, config, scoring_config,
            return_metrics=True,
        )

        window = WalkForwardWindow(
            window_index=window_idx,
            train_start=str(train_df.index[0]),
            train_end=str(train_df.index[-1]),
            test_start=str(test_df.index[0]),
            test_end=str(test_df.index[-1]),
            train_bars=len(train_df),
            test_bars=len(test_df),
            train_trades=train_trades,
            test_trades=test_trades,
            train_score=train_score,
            test_score=test_score,
            train_net_profit=train_net,
            test_net_profit=test_net,
            train_sharpe=train_sharpe,
            test_sharpe=test_sharpe,
            test_metrics=test_metrics,
        )
        windows.append(window)

        window_idx += 1
        start += step_bars

    # Compute aggregate
    result = WalkForwardResult(
        strategy_id=strategy_id,
        instrument=instrument,
        timeframe=timeframe.value,
        train_window_bars=train_window_bars,
        test_window_bars=test_window_bars,
        step_bars=step_bars,
        total_windows=len(windows),
        windows=windows,
    )

    if windows:
        result.avg_test_score = sum(w.test_score for w in windows) / len(windows)
        result.avg_test_sharpe = sum(w.test_sharpe for w in windows) / len(windows)
        result.avg_test_net_profit = sum(w.test_net_profit for w in windows) / len(windows)
        result.total_test_trades = sum(w.test_trades for w in windows)
        avg_train = sum(w.train_score for w in windows) / len(windows)
        result.score_degradation = round(avg_train - result.avg_test_score, 4)

    return result


def _run_window(
    strategy,
    df: pd.DataFrame,
    instrument: str,
    timeframe: Timeframe,
    config: BacktestConfig,
    scoring_config: ScoringConfig,
    return_metrics: bool = False,
) -> tuple:
    """Run a backtest on a single data window and return key results."""
    try:
        bt = Backtester(strategy, config)
        bt_result = bt.run(df, instrument, timeframe)
        metrics = compute_metrics(bt_result)
        metrics["equity_curve"] = bt_result.equity_curve
        metrics["initial_capital"] = config.initial_capital
        score = compute_composite_score(metrics, scoring_config)
        trades = metrics.get("total_trades", 0)
        net = metrics.get("total_net_profit", 0.0)
        sharpe = metrics.get("sharpe_ratio", 0.0)
        if return_metrics:
            return score, trades, net, sharpe, metrics
        return score, trades, net, sharpe
    except Exception:
        if return_metrics:
            return 0.0, 0, 0.0, 0.0, {}
        return 0.0, 0, 0.0, 0.0
