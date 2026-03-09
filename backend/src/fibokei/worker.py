"""Paper trading worker — runs bots on candle-aligned schedule.

Designed to run as a separate process from the API (e.g. on Railway).
Recovers active bots from the database on startup and avoids duplicate
candle processing by tracking last_evaluated_bar per bot.
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fibokei.core.models import Timeframe
from fibokei.data.ingestion import fetch_ohlcv
from fibokei.db.models import Base, PaperBotModel
from fibokei.db.repository import (
    get_active_paper_bots,
    get_or_create_paper_account,
    get_paper_bot,
    save_paper_trade,
    update_paper_account,
    update_paper_bot_state,
)
from fibokei.alerts.telegram import TelegramNotifier
from fibokei.paper.account import PaperAccount
from fibokei.paper.bot import BotState, PaperBot
from fibokei.risk.engine import RiskEngine
from fibokei.strategies.registry import strategy_registry

logger = logging.getLogger("fibokei.worker")

# Map timeframes to approximate seconds between candle closes
TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D": 86400,
}

# Default poll interval (seconds) — worker checks for new candles at this rate
DEFAULT_POLL_INTERVAL = 60


class PaperWorker:
    """Manages paper trading bot lifecycle with DB persistence."""

    def __init__(self, session_factory, dry_run: bool = False):
        self.session_factory = session_factory
        self.dry_run = dry_run
        self._running = False

        # Shared in-memory state (rebuilt from DB on startup)
        self.account = PaperAccount()
        self.risk_engine = RiskEngine()
        self.bots: dict[str, PaperBot] = {}
        self.notifier = TelegramNotifier()
        self._last_daily_summary: datetime | None = None
        self._trades_today = 0

    def recover(self) -> int:
        """Recover active bots from database. Returns count recovered."""
        with self.session_factory() as session:
            # Restore account state
            acct_model = get_or_create_paper_account(session)
            self.account.initial_balance = acct_model.initial_balance
            self.account.balance = acct_model.balance
            self.account.equity = acct_model.equity
            self.account.daily_pnl = acct_model.daily_pnl
            self.account.weekly_pnl = acct_model.weekly_pnl

            # Restore active bots
            active_bots = get_active_paper_bots(session)
            for bot_model in active_bots:
                try:
                    strategy = strategy_registry.get(bot_model.strategy_id)
                    tf_enum = Timeframe(bot_model.timeframe.upper())
                    bot = PaperBot(
                        bot_id=bot_model.bot_id,
                        strategy=strategy,
                        instrument=bot_model.instrument,
                        timeframe=tf_enum,
                        account=self.account,
                        risk_pct=bot_model.risk_pct,
                    )
                    bot.state = BotState(bot_model.state)
                    bot.bars_seen = bot_model.bars_seen
                    bot._last_evaluated_bar = bot_model.last_evaluated_bar
                    self.bots[bot_model.bot_id] = bot
                    logger.info(
                        "Recovered bot %s: %s/%s/%s (bars_seen=%d)",
                        bot_model.bot_id,
                        bot_model.strategy_id,
                        bot_model.instrument,
                        bot_model.timeframe,
                        bot_model.bars_seen,
                    )
                except (KeyError, ValueError) as e:
                    logger.error(
                        "Failed to recover bot %s: %s", bot_model.bot_id, e
                    )
                    update_paper_bot_state(
                        session,
                        bot_model.bot_id,
                        "stopped",
                        error_message=f"Recovery failed: {e}",
                    )

        return len(self.bots)

    def _get_instruments_to_poll(self) -> set[str]:
        """Get unique instruments across all active bots."""
        return {bot.instrument for bot in self.bots.values() if bot.state != BotState.STOPPED}

    def _get_timeframes_for_instrument(self, instrument: str) -> set[str]:
        """Get unique timeframes needed for an instrument."""
        return {
            bot.timeframe.value
            for bot in self.bots.values()
            if bot.instrument == instrument and bot.state != BotState.STOPPED
        }

    def evaluate_once(self) -> dict:
        """Run one evaluation cycle. Returns summary of events."""
        instruments = self._get_instruments_to_poll()
        if not instruments:
            return {"instruments": 0, "bars_fed": 0, "events": []}

        all_events = []
        bars_fed = 0

        for instrument in instruments:
            timeframes = self._get_timeframes_for_instrument(instrument)
            for tf in timeframes:
                # Fetch latest data
                df = fetch_ohlcv(instrument, tf)
                if df is None or df.empty:
                    logger.warning("No data for %s/%s", instrument, tf)
                    continue

                # Feed each bar to relevant bots (only new bars)
                for bot in self.bots.values():
                    if (
                        bot.instrument != instrument
                        or bot.timeframe.value != tf
                        or bot.state == BotState.STOPPED
                    ):
                        continue

                    # Determine which bars are new
                    last_eval = getattr(bot, "_last_evaluated_bar", None)
                    if last_eval is not None:
                        # Filter to bars after the last evaluated one
                        new_bars = df[df["timestamp"] > last_eval]
                    else:
                        new_bars = df

                    if new_bars.empty:
                        continue

                    for _, row in new_bars.iterrows():
                        bar_time = row["timestamp"]
                        bar = row[["open", "high", "low", "close", "volume"]]
                        event = bot.on_candle_close(bar, bar_time)
                        bars_fed += 1
                        if event:
                            event["bot_id"] = bot.bot_id
                            all_events.append(event)

                    # Track last evaluated bar
                    bot._last_evaluated_bar = new_bars["timestamp"].iloc[-1]

                    # Persist bot state
                    if not self.dry_run:
                        self._persist_bot_state(bot)

                        # Persist trade if one was closed
                        for event in all_events:
                            if event.get("event") == "trade_closed" and event.get("bot_id") == bot.bot_id:
                                self._persist_trade(bot, event["trade"])

        # Persist account state
        if not self.dry_run and bars_fed > 0:
            self._persist_account()

        return {
            "instruments": len(instruments),
            "bars_fed": bars_fed,
            "events": all_events,
        }

    def _persist_bot_state(self, bot: PaperBot) -> None:
        """Save bot state to DB."""
        with self.session_factory() as session:
            update_paper_bot_state(
                session,
                bot.bot_id,
                state=bot.state.value,
                last_evaluated_bar=getattr(bot, "_last_evaluated_bar", None),
                bars_seen=bot.bars_seen,
                position_json=bot.position.to_dict() if bot.position else None,
            )

    def _persist_trade(self, bot: PaperBot, trade) -> None:
        """Save a closed trade to DB."""
        with self.session_factory() as session:
            bot_model = get_paper_bot(session, bot.bot_id)
            if not bot_model:
                return
            save_paper_trade(session, {
                "paper_bot_id": bot_model.id,
                "bot_id": bot.bot_id,
                "strategy_id": trade.strategy_id,
                "instrument": trade.instrument,
                "direction": trade.direction.value,
                "entry_time": trade.entry_time,
                "entry_price": trade.entry_price,
                "exit_time": trade.exit_time,
                "exit_price": trade.exit_price,
                "exit_reason": trade.exit_reason.value,
                "pnl": trade.pnl,
                "bars_in_trade": trade.bars_in_trade,
            })

    def _persist_account(self) -> None:
        """Save account state snapshot to DB."""
        with self.session_factory() as session:
            update_paper_account(
                session,
                balance=self.account.balance,
                equity=self.account.equity,
                daily_pnl=self.account.daily_pnl,
                weekly_pnl=self.account.weekly_pnl,
            )

    def _maybe_send_daily_summary(self) -> None:
        """Send Telegram daily summary once per UTC day."""
        if not self.notifier.is_configured:
            return
        now = datetime.now(timezone.utc)
        if self._last_daily_summary and self._last_daily_summary.date() == now.date():
            return
        # Send at any point after 21:00 UTC (or first cycle of a new day)
        if now.hour >= 21 or (
            self._last_daily_summary is not None
            and self._last_daily_summary.date() < now.date()
        ):
            status = self.account.get_status()
            self.notifier.send_daily_summary(status, self._trades_today)
            self._last_daily_summary = now
            self._trades_today = 0
            self.account.reset_daily_pnl()
            logger.info("Daily summary sent via Telegram")

    def run_loop(self, poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
        """Main worker loop. Evaluates bots at poll_interval cadence."""
        self._running = True
        logger.info(
            "Worker started (poll=%ds, dry_run=%s, bots=%d)",
            poll_interval, self.dry_run, len(self.bots),
        )

        while self._running:
            try:
                result = self.evaluate_once()
                trade_events = [
                    e for e in result["events"] if e.get("event") == "trade_closed"
                ]
                self._trades_today += len(trade_events)

                # Send Telegram alerts for trade events
                for event in result["events"]:
                    if event.get("event") == "trade_closed" and self.notifier.is_configured:
                        self.notifier.send_trade_closed(event["trade"])

                if result["bars_fed"] > 0:
                    logger.info(
                        "Cycle complete: %d instruments, %d bars fed, %d events",
                        result["instruments"],
                        result["bars_fed"],
                        len(result["events"]),
                    )

                self._maybe_send_daily_summary()
            except Exception:
                logger.exception("Error in worker cycle")

            time.sleep(poll_interval)

    def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False
        logger.info("Worker stop requested")


def _build_db_session():
    """Create engine and session factory from environment."""
    raw_url = os.environ.get("FIBOKEI_DATABASE_URL") or os.environ.get(
        "DATABASE_URL", "sqlite:///fibokei.db"
    )
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql://", 1)

    connect_args = {}
    if raw_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    engine = create_engine(raw_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def main():
    """CLI entry point for the paper trading worker."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Fiboki paper trading worker",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without persisting state changes",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Seconds between evaluation cycles (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single evaluation cycle and exit",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    session_factory = _build_db_session()
    worker = PaperWorker(session_factory, dry_run=args.dry_run)

    # Recovery
    recovered = worker.recover()
    logger.info("Recovered %d active bots", recovered)

    if args.dry_run:
        logger.info("DRY RUN — no state will be persisted")

    if args.once:
        result = worker.evaluate_once()
        print(f"Bars fed: {result['bars_fed']}, Events: {len(result['events'])}")
        for event in result["events"]:
            print(f"  {event}")
        return

    # Handle graceful shutdown
    def _handle_signal(signum, frame):
        worker.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    worker.run_loop(poll_interval=args.poll_interval)


if __name__ == "__main__":
    main()
