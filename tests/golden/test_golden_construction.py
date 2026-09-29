"""Hand-calculated portfolio construction (construction_v2).

Every expected number is derived in the docstring with the arithmetic written
out. If one fails, check the arithmetic with a calculator, then fix the CODE.

Common inputs unless stated: equity 100,000; equal-risk allocator (a single
candidate's base weight is min(max_weight_per_candidate 1.0, max_total_weight
3.0 / 1) = 1.0); correlation soft cap 0.40, hard cap 0.85; unmeasured
correlation 0.30; no margin used; no realised vol (vol target neutral); no
conviction reading (factor 1.0, always recorded).
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.contracts import AccountState, Signal
from fiboki.core.enums import Direction, StrategyLifecycle
from fiboki.portfolio.construction import (
    CandidateSignal,
    ConstructionConfig,
    CorrelationMatrix,
    PortfolioConstructor,
    PortfolioSnapshot,
    StrategyTier,
)
from tests.exec_fixtures import make_position

pytestmark = pytest.mark.golden

NOW = pd.Timestamp("2026-09-29 12:00", tz="UTC")


def _signal(instrument: str, strategy: str, confidence: float = 1.0) -> Signal:
    return Signal(
        strategy_id=strategy, instrument=instrument, timeframe="H4",
        direction=Direction.LONG, bar_time=NOW, reference_price=1.1000,
        stop_price=1.0970, confidence=confidence,
    )


def _snapshot(*, equity=100_000.0, peak=None, positions=(), open_risk=None, corr=None):
    account = AccountState(
        balance=equity, equity=equity, currency="GBP", peak_equity=peak or equity
    )
    return PortfolioSnapshot(
        account=account, as_of=NOW, open_positions=tuple(positions),
        instrument_correlation=corr or CorrelationMatrix(default=0.30),
        open_risk_by_instrument=open_risk,
    )


def test_golden_every_multiplicative_step() -> None:
    """ESTABLISHED on DEMO, degraded, less than confident, correlated, in drawdown.

    tier ESTABLISHED           base risk 0.75% of equity
    lifecycle DEMO             x 0.75
    health 0.8                 x 0.80
    confidence 0.9             x 0.90
    held GBPUSD, rho 0.60      x (1 - (0.60 - 0.40) / (0.85 - 0.40)) = x 0.555556
    strategy s2 unmeasured     0.30 <= soft cap 0.40, x 1.0
    drawdown (106,000 - 100,000) / 106,000 = 5.6604% in [5, 10)  x 0.60
    regime up|normal|trending|deep|calm -> "trend"   x 1.0
    weight = 0.75 x 0.80 x 0.90 x 0.555556 x 0.60 = 0.180000
    correlated budget: open GBPUSD 1,500 = 1.5%; 0.60 x 1.5 = 0.90%; headroom
      3.0 - 0.90 = 2.10% >= planned 0.75 x 0.18 = 0.135%  -> x 1.0
    total risk cap: open 1.5% + 0.135% = 1.635% <= 6%  -> no scaling
    risk = 0.75% x 0.18 = 0.135% of equity = 135.00
    """
    corr = CorrelationMatrix.from_mapping({("EURUSD", "GBPUSD"): 0.60}, default=0.30)
    snap = _snapshot(
        peak=106_000.0,
        positions=(make_position(instrument="GBPUSD", strategy_id="s2"),),
        open_risk={"GBPUSD": 1_500.0},
        corr=corr,
    )
    candidate = CandidateSignal(
        signal=_signal("EURUSD", "s1", confidence=0.9), health=0.8,
        lifecycle=StrategyLifecycle.DEMO, tier=StrategyTier.ESTABLISHED,
        regime="up|normal|trending|deep|calm",
    )
    result = PortfolioConstructor("equal_risk", ConstructionConfig()).allocate([candidate], snap)
    a = result.allocations[0]
    factors = {r.step: r.factor for r in a.trace}
    assert factors["lifecycle"] == pytest.approx(0.75)
    assert factors["health"] == pytest.approx(0.80)
    assert factors["confidence"] == pytest.approx(0.90)
    assert factors["instrument_correlation"] == pytest.approx(0.5555556, abs=1e-6)
    assert factors["strategy_correlation"] == 1.0
    assert factors["drawdown"] == pytest.approx(0.60)
    assert factors["regime"] == 1.0
    assert factors["correlated_risk_budget"] == 1.0
    assert factors["conviction"] == 1.0
    assert a.base_risk_pct == pytest.approx(0.75)
    assert a.weight == pytest.approx(0.18, abs=1e-9)
    assert a.risk_pct == pytest.approx(0.135, abs=1e-9)
    assert a.risk_pct / 100.0 * 100_000.0 == pytest.approx(135.0, abs=1e-6)


def test_golden_both_open_risk_budgets_bind_exactly() -> None:
    """FLAGSHIP next to a large, moderately correlated open position.

    base risk FLAGSHIP 2.00%; every quality factor 1.0; held GBPUSD rho 0.40
    (<= soft cap: x 1.0); weight before budgets 1.0
    correlated budget: open GBPUSD 5,000 = 5.0%; 0.40 x 5.0 = 2.00%;
      headroom 3.00 - 2.00 = 1.00%; planned 2.00 x 1.0 = 2.00%
      -> x 1.00 / 2.00 = 0.50; weight 0.50; risk 1.00%
    total risk cap: open 5.0% + 1.0% = 6.0% <= 6.0%  -> no scaling (exactly at cap)
    risk = 2.00% x 0.50 = 1.00% of equity = 1,000.00
    """
    corr = CorrelationMatrix.from_mapping({("EURUSD", "GBPUSD"): 0.40}, default=0.30)
    snap = _snapshot(
        positions=(make_position(instrument="GBPUSD", strategy_id="s2"),),
        open_risk={"GBPUSD": 5_000.0},
        corr=corr,
    )
    candidate = CandidateSignal(
        signal=_signal("EURUSD", "s1"), tier=StrategyTier.FLAGSHIP, regime="trend"
    )
    a = PortfolioConstructor().allocate([candidate], snap).allocations[0]
    factors = {r.step: r.factor for r in a.trace}
    assert factors["correlated_risk_budget"] == pytest.approx(0.50)
    assert "total_risk_cap" not in factors
    assert a.weight == pytest.approx(0.50)
    assert a.risk_pct == pytest.approx(1.00)


def test_golden_total_risk_cap_scales_the_batch() -> None:
    """Two ESTABLISHED candidates against a book already holding 5% at risk.

    each: base 0.75%, weight 1.0 (equal risk: min(1.0, 3.0 / 2) = 1.0)
    held EURJPY unmeasured vs AUDUSD / NZDUSD: 0.30 <= soft cap, x 1.0
    correlated budget each: 0.30 x 5.0% = 1.50%; headroom 1.50% >= 0.75% -> x 1.0
    total risk cap: headroom 6.0 - 5.0 = 1.00%; batch 0.75 + 0.75 = 1.50%
      -> scale 1.00 / 1.50 = 0.666667; each weight 0.666667, risk 0.50%
    """
    snap = _snapshot(
        positions=(make_position(instrument="EURJPY", strategy_id="s9"),),
        open_risk={"EURJPY": 5_000.0},
    )
    candidates = [
        CandidateSignal(signal=_signal("AUDUSD", "a"), tier=StrategyTier.ESTABLISHED,
                        regime="trend"),
        CandidateSignal(signal=_signal("NZDUSD", "b"), tier=StrategyTier.ESTABLISHED,
                        regime="trend"),
    ]
    result = PortfolioConstructor().allocate(candidates, snap)
    for a in result.allocations:
        assert a.weight == pytest.approx(2.0 / 3.0)
        assert a.risk_pct == pytest.approx(0.50)
        assert a.reasons[-1].step == "total_risk_cap"
    assert result.total_risk_pct == pytest.approx(1.00)
