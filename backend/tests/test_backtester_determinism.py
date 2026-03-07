"""Test backtest determinism — identical inputs must produce identical outputs."""

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.core.models import Timeframe
from fibokei.strategies.bot01_sanyaku import PureSanyakuConfluence


class TestBacktestDeterminism:
    def test_identical_runs_produce_identical_results(self, sample_eurusd_h1_path):
        from fibokei.data.loader import load_ohlcv_csv

        config = BacktestConfig(initial_capital=10000.0, risk_per_trade_pct=1.0)

        # Run 1
        df1 = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        bt1 = Backtester(PureSanyakuConfluence(), config)
        result1 = bt1.run(df1, "EURUSD", Timeframe.H1)

        # Run 2
        df2 = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        bt2 = Backtester(PureSanyakuConfluence(), config)
        result2 = bt2.run(df2, "EURUSD", Timeframe.H1)

        # Same number of trades
        assert len(result1.trades) == len(result2.trades)

        # Each trade must be identical
        for t1, t2 in zip(result1.trades, result2.trades):
            assert t1.entry_price == t2.entry_price
            assert t1.exit_price == t2.exit_price
            assert t1.entry_time == t2.entry_time
            assert t1.exit_time == t2.exit_time
            assert t1.pnl == t2.pnl
            assert t1.direction == t2.direction
            assert t1.exit_reason == t2.exit_reason

        # Equity curves identical
        assert result1.equity_curve == result2.equity_curve
