"""Paper trading worker — runs bots on candle-aligned schedule.

Designed to run as a separate process from the API (e.g. on Railway).
Recovers active bots from the database on startup and avoids duplicate
candle processing by tracking last_evaluated_bar per bot.

Phase 1 of the multi-broker fan-out architecture: the worker now
instantiates an :class:`ExecutionRouter` from environment variables and
hands it to every bot. In ``legacy_single`` router mode this is exactly
the pre-Phase-1 single-adapter behaviour (paper, or IG demo when
``FIBOKEI_LIVE_EXECUTION_ENABLED=true``). In ``env_global_fanout`` mode
every bot signal fans out to every enabled execution account, with each
attempt persisted as its own audit row tagged with a shared
``parent_signal_id``.
"""

import logging
import os
import signal
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    # Type-only — runtime callers either receive None or a DataFrame produced
    # by the data adapters, which already import pandas themselves.
    import pandas as pd  # noqa: F401

from fibokei.alerts.telegram import TelegramNotifier
from fibokei.core.feature_flags import FeatureFlags
from fibokei.core.models import Timeframe
from fibokei.data.ingestion import fetch_ohlcv
from fibokei.data.live_provider import is_live_available, load_live
from fibokei.db.models import Base
from fibokei.db.repository import (
    get_active_paper_bots,
    get_kill_switch,
    get_or_create_paper_account,
    get_paper_bot,
    get_paper_bots,
    save_execution_audit,
    save_paper_trade,
    update_paper_account,
    update_paper_bot_state,
)
from fibokei.execution.router_factory import build_execution_router_from_env
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


def _fetch_candles_for_monitoring(instrument: str, tf: str) -> "pd.DataFrame | None":
    """Fetch candle data for live bot monitoring.

    Priority:
    1. IG demo REST API (live_provider) — same price feed as execution,
       CFD-accurate prices, TTL-cached per timeframe. Requires IG credentials
       to be configured (FIBOKEI_IG_API_KEY / USERNAME / PASSWORD).
    2. Yahoo Finance (fetch_ohlcv) — fallback if IG is unavailable or errors.

    Both paths return a DataFrame with a flat 'timestamp' column (UTC) and
    open/high/low/close/volume columns, matching the format the worker expects.

    Note: IG provides max 200 candles per request which is sufficient for
    Ichimoku (needs ~52 bars) and all supported timeframes.
    Backtesting and research always use yfinance / canonical data — this
    function is worker-only.
    """
    import pandas as pd

    # Quota economics: IG's historical-price allowance is ~10,000 points per
    # WEEK. Polling ~18 instrument/TF combos at 200 candles per fetch burns
    # the entire allowance in minutes (observed: every /prices request → 403
    # exceeded-allowance within an hour of deploy). Until the Lightstreamer
    # streaming supervisor exists (which does not consume the allowance),
    # yfinance is the routine monitoring feed and IG prices are reserved for
    # charts/diagnostics. Set FIBOKEI_MONITOR_FEED=ig to deliberately opt
    # the worker back into IG REST polling.
    monitor_feed = os.environ.get("FIBOKEI_MONITOR_FEED", "yfinance").strip().lower()

    if monitor_feed == "ig" and is_live_available():
        try:
            df, source = load_live(instrument, tf)
            # load_live returns a DatetimeIndex; reset to flat timestamp column
            # to match the format fetch_ohlcv produces.
            df = df.reset_index()
            if "timestamp" not in df.columns and df.index.name:
                df = df.rename(columns={df.columns[0]: "timestamp"})
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            logger.debug(
                "Fetched %d bars for %s/%s via %s", len(df), instrument, tf, source
            )
            return df
        except Exception as exc:
            logger.warning(
                "IG live feed failed for %s/%s (%s) — falling back to yfinance",
                instrument, tf, exc,
            )

    return fetch_ohlcv(instrument, tf)


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
        # Build the multi-broker execution router with a kill-switch hook.
        # session_factory is passed so Phase 2 ``db_targets`` mode can read
        # ``execution_accounts`` and ``bot_execution_targets`` per signal.
        self._router = build_execution_router_from_env(
            account=self.account,
            kill_switch_check=self._kill_switch_active,
            session_factory=self.session_factory,
        )
        logger.info(
            "ExecutionRouter ready: mode=%s targets=%d",
            self._router.mode, len(self._router.targets),
        )

    # ── Kill-switch hook ────────────────────────────────────────────

    def _kill_switch_active(self) -> bool:
        """Return True if the DB-backed kill switch is currently active."""
        try:
            with self.session_factory() as session:
                ks = get_kill_switch(session)
                return bool(ks.is_active)
        except Exception:
            logger.exception("Kill-switch check failed; assuming active for safety")
            return True

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
                        router=self._router,
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

    def _sync_states_from_db(self) -> None:
        """Sync bot state changes (stop/pause/resume) from DB → memory every cycle.

        This runs at the start of every evaluate_once() call so that an API-triggered
        stop/pause/resume takes effect *before* candle processing and before
        _persist_bot_state writes the in-memory state back to DB.

        Without this, the worker overwrites an API-set "stopped" state with the
        previous in-memory "monitoring" state on the very next cycle.
        """
        if not self.bots:
            return
        with self.session_factory() as session:
            all_db_bots = {b.bot_id: b for b in get_paper_bots(session)}
            for bot_id, bot in list(self.bots.items()):
                db_bot = all_db_bots.get(bot_id)
                if db_bot is None:
                    continue  # deletion handled by sync_bots_from_db (every 5 cycles)
                if db_bot.state != bot.state.value:
                    prev = bot.state.value
                    try:
                        bot.state = BotState(db_bot.state)
                        logger.info(
                            "Bot %s state synced %s → %s (API-triggered)",
                            bot_id, prev, db_bot.state,
                        )
                    except ValueError:
                        logger.warning(
                            "Bot %s: unknown state from DB: %s", bot_id, db_bot.state
                        )

    def evaluate_once(self) -> dict:
        """Run one evaluation cycle. Returns summary of events."""
        # Sync API-controlled state changes every cycle so stop/pause/resume
        # are respected before candle processing and state persist.
        self._sync_states_from_db()

        instruments = self._get_instruments_to_poll()
        if not instruments:
            return {"instruments": 0, "bars_fed": 0, "events": []}

        all_events = []
        bars_fed = 0

        for instrument in instruments:
            timeframes = self._get_timeframes_for_instrument(instrument)
            for tf in timeframes:
                # Fetch latest data — IG live feed preferred, yfinance fallback
                df = _fetch_candles_for_monitoring(instrument, tf)
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
                        # Ensure tz-aware comparison (Yahoo data is UTC)
                        from datetime import timezone as _tz

                        le = last_eval
                        if hasattr(le, "tzinfo") and le.tzinfo is None:
                            le = le.replace(tzinfo=_tz.utc)
                        # Filter to bars after the last evaluated one
                        new_bars = df[df["timestamp"] > le]
                    else:
                        # First run for this bot — use history for indicator
                        # warmup but only evaluate the final bar as "live".
                        # Feed the last 120 bars silently, then the final bar
                        # with signal evaluation enabled.
                        warmup_count = 120
                        if len(df) > warmup_count:
                            warmup = df.iloc[-warmup_count:-1]
                            # Detach router/adapter during warmup so no real
                            # broker orders fire on historical bars. Also
                            # freeze account state so warmup trades don't
                            # corrupt balance.
                            saved_adapter = bot._adapter
                            saved_router = bot._router
                            bot._adapter = None
                            bot._router = None
                            _saved_balance = self.account.balance
                            _saved_equity = self.account.equity
                            _saved_daily_pnl = self.account.daily_pnl
                            _saved_weekly_pnl = self.account.weekly_pnl
                            for _, row in warmup.iterrows():
                                bar_time = row["timestamp"]
                                bar = row[["open", "high", "low", "close", "volume"]]
                                bot.on_candle_close(bar, bar_time)
                                bars_fed += 1
                            # Restore account state — warmup PnL must not
                            # bleed into the live balance or daily counters
                            self.account.balance = _saved_balance
                            self.account.equity = _saved_equity
                            self.account.daily_pnl = _saved_daily_pnl
                            self.account.weekly_pnl = _saved_weekly_pnl
                            # Close any position opened during warmup so
                            # the bot starts clean for live evaluation
                            if bot.position is not None:
                                bot.position = None
                                bot.state = BotState.MONITORING
                            bot._adapter = saved_adapter
                            bot._router = saved_router
                            # Only the most recent bar is "new"
                            new_bars = df.iloc[-1:]
                        else:
                            new_bars = df.iloc[-1:] if len(df) > 0 else df

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

                        # Persist trade and execution audit for events from this bot
                        for event in all_events:
                            if event.get("bot_id") != bot.bot_id:
                                continue
                            if event.get("event") == "trade_closed":
                                self._persist_trade(bot, event["trade"])
                            self._persist_execution_audit(event)

        # Persist account state
        if not self.dry_run and bars_fed > 0:
            self._persist_account()

        return {
            "instruments": len(instruments),
            "bars_fed": bars_fed,
            "events": all_events,
        }

    @staticmethod
    def _make_json_safe(obj):
        """Convert position dict to JSON-serializable types."""
        from datetime import datetime as _dt
        from enum import Enum

        def _convert(v):
            if v is None:
                return v
            if isinstance(v, Enum):
                return v.value
            if isinstance(v, _dt):
                return v.isoformat()
            if hasattr(v, "isoformat"):  # pandas Timestamp
                return v.isoformat()
            if isinstance(v, dict):
                return {k: _convert(val) for k, val in v.items()}
            if isinstance(v, (list, tuple)):
                return [_convert(i) for i in v]
            return v

        return _convert(obj)

    def _persist_bot_state(self, bot: PaperBot) -> None:
        """Save bot state to DB."""
        pos_dict = self._make_json_safe(bot.position.to_dict()) if bot.position else None
        last_eval = getattr(bot, "_last_evaluated_bar", None)
        # Convert pandas Timestamp to Python datetime for DB
        if hasattr(last_eval, "to_pydatetime"):
            last_eval = last_eval.to_pydatetime()
        with self.session_factory() as session:
            update_paper_bot_state(
                session,
                bot.bot_id,
                state=bot.state.value,
                last_evaluated_bar=last_eval,
                bars_seen=bot.bars_seen,
                position_json=pos_dict,
            )

    def _persist_trade(self, bot: PaperBot, trade) -> None:
        """Save a closed trade to DB."""
        with self.session_factory() as session:
            bot_model = get_paper_bot(session, bot.bot_id)
            if not bot_model:
                return
            # Determine is_live: was the trade entry AFTER the bot was created?
            # Warmup (historical replay) trades have entry_time < created_at.
            bot_created = bot_model.created_at
            entry_time = trade.entry_time
            if hasattr(bot_created, "tzinfo") and bot_created.tzinfo is None:
                bot_created = bot_created.replace(tzinfo=timezone.utc)
            if hasattr(entry_time, "tzinfo") and entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            is_live = entry_time >= bot_created
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
                "is_live": is_live,
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

    # ── Audit log writers ──────────────────────────────────────────

    def _persist_execution_audit(self, event: dict) -> None:
        """Write execution audit entries for a trade_opened or trade_closed event.

        Phase 3 first-class parent-child: each fan-out also writes one
        ``bot_signals`` parent row plus one ``execution_attempts`` child
        row per attempt. The legacy ``execution_audit`` table is still
        populated for back-compat with the existing UI/API.

        Phase 1 single-broker (``adapter`` only) path is unchanged.
        """
        ev_type = event.get("event", "")
        bot_id = event.get("bot_id", "")
        attempts = event.get("attempts") or event.get("close_attempts")

        try:
            if attempts:
                self._persist_parent_child(ev_type, bot_id, event, attempts)
                self._persist_attempts(ev_type, bot_id, event, attempts)
            else:
                self._persist_legacy_single(ev_type, bot_id, event)
        except Exception:
            logger.exception("Failed to persist execution audit for event %s", ev_type)

    def _persist_parent_child(
        self,
        ev_type: str,
        bot_id: str,
        event: dict,
        attempts: list[dict],
    ) -> None:
        """Write Phase 3 parent ``bot_signals`` + child ``execution_attempts`` rows.

        Idempotent across repeated retries: each call creates a fresh signal
        row, so callers must invoke this exactly once per event.
        """
        from fibokei.db.repository import (
            create_bot_signal,
            create_execution_attempt,
        )

        signal = event.get("signal")
        bot = self.bots.get(bot_id)
        instrument = (
            (bot.instrument if bot else "")
            or (attempts[0].get("instrument") if attempts else "")
        )
        timeframe = bot.timeframe.value if bot else ""
        strategy_id = (signal.strategy_id if signal else None) or (
            bot.strategy.strategy_id if bot else "unknown"
        )
        if ev_type == "trade_opened":
            kind = "open"
            direction = signal.direction.value if signal else (
                attempts[0].get("direction") if attempts else "LONG"
            )
            plan_json = {
                "entry_price": signal.proposed_entry if signal else None,
                "stop_loss": signal.stop_loss if signal else None,
            }
        else:
            kind = "close"
            trade = event.get("trade")
            direction = "CLOSE"
            plan_json = {
                "exit_reason": trade.exit_reason.value if trade else None,
                "pnl": trade.pnl if trade else None,
            }

        # Bar time / timestamp from the first attempt (all siblings share these)
        bar_time = None
        signal_ts = datetime.now(timezone.utc)

        with self.session_factory() as session:
            parent = create_bot_signal(session, {
                "bot_id": bot_id,
                "strategy_id": strategy_id,
                "instrument": instrument,
                "timeframe": timeframe,
                "direction": direction,
                "signal_timestamp": signal_ts,
                "bar_time": bar_time,
                "plan_json": plan_json,
                "kind": kind,
            })

            for attempt in attempts:
                # Map the Phase 1 status vocabulary to the Phase 3 column.
                ph1_status = attempt.get("status") or "pending"
                if ph1_status in ("filled", "paper_filled"):
                    db_status = "filled" if kind == "open" else "closed"
                elif ph1_status == "rejected":
                    db_status = "rejected"
                elif ph1_status == "skipped":
                    db_status = "skipped"
                elif ph1_status == "error":
                    db_status = "failed"
                else:
                    db_status = "pending"

                target_id = attempt.get("target_id")
                # ``target_id`` from the router is currently a stable string
                # (e.g. ``acct-3``); only the integer DB ids map to FK rows.
                exec_target_id = None
                exec_account_id = None
                if isinstance(target_id, str) and target_id.startswith("acct-"):
                    try:
                        exec_account_id = int(target_id[5:])
                    except (TypeError, ValueError):
                        exec_account_id = None

                create_execution_attempt(session, {
                    "bot_signal_id": parent.id,
                    "execution_target_id": exec_target_id,
                    "execution_account_id": exec_account_id,
                    "broker": attempt.get("broker") or "paper",
                    "environment": attempt.get("environment") or "paper",
                    "broker_account_id": None,
                    "instrument": attempt.get("instrument") or instrument,
                    "broker_symbol": attempt.get("broker_symbol"),
                    "direction": attempt.get("direction"),
                    "requested_size": attempt.get("requested_size"),
                    "adjusted_size": attempt.get("adjusted_size"),
                    "filled_size": attempt.get("filled_size"),
                    "requested_price": attempt.get("requested_price"),
                    "filled_price": attempt.get("filled_price"),
                    "status": db_status,
                    "broker_order_id": attempt.get("broker_order_id"),
                    "broker_deal_id": attempt.get("broker_deal_id"),
                    "broker_fill_id": None,
                    "rejection_reason": attempt.get("rejection_reason"),
                    "error_code": attempt.get("error_code"),
                    "latency_ms": attempt.get("latency_ms"),
                    "slippage_pips": attempt.get("slippage_pips"),
                    "detail_json": attempt.get("extra") or None,
                })

    def _persist_attempts(
        self,
        ev_type: str,
        bot_id: str,
        event: dict,
        attempts: list[dict],
    ) -> None:
        """Persist one audit row per child attempt (router fan-out path)."""
        action = "place_order" if ev_type == "trade_opened" else "close_position"
        with self.session_factory() as session:
            for attempt in attempts:
                broker = attempt.get("broker") or "paper"
                env = attempt.get("environment") or "paper"
                # Map (broker, env) → existing execution_mode vocabulary so
                # the legacy /execution/audit?execution_mode=... filter
                # still works.
                if broker == "paper":
                    mode = "paper"
                elif broker == "ig":
                    mode = "ig_demo" if env == "demo" else "ig_live"
                elif broker == "tradovate":
                    mode = "tradovate_demo" if env == "demo" else "tradovate_live"
                else:
                    mode = f"{broker}_{env}"
                status_norm = attempt.get("status") or "unknown"
                # Compress paper_filled → success for the existing column
                # vocabulary (success/failed/rejected/paper_only).
                if status_norm in ("filled", "paper_filled"):
                    audit_status = "success"
                elif status_norm == "rejected":
                    audit_status = "rejected"
                elif status_norm in ("skipped", "error"):
                    audit_status = "failed"
                else:
                    audit_status = "unknown"

                error_msg = attempt.get("rejection_reason")
                error_code = attempt.get("error_code")
                if error_msg and error_code:
                    error_message = f"{error_code}: {error_msg}"
                elif error_msg:
                    error_message = error_msg
                elif error_code:
                    error_message = error_code
                else:
                    error_message = None

                detail = {
                    "parent_signal_id": attempt.get("parent_signal_id"),
                    "target_id": attempt.get("target_id"),
                    "target_name": attempt.get("target_name"),
                    "broker": broker,
                    "environment": env,
                    "broker_symbol": attempt.get("broker_symbol"),
                    "account_capital": attempt.get("account_capital"),
                    "risk_pct": attempt.get("risk_pct"),
                    "rejection_reason": attempt.get("rejection_reason"),
                    "error_code": attempt.get("error_code"),
                    "extra": attempt.get("extra") or {},
                    # Backwards-compat keys for the existing UI
                    "broker_reason": attempt.get("rejection_reason"),
                    "broker_error_code": attempt.get("error_code"),
                }
                if ev_type == "trade_closed":
                    trade = event.get("trade")
                    if trade is not None:
                        detail["exit_reason"] = trade.exit_reason.value
                        detail["pnl"] = trade.pnl
                        detail["bars_in_trade"] = trade.bars_in_trade

                save_execution_audit(session, {
                    "execution_mode": mode,
                    "action": action,
                    "instrument": attempt.get("instrument") or "",
                    "direction": attempt.get("direction"),
                    "size": attempt.get("filled_size") or attempt.get("requested_size"),
                    "deal_id": attempt.get("broker_deal_id") or "",
                    "status": audit_status,
                    "detail_json": detail,
                    "error_message": error_message,
                    "bot_id": bot_id,
                    "requested_price": attempt.get("requested_price"),
                    "filled_price": attempt.get("filled_price"),
                    "slippage_pips": attempt.get("slippage_pips"),
                    "fill_latency_ms": attempt.get("latency_ms"),
                })

    def _persist_legacy_single(self, ev_type: str, bot_id: str, event: dict) -> None:
        """Single-broker audit path — preserves the pre-Phase-1 row shape."""
        execution_mode = FeatureFlags().execution_mode
        with self.session_factory() as session:
            if ev_type == "trade_opened":
                signal = event.get("signal")
                deal_id = event.get("deal_id")
                ig_reason = event.get("ig_reason", "")
                ig_error_code = event.get("ig_error_code", "")
                bot = self.bots.get(bot_id)
                instrument = bot.instrument if bot else ""
                direction = signal.direction.value if signal else ""
                if deal_id:
                    error_msg = None
                elif ig_error_code:
                    error_msg = f"IG rejected: {ig_reason} (error_code={ig_error_code})"
                elif ig_reason:
                    error_msg = f"IG rejected: {ig_reason}"
                else:
                    error_msg = "No IG deal placed (adapter rejected or paper mode)"
                save_execution_audit(session, {
                    "execution_mode": execution_mode,
                    "action": "place_order",
                    "instrument": instrument,
                    "direction": direction,
                    "bot_id": bot_id,
                    "deal_id": deal_id or "",
                    "status": "success" if deal_id else "paper_only",
                    "detail_json": {
                        "strategy_id": signal.strategy_id if signal else "",
                        "entry_price": signal.proposed_entry if signal else None,
                        "stop_loss": signal.stop_loss if signal else None,
                        "deal_id": deal_id,
                        "ig_reason": ig_reason,
                        "ig_error_code": ig_error_code,
                    },
                    "error_message": error_msg,
                })
            elif ev_type == "trade_closed":
                trade = event.get("trade")
                closed_deal_id = event.get("closed_deal_id", "")
                save_execution_audit(session, {
                    "execution_mode": execution_mode,
                    "action": "close_position",
                    "instrument": trade.instrument if trade else "",
                    "direction": trade.direction.value if trade else "",
                    "bot_id": bot_id,
                    "deal_id": closed_deal_id,
                    "status": "success" if closed_deal_id else "paper_only",
                    "detail_json": {
                        "exit_reason": trade.exit_reason.value if trade else "",
                        "pnl": trade.pnl if trade else None,
                        "bars_in_trade": trade.bars_in_trade if trade else None,
                    },
                    "error_message": (
                        None if closed_deal_id
                        else "No IG position closed (opened as paper_only)"
                    ),
                })

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

    def sync_bots_from_db(self) -> int:
        """Pick up newly created bots from DB that aren't in memory yet.
        Returns count of new bots added."""
        added = 0
        with self.session_factory() as session:
            active_bots = get_active_paper_bots(session)
            for bot_model in active_bots:
                if bot_model.bot_id in self.bots:
                    continue  # already tracked
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
                        router=self._router,
                    )
                    bot.state = BotState(bot_model.state)
                    bot.bars_seen = bot_model.bars_seen
                    bot._last_evaluated_bar = bot_model.last_evaluated_bar
                    self.bots[bot_model.bot_id] = bot
                    added += 1
                    logger.info(
                        "Picked up new bot %s: %s/%s/%s",
                        bot_model.bot_id,
                        bot_model.strategy_id,
                        bot_model.instrument,
                        bot_model.timeframe,
                    )
                except (KeyError, ValueError) as e:
                    logger.error("Failed to load bot %s: %s", bot_model.bot_id, e)

            # Sync state changes (pause/stop/resume) from DB → memory
            all_db_bots = {b.bot_id: b for b in get_paper_bots(session)}
            for bot_id, bot in list(self.bots.items()):
                db_bot = all_db_bots.get(bot_id)
                if db_bot is None:
                    # Deleted from DB — remove from memory
                    del self.bots[bot_id]
                    logger.info("Removed deleted bot %s from worker", bot_id)
                elif db_bot.state != bot.state.value:
                    # State changed via API (pause/stop/resume)
                    try:
                        bot.state = BotState(db_bot.state)
                        logger.info("Bot %s state synced to %s", bot_id, db_bot.state)
                    except ValueError:
                        pass

        return added

    def run_loop(self, poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
        """Main worker loop. Evaluates bots at poll_interval cadence."""
        self._running = True
        logger.info(
            "Worker started (poll=%ds, dry_run=%s, bots=%d)",
            poll_interval, self.dry_run, len(self.bots),
        )

        cycles_since_sync = 0
        SYNC_EVERY = 5  # Re-sync bots from DB every N cycles

        # Heartbeat identity: stable per service, distinct per process kind.
        import socket
        worker_id = os.environ.get("FIBOKEI_WORKER_ID", "railway-worker")
        hostname = socket.gethostname()
        loops_completed = 0

        while self._running:
            loop_error: str | None = None
            try:
                # Periodically pick up new/changed bots from DB
                cycles_since_sync += 1
                if cycles_since_sync >= SYNC_EVERY:
                    new = self.sync_bots_from_db()
                    if new > 0:
                        logger.info("Synced %d new bots from DB", new)
                    cycles_since_sync = 0

                result = self.evaluate_once()
                trade_events = [
                    e for e in result["events"] if e.get("event") == "trade_closed"
                ]
                self._trades_today += len(trade_events)

                # Send Telegram alerts for trade events
                for event in result["events"]:
                    ev_type = event.get("event")
                    if not self.notifier.is_configured:
                        continue
                    if ev_type == "trade_closed":
                        self.notifier.send_trade_closed(event["trade"])
                    elif ev_type == "trade_opened":
                        signal = event.get("signal")
                        if signal is not None:
                            self.notifier.send_trade_opened(
                                signal=signal,
                                bot_id=event.get("bot_id", ""),
                                deal_id=event.get("deal_id", "") or "",
                            )

                if result["bars_fed"] > 0:
                    logger.info(
                        "Cycle complete: %d instruments, %d bars fed, %d events",
                        result["instruments"],
                        result["bars_fed"],
                        len(result["events"]),
                    )

                self._maybe_send_daily_summary()
            except Exception as exc:
                loop_error = str(exc)[:500]
                logger.exception("Error in worker cycle")

            # Heartbeat: visible via /system/status without Railway access.
            loops_completed += 1
            try:
                from fibokei.db.repository import beat_worker_heartbeat
                with self.session_factory() as hb_session:
                    beat_worker_heartbeat(
                        hb_session,
                        worker_id=worker_id,
                        hostname=hostname,
                        poll_interval_s=poll_interval,
                        bots_active=len(self.bots),
                        loops_completed=loops_completed,
                        last_error=loop_error,
                    )
            except Exception:
                logger.exception("Failed to write worker heartbeat")

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
