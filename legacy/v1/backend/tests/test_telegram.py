"""Tests for Telegram notifications (mocked HTTP)."""

from unittest.mock import MagicMock, patch

import pytest

from fibokei.alerts.events import AlertDispatcher, AlertEvent
from fibokei.alerts.telegram import TelegramNotifier
from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradeResult


def _make_signal():
    return Signal(
        timestamp="2025-01-01T00:00:00Z",
        instrument="EURUSD",
        timeframe=Timeframe.H1,
        strategy_id="bot01_sanyaku",
        direction=Direction.LONG,
        setup_type="sanyaku",
        proposed_entry=1.1000,
        stop_loss=1.0950,
        take_profit_primary=1.1100,
        confidence_score=0.65,
        regime_label="trending_bullish",
    )


def _make_trade():
    return TradeResult(
        trade_id="test-123",
        strategy_id="bot01_sanyaku",
        instrument="EURUSD",
        timeframe=Timeframe.H1,
        direction=Direction.LONG,
        entry_time="2025-01-01T00:00:00Z",
        entry_price=1.1000,
        exit_time="2025-01-01T06:00:00Z",
        exit_price=1.1080,
        exit_reason=ExitReason.TAKE_PROFIT_HIT,
        pnl=80.0,
        pnl_pct=0.73,
        position_size=1000.0,
        bars_in_trade=6,
        max_favorable_excursion=0.009,
        max_adverse_excursion=0.002,
    )


class TestTelegramNotifier:
    def test_not_configured(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        assert notifier.is_configured is False
        assert notifier.send_message("test") is False

    def test_configured(self):
        notifier = TelegramNotifier(bot_token="123:ABC", chat_id="456")
        assert notifier.is_configured is True

    @patch("fibokei.alerts.telegram.httpx.post")
    def test_send_message_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        notifier = TelegramNotifier(bot_token="123:ABC", chat_id="456")
        result = notifier.send_message("Hello")
        assert result is True
        mock_post.assert_called_once()

    @patch("fibokei.alerts.telegram.httpx.post")
    def test_send_signal_alert(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        notifier = TelegramNotifier(bot_token="123:ABC", chat_id="456")
        signal = _make_signal()
        result = notifier.send_signal_alert(signal)
        assert result is True
        call_args = mock_post.call_args
        text = call_args[1]["json"]["text"]
        assert "bot01_sanyaku" in text
        assert "EURUSD" in text
        assert "LONG" in text

    @patch("fibokei.alerts.telegram.httpx.post")
    def test_send_trade_closed(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        notifier = TelegramNotifier(bot_token="123:ABC", chat_id="456")
        trade = _make_trade()
        result = notifier.send_trade_closed(trade)
        assert result is True
        call_args = mock_post.call_args
        text = call_args[1]["json"]["text"]
        assert "+80.00" in text
        assert "take_profit_hit" in text

    @patch("fibokei.alerts.telegram.httpx.post")
    def test_send_risk_alert(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        notifier = TelegramNotifier(bot_token="123:ABC", chat_id="456")
        result = notifier.send_risk_alert("daily_hard_stop", "Daily loss limit reached")
        assert result is True


class TestAlertDispatcher:
    @patch("fibokei.alerts.telegram.httpx.post")
    def test_dispatch_signal(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        notifier = TelegramNotifier(bot_token="123:ABC", chat_id="456")
        dispatcher = AlertDispatcher(telegram=notifier)
        dispatcher.dispatch(AlertEvent.SIGNAL_DETECTED, signal=_make_signal())
        mock_post.assert_called_once()

    def test_dispatch_no_telegram(self):
        dispatcher = AlertDispatcher(telegram=None)
        # Should not raise
        dispatcher.dispatch(AlertEvent.SIGNAL_DETECTED, signal=_make_signal())
