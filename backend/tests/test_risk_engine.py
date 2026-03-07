"""Tests for risk engine."""

import pytest

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.paper.account import PaperAccount
from fibokei.risk.engine import RiskEngine


def _make_signal(**kwargs):
    defaults = {
        "timestamp": "2025-01-01T00:00:00Z",
        "instrument": "EURUSD",
        "timeframe": Timeframe.H1,
        "strategy_id": "test",
        "direction": Direction.LONG,
        "setup_type": "test",
        "proposed_entry": 1.1000,
        "stop_loss": 1.0950,
        "take_profit_primary": 1.1100,
        "confidence_score": 0.6,
        "regime_label": "trending_bullish",
    }
    defaults.update(kwargs)
    return Signal(**defaults)


class TestRiskEngine:
    def test_trade_allowed_basic(self):
        engine = RiskEngine()
        account = PaperAccount(initial_balance=10000.0)
        signal = _make_signal()
        allowed, reason = engine.check_trade_allowed(signal, account)
        assert allowed is True
        assert reason == ""

    def test_max_open_trades_reached(self):
        engine = RiskEngine(max_open_trades=2)
        account = PaperAccount()
        account.open_positions = [{"instrument": "GBPUSD"}, {"instrument": "USDJPY"}]
        signal = _make_signal()
        allowed, reason = engine.check_trade_allowed(signal, account)
        assert allowed is False
        assert "Max open trades" in reason

    def test_max_per_instrument_reached(self):
        engine = RiskEngine(max_per_instrument=1)
        account = PaperAccount()
        account.open_positions = [{"instrument": "EURUSD"}]
        signal = _make_signal(instrument="EURUSD")
        allowed, reason = engine.check_trade_allowed(signal, account)
        assert allowed is False
        assert "per instrument" in reason

    def test_daily_hard_stop(self):
        engine = RiskEngine(daily_hard_stop_pct=4.0)
        account = PaperAccount(initial_balance=10000.0)
        account.daily_pnl = -500.0  # 5% loss
        safe, alert = engine.check_drawdown_limits(account)
        assert safe is False
        assert alert == "daily_hard_stop"

    def test_daily_soft_stop(self):
        engine = RiskEngine(daily_soft_stop_pct=3.0, daily_hard_stop_pct=4.0)
        account = PaperAccount(initial_balance=10000.0)
        account.daily_pnl = -350.0  # 3.5% loss
        safe, alert = engine.check_drawdown_limits(account)
        assert safe is True
        assert alert == "daily_soft_stop"

    def test_weekly_hard_stop(self):
        engine = RiskEngine(weekly_hard_stop_pct=8.0)
        account = PaperAccount(initial_balance=10000.0)
        account.weekly_pnl = -900.0  # 9% loss
        safe, alert = engine.check_drawdown_limits(account)
        assert safe is False
        assert alert == "weekly_hard_stop"

    def test_no_drawdown_issue(self):
        engine = RiskEngine()
        account = PaperAccount(initial_balance=10000.0)
        account.daily_pnl = 200.0  # Positive
        safe, alert = engine.check_drawdown_limits(account)
        assert safe is True
        assert alert == ""

    def test_trade_rejected_on_hard_stop(self):
        engine = RiskEngine(daily_hard_stop_pct=4.0)
        account = PaperAccount(initial_balance=10000.0)
        account.daily_pnl = -500.0
        signal = _make_signal()
        allowed, reason = engine.check_trade_allowed(signal, account)
        assert allowed is False
        assert "Drawdown limit" in reason
