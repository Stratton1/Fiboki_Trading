"""Portfolio-aware risk engine."""

from __future__ import annotations

import math

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
        # Fleet-level limits
        fleet_max_bots_per_instrument: int = 5,
        fleet_max_total_positions: int = 20,
        fleet_max_exposure_per_instrument: int = 6,
        fleet_correlation_threshold: float = 0.85,
        fleet_cull_sigma: float = 2.0,
        fleet_cull_min_trades: int = 50,
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
        # Fleet-level limits
        self.fleet_max_bots_per_instrument = fleet_max_bots_per_instrument
        self.fleet_max_total_positions = fleet_max_total_positions
        self.fleet_max_exposure_per_instrument = fleet_max_exposure_per_instrument
        self.fleet_correlation_threshold = fleet_correlation_threshold
        self.fleet_cull_sigma = fleet_cull_sigma
        self.fleet_cull_min_trades = fleet_cull_min_trades

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

    # ── Fleet-level checks ──────────────────────────────────

    def check_fleet_trade_allowed(
        self,
        instrument: str,
        fleet_positions: list[dict],
    ) -> tuple[bool, str]:
        """Check if a new trade is allowed under fleet-level limits.

        fleet_positions: list of dicts with keys 'instrument', 'direction', 'bot_id'.
        Returns (allowed, rejection_reason).
        """
        # Total open positions across fleet
        if len(fleet_positions) >= self.fleet_max_total_positions:
            return False, f"Fleet max total positions reached ({self.fleet_max_total_positions})"

        # Bots per instrument
        bots_on_instrument = sum(
            1 for p in fleet_positions if p.get("instrument") == instrument
        )
        if bots_on_instrument >= self.fleet_max_bots_per_instrument:
            return False, f"Fleet max bots per instrument reached ({self.fleet_max_bots_per_instrument})"

        # Aggregate exposure per instrument (long + short)
        if bots_on_instrument >= self.fleet_max_exposure_per_instrument:
            return False, f"Fleet max exposure per instrument reached ({self.fleet_max_exposure_per_instrument})"

        return True, ""

    @staticmethod
    def compute_trade_overlap(
        trades_a: list[tuple[str, str]],
        trades_b: list[tuple[str, str]],
    ) -> float:
        """Compute trade overlap between two bots.

        Each trade is (entry_time_iso, exit_time_iso).
        Returns Jaccard similarity: |A ∩ B| / |A ∪ B|.
        Overlap is defined by matching entry timestamps.
        """
        if not trades_a or not trades_b:
            return 0.0
        entries_a = {t[0] for t in trades_a}
        entries_b = {t[0] for t in trades_b}
        intersection = entries_a & entries_b
        union = entries_a | entries_b
        if not union:
            return 0.0
        return len(intersection) / len(union)

    def find_correlated_bots(
        self,
        bot_trades: dict[str, list[tuple[str, str]]],
    ) -> list[dict]:
        """Find pairs of bots with trade overlap above the correlation threshold.

        bot_trades: {bot_id: [(entry_time, exit_time), ...]}
        Returns list of {bot_a, bot_b, overlap} dicts.
        """
        bot_ids = list(bot_trades.keys())
        alerts: list[dict] = []
        for i, bot_a in enumerate(bot_ids):
            for bot_b in bot_ids[i + 1:]:
                overlap = self.compute_trade_overlap(
                    bot_trades[bot_a], bot_trades[bot_b]
                )
                if overlap >= self.fleet_correlation_threshold:
                    alerts.append({
                        "bot_a": bot_a,
                        "bot_b": bot_b,
                        "overlap": round(overlap, 3),
                    })
        return alerts

    def find_underperformers(
        self,
        bot_pnls: dict[str, list[float]],
    ) -> list[dict]:
        """Identify bots whose performance is >N sigma below fleet median.

        bot_pnls: {bot_id: [pnl_per_trade, ...]}
        Returns list of {bot_id, avg_pnl, fleet_median, fleet_std, sigma_below}.
        """
        # Only consider bots with enough trades
        eligible = {
            bid: pnls for bid, pnls in bot_pnls.items()
            if len(pnls) >= self.fleet_cull_min_trades
        }
        if len(eligible) < 2:
            return []

        # Compute per-bot average PnL
        bot_avgs = {bid: sum(pnls) / len(pnls) for bid, pnls in eligible.items()}
        avg_values = sorted(bot_avgs.values())

        # Fleet median
        n = len(avg_values)
        if n % 2 == 1:
            fleet_median = avg_values[n // 2]
        else:
            fleet_median = (avg_values[n // 2 - 1] + avg_values[n // 2]) / 2

        # Fleet standard deviation
        fleet_mean = sum(avg_values) / n
        variance = sum((v - fleet_mean) ** 2 for v in avg_values) / n
        fleet_std = math.sqrt(variance) if variance > 0 else 0.0

        if fleet_std == 0:
            return []

        underperformers: list[dict] = []
        for bid, avg in bot_avgs.items():
            sigma_below = (fleet_median - avg) / fleet_std
            if sigma_below >= self.fleet_cull_sigma:
                underperformers.append({
                    "bot_id": bid,
                    "avg_pnl": round(avg, 4),
                    "fleet_median": round(fleet_median, 4),
                    "fleet_std": round(fleet_std, 4),
                    "sigma_below": round(sigma_below, 2),
                })
        return underperformers
