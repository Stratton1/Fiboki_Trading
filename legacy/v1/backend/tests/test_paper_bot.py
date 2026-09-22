"""Tests for paper trading bot."""

import pandas as pd
import pytest

from fibokei.core.models import Timeframe
from fibokei.paper.account import PaperAccount
from fibokei.paper.bot import BotState, PaperBot
from fibokei.strategies.registry import strategy_registry


class TestPaperBot:
    def _make_bot(self):
        strategy = strategy_registry.get("bot01_sanyaku")
        account = PaperAccount(initial_balance=10000.0)
        return PaperBot(
            bot_id="test-bot",
            strategy=strategy,
            instrument="EURUSD",
            timeframe=Timeframe.H1,
            account=account,
        )

    def test_initial_state(self):
        bot = self._make_bot()
        assert bot.state == BotState.IDLE
        assert bot.position is None
        assert bot.bars_seen == 0

    def test_start_stop(self):
        bot = self._make_bot()
        bot.start()
        assert bot.state == BotState.MONITORING
        bot.stop()
        assert bot.state == BotState.STOPPED

    def test_pause(self):
        bot = self._make_bot()
        bot.start()
        bot.pause()
        assert bot.state == BotState.PAUSED

    def test_pause_from_idle_does_nothing(self):
        bot = self._make_bot()
        bot.pause()
        assert bot.state == BotState.IDLE

    def test_get_status(self):
        bot = self._make_bot()
        status = bot.get_status()
        assert status["bot_id"] == "test-bot"
        assert status["strategy_id"] == "bot01_sanyaku"
        assert status["state"] == "idle"
        assert status["has_position"] is False

    def test_candle_while_stopped_returns_none(self):
        bot = self._make_bot()
        bar = pd.Series({"open": 1.1, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 100})
        result = bot.on_candle_close(bar, pd.Timestamp("2025-01-01"))
        assert result is None

    def test_candle_processing_increments_bars(self):
        bot = self._make_bot()
        bot.start()
        bar = pd.Series({"open": 1.1, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 100})
        bot.on_candle_close(bar, pd.Timestamp("2025-01-01"))
        assert bot.bars_seen == 1
