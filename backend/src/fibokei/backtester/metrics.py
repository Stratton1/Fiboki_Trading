"""Performance metrics computation for backtest results."""

import math

import numpy as np

from fibokei.backtester.result import BacktestResult
from fibokei.core.models import Direction, Timeframe

# Approximate bars per year for each timeframe (for Sharpe annualization)
_BARS_PER_YEAR = {
    Timeframe.M1: 365 * 24 * 60,
    Timeframe.M2: 365 * 24 * 30,
    Timeframe.M5: 365 * 24 * 12,
    Timeframe.M15: 365 * 24 * 4,
    Timeframe.M30: 365 * 24 * 2,
    Timeframe.H1: 365 * 24,
    Timeframe.H4: 365 * 6,
}


def compute_metrics(result: BacktestResult) -> dict:
    """Compute all performance metrics from a BacktestResult."""
    trades = result.trades
    equity = result.equity_curve
    total_bars = result.total_bars
    periods_per_year = _BARS_PER_YEAR.get(result.timeframe, 252)

    if not trades:
        return _empty_metrics()

    # Basic trade stats
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_net_profit = sum(pnls)
    gross_profit = sum(wins)
    gross_loss = sum(losses)

    total_trades = len(trades)
    win_count = len(wins)
    loss_count = len(losses)

    win_rate = win_count / total_trades if total_trades > 0 else 0.0
    loss_rate = 1 - win_rate

    average_win = sum(wins) / win_count if win_count > 0 else 0.0
    average_loss = sum(losses) / loss_count if loss_count > 0 else 0.0

    profit_factor = (
        gross_profit / abs(gross_loss) if gross_loss != 0 else float("inf")
    )

    expectancy = total_net_profit / total_trades if total_trades > 0 else 0.0

    reward_to_risk_ratio = (
        abs(average_win / average_loss) if average_loss != 0 else float("inf")
    )

    long_trades = sum(1 for t in trades if t.direction == Direction.LONG)
    short_trades = sum(1 for t in trades if t.direction == Direction.SHORT)

    # Drawdown from equity curve
    max_drawdown, max_drawdown_pct = _compute_drawdown(equity)

    # Risk-adjusted metrics
    sharpe = _compute_sharpe(equity, periods_per_year)
    sortino = _compute_sortino(equity, periods_per_year)
    calmar = _compute_calmar(equity, max_drawdown_pct)
    recovery_factor = (
        total_net_profit / max_drawdown if max_drawdown > 0 else float("inf")
    )

    # Trade extremes
    best_trade = max(pnls)
    worst_trade = min(pnls)
    avg_duration = sum(t.bars_in_trade for t in trades) / total_trades

    # Exposure
    bars_with_position = sum(t.bars_in_trade for t in trades)
    exposure_pct = (bars_with_position / total_bars * 100) if total_bars > 0 else 0.0

    # Streaks
    consecutive_wins, consecutive_losses = _compute_streaks(pnls)

    # Monthly/yearly returns
    monthly_returns, yearly_returns = _compute_period_returns(
        equity, result.start_date
    )

    return {
        "initial_capital": result.config.initial_capital,
        "total_net_profit": total_net_profit,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "expectancy": expectancy,
        "average_win": average_win,
        "average_loss": average_loss,
        "reward_to_risk_ratio": reward_to_risk_ratio,
        "total_trades": total_trades,
        "long_trades": long_trades,
        "short_trades": short_trades,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "recovery_factor": recovery_factor,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "avg_trade_duration_bars": avg_duration,
        "exposure_pct": exposure_pct,
        "consecutive_wins": consecutive_wins,
        "consecutive_losses": consecutive_losses,
        "monthly_returns": monthly_returns,
        "yearly_returns": yearly_returns,
    }


def _empty_metrics() -> dict:
    """Return metrics dict with zero values for no-trade case."""
    return {
        "total_net_profit": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": 0.0,
        "win_rate": 0.0,
        "loss_rate": 0.0,
        "expectancy": 0.0,
        "average_win": 0.0,
        "average_loss": 0.0,
        "reward_to_risk_ratio": 0.0,
        "total_trades": 0,
        "long_trades": 0,
        "short_trades": 0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "calmar_ratio": 0.0,
        "recovery_factor": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "avg_trade_duration_bars": 0.0,
        "exposure_pct": 0.0,
        "consecutive_wins": 0,
        "consecutive_losses": 0,
        "monthly_returns": {},
        "yearly_returns": {},
    }


def _compute_drawdown(equity: list[float]) -> tuple[float, float]:
    """Compute max drawdown (absolute) and max drawdown percentage."""
    if len(equity) < 2:
        return 0.0, 0.0

    peak = equity[0]
    max_dd = 0.0
    max_dd_pct = 0.0

    for val in equity:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak * 100) if peak > 0 else 0.0

    return max_dd, max_dd_pct


def _compute_sharpe(equity: list[float], periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio from equity curve."""
    if len(equity) < 3:
        return 0.0

    returns = np.diff(equity) / np.array(equity[:-1])
    if np.std(returns) < 1e-12:
        return 0.0

    return float(np.mean(returns) / np.std(returns) * math.sqrt(periods_per_year))


def _compute_sortino(equity: list[float], periods_per_year: int = 252) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    if len(equity) < 3:
        return 0.0

    returns = np.diff(equity) / np.array(equity[:-1])
    downside = returns[returns < 0]
    if len(downside) == 0 or np.std(downside) < 1e-12:
        return 0.0 if np.mean(returns) <= 0 else float("inf")

    return float(np.mean(returns) / np.std(downside) * math.sqrt(periods_per_year))


def _compute_calmar(equity: list[float], max_dd_pct: float) -> float:
    """Calmar ratio: annualized return / max drawdown %."""
    if len(equity) < 2 or max_dd_pct < 1e-10:
        return 0.0

    total_return_pct = (equity[-1] - equity[0]) / equity[0] * 100
    return total_return_pct / max_dd_pct


def _compute_streaks(pnls: list[float]) -> tuple[int, int]:
    """Count max consecutive wins and losses."""
    max_wins = 0
    max_losses = 0
    current_wins = 0
    current_losses = 0

    for pnl in pnls:
        if pnl > 0:
            current_wins += 1
            current_losses = 0
            max_wins = max(max_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_losses = max(max_losses, current_losses)

    return max_wins, max_losses


def _compute_period_returns(
    equity: list[float], start_date
) -> tuple[dict[str, float], dict[str, float]]:
    """Compute monthly and yearly returns from equity curve."""
    import pandas as pd

    if len(equity) < 2:
        return {}, {}

    # Create a date-indexed series (tz-naive to avoid period conversion warnings)
    start_naive = start_date.replace(tzinfo=None) if hasattr(start_date, 'tzinfo') else start_date
    dates = pd.date_range(start=start_naive, periods=len(equity), freq="h")
    eq_series = pd.Series(equity, index=dates)

    monthly = {}
    for period, group in eq_series.groupby(eq_series.index.to_period("M")):
        if len(group) < 2:
            continue
        ret = (group.iloc[-1] - group.iloc[0]) / group.iloc[0] * 100
        monthly[str(period)] = round(ret, 4)

    yearly = {}
    for period, group in eq_series.groupby(eq_series.index.to_period("Y")):
        if len(group) < 2:
            continue
        ret = (group.iloc[-1] - group.iloc[0]) / group.iloc[0] * 100
        yearly[str(period)] = round(ret, 4)

    return monthly, yearly
