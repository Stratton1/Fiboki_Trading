"""Tests for paper trading account."""

import pytest

from fibokei.paper.account import PaperAccount


class TestPaperAccount:
    def test_initial_state(self):
        acc = PaperAccount(initial_balance=10000.0)
        assert acc.balance == 10000.0
        assert acc.equity == 10000.0
        assert acc.daily_pnl == 0.0
        assert acc.weekly_pnl == 0.0
        assert len(acc.open_positions) == 0
        assert len(acc.closed_trades) == 0

    def test_deposit(self):
        acc = PaperAccount(initial_balance=10000.0)
        acc.deposit(5000.0)
        assert acc.balance == 15000.0
        assert acc.equity == 15000.0

    def test_reset(self):
        acc = PaperAccount(initial_balance=10000.0)
        acc.balance = 12000.0
        acc.daily_pnl = 500.0
        acc.open_positions.append({"test": True})
        acc.reset()
        assert acc.balance == 10000.0
        assert acc.daily_pnl == 0.0
        assert len(acc.open_positions) == 0

    def test_update_equity_with_unrealised(self):
        acc = PaperAccount(initial_balance=10000.0)
        acc.open_positions.append({"unrealised_pnl": 250.0})
        acc.open_positions.append({"unrealised_pnl": -100.0})
        equity = acc.update_equity()
        assert equity == 10150.0

    def test_get_status(self):
        acc = PaperAccount(initial_balance=10000.0)
        acc.balance = 10500.0
        acc.daily_pnl = 500.0
        acc.update_equity()
        status = acc.get_status()
        assert status["balance"] == 10500.0
        assert status["total_pnl"] == 500.0
        assert status["total_pnl_pct"] == 5.0
        assert status["daily_pnl"] == 500.0

    def test_reset_daily_pnl(self):
        acc = PaperAccount()
        acc.daily_pnl = 200.0
        acc.reset_daily_pnl()
        assert acc.daily_pnl == 0.0

    def test_reset_weekly_pnl(self):
        acc = PaperAccount()
        acc.weekly_pnl = 1000.0
        acc.reset_weekly_pnl()
        assert acc.weekly_pnl == 0.0
