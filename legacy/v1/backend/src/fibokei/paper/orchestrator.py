"""Bot orchestrator — manages multiple paper trading bots."""

import uuid

import pandas as pd

from fibokei.core.models import Timeframe
from fibokei.paper.account import PaperAccount
from fibokei.paper.bot import PaperBot
from fibokei.risk.engine import RiskEngine
from fibokei.strategies.registry import strategy_registry


class BotOrchestrator:
    """Manages multiple PaperBot instances with shared account and risk."""

    def __init__(
        self,
        account: PaperAccount | None = None,
        risk_engine: RiskEngine | None = None,
    ):
        self.account = account or PaperAccount()
        self.risk_engine = risk_engine or RiskEngine()
        self.bots: dict[str, PaperBot] = {}

    def add_bot(
        self,
        strategy_id: str,
        instrument: str,
        timeframe: str,
        risk_pct: float = 1.0,
    ) -> str:
        """Create and register a new paper bot. Returns bot_id."""
        strategy = strategy_registry.get(strategy_id)
        tf_enum = Timeframe(timeframe.upper())
        bot_id = str(uuid.uuid4())[:8]
        bot = PaperBot(
            bot_id=bot_id,
            strategy=strategy,
            instrument=instrument,
            timeframe=tf_enum,
            account=self.account,
            risk_pct=risk_pct,
        )
        self.bots[bot_id] = bot
        return bot_id

    def remove_bot(self, bot_id: str) -> None:
        """Stop and remove a bot."""
        if bot_id in self.bots:
            self.bots[bot_id].stop()
            del self.bots[bot_id]

    def start_all(self) -> None:
        """Start all bots."""
        for bot in self.bots.values():
            bot.start()

    def stop_all(self) -> None:
        """Stop all bots."""
        for bot in self.bots.values():
            bot.stop()

    def get_bot(self, bot_id: str) -> PaperBot | None:
        """Get a bot by ID."""
        return self.bots.get(bot_id)

    def get_all_status(self) -> list[dict]:
        """Get status of all bots."""
        return [bot.get_status() for bot in self.bots.values()]

    def on_tick(self, instrument: str, bar: pd.Series, bar_time) -> list[dict]:
        """Route a new bar to relevant bots. Returns list of events."""
        events = []
        for bot in self.bots.values():
            if bot.instrument == instrument:
                event = bot.on_candle_close(bar, bar_time)
                if event:
                    events.append({"bot_id": bot.bot_id, **event})
        return events
