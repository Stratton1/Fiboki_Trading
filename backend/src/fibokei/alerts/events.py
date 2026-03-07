"""Alert event types and dispatcher."""

from enum import Enum

from fibokei.alerts.telegram import TelegramNotifier
from fibokei.core.signals import Signal
from fibokei.core.trades import TradeResult


class AlertEvent(str, Enum):
    SIGNAL_DETECTED = "signal_detected"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    RISK_BREACH = "risk_breach"
    DAILY_SUMMARY = "daily_summary"
    BOT_STARTED = "bot_started"
    BOT_STOPPED = "bot_stopped"


class AlertDispatcher:
    """Routes alert events to configured notifiers."""

    def __init__(self, telegram: TelegramNotifier | None = None):
        self.telegram = telegram

    def dispatch(self, event: AlertEvent, **kwargs) -> None:
        """Send alert based on event type."""
        if not self.telegram or not self.telegram.is_configured:
            return

        if event == AlertEvent.SIGNAL_DETECTED:
            signal = kwargs.get("signal")
            if isinstance(signal, Signal):
                self.telegram.send_signal_alert(signal)

        elif event == AlertEvent.TRADE_CLOSED:
            trade = kwargs.get("trade")
            if isinstance(trade, TradeResult):
                self.telegram.send_trade_closed(trade)

        elif event == AlertEvent.RISK_BREACH:
            self.telegram.send_risk_alert(
                kwargs.get("alert_type", "unknown"),
                kwargs.get("details", ""),
            )

        elif event == AlertEvent.DAILY_SUMMARY:
            self.telegram.send_daily_summary(
                kwargs.get("account_status", {}),
                kwargs.get("trades_today", 0),
            )

        elif event in (AlertEvent.BOT_STARTED, AlertEvent.BOT_STOPPED):
            bot_id = kwargs.get("bot_id", "unknown")
            strategy_id = kwargs.get("strategy_id", "unknown")
            self.telegram.send_message(
                f"Bot {event.value}: {strategy_id} ({bot_id})"
            )
