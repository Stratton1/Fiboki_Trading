"""construction_v2: tier base risk, step drawdown throttle, budgets, DOWN ONLY.

The property test is the one that matters: whatever the inputs, a
candidate's final risk never exceeds its tier's base risk, no step ever
multiplies by more than 1.0, and the book never exceeds the total risk cap.
The hand-calculated cases live in ``tests/golden/test_golden_construction.py``.
"""
from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fiboki.core.contracts import AccountState, ConvictionReading, Signal
from fiboki.core.enums import Direction, StrategyLifecycle
from fiboki.core.tier import AgentInfluenceTier, TierReading
from fiboki.portfolio.construction import (
    CONSTRUCTION_VERSION,
    CandidateSignal,
    ConstructionConfig,
    ConvictionPolicy,
    CorrelationMatrix,
    PortfolioConstructor,
    PortfolioSnapshot,
    StrategyTier,
    coarse_regime,
)
from tests.exec_fixtures import make_position

NOW = pd.Timestamp("2026-09-29 12:00", tz="UTC")
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD", "EURJPY")


def _signal(instrument="EURUSD", strategy="s1", direction=Direction.LONG, confidence=1.0):
    ref = 1.1000 if not instrument.endswith("JPY") and instrument != "XAUUSD" else 150.0
    stop = ref * (0.997 if direction is Direction.LONG else 1.003)
    return Signal(
        strategy_id=strategy, instrument=instrument, timeframe="H4", direction=direction,
        bar_time=NOW, reference_price=ref, stop_price=stop, confidence=confidence,
    )


def _snap(*, equity=100_000.0, peak=None, positions=(), open_risk=None, regime="trend"):
    return PortfolioSnapshot(
        account=AccountState(balance=equity, equity=equity, peak_equity=peak or equity),
        as_of=NOW, open_positions=tuple(positions), regime=regime,
        open_risk_by_instrument=open_risk,
    )


def _one(candidate, snap=None, config=None, tier=None):
    return (
        PortfolioConstructor("equal_risk", config or ConstructionConfig(), agent_tier=tier)
        .allocate([candidate], snap or _snap())
        .allocations[0]
    )


# ------------------------------------------------------------ the property


@st.composite
def _world(draw):
    n = draw(st.integers(1, 5))
    tiers = list(StrategyTier)
    lifecycles = [
        StrategyLifecycle.PAPER, StrategyLifecycle.DEMO, StrategyLifecycle.WATCH,
        StrategyLifecycle.APPROVED, StrategyLifecycle.LIVE, StrategyLifecycle.SHADOW,
    ]
    candidates = []
    for i in range(n):
        sym = draw(st.sampled_from(SYMBOLS))
        direction = draw(st.sampled_from([Direction.LONG, Direction.SHORT]))
        conviction = None
        if draw(st.booleans()):
            stance = draw(st.sampled_from(["long", "short", "none"]))
            strength = 0 if stance == "none" else draw(st.sampled_from([1, 2]))
            start = NOW - pd.Timedelta(hours=draw(st.integers(0, 30)))
            conviction = ConvictionReading(
                instrument=sym, stance=stance, strength=strength, as_of=start,
                valid_until=start + pd.Timedelta(hours=draw(st.integers(1, 30))),
                artefact_id=f"cv_{i}", policy_version="thesis_debate_v1",
            )
        candidates.append(
            CandidateSignal(
                signal=_signal(sym, f"s{i}", direction,
                               confidence=draw(st.floats(0.01, 1.0))),
                annualised_vol=draw(st.floats(0.01, 0.6)),
                health=draw(st.floats(0.01, 1.0)),
                lifecycle=draw(st.sampled_from(lifecycles)),
                tier=draw(st.sampled_from(tiers)),
                regime=draw(st.sampled_from(
                    ["trend", "range", "high_vol", "crisis", "unknown",
                     "up|low|trending|deep|calm", "down|high|random|thin|elevated", None]
                )),
                conviction=conviction,
            )
        )
    held = draw(st.lists(st.sampled_from(SYMBOLS), max_size=4))
    positions = tuple(make_position(instrument=s, strategy_id=f"h{j}") for j, s in enumerate(held))
    open_risk = (
        {s: draw(st.floats(0.0, 4_000.0)) for s in held}
        if draw(st.booleans()) else None
    )
    peak = 100_000.0 / (1.0 - draw(st.floats(0.0, 0.25)))
    snap = PortfolioSnapshot(
        account=AccountState(balance=100_000.0, equity=100_000.0, peak_equity=peak,
                             margin_used=draw(st.floats(0.0, 45_000.0))),
        as_of=NOW, open_positions=positions,
        instrument_correlation=CorrelationMatrix.from_mapping(
            {("EURUSD", "GBPUSD"): 0.7, ("AUDUSD", "EURUSD"): 0.5}, default=0.30
        ),
        realised_portfolio_vol=draw(st.floats(0.0, 0.5)),
        open_risk_by_instrument=open_risk,
    )
    policy = ConvictionPolicy(enabled=draw(st.booleans()))
    tier = draw(st.sampled_from(list(AgentInfluenceTier)))
    allocator = draw(st.sampled_from(["equal_risk", "volatility_parity", "correlation_penalised"]))
    return candidates, snap, policy, tier, allocator


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_world())
def test_final_risk_never_exceeds_tier_base_and_no_step_scales_up(world) -> None:
    candidates, snap, policy, tier, allocator = world
    config = ConstructionConfig(conviction=policy)
    result = PortfolioConstructor(allocator, config, agent_tier=tier).allocate(candidates, snap)
    for a in result.allocations:
        assert a.weight <= config.max_scale_vs_tier + 1e-12
        assert a.risk_pct <= a.base_risk_pct + 1e-12
        assert a.base_risk_pct == config.base_risk_pct(a.candidate.tier)
        for reason in a.trace:
            assert reason.factor <= 1.0 + 1e-12, reason
    open_pct = snap.open_risk_pct
    if open_pct is not None:
        if open_pct >= config.max_total_risk_pct:
            assert result.total_risk_pct == 0.0
        else:
            assert open_pct + result.total_risk_pct <= config.max_total_risk_pct + 1e-9
    else:
        assert result.total_risk_pct <= config.max_total_risk_pct + 1e-9
    for row in result.shadow:
        assert row.applied_factor <= 1.0 and row.would_be_factor <= 1.0
        assert row.would_be_factor >= policy.floor


# -------------------------------------------------------------- the tiers


@pytest.mark.parametrize(
    ("tier", "base"),
    [(StrategyTier.PROBATIONARY, 0.25), (StrategyTier.ESTABLISHED, 0.75),
     (StrategyTier.PROVEN, 1.50), (StrategyTier.FLAGSHIP, 2.00)],
)
def test_tier_base_risk_is_risk_governance_governor_1(tier, base) -> None:
    a = _one(CandidateSignal(signal=_signal(), tier=tier, regime="trend"))
    assert a.base_risk_pct == pytest.approx(base)
    assert a.risk_pct == pytest.approx(base)  # every factor 1.0 on a clean book


def test_the_sizing_ceiling_can_only_lower_the_tier_base() -> None:
    config = ConstructionConfig(risk_ceiling_pct=0.5)
    assert config.base_risk_pct(StrategyTier.FLAGSHIP) == 0.5
    assert config.base_risk_pct(StrategyTier.PROBATIONARY) == 0.25


def test_a_strategy_at_its_tier_concurrency_cap_is_dropped() -> None:
    positions = tuple(make_position(instrument="USDJPY", strategy_id="s1") for _ in range(2))
    a = _one(CandidateSignal(signal=_signal("AUDUSD", "s1"), regime="trend"),
             _snap(positions=positions))
    assert a.dropped and a.drop_reason.startswith("tier:")


# -------------------------------------------------------- the drawdown ladder


@pytest.mark.parametrize(
    ("dd_pct", "tier", "factor", "dropped", "why"),
    [
        (4.99, StrategyTier.ESTABLISHED, 1.0, False, ""),
        (5.00, StrategyTier.ESTABLISHED, 0.6, False, "derisk"),
        (9.99, StrategyTier.ESTABLISHED, 0.6, False, "derisk"),
        (10.0, StrategyTier.ESTABLISHED, 0.3, False, "severe"),
        (10.0, StrategyTier.PROBATIONARY, 0.0, True, "probationary_suspended"),
        (15.0, StrategyTier.FLAGSHIP, 0.0, True, "pause"),
        (20.0, StrategyTier.FLAGSHIP, 0.0, True, "flatten_required"),
    ],
)
def test_the_drawdown_throttle_is_governor_3(dd_pct, tier, factor, dropped, why) -> None:
    # Exact decimal arithmetic: equity = peak - dd% of peak, so dd is exactly dd_pct.
    equity = 100_000.0 - dd_pct * 1_000.0
    a = _one(CandidateSignal(signal=_signal(), tier=tier, regime="trend"),
             _snap(equity=equity, peak=100_000.0))
    step = next(r for r in a.trace if r.step == "drawdown")
    assert step.factor == pytest.approx(factor)
    assert a.dropped is dropped
    assert why in step.detail


# ---------------------------------------------------------------- regime


@pytest.mark.parametrize(
    ("label", "coarse"),
    [
        ("up|normal|trending|deep|calm", "trend"),
        ("down|low|mean_reverting|normal|calm", "range"),
        ("neutral|normal|random|thin|calm", "range"),
        ("up|high|trending|deep|calm", "high_vol"),
        ("up|normal|trending|deep|elevated", "high_vol"),
        ("up|low|trending|deep|stressed", "crisis"),
        ("up|unknown|trending|deep|calm", "unknown"),
        ("garbage", "unknown"),
        (None, "unknown"),
        ("crisis", "crisis"),
    ],
)
def test_the_five_axis_key_maps_onto_the_regime_scalars(label, coarse) -> None:
    assert coarse_regime(label) == coarse


def test_the_candidates_own_regime_beats_the_book_label() -> None:
    a = _one(CandidateSignal(signal=_signal(), regime="up|low|trending|deep|stressed"),
             _snap(regime="trend"))
    assert next(r for r in a.trace if r.step == "regime").factor == pytest.approx(0.25)


# --------------------------------------------------------------- budgets


def test_unmeasured_open_risk_is_said_not_assumed() -> None:
    a = _one(CandidateSignal(signal=_signal(), regime="trend"),
             _snap(positions=(make_position(instrument="GBPUSD", strategy_id="x"),)))
    assert next(r for r in a.trace if r.step == "correlated_risk_budget").detail == (
        "open_risk_unmeasured"
    )
    result = PortfolioConstructor().allocate(
        [CandidateSignal(signal=_signal(), regime="trend")], _snap()
    )
    assert result.stamp["open_risk_measured"] is False
    assert any("NOT MEASURED" in n for n in result.notes)


def test_a_full_book_takes_no_new_risk() -> None:
    a = _one(
        CandidateSignal(signal=_signal("AUDUSD"), regime="trend"),
        _snap(positions=(make_position(instrument="EURJPY", strategy_id="x"),),
              open_risk={"EURJPY": 6_000.0}),
    )
    assert a.dropped and a.drop_reason.startswith("total_risk_cap")


def test_a_correlated_book_with_no_headroom_drops_the_candidate() -> None:
    a = _one(
        CandidateSignal(signal=_signal("AUDUSD"), regime="trend"),
        _snap(positions=(make_position(instrument="EURJPY", strategy_id="x"),),
              open_risk={"EURJPY": 10_000.0}),  # 0.30 x 10% = 3% = the cap
    )
    assert a.dropped and a.drop_reason.startswith("correlated_risk_budget")


# ------------------------------------------------------ down-only config


def test_every_scale_up_path_is_refused_at_configuration() -> None:
    with pytest.raises(ValueError, match="vol_target_max_scale"):
        ConstructionConfig(vol_target_max_scale=1.01)
    with pytest.raises(ValueError, match="max_scale_vs_tier"):
        ConstructionConfig(max_scale_vs_tier=1.5)
    with pytest.raises(ValueError, match="regime_scalars"):
        ConstructionConfig(regime_scalars={"trend": 1.5, "unknown": 0.6})
    with pytest.raises(ValueError, match="lifecycle_scalars"):
        ConstructionConfig(lifecycle_scalars={StrategyLifecycle.WATCH: 1.2})
    with pytest.raises(ValueError, match="drawdown"):
        ConstructionConfig(drawdown_severe_pct=16.0)


def test_the_policy_versions_and_tier_are_stamped_on_every_row() -> None:
    reading = TierReading(tier=AgentInfluenceTier.T1_ANNOTATE_SHADOW, source="record")
    result = PortfolioConstructor(agent_tier=reading).allocate(
        [CandidateSignal(signal=_signal(), regime="trend")], _snap(open_risk={})
    )
    assert result.config_version == CONSTRUCTION_VERSION == "construction_v2"
    for row in result.as_rows():
        assert row["construction_version"] == "construction_v2"
        assert row["conviction_policy"] == "conviction_v1"
        assert row["conviction_effective"] is False
        assert row["agent_tier"] == "t1_annotate_shadow"
        assert row["agent_tier_source"] == "record"
        assert row["open_risk_measured"] is True
        assert row["trace"][0]["step"] == "lifecycle"
        assert row["risk_pct"] <= row["base_risk_pct"]
