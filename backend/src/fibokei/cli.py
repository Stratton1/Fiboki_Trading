"""FIBOKEI command-line interface."""

import argparse
import sys
from pathlib import Path

from tabulate import tabulate


def _default_data_path() -> Path:
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "data" / "fixtures" / "sample_eurusd_h1.csv"


def demo_indicators():
    """Load sample data and display Ichimoku + ATR values."""
    from fibokei.core.models import Timeframe
    from fibokei.data.loader import load_ohlcv_csv
    from fibokei.indicators.atr import ATR
    from fibokei.indicators.ichimoku import IchimokuCloud

    fixture_path = _default_data_path()
    if not fixture_path.exists():
        print(f"Sample data not found at {fixture_path}")
        print("Run: python scripts/generate_sample_data.py")
        sys.exit(1)

    print("FIBOKEI — Indicator Demo")
    print(f"Loading: {fixture_path.name}")
    print()

    df = load_ohlcv_csv(fixture_path, "EURUSD", Timeframe.H1)
    print(f"Loaded {len(df)} bars of EURUSD H1 data")
    print()

    ichimoku = IchimokuCloud()
    atr = ATR()
    df = ichimoku.compute(df)
    df = atr.compute(df)

    ichimoku_cols = [
        "tenkan_sen", "kijun_sen", "senkou_span_a", "senkou_span_b",
    ]
    display_df = df.dropna(subset=[*ichimoku_cols, "atr"])
    last_10 = display_df.tail(10)

    rows = []
    for ts, row in last_10.iterrows():
        chikou = row["chikou_span"]
        chikou_str = f"{chikou:.5f}" if chikou == chikou else "—"
        rows.append([
            ts.strftime("%Y-%m-%d %H:%M"),
            f"{row['close']:.5f}",
            f"{row['tenkan_sen']:.5f}",
            f"{row['kijun_sen']:.5f}",
            f"{row['senkou_span_a']:.5f}",
            f"{row['senkou_span_b']:.5f}",
            chikou_str,
            f"{row['atr']:.5f}",
        ])

    headers = [
        "Timestamp", "Close", "Tenkan", "Kijun",
        "Senkou A", "Senkou B", "Chikou", "ATR",
    ]
    print(tabulate(rows, headers=headers, tablefmt="simple"))
    print()
    print(
        f"Ichimoku warmup: {ichimoku.warmup_period} bars"
        f" | ATR warmup: {atr.warmup_period} bars"
    )


def list_indicators():
    """List available indicators."""
    from fibokei.indicators.registry import registry

    print("Available indicators:")
    for name in registry.list_available():
        indicator = registry.get(name)
        print(f"  {name} (warmup: {indicator.warmup_period} bars)")


def list_strategies():
    """List available strategies."""
    from fibokei.strategies.registry import strategy_registry

    print("Available strategies:")
    for info in strategy_registry.list_available():
        print(
            f"  {info['id']} — {info['name']}"
            f" [{info['family']}] ({info['complexity']})"
        )


def run_backtest(args):
    """Run a backtest and print results."""
    from fibokei.backtester.config import BacktestConfig
    from fibokei.backtester.display import print_metrics, print_trade_list
    from fibokei.backtester.engine import Backtester
    from fibokei.backtester.metrics import compute_metrics
    from fibokei.core.models import Timeframe
    from fibokei.data.loader import load_ohlcv_csv
    from fibokei.strategies.registry import strategy_registry

    # Resolve data path
    data_path = Path(args.data) if args.data else _default_data_path()
    if not data_path.exists():
        print(f"Data file not found: {data_path}")
        sys.exit(1)

    # Resolve timeframe
    try:
        timeframe = Timeframe(args.timeframe)
    except ValueError:
        print(f"Invalid timeframe: {args.timeframe}")
        print(f"Valid options: {', '.join(t.value for t in Timeframe)}")
        sys.exit(1)

    # Get strategy
    try:
        strategy = strategy_registry.get(args.strategy)
    except KeyError:
        print(f"Unknown strategy: {args.strategy}")
        available = strategy_registry.list_available()
        print(f"Available: {', '.join(s['id'] for s in available)}")
        sys.exit(1)

    # Load data
    print(f"FIBOKEI — Backtest: {strategy.strategy_name}")
    print(f"Instrument: {args.instrument} | Timeframe: {args.timeframe}")
    print(f"Data: {data_path.name}")

    df = load_ohlcv_csv(data_path, args.instrument, timeframe)
    print(f"Loaded {len(df)} bars")

    # Configure and run
    config = BacktestConfig(
        initial_capital=args.capital,
        risk_per_trade_pct=args.risk_pct,
    )

    bt = Backtester(strategy, config)
    result = bt.run(df, args.instrument, timeframe)

    # Compute and display metrics
    metrics = compute_metrics(result)
    print_metrics(metrics)
    print_trade_list(result.trades)


def run_research(args):
    """Run research matrix across strategies, instruments, and timeframes."""
    from fibokei.backtester.config import BacktestConfig
    from fibokei.core.models import Timeframe
    from fibokei.research.display import print_best_by, print_leaderboard
    from fibokei.research.filter import apply_minimum_trade_filter
    from fibokei.research.matrix import ResearchMatrix
    from fibokei.strategies.registry import strategy_registry

    # Resolve strategies
    if args.strategies == "all":
        strategy_ids = [s["id"] for s in strategy_registry.list_available()]
    else:
        strategy_ids = [s.strip() for s in args.strategies.split(",")]

    # Resolve instruments and timeframes
    instruments = [i.strip() for i in args.instruments.split(",")]
    timeframes = []
    for tf_str in args.timeframes.split(","):
        try:
            timeframes.append(Timeframe(tf_str.strip()))
        except ValueError:
            print(f"Invalid timeframe: {tf_str}")
            sys.exit(1)

    # Resolve data directory
    data_dir = args.data_dir if args.data_dir else str(
        Path(__file__).parent.parent.parent.parent / "data" / "fixtures"
    )

    config = BacktestConfig(initial_capital=args.capital)

    print("FIBOKEI — Research Matrix")
    print(f"Strategies: {', '.join(strategy_ids)}")
    print(f"Instruments: {', '.join(instruments)}")
    print(f"Timeframes: {', '.join(tf.value for tf in timeframes)}")
    print(f"Data dir: {data_dir}")
    print()

    matrix = ResearchMatrix(strategy_ids, instruments, timeframes, config)
    results = matrix.run(data_dir)

    # Apply trade filter
    qualified, insufficient = apply_minimum_trade_filter(results, args.min_trades)

    print_leaderboard(results)

    if qualified:
        print(f"Qualified ({args.min_trades}+ trades): {len(qualified)}")
    if insufficient:
        print(f"Insufficient (<{args.min_trades} trades): {len(insufficient)}")

    print_best_by(results, "composite_score", limit=5)


def main():
    parser = argparse.ArgumentParser(
        prog="fibokei",
        description="FIBOKEI — Multi-strategy trading platform",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run indicator demo on sample data")
    subparsers.add_parser("list-indicators", help="List available indicators")
    subparsers.add_parser("list-strategies", help="List available strategies")

    bt_parser = subparsers.add_parser("backtest", help="Run a backtest")
    bt_parser.add_argument(
        "--strategy", required=True, help="Strategy ID (e.g. bot01_sanyaku)"
    )
    bt_parser.add_argument(
        "--instrument", default="EURUSD", help="Instrument symbol"
    )
    bt_parser.add_argument(
        "--timeframe", default="H1", help="Timeframe (M1..H4)"
    )
    bt_parser.add_argument(
        "--data", default=None, help="Path to CSV data file"
    )
    bt_parser.add_argument(
        "--capital", type=float, default=10000.0, help="Initial capital"
    )
    bt_parser.add_argument(
        "--risk-pct", type=float, default=1.0, help="Risk per trade %%"
    )

    res_parser = subparsers.add_parser("research", help="Run research matrix")
    res_parser.add_argument(
        "--strategies", required=True,
        help="Comma-separated strategy IDs or 'all'",
    )
    res_parser.add_argument(
        "--instruments", default="EURUSD",
        help="Comma-separated instruments",
    )
    res_parser.add_argument(
        "--timeframes", default="H1",
        help="Comma-separated timeframes",
    )
    res_parser.add_argument(
        "--data-dir", default=None,
        help="Path to data directory",
    )
    res_parser.add_argument(
        "--min-trades", type=int, default=80,
        help="Minimum trades for qualification",
    )
    res_parser.add_argument(
        "--capital", type=float, default=10000.0,
        help="Initial capital",
    )

    args = parser.parse_args()

    if args.command == "demo":
        demo_indicators()
    elif args.command == "list-indicators":
        list_indicators()
    elif args.command == "list-strategies":
        list_strategies()
    elif args.command == "backtest":
        run_backtest(args)
    elif args.command == "research":
        run_research(args)
    else:
        demo_indicators()


if __name__ == "__main__":
    main()
