"""Tests for research composite scoring."""

import pytest

from fibokei.research.scorer import (
    ScoringConfig,
    _score_drawdown,
    _score_profit_factor,
    _score_return,
    _score_risk_adjusted,
    _score_sample,
    _score_stability,
    compute_composite_score,
)


class TestScoreComponents:
    def test_risk_adjusted_zero_sharpe(self):
        assert _score_risk_adjusted({"sharpe_ratio": 0.0}, ScoringConfig()) == 0.0

    def test_risk_adjusted_positive_sharpe(self):
        score = _score_risk_adjusted({"sharpe_ratio": 1.5}, ScoringConfig())
        assert score == pytest.approx(0.5)

    def test_risk_adjusted_capped(self):
        score = _score_risk_adjusted({"sharpe_ratio": 5.0}, ScoringConfig())
        assert score == pytest.approx(1.0)

    def test_profit_factor_normal(self):
        score = _score_profit_factor({"profit_factor": 2.5}, ScoringConfig())
        assert score == pytest.approx(0.5)

    def test_profit_factor_inf(self):
        score = _score_profit_factor({"profit_factor": float("inf")}, ScoringConfig())
        assert score == pytest.approx(1.0)

    def test_return_normal(self):
        # 5000 profit on 10000 capital = 50% return, capped at 100%
        score = _score_return(
            {"total_net_profit": 5000.0, "initial_capital": 10000.0},
            ScoringConfig(),
        )
        assert score == pytest.approx(0.5)

    def test_return_negative(self):
        score = _score_return(
            {"total_net_profit": -1000.0, "initial_capital": 10000.0},
            ScoringConfig(),
        )
        assert score == 0.0

    def test_drawdown_none(self):
        score = _score_drawdown({"max_drawdown_pct": 0.0}, ScoringConfig())
        assert score == pytest.approx(1.0)

    def test_drawdown_moderate(self):
        score = _score_drawdown({"max_drawdown_pct": 15.0}, ScoringConfig())
        assert score == pytest.approx(0.5)

    def test_drawdown_extreme(self):
        score = _score_drawdown({"max_drawdown_pct": 30.0}, ScoringConfig())
        assert score == pytest.approx(0.0)

    def test_sample_full(self):
        score = _score_sample({"total_trades": 80}, ScoringConfig())
        assert score == pytest.approx(1.0)

    def test_sample_partial(self):
        score = _score_sample({"total_trades": 40}, ScoringConfig())
        assert score == pytest.approx(0.5)

    def test_stability_flat_curve(self):
        score = _score_stability({"equity_curve": [10000.0] * 50}, ScoringConfig())
        assert score == pytest.approx(1.0)

    def test_stability_linear_curve(self):
        curve = [10000 + i * 10 for i in range(100)]
        score = _score_stability({"equity_curve": curve}, ScoringConfig())
        assert score > 0.99  # Near-perfect R²

    def test_stability_no_curve(self):
        score = _score_stability({}, ScoringConfig())
        assert score == 0.0


class TestCompositeScore:
    def test_known_metrics(self):
        metrics = {
            "sharpe_ratio": 1.5,
            "profit_factor": 2.5,
            "total_net_profit": 5000.0,
            "initial_capital": 10000.0,
            "max_drawdown_pct": 10.0,
            "total_trades": 80,
            "equity_curve": [10000 + i * 50 for i in range(100)],
        }
        score = compute_composite_score(metrics)
        assert 0.0 <= score <= 1.0
        assert score > 0.4  # Should be a decent score

    def test_zero_trades(self):
        metrics = {
            "sharpe_ratio": 0.0,
            "profit_factor": 0.0,
            "total_net_profit": 0.0,
            "max_drawdown_pct": 0.0,
            "total_trades": 0,
        }
        score = compute_composite_score(metrics)
        # Drawdown score = 1.0 (no drawdown), but everything else is 0
        assert 0.0 <= score <= 0.3

    def test_custom_weights(self):
        config = ScoringConfig(
            weight_risk_adjusted=1.0,
            weight_profit_factor=0.0,
            weight_return=0.0,
            weight_drawdown=0.0,
            weight_sample=0.0,
            weight_stability=0.0,
        )
        metrics = {"sharpe_ratio": 1.5}
        score = compute_composite_score(metrics, config)
        assert score == pytest.approx(0.5)

    def test_score_between_0_and_1(self):
        metrics = {
            "sharpe_ratio": 10.0,
            "profit_factor": 100.0,
            "total_net_profit": 50000.0,
            "initial_capital": 10000.0,
            "max_drawdown_pct": 0.0,
            "total_trades": 200,
            "equity_curve": [10000 + i * 500 for i in range(100)],
        }
        score = compute_composite_score(metrics)
        assert 0.0 <= score <= 1.0
