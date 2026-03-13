"""Paper trading account."""

import os

from fibokei.core.trades import TradeResult

# Configurable via env var — defaults to £1,000
DEFAULT_INITIAL_BALANCE = float(os.environ.get("FIBOKEI_PAPER_INITIAL_BALANCE", "1000.0"))
DEFAULT_CURRENCY = os.environ.get("FIBOKEI_PAPER_CURRENCY", "GBP")


class PaperAccount:
    """Virtual trading account for paper trading."""

    def __init__(self, initial_balance: float = DEFAULT_INITIAL_BALANCE):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.open_positions: list[dict] = []
        self.closed_trades: list[TradeResult] = []
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0

    def update_equity(self) -> float:
        """Recalculate equity from balance + unrealised PnL."""
        unrealised = sum(p.get("unrealised_pnl", 0.0) for p in self.open_positions)
        self.equity = self.balance + unrealised
        return self.equity

    def record_trade(self, trade: TradeResult) -> None:
        """Record a closed trade and update balance."""
        self.balance += trade.pnl
        self.daily_pnl += trade.pnl
        self.weekly_pnl += trade.pnl
        self.closed_trades.append(trade)
        self.update_equity()

    def deposit(self, amount: float) -> None:
        """Add funds to account."""
        self.balance += amount
        self.initial_balance += amount
        self.update_equity()

    def reset(self) -> None:
        """Reset account to initial state."""
        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.open_positions.clear()
        self.closed_trades.clear()
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0

    def reset_daily_pnl(self) -> None:
        """Reset daily PnL counter (call at start of day)."""
        self.daily_pnl = 0.0

    def reset_weekly_pnl(self) -> None:
        """Reset weekly PnL counter (call at start of week)."""
        self.weekly_pnl = 0.0

    def get_status(self) -> dict:
        """Return account summary."""
        return {
            "balance": self.balance,
            "equity": self.equity,
            "initial_balance": self.initial_balance,
            "total_pnl": self.balance - self.initial_balance,
            "total_pnl_pct": (self.balance - self.initial_balance) / self.initial_balance * 100,
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "open_positions": len(self.open_positions),
            "total_trades": len(self.closed_trades),
        }
