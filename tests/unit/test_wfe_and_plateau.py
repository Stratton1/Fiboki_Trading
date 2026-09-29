"""Walk-forward efficiency on log growth (audit P2-11) and a scale-free plateau ratio (P2-12)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from fiboki.stats.stability import (
    PLATEAU_RATIO_DEFINITION,
    analyse_parameter_stability,
    full_grid,
    plateau_ratio,
)
from fiboki.validation.evaluation import DateWindow, WindowEvaluation
from fiboki.validation.ladder import (
    WFE_RATE_LOG_GROWTH,
    WFE_RATE_PROFIT,
    _efficiency,
    _fold_rates,
    _growth_rate,
)


def _ev(days: int, net: float, opening: float | None = 10_000.0) -> WindowEvaluation:
    start = np.datetime64("2020-01-01")
    window = DateWindow("w", str(start), str(start + np.timedelta64(days, "D")))
    meta = {"opening_equity": opening} if opening is not None else {}
    return WindowEvaluation(
        window=window, params={}, n_trades=10, net_profit=net,
        returns=np.array([net / 10] * 10), meta=meta,
    )


# ------------------------------------------------------------------ WFE


def test_log_growth_per_day_arithmetic() -> None:
    """10,000 -> 12,100 over 200 days: ln(1.21) / 200 = 0.19062036 / 200."""
    assert _growth_rate(_ev(200, 2_100.0)) == pytest.approx(math.log(1.21) / 200, abs=1e-15)


def test_compounding_no_longer_biases_wfe() -> None:
    """A strategy growing 10% per 100 days, exactly, in and out of sample.

    In sample, 400 days compounded: 10,000 * 1.1^4 = 14,641, net 4,641.
    Out of sample, 100 days from a fresh 10,000: net 1,000.

      money per day: IS 4,641 / 400 = 11.6025, OOS 1,000 / 100 = 10.00
                     WFE = 10.00 / 11.6025 = 86.19%  -- biased DOWN by compounding
      log growth:    IS ln(1.4641) / 400 = ln(1.1) / 100 = OOS ln(1.1) / 100
                     WFE = 100%  -- the same edge, measured as the same edge
    """
    is_ev, oos_ev = _ev(400, 4_641.0), _ev(100, 1_000.0)
    money = _efficiency(np.array([is_ev.profit_per_day]), np.array([oos_ev.profit_per_day]))
    assert money == pytest.approx(100.0 * 10.0 / 11.6025, abs=1e-9)
    is_r, oos_r, _, basis = _fold_rates(is_ev, oos_ev, oos_ev)
    assert basis == WFE_RATE_LOG_GROWTH
    assert _efficiency(np.array([is_r]), np.array([oos_r])) == pytest.approx(100.0, abs=1e-9)


def test_an_evaluator_without_an_equity_base_falls_back_to_money_per_day() -> None:
    """A synthetic evaluator does not compound; its money rate is unbiased, and
    the fold says which basis it used. Bases are never mixed within a fold."""
    is_ev, oos_ev = _ev(400, 4_000.0, opening=None), _ev(100, 1_000.0)
    is_r, oos_r, fixed_r, basis = _fold_rates(is_ev, oos_ev, oos_ev)
    assert basis == WFE_RATE_PROFIT
    assert (is_r, oos_r, fixed_r) == (10.0, 10.0, 10.0)


def test_a_wiped_out_window_has_no_log_growth() -> None:
    assert _growth_rate(_ev(100, -10_000.0)) == float("-inf")
    assert math.isnan(_efficiency(np.array([0.001]), np.array([float("-inf")])))


def test_the_engine_evaluator_records_its_opening_equity() -> None:
    import pandas as pd

    from fiboki.core.enums import Timeframe
    from fiboki.validation.engine_evaluator import EngineEvaluator, EvaluatorConfig
    from tests.agents_fixtures import ema_crossover_document, trending_bars

    frame = trending_bars(periods=600)
    ev = EngineEvaluator(
        document=ema_crossover_document(), frame=frame, dataset_version_id="ds",
        config=EvaluatorConfig(instrument="EURUSD", timeframe=Timeframe.H1,
                               account_ccy="USD", initial_balance=25_000.0),
    )
    window = DateWindow("w", frame.index[200], frame.index[-1] + pd.Timedelta(hours=1))
    result = ev(dict(ev.document.default_values()), window)
    assert result.meta["opening_equity"] == 25_000.0
    assert result.meta["sizing"]["policy_id"] == "fixed_fractional_v2"
    assert result.meta["sizing"]["cost_profile"] == "IG_REALISTIC"


# -------------------------------------------------------------- plateau


@pytest.mark.parametrize(
    ("score", "neighbours", "expected"),
    [
        (1.0, 1.0, 1.0),                 # flat: (1+1)/(1+1)
        (1.0, 0.6, 1.25),                # (1+1)/(0.6+1) = 2/1.6 -- the gate's edge
        (0.12, 0.09, 0.24 / 0.21),       # the audit's noise case: 1.1429, a pass (was 1.33)
        (10.0, 1.0, 20.0 / 11.0),        # a spike
        (10.0, 0.0, 2.0),                # the bound for a non-negative neighbourhood
        (1.0, -2.0, math.inf),           # neighbours lose more than the point makes
    ],
)
def test_plateau_ratio_values(score, neighbours, expected) -> None:
    assert plateau_ratio(score, neighbours) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize(("score", "neighbours"), [(0.0, 1.0), (-1.0, 1.0), (1.0, float("nan"))])
def test_plateau_ratio_is_undefined_without_a_positive_point(score, neighbours) -> None:
    assert math.isnan(plateau_ratio(score, neighbours))


def test_the_gate_threshold_means_neighbours_keep_sixty_percent() -> None:
    """For s > 0, ratio = 2 / (1 + m/s) <= 1.25  <=>  m >= 0.6 s, at any scale."""
    for scale in (1e-4, 1.0, 1e4):
        assert plateau_ratio(scale, 0.6 * scale) == pytest.approx(1.25, abs=1e-12)
        assert plateau_ratio(scale, 0.59 * scale) > 1.25
        assert plateau_ratio(scale, 0.61 * scale) < 1.25


def test_the_point_is_excluded_from_its_own_neighbourhood() -> None:
    """3x3 grid, centre 3.0, the eight neighbours 1.0.

    New: (3 + 3) / (1 + 3) = 1.5.
    Old: 3 / mean(including the centre) = 3 / (11 / 9) = 2.4545...
    """
    grid = full_grid({"a": [1, 2, 3], "b": [1, 2, 3]})
    scores = [1.0] * 9
    scores[4] = 3.0
    report = analyse_parameter_stability(grid, scores)
    centre = next(p for p in report.points if p.params == {"a": 2.0, "b": 2.0})
    assert centre.point_plateau_ratio == pytest.approx(1.5, abs=1e-12)
    assert centre.point_plateau_ratio_inclusive == pytest.approx(3.0 / (11.0 / 9.0), abs=1e-12)
    assert PLATEAU_RATIO_DEFINITION == "excluding_point_additive_floor_v2"
