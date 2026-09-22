"""Tests for the promotion gate (research/promotion.py).

Locks the lifecycle gating: hard gates, robustness gates, the cap at
research_watchlist without OOS/MC evidence, demo candidacy from a paper record,
and the hard rule that 'live' is never auto-recommended. Pure/offline.
"""

from fibokei.research.promotion import (
    LifecycleState,
    PromotionThresholds,
    evaluate_promotion,
)

STRONG = dict(total_trades=120, profit_factor=1.4, max_drawdown_pct=8.0,
              total_net_profit=2500.0)


def _eval(metrics=None, score=0.5, **kw):
    return evaluate_promotion(
        strategy_id="factory_trad_macd_cross_v1", instrument="EURUSD",
        timeframe="H4", metrics=metrics or STRONG, composite_score=score, **kw)


def test_rejected_on_too_few_trades():
    d = _eval(metrics={**STRONG, "total_trades": 40})
    assert d.recommended_state == LifecycleState.REJECTED.value
    assert any("trades<80" in f for f in d.failed)


def test_rejected_on_low_profit_factor():
    d = _eval(metrics={**STRONG, "profit_factor": 0.9})
    assert d.recommended_state == LifecycleState.REJECTED.value


def test_rejected_on_excess_drawdown():
    d = _eval(metrics={**STRONG, "max_drawdown_pct": 40.0})
    assert d.recommended_state == LifecycleState.REJECTED.value


def test_rejected_on_negative_expectancy():
    d = _eval(metrics={**STRONG, "total_net_profit": -100.0})
    assert d.recommended_state == LifecycleState.REJECTED.value


def test_watchlist_when_hard_gates_pass_but_no_robustness():
    d = _eval()  # no OOS/MC supplied
    assert d.recommended_state == LifecycleState.RESEARCH_WATCHLIST.value
    assert any("OOS" in n for n in d.notes)
    assert any("Monte Carlo" in n for n in d.notes)


def test_low_score_rejected_even_if_hard_gates_pass():
    d = _eval(score=0.20)
    assert d.recommended_state == LifecycleState.REJECTED.value


def test_paper_candidate_when_robustness_passes():
    d = _eval(score=0.5, oos_robust=True, mc_profit_probability=0.82,
              mc_ruin_probability=0.0)
    assert d.recommended_state == LifecycleState.PAPER_CANDIDATE.value


def test_oos_retention_derives_robustness():
    d = _eval(oos_retention=0.6, mc_profit_probability=0.8, mc_ruin_probability=0.0)
    assert d.recommended_state == LifecycleState.PAPER_CANDIDATE.value
    d2 = _eval(oos_retention=0.3, mc_profit_probability=0.8, mc_ruin_probability=0.0)
    assert d2.recommended_state == LifecycleState.RESEARCH_WATCHLIST.value


def test_realism_warnings_block_paper_candidacy():
    d = _eval(oos_robust=True, mc_profit_probability=0.8, mc_ruin_probability=0.0,
              realism_warnings=["zero slippage"])
    assert d.recommended_state == LifecycleState.RESEARCH_WATCHLIST.value
    assert any("realism" in n for n in d.notes)


def test_concentration_blocks_paper_candidacy():
    d = _eval(oos_robust=True, mc_profit_probability=0.8, mc_ruin_probability=0.0,
              concentration_ok=False)
    assert d.recommended_state == LifecycleState.RESEARCH_WATCHLIST.value


def test_high_ruin_probability_blocks():
    d = _eval(oos_robust=True, mc_profit_probability=0.8, mc_ruin_probability=0.20)
    assert d.recommended_state == LifecycleState.RESEARCH_WATCHLIST.value


def test_demo_candidate_from_paper_record_requires_human():
    d = _eval(oos_robust=True, mc_profit_probability=0.85, mc_ruin_probability=0.0,
              paper_trades=60, paper_positive_expectancy=True)
    assert d.recommended_state == LifecycleState.DEMO_CANDIDATE.value
    assert d.requires_human_approval is True


def test_live_is_never_auto_recommended():
    # Even with maximal evidence, the gate never returns 'live'.
    d = _eval(score=0.9, oos_robust=True, mc_profit_probability=0.99,
              mc_ruin_probability=0.0, paper_trades=500,
              paper_positive_expectancy=True)
    assert d.recommended_state != LifecycleState.LIVE.value
    assert d.recommended_state == LifecycleState.DEMO_CANDIDATE.value


def test_thresholds_are_configurable():
    strict = PromotionThresholds(min_profit_factor=1.5)
    d = evaluate_promotion(
        strategy_id="s", instrument="EURUSD", timeframe="H4",
        metrics={**STRONG, "profit_factor": 1.3}, composite_score=0.5,
        oos_robust=True, mc_profit_probability=0.8, thresholds=strict)
    assert d.recommended_state == LifecycleState.REJECTED.value


def test_deterministic():
    a = _eval(oos_robust=True, mc_profit_probability=0.8, mc_ruin_probability=0.0)
    b = _eval(oos_robust=True, mc_profit_probability=0.8, mc_ruin_probability=0.0)
    assert a.recommended_state == b.recommended_state
    assert a.passed == b.passed and a.failed == b.failed
