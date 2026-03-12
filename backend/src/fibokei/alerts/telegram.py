"""Telegram notification service with dual-write to in-app alerts."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import httpx

from fibokei.core.signals import Signal
from fibokei.core.trades import TradeResult

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _save_alert_to_db(
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    metadata_json: dict | None = None,
    db: Session | None = None,
) -> None:
    """Best-effort dual-write: persist an in-app alert alongside Telegram push."""
    if db is None:
        return
    try:
        from fibokei.db.repository import save_alert

        save_alert(db, alert_type, severity, title, message, metadata_json)
    except Exception:
        logger.debug("Could not persist in-app alert", exc_info=True)


class TelegramNotifier:
    """Send trading notifications via Telegram Bot API."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ):
        self.bot_token = bot_token or os.environ.get("FIBOKEI_TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("FIBOKEI_TELEGRAM_CHAT_ID", "")
        self._base_url = f"https://api.telegram.org/bot{self.bot_token}"

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> bool:
        """Send a text message. Returns True on success."""
        if not self.is_configured:
            return False
        try:
            resp = httpx.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def send_signal_alert(self, signal: Signal, db: Session | None = None) -> bool:
        """Send formatted signal notification."""
        arrow = "\u2191" if signal.direction.value == "LONG" else "\u2193"
        text = (
            f"<b>{arrow} Signal: {signal.strategy_id}</b>\n"
            f"Instrument: {signal.instrument} {signal.timeframe.value}\n"
            f"Direction: {signal.direction.value}\n"
            f"Entry: {signal.proposed_entry:.5f}\n"
            f"Stop Loss: {signal.stop_loss:.5f}\n"
            f"Take Profit: {signal.take_profit_primary:.5f}\n"
            f"Confidence: {signal.confidence_score:.0%}\n"
            f"Regime: {signal.regime_label}"
        )
        _save_alert_to_db(
            alert_type="signal",
            severity="info",
            title=f"Signal: {signal.strategy_id} {signal.direction.value} {signal.instrument}",
            message=f"{signal.instrument} {signal.timeframe.value} — Entry {signal.proposed_entry:.5f}, Confidence {signal.confidence_score:.0%}",
            metadata_json={
                "strategy_id": signal.strategy_id,
                "instrument": signal.instrument,
                "timeframe": signal.timeframe.value,
                "direction": signal.direction.value,
                "entry": signal.proposed_entry,
                "stop_loss": signal.stop_loss,
                "confidence": signal.confidence_score,
            },
            db=db,
        )
        return self.send_message(text)

    def send_trade_closed(self, trade: TradeResult, db: Session | None = None) -> bool:
        """Send trade closed notification."""
        emoji = "\u2705" if trade.pnl > 0 else "\u274c"
        text = (
            f"{emoji} <b>Trade Closed: {trade.strategy_id}</b>\n"
            f"Instrument: {trade.instrument}\n"
            f"Direction: {trade.direction.value}\n"
            f"Entry: {trade.entry_price:.5f} \u2192 Exit: {trade.exit_price:.5f}\n"
            f"PnL: {trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)\n"
            f"Reason: {trade.exit_reason.value}\n"
            f"Duration: {trade.bars_in_trade} bars"
        )
        _save_alert_to_db(
            alert_type="trade",
            severity="info",
            title=f"Trade Closed: {trade.strategy_id} {trade.instrument}",
            message=f"{trade.direction.value} — PnL {trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%), {trade.exit_reason.value}",
            metadata_json={
                "strategy_id": trade.strategy_id,
                "instrument": trade.instrument,
                "direction": trade.direction.value,
                "pnl": trade.pnl,
                "pnl_pct": trade.pnl_pct,
                "exit_reason": trade.exit_reason.value,
            },
            db=db,
        )
        return self.send_message(text)

    def send_risk_alert(self, alert_type: str, details: str, db: Session | None = None) -> bool:
        """Send risk limit breach notification."""
        text = (
            f"\u26a0\ufe0f <b>RISK ALERT: {alert_type}</b>\n"
            f"{details}"
        )
        _save_alert_to_db(
            alert_type="risk",
            severity="critical",
            title=f"Risk Alert: {alert_type}",
            message=details,
            metadata_json={"risk_type": alert_type},
            db=db,
        )
        return self.send_message(text)

    def send_daily_summary(self, account_status: dict, trades_today: int, db: Session | None = None) -> bool:
        """Send daily performance summary."""
        text = (
            f"\ud83d\udcca <b>Daily Summary</b>\n"
            f"Balance: {account_status['balance']:.2f}\n"
            f"Daily PnL: {account_status['daily_pnl']:+.2f}\n"
            f"Equity: {account_status['equity']:.2f}\n"
            f"Trades today: {trades_today}\n"
            f"Open positions: {account_status['open_positions']}"
        )
        _save_alert_to_db(
            alert_type="summary",
            severity="info",
            title="Daily Summary",
            message=f"Balance {account_status['balance']:.2f}, PnL {account_status['daily_pnl']:+.2f}, {trades_today} trades",
            metadata_json=account_status,
            db=db,
        )
        return self.send_message(text)
