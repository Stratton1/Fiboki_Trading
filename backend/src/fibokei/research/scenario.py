"""Scenario Simulator — runs multiple strategy/instrument/timeframe combos
on the same historical period with shared capital and portfolio-level risk."""

import logging
from dataclasses import dataclass, field

from fibokei.backtester.backtester import Backtester
from fibokei.backtester.config import BacktestConfig
from fibokei.core.models import Timeframe
from fibokei.data.loading import load_canonical
from fibokei.research.metrics import compute_metrics
from fibokei.strategies import strategy_registry

logger = logging.getLogger(__name__)


@dataclass
class ComboSpec:
    strategy_id: str
    instrument: str
    timeframe: str
    risk_pct: float = 1.0


@dataclass
class ScenarioResult:
    """Aggregated scenario simulation result."""

    combos: list[dict] = field(default_factory=list)
    per_bot: list[dict] = field(default_factory=list)
    aggregate_equity: list[float] = field(default_factory=list)
    total_trades: int = 0
    aggregate_pnl: float = 0.0
    aggregate_sharpe: float | None = None
    aggregate_max_dd: float | None = None
    aggregate_win_rate: float | None = None


def run_scenario(
    combos: list[ComboSpec],
    capital: float = 10000.0,
    progress_callback=None,
) -> ScenarioResult:
    """Run all combos independently and aggregate results.

    This is a simplified portfolio simulation: each combo gets its own
    isolated backtest, and results are combined at the portfolio level.
    A full correlated simulation would interleave bars — future enhancement.
    """
    result = ScenarioResult()
    result.combos = [
        {"strategy_id": c.strategy_id, "instrument": c.instrument, "timeframe": c.timeframe, "risk_pct": c.risk_pct}
        for c in combos
    ]

    per_capital = capital / len(combos) if combos else capital
    equity_curves: list[list[float]] = []

    for idx, combo in enumerate(combos):
        if progress_callback:
            pct = int(5 + (idx / len(combos)) * 85)
            progress_callback(pct)

        try:
            strategy = strategy_registry.get(combo.strategy_id)
            tf_enum = Timeframe(combo.timeframe.upper())
            df = load_canonical(combo.instrument, tf_enum.value)
            if df is None:
                logger.warning("No data for %s/%s — skipping", combo.instrument, combo.timeframe)
                result.per_bot.append({
                    "strategy_id": combo.strategy_id,
                    "instrument": combo.instrument,
                    "timeframe": combo.timeframe,
                    "error": f"No data for {combo.instrument}/{combo.timeframe}",
                })
                continue

            config = BacktestConfig()
            config.initial_capital = per_capital
            backtester = Backtester(strategy, config)
            bt_result = backtester.run(df, combo.instrument, tf_enum)
            metrics = compute_metrics(bt_result)

            net_profit = float(metrics.get("total_net_profit", 0.0))
            bot_result = {
                "strategy_id": combo.strategy_id,
                "instrument": combo.instrument,
                "timeframe": combo.timeframe,
                "total_trades": bt_result.total_trades,
                "net_profit": net_profit,
                "win_rate": float(metrics.get("win_rate", 0.0)),
                "sharpe_ratio": metrics.get("sharpe_ratio"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "equity_curve": [float(v) for v in bt_result.equity_curve],
            }
            result.per_bot.append(bot_result)
            result.total_trades += bt_result.total_trades
            result.aggregate_pnl += net_profit
            equity_curves.append(bt_result.equity_curve)

        except Exception as e:
            logger.error("Scenario combo %s/%s/%s failed: %s", combo.strategy_id, combo.instrument, combo.timeframe, e)
            result.per_bot.append({
                "strategy_id": combo.strategy_id,
                "instrument": combo.instrument,
                "timeframe": combo.timeframe,
                "error": str(e),
            })

    # Determine if this is a mixed-timeframe scenario
    successful_timeframes = {
        b["timeframe"] for b in result.per_bot if "error" not in b
    }
    is_mixed_timeframe = len(successful_timeframes) > 1

    # Aggregate equity curve: sum across bots, aligned by index.
    # NOTE: When timeframes differ, bar-index alignment is approximate —
    # an H4 bar and an M15 bar cover very different time periods.
    # The aggregate curve is still useful for overall shape but the
    # x-axis does not represent uniform time.
    if equity_curves:
        max_len = max(len(ec) for ec in equity_curves)
        aggregate = []
        for i in range(max_len):
            total = sum(
                ec[min(i, len(ec) - 1)] for ec in equity_curves
            )
            aggregate.append(round(total, 2))
        result.aggregate_equity = aggregate

        # Compute aggregate drawdown
        peak = aggregate[0]
        max_dd = 0.0
        for val in aggregate:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        result.aggregate_max_dd = round(max_dd, 2)

    # Compute aggregate Sharpe from combined equity returns
    if equity_curves:
        all_returns: list[float] = []
        for ec in equity_curves:
            if len(ec) < 2:
                continue
            for i in range(1, len(ec)):
                if ec[i - 1] != 0:
                    all_returns.append((ec[i] - ec[i - 1]) / ec[i - 1])
        if len(all_returns) > 1:
            import statistics
            mean_r = statistics.mean(all_returns)
            std_r = statistics.stdev(all_returns)
            result.aggregate_sharpe = round(mean_r / std_r * (252 ** 0.5) if std_r > 0 else 0.0, 4)

    # Compute aggregate win rate
    successful = [b for b in result.per_bot if "error" not in b]
    total_wins = sum(
        round((b.get("win_rate", 0) or 0) * (b.get("total_trades", 0) or 0))
        for b in successful
    )
    if result.total_trades > 0:
        result.aggregate_win_rate = round(total_wins / result.total_trades, 4)

    if progress_callback:
        progress_callback(100)

    return result
