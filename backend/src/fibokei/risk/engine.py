"""Portfolio-aware risk engine."""

from fibokei.core.signals import Signal
from fibokei.paper.account import PaperAccount


class RiskEngine:
    """Enforces portfolio-level risk constraints."""

    def __init__(
        self,
        max_risk_per_trade_pct: float = 1.0,
        max_portfolio_risk_pct: float = 5.0,
        max_open_trades: int = 8,
        max_per_instrument: int = 2,
        max_correlated_group_pct: float = 2.5,
        daily_soft_stop_pct: float = 3.0,
        daily_hard_stop_pct: float = 4.0,
        weekly_soft_stop_pct: float = 6.0,
        weekly_hard_stop_pct: float = 8.0,
    ):
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_portfolio_risk_pct = max_portfolio_risk_pct
        self.max_open_trades = max_open_trades
        self.max_per_instrument = max_per_instrument
        self.max_correlated_group_pct = max_correlated_group_pct
        self.daily_soft_stop_pct = daily_soft_stop_pct
        self.daily_hard_stop_pct = daily_hard_stop_pct
        self.weekly_soft_stop_pct = weekly_soft_stop_pct
        self.weekly_hard_stop_pct = weekly_hard_stop_pct

    def check_trade_allowed(
        self,
        signal: Signal,
        account: PaperAccount,
        portfolio_state: dict | None = None,
    ) -> tuple[bool, str]:
        """Check if a new trade is allowed under risk limits.

        Returns (allowed, rejection_reason).
        """
        portfolio_state = portfolio_state or {}

        # Max open trades
        if len(account.open_positions) >= self.max_open_trades:
            return False, f"Max open trades reached ({self.max_open_trades})"

        # Per-instrument limit
        inst_count = sum(
            1 for p in account.open_positions
            if p.get("instrument") == signal.instrument
        )
        if inst_count >= self.max_per_instrument:
            return False, f"Max trades per instrument reached ({self.max_per_instrument})"

        # Per-trade risk check
        if signal.stop_loss and signal.proposed_entry:
            risk_per_unit = abs(signal.proposed_entry - signal.stop_loss)
            if risk_per_unit > 0:
                risk_pct = (risk_per_unit / signal.proposed_entry) * 100
                if risk_pct > self.max_risk_per_trade_pct * 5:
                    return False, f"Trade risk too high ({risk_pct:.1f}%)"

        # Drawdown limits
        safe, alert = self.check_drawdown_limits(account)
        if not safe:
            return False, f"Drawdown limit breached: {alert}"

        return True, ""

    def check_drawdown_limits(
        self, account: PaperAccount
    ) -> tuple[bool, str]:
        """Check if drawdown limits are breached.

        Returns (safe, alert_level).
        """
        if account.initial_balance <= 0:
            return True, ""

        daily_dd_pct = abs(min(account.daily_pnl, 0.0)) / account.initial_balance * 100
        weekly_dd_pct = abs(min(account.weekly_pnl, 0.0)) / account.initial_balance * 100

        if daily_dd_pct >= self.daily_hard_stop_pct:
            return False, "daily_hard_stop"

        if weekly_dd_pct >= self.weekly_hard_stop_pct:
            return False, "weekly_hard_stop"

        if daily_dd_pct >= self.daily_soft_stop_pct:
            return True, "daily_soft_stop"

        if weekly_dd_pct >= self.weekly_soft_stop_pct:
            return True, "weekly_soft_stop"

        return True, ""
