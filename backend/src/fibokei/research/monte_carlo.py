"""Monte Carlo robustness checks via bootstrap resampling of trade returns."""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class MonteCarloResult:
    """Result of Monte Carlo robustness analysis."""

    strategy_id: str
    instrument: str
    timeframe: str
    num_simulations: int
    num_trades: int
    original_net_profit: float
    original_max_drawdown_pct: float
    # Distribution of simulated outcomes
    mean_net_profit: float = 0.0
    median_net_profit: float = 0.0
    std_net_profit: float = 0.0
    p5_net_profit: float = 0.0   # 5th percentile (worst reasonable case)
    p25_net_profit: float = 0.0
    p75_net_profit: float = 0.0
    p95_net_profit: float = 0.0  # 95th percentile (best reasonable case)
    mean_max_drawdown: float = 0.0
    p95_max_drawdown: float = 0.0  # worst-case drawdown at 95th percentile
    # Robustness indicators
    profit_probability: float = 0.0  # fraction of sims with net_profit > 0
    ruin_probability: float = 0.0    # fraction of sims with drawdown > 50%
    robust: bool = False  # profit_probability >= 0.7
    status: str = "ok"


def run_monte_carlo(
    trade_pnls: list[float],
    strategy_id: str,
    instrument: str,
    timeframe: str,
    initial_capital: float = 10000.0,
    num_simulations: int = 1000,
    seed: int | None = 42,
) -> MonteCarloResult:
    """Run Monte Carlo analysis by bootstrap-resampling trade P&L sequences.

    For each simulation:
    1. Resample trades with replacement (same count as original)
    2. Compute cumulative equity curve
    3. Compute net profit and max drawdown

    Returns distribution statistics and robustness indicators.
    """
    rng = np.random.default_rng(seed)
    pnls = np.array(trade_pnls, dtype=float)
    n_trades = len(pnls)

    original_net = float(np.sum(pnls))

    # Compute original max drawdown percentage
    original_equity = initial_capital + np.cumsum(pnls)
    original_peak = np.maximum.accumulate(original_equity)
    original_dd = (original_peak - original_equity) / original_peak * 100
    original_max_dd = float(np.max(original_dd)) if len(original_dd) > 0 else 0.0

    result = MonteCarloResult(
        strategy_id=strategy_id,
        instrument=instrument,
        timeframe=timeframe,
        num_simulations=num_simulations,
        num_trades=n_trades,
        original_net_profit=original_net,
        original_max_drawdown_pct=original_max_dd,
    )

    if n_trades < 2:
        result.status = "insufficient trades"
        return result

    sim_profits = np.empty(num_simulations)
    sim_drawdowns = np.empty(num_simulations)

    for i in range(num_simulations):
        # Resample trades with replacement
        sampled = rng.choice(pnls, size=n_trades, replace=True)
        equity = initial_capital + np.cumsum(sampled)
        peak = np.maximum.accumulate(equity)
        dd_pct = (peak - equity) / peak * 100

        sim_profits[i] = float(np.sum(sampled))
        sim_drawdowns[i] = float(np.max(dd_pct))

    result.mean_net_profit = round(float(np.mean(sim_profits)), 2)
    result.median_net_profit = round(float(np.median(sim_profits)), 2)
    result.std_net_profit = round(float(np.std(sim_profits)), 2)
    result.p5_net_profit = round(float(np.percentile(sim_profits, 5)), 2)
    result.p25_net_profit = round(float(np.percentile(sim_profits, 25)), 2)
    result.p75_net_profit = round(float(np.percentile(sim_profits, 75)), 2)
    result.p95_net_profit = round(float(np.percentile(sim_profits, 95)), 2)
    result.mean_max_drawdown = round(float(np.mean(sim_drawdowns)), 2)
    result.p95_max_drawdown = round(float(np.percentile(sim_drawdowns, 95)), 2)
    result.profit_probability = round(float(np.mean(sim_profits > 0)), 4)
    result.ruin_probability = round(float(np.mean(sim_drawdowns > 50)), 4)
    result.robust = result.profit_probability >= 0.7

    return result
