"""Stress suite over closed trades."""
from __future__ import annotations

import inspect

import numpy as np
import pytest
from stats_trades import edge_trades, make_trade

from fiboki.stats.stress import (
    end_date_stress,
    execution_delay_stress,
    expectancy,
    max_drawdown,
    missing_fill_stress,
    net_profit,
    profit_factor,
    random_deletion_stress,
    run_stress_suite,
    slippage_stress,
    spread_multiplier_stress,
    start_date_stress,
    trade_sharpe,
)


@pytest.fixture
def trades():
    return edge_trades()


class TestMetrics:
    def test_net_profit_is_the_sum(self) -> None:
        ts = [make_trade(100.0), make_trade(-40.0), make_trade(10.0)]
        assert net_profit(ts) == pytest.approx(70.0)

    def test_profit_factor(self) -> None:
        ts = [make_trade(100.0), make_trade(-50.0), make_trade(50.0)]
        assert profit_factor(ts) == pytest.approx(3.0)

    def test_profit_factor_edge_cases(self) -> None:
        assert profit_factor([]) == 0.0
        assert profit_factor([make_trade(10.0)]) == float("inf")

    def test_expectancy_and_sharpe(self) -> None:
        ts = [make_trade(10.0), make_trade(-10.0), make_trade(30.0), make_trade(-10.0)]
        assert expectancy(ts) == pytest.approx(5.0)
        assert trade_sharpe(ts) == pytest.approx(
            np.mean([10, -10, 30, -10]) / np.std([10, -10, 30, -10], ddof=1)
        )

    def test_max_drawdown_is_positive_and_hand_checkable(self) -> None:
        ts = [make_trade(p, index=i) for i, p in enumerate([100.0, -30.0, -50.0, 200.0])]
        assert max_drawdown(ts) == pytest.approx(80.0)
        assert max_drawdown([]) == 0.0


class TestBaselineIdentity:
    """Every stress must reproduce the unstressed result at its null level."""

    def test_spread_multiplier_one(self, trades) -> None:
        curve = spread_multiplier_stress(trades, multipliers=(1.0, 2.0), rng=0)
        assert curve.median[0] == pytest.approx(curve.baseline)
        assert curve.retention[0] == pytest.approx(1.0)

    def test_zero_slippage(self, trades) -> None:
        curve = slippage_stress(trades, levels=(0.0, 1.0), rng=0)
        assert curve.median[0] == pytest.approx(net_profit(trades))

    def test_zero_delay(self, trades) -> None:
        for mode in ("capture", "adverse"):
            curve = execution_delay_stress(trades, levels=(0.0, 0.2), mode=mode, rng=0)
            assert curve.median[0] == pytest.approx(curve.baseline)

    def test_zero_deletion(self, trades) -> None:
        curve = random_deletion_stress(trades, fractions=(0.0, 0.2), n_samples=20, rng=0)
        assert np.all(curve.samples[0] == pytest.approx(curve.baseline))

    def test_zero_trim(self, trades) -> None:
        assert start_date_stress(trades, fractions=(0.0, 0.2)).median[0] == pytest.approx(
            net_profit(trades)
        )
        assert end_date_stress(trades, fractions=(0.0, 0.2)).median[0] == pytest.approx(
            net_profit(trades)
        )

    def test_zero_miss_probability(self, trades) -> None:
        curve = missing_fill_stress(trades, probabilities=(0.0, 0.1), n_samples=20, rng=0)
        assert np.all(curve.samples[0] == pytest.approx(curve.baseline))


class TestDegradation:
    def test_more_spread_means_less_profit(self, trades) -> None:
        curve = spread_multiplier_stress(trades, multipliers=(1.0, 1.5, 2.0, 3.0, 5.0), rng=0)
        assert list(curve.median) == sorted(curve.median, reverse=True)
        assert curve.median[-1] < curve.median[0]

    def test_spread_cost_scales_exactly(self) -> None:
        """A 3x spread must remove exactly 2x the original spread cost."""
        ts = [make_trade(100.0, spread_cost=5.0, index=i) for i in range(10)]
        curve = spread_multiplier_stress(ts, multipliers=(1.0, 3.0), rng=0)
        assert curve.median[1] == pytest.approx(curve.median[0] - 2 * 5.0 * 10)

    def test_slippage_in_currency_terms(self) -> None:
        ts = [make_trade(50.0, index=i) for i in range(20)]
        curve = slippage_stress(ts, levels=(0.0, 3.0), basis="currency", rng=0)
        assert curve.median[1] == pytest.approx(curve.median[0] - 3.0 * 20)

    def test_capture_delay_scales_gross(self) -> None:
        ts = [make_trade(100.0, spread_cost=0.0, index=i) for i in range(10)]
        curve = execution_delay_stress(ts, levels=(0.0, 0.5), mode="capture", rng=0)
        assert curve.median[1] == pytest.approx(0.5 * curve.median[0])

    def test_adverse_delay_is_always_a_cost(self) -> None:
        """Unlike capture mode, adverse mode never helps a losing trade."""
        ts = [make_trade(-100.0, spread_cost=0.0, index=i) for i in range(10)]
        capture = execution_delay_stress(ts, levels=(0.0, 0.3), mode="capture", rng=0)
        adverse = execution_delay_stress(ts, levels=(0.0, 0.3), mode="adverse", rng=0)
        assert capture.median[1] > capture.median[0]  # a delay "helps" losers
        assert adverse.median[1] < adverse.median[0]  # never, in adverse mode

    def test_missing_the_best_fills_hurts_more_than_random(self, trades) -> None:
        worst = missing_fill_stress(trades, probabilities=(0.0, 0.1), bias="worst", rng=0)
        random = missing_fill_stress(
            trades, probabilities=(0.0, 0.1), bias="random", n_samples=200, rng=0
        )
        best = missing_fill_stress(trades, probabilities=(0.0, 0.1), bias="best", rng=0)
        assert worst.median[1] < random.median[1] < best.median[1]

    def test_deletion_distribution_widens_with_the_fraction(self, trades) -> None:
        curve = random_deletion_stress(
            trades, fractions=(0.05, 0.35), n_samples=200, rng=1
        )
        assert curve.samples[1].std() > curve.samples[0].std()

    def test_concentration_is_detected(self) -> None:
        """A P&L that lives in one trade must collapse when deletion can remove it."""
        concentrated = [make_trade(-10.0, index=i) for i in range(99)] + [
            make_trade(2_000.0, index=99)
        ]
        curve = random_deletion_stress(concentrated, fractions=(0.1,), n_samples=400, rng=2)
        assert float(np.mean(curve.samples[0] < 0)) > 0.05

    def test_date_trims_change_the_answer(self, trades) -> None:
        start = start_date_stress(trades, fractions=(0.0, 0.35))
        end = end_date_stress(trades, fractions=(0.0, 0.35))
        assert start.median[1] != pytest.approx(start.median[0])
        assert end.median[1] != pytest.approx(end.median[0])

    def test_regime_dependent_edge_is_exposed_by_the_start_date_trim(self) -> None:
        """All the profit is in the first quarter of the sample; trimming the start
        must destroy it, which a single headline number would never show."""
        ts = [make_trade(150.0, index=i) for i in range(40)] + [
            make_trade(-10.0, index=40 + i) for i in range(120)
        ]
        curve = start_date_stress(ts, fractions=(0.0, 0.1, 0.25, 0.4))
        # 40 x +150 then 120 x -10: +4800 overall, but trimming the first 25% of
        # trades removes every winner and leaves -1200.
        assert curve.median[0] == pytest.approx(4800.0)
        assert curve.median[-1] < 0
        assert curve.breaking_level == pytest.approx(0.25)


class TestCurveObject:
    def test_frame_columns(self, trades) -> None:
        frame = random_deletion_stress(trades, n_samples=30, rng=0).as_frame()
        assert list(frame.columns) == [
            "deleted_fraction",
            "median",
            "mean",
            "q05",
            "q95",
            "retention",
        ]
        assert len(frame) == 5

    def test_quantiles_are_ordered(self, trades) -> None:
        curve = random_deletion_stress(trades, n_samples=200, rng=0)
        assert np.all(curve.quantile(0.05) <= curve.quantile(0.95))

    def test_breaking_level_is_none_when_nothing_breaks(self, trades) -> None:
        assert spread_multiplier_stress(trades, multipliers=(1.0, 1.1), rng=0).breaking_level is None

    def test_retention_is_nan_without_a_baseline(self) -> None:
        flat = [make_trade(10.0, index=i) for i in range(10)] + [
            make_trade(-10.0, index=10 + i) for i in range(10)
        ]
        curve = spread_multiplier_stress(flat, multipliers=(1.0,), metric=lambda t: 0.0, rng=0)
        assert np.all(np.isnan(curve.retention))

    def test_zero_recorded_spread_is_flagged_not_hidden(self) -> None:
        ts = [make_trade(10.0, spread_cost=0.0, index=i) for i in range(10)]
        curve = spread_multiplier_stress(ts, multipliers=(1.0, 5.0), rng=0)
        assert "WARNING" in curve.notes
        assert curve.median[1] == pytest.approx(curve.median[0])

    def test_bootstrap_samples_give_a_distribution(self, trades) -> None:
        curve = spread_multiplier_stress(trades, multipliers=(1.0, 2.0), n_samples=100, rng=0)
        assert curve.samples[0].size == 100
        assert curve.samples[0].std() > 0


class TestSuite:
    def test_runs_everything(self, trades) -> None:
        curves = run_stress_suite(trades, n_samples=40, rng=0)
        assert set(curves) == {
            "spread_multiplier",
            "slippage",
            "execution_delay_capture",
            "execution_delay_adverse",
            "random_deletion",
            "start_date",
            "end_date",
            "missing_fill_random",
            "missing_fill_worst",
        }
        for curve in curves.values():
            assert curve.baseline == pytest.approx(net_profit(trades))

    def test_alternative_metric(self, trades) -> None:
        curves = run_stress_suite(trades, metric=profit_factor, n_samples=20, rng=0)
        assert curves["spread_multiplier"].metric_name == "profit_factor"

    def test_empty_trade_list_is_rejected(self) -> None:
        for fn in (
            spread_multiplier_stress,
            slippage_stress,
            execution_delay_stress,
            random_deletion_stress,
            start_date_stress,
            end_date_stress,
            missing_fill_stress,
        ):
            # rng is a required argument wherever a stress can resample
            # (P3-2); the empty-list refusal must still be the error raised.
            kwargs = {"rng": 0} if "rng" in inspect.signature(fn).parameters else {}
            with pytest.raises(ValueError, match="no trades"):
                fn([], **kwargs)

    def test_rejects_bad_options(self, trades) -> None:
        with pytest.raises(ValueError, match="basis"):
            slippage_stress(trades, basis="vibes", rng=0)
        with pytest.raises(ValueError, match="mode"):
            execution_delay_stress(trades, mode="vibes", rng=0)
        with pytest.raises(ValueError, match="bias"):
            missing_fill_stress(trades, bias="vibes", rng=0)
