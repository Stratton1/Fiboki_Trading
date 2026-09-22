"""Forward validation: does the monitor catch what it is for, and stay quiet otherwise.

Every planted defect here is one the V1 forensic baseline actually describes: a
regime-conditional edge that decays when the regime turns, and a cost assumption
that was optimistic. The controls -- the "nothing wrong" cases -- matter as much,
because a monitor that flags every strategy is a monitor nobody reads.
"""
from __future__ import annotations

import numpy as np
import pytest

from fiboki.lifecycle.monitor import (
    DivergenceDimension,
    DivergenceStatus,
    Expectation,
    ForwardMonitor,
    MonitorConfig,
    Observation,
    max_drawdown,
    two_sample_block_ks,
)
from tests.lifecycle_fixtures import HASH_A, expectation, observation

CALM = "up|normal|trending|deep|calm"
STRESSED = "down|high|random|thin|stressed"


def _monitor() -> ForwardMonitor:
    return ForwardMonitor(MonitorConfig())


# ------------------------------------------------------------- arithmetic


@pytest.mark.golden
def test_max_drawdown_is_compounded_and_hand_checkable():
    """+10%, -20%, +5% compounds to 1.10, 0.88, 0.924.

    Peak 1.10, trough 0.88, so the peak-to-trough drawdown is
    1 - 0.88/1.10 = 0.20 exactly. The ADDITIVE version would report
    0.20/1.10 of a different quantity and understate it; the whole reason for
    compounding is that a fixed-fractional strategy's drawdowns are multiplicative.
    """
    assert max_drawdown([0.10, -0.20, 0.05]) == pytest.approx(0.20)
    assert max_drawdown([0.01, 0.01, 0.01]) == pytest.approx(0.0)


def test_max_drawdown_of_an_empty_series_is_zero_not_an_error():
    assert max_drawdown([]) == 0.0


def test_block_ks_is_far_less_trigger_happy_than_the_iid_version():
    """On AUTOCORRELATED data drawn from ONE process, the classical two-sample
    KS p-value is small and wrong. The block null must not be."""
    from scipy import stats as sps

    rng = np.random.default_rng(3)
    noise = rng.normal(0, 0.01, 1200)
    ar = np.zeros_like(noise)
    for i in range(1, ar.size):
        ar[i] = 0.85 * ar[i - 1] + noise[i]
    a, b = ar[:600], ar[600:]
    _, block_p = two_sample_block_ks(a, b, n_boot=300, rng=5)
    iid_p = float(sps.ks_2samp(a, b).pvalue)
    assert block_p > iid_p
    assert block_p > 0.05, "two halves of one AR(1) process are not two distributions"


# -------------------------------------------------------------- the quiet case


def test_a_strategy_performing_as_expected_flags_nothing():
    rng = np.random.default_rng(101)
    report = _monitor().compare(
        expectation(),
        observation(returns=rng.normal(0.004, 0.010, 220)),
    )
    assert report.n_flagged == 0, report.summary()
    assert report.coverage > 0.8
    assert not report.cost_divergence


def test_agreement_is_never_claimed_for_a_dimension_with_no_data():
    exp = Expectation.from_backtest(HASH_A, np.random.default_rng(2).normal(0.004, 0.01, 400))
    obs = Observation(strategy_content_hash=HASH_A, returns=np.array([0.001, -0.002]))
    report = _monitor().compare(exp, obs)
    assert report.n_flagged == 0
    assert all(
        d.status is DivergenceStatus.NOT_EVALUATED for d in report.divergences
    )
    assert report.coverage == 0.0
    assert "no divergence" in report.summary() or report.coverage == 0.0


# ---------------------------------------------------------- planted defects


def test_a_planted_cost_increase_is_caught_on_the_cost_dimensions():
    """Realised spread 2.5x modelled and slippage 4x. The tolerance is 1.25x, so
    both must flag -- and the report must mark it as a COST divergence, because
    that invalidates every stored expectancy rather than merely disappointing."""
    report = _monitor().compare(
        expectation(),
        observation(spread=2.5, slippage=0.80),
    )
    spread = report.dimension(DivergenceDimension.SPREAD)
    slippage = report.dimension(DivergenceDimension.SLIPPAGE)
    assert spread.diverged, spread.describe()
    assert slippage.diverged, slippage.describe()
    assert report.cost_divergence
    assert spread.confidence > 0.95
    assert "re-run rather than accept" in spread.caveat


def test_a_modest_cost_increase_inside_the_tolerance_does_not_flag():
    """1.15x modelled spread. Spreads vary; the static-spread approximation was
    always understood to be wrong by about this much."""
    report = _monitor().compare(expectation(), observation(spread=1.15))
    assert not report.dimension(DivergenceDimension.SPREAD).diverged


def test_a_latency_blowout_is_caught():
    report = _monitor().compare(expectation(), observation(latency=400.0))
    assert report.dimension(DivergenceDimension.LATENCY).diverged


def test_a_rejection_burst_is_caught():
    report = _monitor().compare(
        expectation(), observation(orders_submitted=200, orders_rejected=40)
    )
    d = report.dimension(DivergenceDimension.REJECTED_ORDERS)
    assert d.diverged
    assert d.observed == pytest.approx(0.20)


def test_a_planted_return_collapse_is_caught_on_return_and_sharpe():
    rng = np.random.default_rng(77)
    report = _monitor().compare(
        expectation(),
        observation(returns=rng.normal(-0.002, 0.010, 220)),
    )
    assert report.dimension(DivergenceDimension.RETURN).diverged
    assert report.dimension(DivergenceDimension.SHARPE).diverged
    assert report.worst is not None


def test_a_planted_regime_shift_is_caught_while_the_aggregate_still_agrees():
    """The V1 audit's finding made continuous.

    The strategy's edge lives in the calm regime. Forward, calm still delivers
    and the stressed regime has turned decisively negative. The blended mean is
    dragged down only a little -- but the REGIME dimension must see it.
    """
    rng = np.random.default_rng(404)
    calm_live = rng.normal(0.0045, 0.008, 140)
    stressed_live = rng.normal(-0.010, 0.008, 60)
    blended = np.concatenate([calm_live, stressed_live])
    rng.shuffle(blended)

    exp = expectation(
        regime_mean_return={CALM: 0.0045, STRESSED: 0.0035},
    )
    obs = observation(
        returns=blended,
        regime_returns={CALM: calm_live, STRESSED: stressed_live},
    )
    report = _monitor().compare(exp, obs)
    regime = report.dimension(DivergenceDimension.REGIME)
    assert regime.diverged, regime.describe()
    assert regime.detail["worst_regime"] == STRESSED
    rows = {r["regime"]: r for r in regime.detail["regimes"]}
    assert rows[CALM]["status"] == "agrees"
    assert rows[STRESSED]["status"] == "diverged"


def test_a_regime_with_too_few_observations_is_not_evaluated_rather_than_cleared():
    rng = np.random.default_rng(9)
    exp = expectation(regime_mean_return={CALM: 0.004, STRESSED: 0.004})
    obs = observation(
        regime_returns={
            CALM: rng.normal(0.004, 0.01, 40),
            STRESSED: rng.normal(-0.05, 0.01, 3),
        }
    )
    regime = _monitor().compare(exp, obs).dimension(DivergenceDimension.REGIME)
    rows = {r["regime"]: r for r in regime.detail["regimes"]}
    assert rows[STRESSED]["status"] == "not_evaluated"
    assert not regime.diverged


def test_a_trade_frequency_collapse_is_caught():
    rng = np.random.default_rng(5)
    exp = expectation(elapsed_days=300.0)  # 2 trades/day
    obs = observation(returns=rng.normal(0.004, 0.01, 60), elapsed_days=100.0)
    d = _monitor().compare(exp, obs).dimension(DivergenceDimension.TRADE_FREQUENCY)
    assert d.diverged
    assert "APPROXIMATION" in d.caveat


def test_a_drawdown_blowout_is_caught_and_says_it_is_not_the_halt():
    rng = np.random.default_rng(31)
    live = rng.normal(0.004, 0.01, 200)
    live[60:110] = -0.02  # a sustained losing run the backtest never had
    d = _monitor().compare(
        expectation(), observation(returns=live)
    ).dimension(DivergenceDimension.DRAWDOWN)
    assert d.diverged
    assert "lifecycle.stopping_rules" in d.caveat


def test_a_distribution_change_with_an_unchanged_mean_is_caught():
    """Same mean, different SHAPE: exits changed from a spread of outcomes to a
    two-point win/lose. The mean dimension cannot see it; the KS must."""
    rng = np.random.default_rng(808)
    bt = rng.normal(0.004, 0.010, 800)
    live = np.where(rng.random(400) < 0.60, 0.0157, -0.0135)
    assert live.mean() == pytest.approx(0.004, abs=0.002)
    report = _monitor().compare(
        expectation(returns=bt), observation(returns=live)
    )
    assert report.dimension(DivergenceDimension.RETURN_DISTRIBUTION).diverged


# ----------------------------------------------------------------- hygiene


def test_the_monitor_refuses_to_compare_two_different_strategies():
    with pytest.raises(ValueError, match="different strategies"):
        _monitor().compare(expectation(HASH_A), observation("b" * 64))


def test_a_regime_key_that_is_not_a_regime_vector_is_refused():
    with pytest.raises(ValueError, match="not a RegimeVector key"):
        expectation(regime_mean_return={"sideways": 0.001})
    with pytest.raises(ValueError, match="not a RegimeVector key"):
        observation(regime_returns={"sideways": [0.1, 0.2]})


def test_the_report_is_deterministic_for_one_seed():
    exp, obs = expectation(), observation(spread=2.0)
    first = _monitor().compare(exp, obs)
    second = _monitor().compare(exp, obs)
    assert first.to_dict() == second.to_dict()


def test_an_expectation_must_be_derived_from_one_series():
    with pytest.raises(ValueError, match="at least 2 finite"):
        Expectation.from_backtest(HASH_A, [0.01])


def test_severity_is_zero_for_an_unevaluated_dimension():
    exp = Expectation.from_backtest(HASH_A, np.random.default_rng(1).normal(0.004, 0.01, 200))
    obs = Observation(strategy_content_hash=HASH_A, returns=np.array([0.001, 0.002]))
    for d in _monitor().compare(exp, obs).divergences:
        assert d.severity == 0.0
