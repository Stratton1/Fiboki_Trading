"""Tests for performance metrics computation."""

from datetime import datetime, timezone

import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.metrics import compute_metrics
from fibokei.backtester.result import BacktestResult
from fibokei.core.models import Direction, Timeframe
from fibokei.core.trades import ExitReason, TradeResult


def _make_trade(pnl: float, direction=Direction.LONG, bars=10) -> TradeResult:
    return TradeResult(
        trade_id=f"t_{pnl}",
        strategy_id="test",
        instrument="EURUSD",
        timeframe=Timeframe.H1,
        direction=direction,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        entry_price=1.10,
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        exit_price=1.10 + pnl / 10000,
        exit_reason=ExitReason.TAKE_PROFIT_HIT if pnl > 0 else ExitReason.STOP_LOSS_HIT,
        pnl=pnl,
        pnl_pct=pnl / 100,
        position_size=10000,
        bars_in_trade=bars,
    )


def _make_result(trades, equity_curve=None) -> BacktestResult:
    if equity_curve is None:
        equity_curve = [10000.0] * 100
    return BacktestResult(
        strategy_id="test",
        instrument="EURUSD",
        timeframe=Timeframe.H1,
        config=BacktestConfig(),
        trades=trades,
        equity_curve=equity_curve,
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 5, tzinfo=timezone.utc),
        total_bars=len(equity_curve),
    )


class TestMetrics:
    def test_known_trade_values(self):
        """3 wins of $100, 2 losses of -$50."""
        trades = [
            _make_trade(100), _make_trade(100), _make_trade(100),
            _make_trade(-50), _make_trade(-50),
        ]
        result = _make_result(trades)
        m = compute_metrics(result)

        assert m["total_net_profit"] == pytest.approx(200.0)
        assert m["gross_profit"] == pytest.approx(300.0)
        assert m["gross_loss"] == pytest.approx(-100.0)
        assert m["win_rate"] == pytest.approx(0.6)
        assert m["profit_factor"] == pytest.approx(3.0)
        assert m["expectancy"] == pytest.approx(40.0)
        assert m["total_trades"] == 5

    def test_max_drawdown(self):
        equity = [100, 110, 95, 105, 90]
        result = _make_result(
            [_make_trade(10), _make_trade(-15)],
            equity_curve=equity,
        )
        m = compute_metrics(result)
        # Peak 110, trough 90 → drawdown = 20
        assert m["max_drawdown"] == pytest.approx(20.0)
        assert m["max_drawdown_pct"] == pytest.approx(20.0 / 110 * 100)

    def test_consecutive_wins_losses(self):
        trades = [
            _make_trade(100), _make_trade(100), _make_trade(100),
            _make_trade(-50), _make_trade(-50),
            _make_trade(100),
        ]
        result = _make_result(trades)
        m = compute_metrics(result)
        assert m["consecutive_wins"] == 3
        assert m["consecutive_losses"] == 2

    def test_zero_trades(self):
        result = _make_result([])
        m = compute_metrics(result)
        assert m["total_trades"] == 0
        assert m["total_net_profit"] == 0.0
        assert m["win_rate"] == 0.0

    def test_all_wins(self):
        trades = [_make_trade(100) for _ in range(5)]
        result = _make_result(trades)
        m = compute_metrics(result)
        assert m["win_rate"] == pytest.approx(1.0)
        assert m["loss_rate"] == pytest.approx(0.0)
        assert m["profit_factor"] == float("inf")

    def test_all_losses(self):
        trades = [_make_trade(-50) for _ in range(5)]
        result = _make_result(trades)
        m = compute_metrics(result)
        assert m["win_rate"] == pytest.approx(0.0)
        assert m["profit_factor"] == pytest.approx(0.0)

    def test_sharpe_ratio_nonzero(self):
        # Rising equity → positive Sharpe
        equity = [10000 + i * 10 for i in range(100)]
        result = _make_result([_make_trade(100)], equity_curve=equity)
        m = compute_metrics(result)
        assert m["sharpe_ratio"] > 0

    def test_long_short_counts(self):
        trades = [
            _make_trade(100, Direction.LONG),
            _make_trade(100, Direction.LONG),
            _make_trade(-50, Direction.SHORT),
        ]
        result = _make_result(trades)
        m = compute_metrics(result)
        assert m["long_trades"] == 2
        assert m["short_trades"] == 1

    def test_exposure_pct(self):
        # 5 trades, each 10 bars = 50 bars. Total bars = 100 → 50%
        trades = [_make_trade(100, bars=10) for _ in range(5)]
        result = _make_result(trades)
        m = compute_metrics(result)
        assert m["exposure_pct"] == pytest.approx(50.0)
