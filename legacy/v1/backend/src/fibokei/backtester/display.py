"""Display functions for backtest results."""

from tabulate import tabulate

from fibokei.core.trades import TradeResult


def print_metrics(metrics: dict) -> None:
    """Print formatted metrics tables."""
    print()
    print("=" * 60)
    print("  BACKTEST RESULTS")
    print("=" * 60)

    # Performance Summary
    perf = [
        ["Total Net Profit", f"${metrics['total_net_profit']:.2f}"],
        ["Gross Profit", f"${metrics['gross_profit']:.2f}"],
        ["Gross Loss", f"${metrics['gross_loss']:.2f}"],
        ["Profit Factor", _fmt_ratio(metrics["profit_factor"])],
        ["Expectancy", f"${metrics['expectancy']:.2f}"],
        ["Recovery Factor", _fmt_ratio(metrics["recovery_factor"])],
    ]
    print("\n  Performance Summary")
    print("  " + "-" * 40)
    print(tabulate(perf, tablefmt="plain", colalign=("left", "right")))

    # Trade Statistics
    stats = [
        ["Total Trades", f"{metrics['total_trades']}"],
        ["Long Trades", f"{metrics['long_trades']}"],
        ["Short Trades", f"{metrics['short_trades']}"],
        ["Win Rate", f"{metrics['win_rate']:.1%}"],
        ["Average Win", f"${metrics['average_win']:.2f}"],
        ["Average Loss", f"${metrics['average_loss']:.2f}"],
        ["Reward:Risk Ratio", _fmt_ratio(metrics["reward_to_risk_ratio"])],
        ["Best Trade", f"${metrics['best_trade']:.2f}"],
        ["Worst Trade", f"${metrics['worst_trade']:.2f}"],
        ["Avg Duration (bars)", f"{metrics['avg_trade_duration_bars']:.1f}"],
        ["Exposure %", f"{metrics['exposure_pct']:.1f}%"],
    ]
    print("\n  Trade Statistics")
    print("  " + "-" * 40)
    print(tabulate(stats, tablefmt="plain", colalign=("left", "right")))

    # Risk Metrics
    risk = [
        ["Max Drawdown", f"${metrics['max_drawdown']:.2f}"],
        ["Max Drawdown %", f"{metrics['max_drawdown_pct']:.1f}%"],
        ["Sharpe Ratio", _fmt_ratio(metrics["sharpe_ratio"])],
        ["Sortino Ratio", _fmt_ratio(metrics["sortino_ratio"])],
        ["Calmar Ratio", _fmt_ratio(metrics["calmar_ratio"])],
    ]
    print("\n  Risk Metrics")
    print("  " + "-" * 40)
    print(tabulate(risk, tablefmt="plain", colalign=("left", "right")))

    # Streaks
    streaks = [
        ["Max Consecutive Wins", f"{metrics['consecutive_wins']}"],
        ["Max Consecutive Losses", f"{metrics['consecutive_losses']}"],
    ]
    print("\n  Streaks")
    print("  " + "-" * 40)
    print(tabulate(streaks, tablefmt="plain", colalign=("left", "right")))
    print()


def print_trade_list(trades: list[TradeResult], limit: int = 20) -> None:
    """Print tabulated trade list."""
    if not trades:
        print("  No trades.")
        return

    shown = trades[-limit:]
    rows = []
    for t in shown:
        rows.append([
            t.entry_time.strftime("%Y-%m-%d %H:%M"),
            t.exit_time.strftime("%Y-%m-%d %H:%M"),
            t.direction.value,
            f"{t.entry_price:.5f}",
            f"{t.exit_price:.5f}",
            f"${t.pnl:.2f}",
            t.exit_reason.value,
            f"{t.bars_in_trade}",
        ])

    headers = [
        "Entry", "Exit", "Dir", "Entry$", "Exit$", "PnL", "Reason", "Bars",
    ]
    print(f"\n  Trade List (last {len(shown)} of {len(trades)})")
    print("  " + "-" * 70)
    print(tabulate(rows, headers=headers, tablefmt="simple"))
    print()


def _fmt_ratio(value: float) -> str:
    """Format a ratio value, handling inf."""
    if value == float("inf"):
        return "inf"
    return f"{value:.4f}"
