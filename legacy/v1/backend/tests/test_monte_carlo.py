"""Tests for Monte Carlo robustness analysis."""

import numpy as np
import pytest

from fibokei.research.monte_carlo import MonteCarloResult, run_monte_carlo


class TestMonteCarloResult:
    """Test MonteCarloResult dataclass."""

    def test_dataclass_fields(self):
        r = MonteCarloResult(
            strategy_id="s1", instrument="EURUSD", timeframe="H1",
            num_simulations=100, num_trades=50,
            original_net_profit=500.0, original_max_drawdown_pct=10.0,
        )
        assert r.strategy_id == "s1"
        assert r.robust is False
        assert r.status == "ok"


class TestRunMonteCarlo:
    """Test run_monte_carlo function."""

    def test_deterministic_with_seed(self):
        pnls = [10.0, -5.0, 20.0, -3.0, 15.0, -8.0, 7.0, 12.0, -2.0, 6.0]
        r1 = run_monte_carlo(pnls, "s1", "EURUSD", "H1", seed=42)
        r2 = run_monte_carlo(pnls, "s1", "EURUSD", "H1", seed=42)
        assert r1.mean_net_profit == r2.mean_net_profit
        assert r1.p5_net_profit == r2.p5_net_profit
        assert r1.p95_net_profit == r2.p95_net_profit
        assert r1.profit_probability == r2.profit_probability

    def test_different_seeds_produce_different_results(self):
        pnls = [10.0, -5.0, 20.0, -3.0, 15.0, -8.0, 7.0, 12.0, -2.0, 6.0]
        r1 = run_monte_carlo(pnls, "s1", "EURUSD", "H1", seed=42)
        r2 = run_monte_carlo(pnls, "s1", "EURUSD", "H1", seed=99)
        # Statistically extremely unlikely to be identical
        assert r1.mean_net_profit != r2.mean_net_profit

    def test_mostly_profitable_trades_robust(self):
        pnls = [10.0, 15.0, 8.0, 20.0, -2.0, 12.0, 5.0, 18.0, -1.0, 25.0]
        r = run_monte_carlo(pnls, "s1", "EURUSD", "H1", seed=42, num_simulations=500)
        assert r.robust is True
        assert r.profit_probability >= 0.7
        assert r.mean_net_profit > 0

    def test_mostly_losing_trades_not_robust(self):
        pnls = [-10.0, -15.0, -8.0, -20.0, 2.0, -12.0, -5.0, -18.0, 1.0, -25.0]
        r = run_monte_carlo(pnls, "s1", "EURUSD", "H1", seed=42, num_simulations=500)
        assert r.robust is False
        assert r.profit_probability < 0.7
        assert r.mean_net_profit < 0

    def test_insufficient_trades(self):
        r = run_monte_carlo([10.0], "s1", "EURUSD", "H1")
        assert r.status == "insufficient trades"
        assert r.num_trades == 1

    def test_empty_trades(self):
        r = run_monte_carlo([], "s1", "EURUSD", "H1")
        assert r.status == "insufficient trades"
        assert r.num_trades == 0

    def test_output_structure(self):
        pnls = [10.0, -5.0, 20.0, -3.0, 15.0, -8.0, 7.0, 12.0, -2.0, 6.0]
        r = run_monte_carlo(pnls, "s1", "EURUSD", "H1", seed=42, num_simulations=100)
        assert r.num_simulations == 100
        assert r.num_trades == 10
        assert r.original_net_profit == sum(pnls)
        assert r.p5_net_profit <= r.p25_net_profit
        assert r.p25_net_profit <= r.median_net_profit
        assert r.median_net_profit <= r.p75_net_profit
        assert r.p75_net_profit <= r.p95_net_profit
        assert 0.0 <= r.profit_probability <= 1.0
        assert 0.0 <= r.ruin_probability <= 1.0

    def test_original_max_drawdown(self):
        pnls = [100.0, -50.0, 30.0, -80.0, 20.0]
        r = run_monte_carlo(pnls, "s1", "EURUSD", "H1", initial_capital=10000.0, seed=42)
        assert r.original_max_drawdown_pct > 0
        assert r.original_net_profit == sum(pnls)
