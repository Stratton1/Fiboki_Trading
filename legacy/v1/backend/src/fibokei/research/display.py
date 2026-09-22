"""Display utilities for research results."""

from tabulate import tabulate

from fibokei.research.matrix import ResearchResult


def print_leaderboard(
    results: list[ResearchResult],
    sort_by: str = "composite_score",
    limit: int = 20,
) -> None:
    """Print a formatted ranked leaderboard table."""
    if not results:
        print("No results to display.")
        return

    sorted_results = sorted(
        results,
        key=lambda r: getattr(r, sort_by, 0.0),
        reverse=True,
    )[:limit]

    print()
    print("=" * 100)
    print("RESEARCH MATRIX — LEADERBOARD")
    print("=" * 100)

    rows = []
    for i, r in enumerate(sorted_results, 1):
        pf = r.profit_factor
        pf_str = f"{pf:.2f}" if pf < 1000 else "INF"
        rows.append([
            i,
            r.strategy_id,
            r.instrument,
            r.timeframe,
            f"{r.net_profit:+.2f}",
            f"{r.sharpe_ratio:.2f}",
            pf_str,
            f"{r.max_drawdown_pct:.1f}%",
            f"{r.win_rate:.0%}",
            r.total_trades,
            f"{r.composite_score:.4f}",
            r.status,
        ])

    headers = [
        "#", "Strategy", "Instrument", "TF",
        "Net P/L", "Sharpe", "PF", "Max DD",
        "Win%", "Trades", "Score", "Status",
    ]
    print(tabulate(rows, headers=headers, tablefmt="simple"))
    print()


def print_best_by(
    results: list[ResearchResult],
    metric: str,
    limit: int = 10,
) -> None:
    """Print top N results by a specific metric."""
    if not results:
        return

    sorted_results = sorted(
        results,
        key=lambda r: getattr(r, metric, r.metrics.get(metric, 0.0))
        if hasattr(r, metric)
        else r.metrics.get(metric, 0.0),
        reverse=True,
    )[:limit]

    print(f"\n--- Best by {metric} ---")
    rows = []
    for i, r in enumerate(sorted_results, 1):
        val = getattr(r, metric, r.metrics.get(metric, 0.0))
        if hasattr(r, metric):
            val = getattr(r, metric)
        else:
            val = r.metrics.get(metric, 0.0)
        rows.append([
            i, r.strategy_id, r.instrument, r.timeframe,
            f"{val:.4f}" if isinstance(val, float) else val,
        ])

    headers = ["#", "Strategy", "Instrument", "TF", metric]
    print(tabulate(rows, headers=headers, tablefmt="simple"))
