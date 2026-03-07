"""Composite scoring for strategy-instrument-timeframe combinations."""

import numpy as np
from pydantic import BaseModel, Field


class ScoringConfig(BaseModel):
    """Configurable weights for composite score components."""

    weight_risk_adjusted: float = Field(default=0.25, description="Sharpe ratio weight")
    weight_profit_factor: float = Field(default=0.20, description="Profit factor weight")
    weight_return: float = Field(default=0.20, description="Normalized return weight")
    weight_drawdown: float = Field(default=0.15, description="Drawdown control weight")
    weight_sample: float = Field(default=0.10, description="Sample sufficiency weight")
    weight_stability: float = Field(default=0.10, description="Equity curve stability weight")
    sharpe_cap: float = 3.0
    profit_factor_cap: float = 5.0
    return_cap: float = 1.0  # 100% return cap
    drawdown_cap: float = 30.0  # Max DD% before score = 0
    min_trades_full: int = 80


def _score_risk_adjusted(metrics: dict, config: ScoringConfig) -> float:
    """Normalized Sharpe ratio score (0-1)."""
    sharpe = metrics.get("sharpe_ratio", 0.0)
    if sharpe is None or np.isnan(sharpe) or np.isinf(sharpe):
        return 0.0
    return max(0.0, min(sharpe / config.sharpe_cap, 1.0))


def _score_profit_factor(metrics: dict, config: ScoringConfig) -> float:
    """Profit factor score (0-1)."""
    pf = metrics.get("profit_factor", 0.0)
    if pf is None or np.isnan(pf) or np.isinf(pf):
        pf = config.profit_factor_cap
    return max(0.0, min(pf / config.profit_factor_cap, 1.0))


def _score_return(metrics: dict, config: ScoringConfig) -> float:
    """Normalized return score (0-1)."""
    net = metrics.get("total_net_profit", 0.0)
    capital = metrics.get("initial_capital", 10000.0)
    if capital <= 0:
        return 0.0
    normalized = net / capital
    return max(0.0, min(normalized / config.return_cap, 1.0))


def _score_drawdown(metrics: dict, config: ScoringConfig) -> float:
    """Drawdown control score (0-1). Lower DD = higher score."""
    dd_pct = metrics.get("max_drawdown_pct", 0.0)
    if dd_pct is None or np.isnan(dd_pct):
        dd_pct = 0.0
    return max(0.0, 1.0 - min(dd_pct / config.drawdown_cap, 1.0))


def _score_sample(metrics: dict, config: ScoringConfig) -> float:
    """Sample sufficiency score (0-1)."""
    trades = metrics.get("total_trades", 0)
    return min(trades / config.min_trades_full, 1.0)


def _score_stability(metrics: dict, config: ScoringConfig) -> float:
    """Equity curve stability via R-squared of linear fit."""
    equity_curve = metrics.get("equity_curve")
    if equity_curve is None or len(equity_curve) < 2:
        return 0.0

    y = np.array(equity_curve, dtype=float)
    x = np.arange(len(y))

    # Linear regression R²
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    if ss_tot < 1e-10:
        return 1.0  # Perfectly flat = stable

    coeffs = np.polyfit(x, y, 1)
    y_pred = np.polyval(coeffs, x)
    ss_res = np.sum((y - y_pred) ** 2)
    r_squared = 1.0 - (ss_res / ss_tot)
    return max(0.0, min(r_squared, 1.0))


def compute_composite_score(
    metrics: dict, config: ScoringConfig | None = None
) -> float:
    """Compute weighted composite score from backtest metrics.

    Returns a score between 0.0 and 1.0.
    """
    if config is None:
        config = ScoringConfig()

    components = {
        "risk_adjusted": (_score_risk_adjusted(metrics, config), config.weight_risk_adjusted),
        "profit_factor": (_score_profit_factor(metrics, config), config.weight_profit_factor),
        "return": (_score_return(metrics, config), config.weight_return),
        "drawdown": (_score_drawdown(metrics, config), config.weight_drawdown),
        "sample": (_score_sample(metrics, config), config.weight_sample),
        "stability": (_score_stability(metrics, config), config.weight_stability),
    }

    total_weight = sum(w for _, w in components.values())
    if total_weight < 1e-10:
        return 0.0

    score = sum(s * w for s, w in components.values()) / total_weight
    return round(score, 4)
