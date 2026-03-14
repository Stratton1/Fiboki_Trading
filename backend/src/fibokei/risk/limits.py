"""Configurable risk limits — loaded from environment variables."""

import os


def _float(var: str, default: float) -> float:
    return float(os.environ.get(var, str(default)))


def _int(var: str, default: int) -> int:
    return int(os.environ.get(var, str(default)))


def get_risk_limits() -> dict:
    """Return current risk limit configuration from env vars."""
    return {
        "max_risk_per_trade_pct": _float("FIBOKEI_MAX_RISK_PER_TRADE_PCT", 1.0),
        "max_portfolio_risk_pct": _float("FIBOKEI_MAX_PORTFOLIO_RISK_PCT", 5.0),
        "max_open_trades": _int("FIBOKEI_MAX_OPEN_TRADES", 8),
        "max_per_instrument": _int("FIBOKEI_MAX_PER_INSTRUMENT", 2),
        "max_correlated_group_pct": _float("FIBOKEI_MAX_CORRELATED_GROUP_PCT", 2.5),
        "daily_soft_stop_pct": _float("FIBOKEI_DAILY_SOFT_STOP_PCT", 3.0),
        "daily_hard_stop_pct": _float("FIBOKEI_DAILY_HARD_STOP_PCT", 4.0),
        "weekly_soft_stop_pct": _float("FIBOKEI_WEEKLY_SOFT_STOP_PCT", 6.0),
        "weekly_hard_stop_pct": _float("FIBOKEI_WEEKLY_HARD_STOP_PCT", 8.0),
        # Fleet-level limits
        "fleet_max_bots_per_instrument": _int("FIBOKEI_FLEET_MAX_BOTS_PER_INSTRUMENT", 5),
        "fleet_max_total_positions": _int("FIBOKEI_FLEET_MAX_TOTAL_POSITIONS", 20),
        "fleet_max_exposure_per_instrument": _int("FIBOKEI_FLEET_MAX_EXPOSURE_PER_INSTRUMENT", 6),
        "fleet_correlation_threshold": _float("FIBOKEI_FLEET_CORRELATION_THRESHOLD", 0.85),
        "fleet_cull_sigma": _float("FIBOKEI_FLEET_CULL_SIGMA", 2.0),
        "fleet_cull_min_trades": _int("FIBOKEI_FLEET_CULL_MIN_TRADES", 50),
    }


def create_risk_engine():
    """Create a RiskEngine with limits from environment."""
    from fibokei.risk.engine import RiskEngine
    return RiskEngine(**get_risk_limits())
