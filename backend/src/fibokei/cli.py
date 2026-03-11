"""Fiboki Trading command-line interface."""

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

    print("Fiboki Trading — Indicator Demo")
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
    print(f"Fiboki Trading — Backtest: {strategy.strategy_name}")
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


def refresh_data(args):
    """Fetch latest market data from Yahoo Finance."""
    from fibokei.data.ingestion import refresh_all

    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

    timeframe = args.timeframe.upper()
    data_dir = args.data_dir if args.data_dir else None

    print(f"Fiboki Trading — Data Refresh ({timeframe})")
    print(f"Symbols: {', '.join(symbols) if symbols else 'all'}")
    print()

    results = refresh_all(symbols=symbols, timeframe=timeframe, data_dir=data_dir)

    success = {k: v for k, v in results.items() if v > 0}
    failed = {k: v for k, v in results.items() if v == 0}

    if success:
        rows = [[sym, count] for sym, count in sorted(success.items())]
        print(tabulate(rows, headers=["Symbol", "Bars"], tablefmt="simple"))
        print()

    print(f"Success: {len(success)} | Failed: {len(failed)}")
    if failed:
        print(f"Failed symbols: {', '.join(sorted(failed.keys()))}")


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

    print("Fiboki Trading — Research Matrix")
    print(f"Strategies: {', '.join(strategy_ids)}")
    print(f"Instruments: {', '.join(instruments)}")
    print(f"Timeframes: {', '.join(tf.value for tf in timeframes)}")
    print(f"Data dir: {data_dir}")
    print()

    provider = getattr(args, "provider", None)
    matrix = ResearchMatrix(strategy_ids, instruments, timeframes, config, provider=provider)
    results = matrix.run(data_dir)

    # Apply trade filter
    qualified, insufficient = apply_minimum_trade_filter(results, args.min_trades)

    print_leaderboard(results)

    if qualified:
        print(f"Qualified ({args.min_trades}+ trades): {len(qualified)}")
    if insufficient:
        print(f"Insufficient (<{args.min_trades} trades): {len(insufficient)}")

    print_best_by(results, "composite_score", limit=5)


def download_data(args):
    """Download M1 data from histdata.com and ingest into canonical store."""
    from fibokei.data.providers.base import ProviderID
    from fibokei.data.providers.histdata import HistDataProvider
    from fibokei.data.providers.symbol_map import list_mapped_symbols

    provider = HistDataProvider()

    # Resolve symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = list_mapped_symbols(ProviderID.HISTDATA)

    # Resolve years
    from datetime import datetime as dt
    current_year = dt.now().year
    if args.years:
        years = [int(y.strip()) for y in args.years.split(",")]
    else:
        years = list(range(2019, current_year + 1))

    # Resolve canonical data dir
    project_root = Path(__file__).parent.parent.parent.parent
    data_dir = project_root / "data" / "canonical"

    print("Fiboki Trading — Download & Ingest (histdata.com)")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Years: {', '.join(str(y) for y in years)}")
    print()

    for symbol in symbols:
        print(f"  {symbol}:")

        # Download
        print("    Downloading...", end=" ")
        try:
            zips = provider.download(symbol, years=years)
            print(f"{len(zips)} files")
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        if not zips:
            print("    No data available, skipping")
            continue

        # Ingest
        print("    Ingesting...", end=" ")
        try:
            results = provider.ingest_all_timeframes(
                symbol, data_dir=data_dir,
            )
            bars = sum(m.row_count for m in results.values())
            tfs = ", ".join(sorted(results.keys()))
            print(f"OK ({bars:,} bars across {tfs})")
        except Exception as e:
            print(f"ERROR: {e}")

    print()
    print("Done. Run 'fibokei list-data' to see available datasets.")


def ingest_data(args):
    """Ingest raw data from a provider into the canonical store."""
    from fibokei.data.providers.base import ProviderID
    from fibokei.data.providers.registry import get_provider
    from fibokei.data.providers.symbol_map import list_mapped_symbols

    try:
        provider_id = ProviderID(args.provider.lower())
    except ValueError:
        print(f"Unknown provider: {args.provider}")
        print("Available: histdata, dukascopy")
        sys.exit(1)

    provider = get_provider(provider_id)

    # Resolve symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = list_mapped_symbols(provider_id)

    # Resolve canonical data dir
    project_root = Path(__file__).parent.parent.parent.parent
    data_dir = Path(args.data_dir) if args.data_dir else project_root / "data" / "canonical"

    # If raw_dir specified, set it on the provider
    if args.raw_dir:
        if hasattr(provider, "raw_dir"):
            provider.raw_dir = Path(args.raw_dir)

    print(f"Fiboki Trading — Data Ingestion ({provider_id.value})")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Output: {data_dir}")
    print()

    success = 0
    failed = 0
    for symbol in symbols:
        print(f"  {symbol}...", end=" ")
        try:
            if hasattr(provider, "ingest_all_timeframes"):
                results = provider.ingest_all_timeframes(symbol, data_dir=data_dir)
                bars = sum(meta.row_count for _, meta in results)
                print(f"OK ({len(results)} timeframes, {bars:,} total bars)")
            else:
                df, meta = provider.ingest(symbol, "M1", data_dir=data_dir)
                print(f"OK ({meta.row_count:,} bars)")
            success += 1
        except Exception as e:
            print(f"ERROR: {e}")
            failed += 1

    print()
    print(f"Done. Success: {success} | Failed: {failed}")


def list_data(_args=None):
    """List available canonical datasets."""
    project_root = Path(__file__).parent.parent.parent.parent
    canonical_dir = project_root / "data" / "canonical"

    if not canonical_dir.exists():
        print("No canonical data directory found.")
        print(f"Expected at: {canonical_dir}")
        print("Run 'fibokei ingest-data --provider histdata' to populate it.")
        return

    rows = []
    for provider_dir in sorted(canonical_dir.iterdir()):
        if not provider_dir.is_dir():
            continue
        provider_name = provider_dir.name
        for symbol_dir in sorted(provider_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue
            symbol = symbol_dir.name.upper()
            for data_file in sorted(symbol_dir.iterdir()):
                if data_file.suffix not in (".parquet", ".csv"):
                    continue
                # Extract timeframe from filename: eurusd_h1.parquet -> H1
                parts = data_file.stem.split("_")
                tf = parts[-1].upper() if len(parts) >= 2 else "?"
                size_mb = data_file.stat().st_size / (1024 * 1024)
                rows.append([provider_name, symbol, tf, data_file.suffix, f"{size_mb:.2f} MB"])

    if rows:
        hdrs = ["Provider", "Symbol", "TF", "Format", "Size"]
        print(tabulate(rows, headers=hdrs, tablefmt="simple"))
        print(f"\nTotal datasets: {len(rows)}")
    else:
        print("No canonical datasets found.")
        print("Run 'fibokei ingest-data --provider histdata' to populate.")


def run_paper_worker(args):
    """Run the paper trading worker."""
    from fibokei.worker import main as worker_main
    # Re-use worker's own CLI parsing by calling its main directly
    import sys as _sys
    worker_args = []
    if args.dry_run:
        worker_args.append("--dry-run")
    if args.once:
        worker_args.append("--once")
    if args.poll_interval != 60:
        worker_args.extend(["--poll-interval", str(args.poll_interval)])
    _sys.argv = ["fibokei-worker"] + worker_args
    worker_main()


def show_paper_status():
    """Show paper bot and account status from the database."""
    from fibokei.worker import _build_db_session
    from fibokei.db.repository import (
        get_or_create_paper_account,
        get_paper_bots,
        get_paper_trades,
    )

    session_factory = _build_db_session()
    with session_factory() as session:
        acct = get_or_create_paper_account(session)
        bots = get_paper_bots(session)
        trades = get_paper_trades(session, limit=10000)

    print("Fiboki Trading — Paper Status")
    print()
    print(f"Account: balance={acct.balance:.2f}  equity={acct.equity:.2f}  "
          f"daily_pnl={acct.daily_pnl:+.2f}  weekly_pnl={acct.weekly_pnl:+.2f}")
    print(f"Total trades: {len(trades)}")
    print()

    if bots:
        rows = []
        for b in bots:
            last_bar = b.last_evaluated_bar.strftime("%Y-%m-%d %H:%M") if b.last_evaluated_bar else "-"
            rows.append([
                b.bot_id, b.strategy_id, b.instrument, b.timeframe,
                b.state, b.bars_seen, last_bar,
            ])
        hdrs = ["Bot ID", "Strategy", "Instrument", "TF", "State", "Bars", "Last Eval"]
        print(tabulate(rows, headers=hdrs, tablefmt="simple"))
    else:
        print("No paper bots configured.")


def data_sync(args):
    """Bulk data sync: scan, validate, and optionally copy canonical data to a target directory."""
    from fibokei.data.paths import get_canonical_dir

    canonical = Path(args.data_dir) if args.data_dir else get_canonical_dir()
    if not canonical.exists():
        print(f"Canonical directory not found: {canonical}")
        sys.exit(1)

    files = list(canonical.rglob("*.parquet"))
    print("Fiboki Trading — Bulk Data Sync")
    print(f"Source: {canonical}")
    print(f"Found {len(files)} parquet files")
    print()

    if not files:
        print("Nothing to sync.")
        return

    import pyarrow.parquet as pq

    valid = []
    broken = []
    total_rows = 0
    for f in sorted(files):
        try:
            meta = pq.read_metadata(f)
            total_rows += meta.num_rows
            valid.append((f, meta.num_rows))
        except Exception as e:
            broken.append((f, str(e)))

    rows = []
    for f, count in valid:
        rel = f.relative_to(canonical)
        size_mb = f.stat().st_size / (1024 * 1024)
        rows.append([str(rel), f"{count:,}", f"{size_mb:.2f} MB"])
    print(tabulate(rows, headers=["Dataset", "Rows", "Size"], tablefmt="simple"))
    print()

    if broken:
        print(f"Broken files ({len(broken)}):")
        for f, err in broken:
            print(f"  {f.relative_to(canonical)}: {err}")
        print()

    print(f"Valid: {len(valid)} | Broken: {len(broken)} | Total rows: {total_rows:,}")
    total_size = sum(f.stat().st_size for f, _ in valid) / (1024 * 1024)
    print(f"Total size: {total_size:.1f} MB")

    if args.target:
        import shutil

        target = Path(args.target)
        print(f"\nSyncing to: {target}")
        target.mkdir(parents=True, exist_ok=True)
        synced = 0
        for f, _ in valid:
            rel = f.relative_to(canonical)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or f.stat().st_mtime > dest.stat().st_mtime:
                shutil.copy2(f, dest)
                synced += 1
        print(f"Synced {synced} files ({len(valid) - synced} already up-to-date)")

    if not args.skip_manifest:
        from fibokei.data.manifest import generate_manifest

        generate_manifest(canonical)
        print("Manifest regenerated.")


def generate_manifest_cmd(_args=None):
    """Generate manifest.json for the canonical data directory."""
    from fibokei.data.manifest import generate_manifest
    from fibokei.data.paths import get_canonical_dir

    canonical = get_canonical_dir()
    print(f"Scanning: {canonical}")

    manifest = generate_manifest(canonical)
    count = len(manifest["datasets"])
    print(f"Generated manifest with {count} datasets")

    if count > 0:
        # Summary by provider
        providers = {}
        for d in manifest["datasets"]:
            p = d["provider"]
            providers[p] = providers.get(p, 0) + 1
        for p, c in sorted(providers.items()):
            print(f"  {p}: {c} files")


def main():
    parser = argparse.ArgumentParser(
        prog="fibokei",
        description="Fiboki Trading — Multi-strategy trading platform",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run indicator demo on sample data")
    subparsers.add_parser("list-indicators", help="List available indicators")
    subparsers.add_parser("list-strategies", help="List available strategies")

    refresh_parser = subparsers.add_parser(
        "refresh-data", help="Fetch market data from Yahoo Finance",
    )
    refresh_parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbols (default: all mapped symbols)",
    )
    refresh_parser.add_argument(
        "--timeframe", default="H1",
        help="Timeframe (M1..H4)",
    )
    refresh_parser.add_argument(
        "--data-dir", default=None,
        help="Output directory for CSV files",
    )

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
    res_parser.add_argument(
        "--provider", default=None,
        help="Data provider: histdata, dukascopy (default: auto-detect)",
    )

    # --- ingest-data ---
    ingest_parser = subparsers.add_parser(
        "ingest-data",
        help="Ingest raw data from a provider into canonical store",
    )
    ingest_parser.add_argument(
        "--provider", required=True,
        help="Data provider: histdata, dukascopy",
    )
    ingest_parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated Fiboki symbols (default: all mapped for provider)",
    )
    ingest_parser.add_argument(
        "--raw-dir", default=None,
        help="Directory containing raw provider files",
    )
    ingest_parser.add_argument(
        "--data-dir", default=None,
        help="Output canonical data directory",
    )

    # --- download-data ---
    dl_parser = subparsers.add_parser(
        "download-data",
        help="Download M1 data from histdata.com and ingest",
    )
    dl_parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbols (default: all HistData-mapped)",
    )
    dl_parser.add_argument(
        "--years", default=None,
        help="Comma-separated years (default: 2019-current)",
    )

    # --- list-data ---
    subparsers.add_parser(
        "list-data",
        help="List available canonical datasets",
    )

    # --- paper-worker ---
    pw_parser = subparsers.add_parser(
        "paper-worker",
        help="Run the paper trading worker process",
    )
    pw_parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without persisting state changes",
    )
    pw_parser.add_argument(
        "--poll-interval", type=int, default=60,
        help="Seconds between evaluation cycles (default: 60)",
    )
    pw_parser.add_argument(
        "--once", action="store_true",
        help="Run a single evaluation cycle and exit",
    )

    # --- paper-status ---
    subparsers.add_parser(
        "paper-status",
        help="Show paper trading bot and account status",
    )

    # --- data-sync ---
    sync_parser = subparsers.add_parser(
        "data-sync",
        help="Scan, validate, and sync canonical parquet data",
    )
    sync_parser.add_argument(
        "--data-dir", default=None,
        help="Source canonical data directory (default: auto-detect)",
    )
    sync_parser.add_argument(
        "--target", default=None,
        help="Target directory to copy valid files to (e.g. Railway volume mount)",
    )
    sync_parser.add_argument(
        "--skip-manifest", action="store_true",
        help="Skip manifest regeneration after sync",
    )

    # --- manifest ---
    subparsers.add_parser(
        "manifest",
        help="Generate manifest.json for canonical datasets",
    )

    args = parser.parse_args()

    if args.command == "demo":
        demo_indicators()
    elif args.command == "list-indicators":
        list_indicators()
    elif args.command == "list-strategies":
        list_strategies()
    elif args.command == "refresh-data":
        refresh_data(args)
    elif args.command == "backtest":
        run_backtest(args)
    elif args.command == "research":
        run_research(args)
    elif args.command == "ingest-data":
        ingest_data(args)
    elif args.command == "download-data":
        download_data(args)
    elif args.command == "list-data":
        list_data(args)
    elif args.command == "paper-worker":
        run_paper_worker(args)
    elif args.command == "paper-status":
        show_paper_status()
    elif args.command == "data-sync":
        data_sync(args)
    elif args.command == "manifest":
        generate_manifest_cmd(args)
    else:
        demo_indicators()


if __name__ == "__main__":
    main()
