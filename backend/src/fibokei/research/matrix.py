"""Research matrix: batch-run strategies across instruments and timeframes."""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.data.loader import load_ohlcv_csv
from fibokei.data.providers.registry import load_canonical
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.strategies.registry import strategy_registry


@dataclass
class ResearchResult:
    """Result of a single strategy-instrument-timeframe combination."""

    strategy_id: str
    instrument: str
    timeframe: str
    provider: str | None = None
    total_trades: int = 0
    net_profit: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    composite_score: float = 0.0
    rank: int = 0
    metrics: dict = field(default_factory=dict)
    status: str = "ok"


class ResearchMatrix:
    """Batch-run strategies across instruments and timeframes."""

    def __init__(
        self,
        strategies: list[str],
        instruments: list[str],
        timeframes: list[Timeframe],
        config: BacktestConfig | None = None,
        scoring_config: ScoringConfig | None = None,
        provider: str | None = None,
    ):
        self.strategies = strategies
        self.instruments = instruments
        self.timeframes = timeframes
        self.config = config or BacktestConfig()
        self.scoring_config = scoring_config or ScoringConfig()
        self.provider = provider

    def run(self, data_dir: str, progress_callback=None) -> list[ResearchResult]:
        """Run all combinations and return ranked results.

        If progress_callback is provided, it is called with (completed, total)
        after each combination finishes.
        """
        data_path = Path(data_dir)
        results = []

        total = len(self.strategies) * len(self.instruments) * len(self.timeframes)
        count = 0

        for strategy_id in self.strategies:
            try:
                strategy = strategy_registry.get(strategy_id)
            except KeyError:
                print(f"  WARNING: Unknown strategy {strategy_id}, skipping")
                continue

            for instrument in self.instruments:
                for timeframe in self.timeframes:
                    count += 1
                    tf_str = timeframe.value
                    print(
                        f"  [{count}/{total}] {strategy_id} on {instrument} {tf_str}...",
                        end=" ",
                    )
                    sys.stdout.flush()

                    # Load data: canonical provider store first, then legacy files
                    df = self._load_data(data_path, instrument, tf_str, timeframe)
                    if df is None:
                        print("SKIP (no data)")
                        if progress_callback:
                            progress_callback(count, total)
                        continue

                    try:
                        bt = Backtester(strategy, self.config)
                        bt_result = bt.run(df, instrument, timeframe)
                        metrics = compute_metrics(bt_result)
                        metrics["equity_curve"] = bt_result.equity_curve
                        metrics["initial_capital"] = self.config.initial_capital

                        score = compute_composite_score(metrics, self.scoring_config)

                        result = ResearchResult(
                            strategy_id=strategy_id,
                            instrument=instrument,
                            timeframe=tf_str,
                            provider=self.provider,
                            total_trades=metrics.get("total_trades", 0),
                            net_profit=metrics.get("total_net_profit", 0.0),
                            sharpe_ratio=metrics.get("sharpe_ratio", 0.0),
                            profit_factor=metrics.get("profit_factor", 0.0),
                            max_drawdown_pct=metrics.get("max_drawdown_pct", 0.0),
                            win_rate=metrics.get("win_rate", 0.0),
                            composite_score=score,
                            metrics=metrics,
                        )
                        print(f"OK ({metrics.get('total_trades', 0)} trades)")

                    except Exception as e:
                        result = ResearchResult(
                            strategy_id=strategy_id,
                            instrument=instrument,
                            timeframe=tf_str,
                            provider=self.provider,
                            status=f"error: {e}",
                        )
                        print(f"ERROR: {e}")

                    results.append(result)

                    if progress_callback:
                        progress_callback(count, total)

        # Sort by composite score and assign ranks
        results.sort(key=lambda r: r.composite_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        return results

    def _load_data(
        self,
        data_dir: Path,
        instrument: str,
        tf_str: str,
        timeframe: Timeframe,
    ) -> pd.DataFrame | None:
        """Load data from canonical provider store, falling back to legacy files."""
        # Try canonical provider store first
        df = load_canonical(instrument, tf_str, provider=self.provider)
        if df is not None:
            df["instrument"] = instrument
            df["timeframe"] = tf_str
            return df

        # Fall back to legacy CSV file search
        csv_path = self._find_data_file(data_dir, instrument, tf_str)
        if csv_path is not None:
            return load_ohlcv_csv(csv_path, instrument, timeframe)

        return None

    def _find_data_file(self, data_dir: Path, instrument: str, timeframe: str) -> Path | None:
        """Find data CSV file with common naming patterns."""
        patterns = [
            f"sample_{instrument.lower()}_{timeframe.lower()}.csv",
            f"{instrument.lower()}_{timeframe.lower()}.csv",
            f"{instrument}_{timeframe}.csv",
        ]
        for pattern in patterns:
            path = data_dir / pattern
            if path.exists():
                return path
        return None
