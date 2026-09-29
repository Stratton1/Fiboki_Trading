"""Portfolio construction: allocation with the reasoning recorded.

V1 had no portfolio layer. Twelve bots each sized "1% risk" against fleet
equity on six correlated FX majors and the result was called 12% risk.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.enums import StrategyLifecycle
from fiboki.portfolio.construction import (
    ConstructionConfig,
    CorrelationMatrix,
    EqualRiskAllocator,
    PortfolioConstructor,
    get_allocator,
)
from tests.exec_fixtures import make_account, make_candidate, make_position, make_snapshot


def _allocate(candidates, snapshot=None, allocator="equal_risk", config=None):
    return PortfolioConstructor(allocator, config or ConstructionConfig()).allocate(
        candidates, snapshot or make_snapshot()
    )


# ------------------------------------------------------- the interface


@pytest.mark.parametrize(
    "name", ["equal_risk", "volatility_parity", "correlation_penalised"]
)
def test_every_allocator_is_reachable_through_one_interface(name: str) -> None:
    allocator = get_allocator(name)
    assert allocator.name == name
    result = _allocate([make_candidate()], allocator=name)
    assert result.allocator == name


def test_an_unknown_allocator_raises() -> None:
    with pytest.raises(KeyError):
        get_allocator("wishful_thinking")


def test_no_candidates_yields_no_allocations() -> None:
    result = _allocate([])
    assert result.allocations == ()
    assert result.total_weight == 0.0


# ------------------------------------------------------------ allocators


def test_equal_risk_splits_the_budget_evenly() -> None:
    candidates = [
        make_candidate(instrument="EURUSD", strategy_id="a"),
        make_candidate(instrument="USDJPY", strategy_id="b"),
        make_candidate(instrument="AUDUSD", strategy_id="c"),
    ]
    result = _allocate(candidates, allocator="equal_risk")
    weights = {a.candidate.symbol: a.base_weight for a in result.allocations}
    assert len(set(weights.values())) == 1


def test_volatility_parity_gives_the_quieter_instrument_more_risk() -> None:
    quiet = make_candidate(instrument="EURUSD", strategy_id="a", vol=0.05)
    noisy = make_candidate(instrument="XAUUSD", strategy_id="b", vol=0.25)
    # Per-candidate cap lifted so the inverse-vol ratio is visible rather than
    # clipped; the cap itself is tested separately.
    config = ConstructionConfig(max_weight_per_candidate=3.0)
    result = _allocate([quiet, noisy], allocator="volatility_parity", config=config)
    by_symbol = {a.candidate.symbol: a.base_weight for a in result.allocations}
    assert by_symbol["EURUSD"] > by_symbol["XAUUSD"]
    assert by_symbol["EURUSD"] == pytest.approx(by_symbol["XAUUSD"] * 5.0, rel=1e-6)


def test_the_per_candidate_cap_clips_an_allocator_that_would_exceed_it() -> None:
    config = ConstructionConfig(max_weight_per_candidate=0.4)
    result = _allocate([make_candidate()], allocator="volatility_parity", config=config)
    assert result.allocations[0].base_weight == pytest.approx(0.4)


def test_correlation_penalised_rewards_the_diversifier() -> None:
    corr = CorrelationMatrix.from_mapping(
        {
            ("EURUSD", "GBPUSD"): 0.90,
            ("EURUSD", "USDJPY"): -0.10,
            ("GBPUSD", "USDJPY"): -0.05,
        }
    )
    snapshot = make_snapshot(instrument_correlation=corr)
    candidates = [
        make_candidate(instrument="EURUSD", strategy_id="a"),
        make_candidate(instrument="GBPUSD", strategy_id="b"),
        make_candidate(instrument="USDJPY", strategy_id="c"),
    ]
    result = _allocate(candidates, snapshot, allocator="correlation_penalised")
    by_symbol = {a.candidate.symbol: a.base_weight for a in result.allocations}
    assert by_symbol["USDJPY"] > by_symbol["EURUSD"]
    assert by_symbol["USDJPY"] > by_symbol["GBPUSD"]


def test_a_single_candidate_takes_the_whole_budget_under_any_allocator() -> None:
    for name in ("equal_risk", "volatility_parity", "correlation_penalised"):
        result = _allocate([make_candidate()], allocator=name)
        assert result.allocations[0].base_weight > 0


# -------------------------------------------------- recorded reasoning


def test_every_adjustment_is_recorded_with_its_factor() -> None:
    candidate = make_candidate(confidence=0.6, health=0.8)
    result = _allocate([candidate])
    allocation = result.allocations[0]
    steps = {r.step: r.factor for r in allocation.reasons}
    assert steps["confidence"] == pytest.approx(0.6)
    assert steps["health"] == pytest.approx(0.8)
    assert "regime" in steps
    assert allocation.explanation


def test_a_dropped_candidate_records_why() -> None:
    candidate = make_candidate(lifecycle=StrategyLifecycle.QUARANTINED)
    allocation = _allocate([candidate]).allocations[0]
    assert allocation.dropped
    assert "lifecycle" in allocation.drop_reason
    assert allocation.weight == 0.0


def test_the_result_serialises_its_reasoning() -> None:
    rows = _allocate([make_candidate(confidence=0.5)]).as_rows()
    assert rows[0]["reasons"]
    assert rows[0]["instrument"] == "EURUSD"


def test_the_config_version_is_stamped_on_the_result() -> None:
    result = _allocate([make_candidate()], config=ConstructionConfig(version="cfg_v9"))
    assert result.config_version == "cfg_v9"


# ------------------------------------------------------ the adjustments


def test_confidence_scales_the_allocation_linearly() -> None:
    full = _allocate([make_candidate(confidence=1.0)]).allocations[0].weight
    half = _allocate([make_candidate(confidence=0.5)]).allocations[0].weight
    assert half == pytest.approx(full * 0.5)


def test_zero_confidence_drops_rather_than_allocating_a_sliver() -> None:
    allocation = _allocate([make_candidate(confidence=0.0)]).allocations[0]
    assert allocation.dropped


def test_strategy_degradation_reduces_risk_and_full_degradation_drops() -> None:
    degrading = _allocate([make_candidate(health=0.4)]).allocations[0]
    assert not degrading.dropped
    broken = _allocate([make_candidate(health=0.0)]).allocations[0]
    assert broken.dropped
    assert "degraded" in broken.drop_reason


@pytest.mark.parametrize(
    "lifecycle",
    [StrategyLifecycle.QUARANTINED, StrategyLifecycle.RETIRED, StrategyLifecycle.RESEARCH],
)
def test_ineligible_lifecycles_receive_nothing(lifecycle) -> None:
    assert _allocate([make_candidate(lifecycle=lifecycle)]).allocations[0].dropped


def test_watch_lifecycle_trades_at_reduced_size() -> None:
    normal = _allocate([make_candidate(lifecycle=StrategyLifecycle.APPROVED)])
    watched = _allocate([make_candidate(lifecycle=StrategyLifecycle.WATCH)])
    assert watched.allocations[0].weight < normal.allocations[0].weight


def test_instrument_correlation_with_the_open_book_reduces_the_allocation() -> None:
    corr = CorrelationMatrix.from_mapping({("EURUSD", "GBPUSD"): 0.75})
    held = make_snapshot(
        positions=(make_position(instrument="GBPUSD", strategy_id="other"),),
        instrument_correlation=corr,
        strategy_correlation=CorrelationMatrix.from_mapping(
            {("ichimoku_a", "other"): 0.0}
        ),
    )
    flat = make_snapshot()
    with_book = _allocate([make_candidate()], held).allocations[0].weight
    without = _allocate([make_candidate()], flat).allocations[0].weight
    assert with_book < without


def test_a_correlation_above_the_hard_cap_drops_the_candidate() -> None:
    corr = CorrelationMatrix.from_mapping({("EURUSD", "GBPUSD"): 0.97})
    snapshot = make_snapshot(
        positions=(make_position(instrument="GBPUSD", strategy_id="ichimoku_a"),),
        instrument_correlation=corr,
    )
    allocation = _allocate([make_candidate()], snapshot).allocations[0]
    assert allocation.dropped
    assert "hard_cap" in allocation.drop_reason


def test_unmeasured_correlation_defaults_to_a_POSITIVE_assumption() -> None:
    """Assuming zero correlation where you have not measured is the dangerous
    default: maximally permissive exactly where you know least."""
    config = ConstructionConfig()
    assert config.unmeasured_correlation > 0.0
    assert CorrelationMatrix(default=0.3).get("A", "B") == 0.3


def test_strategy_correlation_is_considered_separately_from_instruments() -> None:
    snapshot = make_snapshot(
        positions=(make_position(instrument="USDJPY", strategy_id="other"),),
        instrument_correlation=CorrelationMatrix.from_mapping(
            {("EURUSD", "USDJPY"): 0.0}
        ),
        strategy_correlation=CorrelationMatrix.from_mapping(
            {("ichimoku_a", "other"): 0.80}
        ),
    )
    allocation = _allocate([make_candidate()], snapshot).allocations[0]
    steps = {r.step for r in allocation.reasons}
    assert "strategy_correlation" in steps
    assert allocation.weight < 1.0


def test_instrument_concentration_scales_down_as_the_cap_is_approached() -> None:
    equity = 100_000.0
    light = make_snapshot(equity=equity, instrument_exposure={"EURUSD": 100_000.0})
    heavy = make_snapshot(equity=equity, instrument_exposure={"EURUSD": 900_000.0})
    assert (
        _allocate([make_candidate()], heavy).allocations[0].weight
        < _allocate([make_candidate()], light).allocations[0].weight
    )


def test_hitting_the_instrument_cap_drops_the_candidate() -> None:
    snapshot = make_snapshot(equity=100_000.0, instrument_exposure={"EURUSD": 5_000_000.0})
    assert _allocate([make_candidate()], snapshot).allocations[0].dropped


def test_asset_class_concentration_is_considered() -> None:
    snapshot = make_snapshot(
        equity=100_000.0, asset_class_exposure={"fx_major": 50_000_000.0}
    )
    allocation = _allocate([make_candidate()], snapshot).allocations[0]
    assert allocation.dropped
    assert "fx_major" in allocation.drop_reason


def test_currency_exposure_considers_both_legs_of_the_pair() -> None:
    base_heavy = make_snapshot(equity=100_000.0, currency_exposure={"EUR": 1_000_000.0})
    quote_heavy = make_snapshot(equity=100_000.0, currency_exposure={"USD": 1_000_000.0})
    flat = make_snapshot(equity=100_000.0)
    for snapshot in (base_heavy, quote_heavy):
        assert (
            _allocate([make_candidate()], snapshot).allocations[0].weight
            < _allocate([make_candidate()], flat).allocations[0].weight
        )


def test_volatility_targeting_scales_down_a_hot_portfolio() -> None:
    config = ConstructionConfig(target_portfolio_vol=0.10)
    hot = make_snapshot(realised_portfolio_vol=0.30)
    calm = make_snapshot(realised_portfolio_vol=0.05)
    hot_w = _allocate([make_candidate()], hot, config=config).allocations[0].weight
    calm_w = _allocate([make_candidate()], calm, config=config).allocations[0].weight
    assert hot_w < calm_w


def test_volatility_targeting_never_scales_up() -> None:
    """construction_v2 (audit F P1-11): v1 allowed a 1.5x scale-up here, which
    fought the per-trade gateway cap and the down-only principle. A scale above
    1.0 is now refused at configuration, and a very calm book gets exactly 1.0."""
    with pytest.raises(ValueError, match="vol_target_max_scale"):
        ConstructionConfig(target_portfolio_vol=0.10, vol_target_max_scale=1.5)
    config = ConstructionConfig(target_portfolio_vol=0.10)
    snapshot = make_snapshot(realised_portfolio_vol=0.001)
    allocation = _allocate([make_candidate()], snapshot, config=config).allocations[0]
    factor = next(r.factor for r in allocation.trace if r.step == "volatility_target")
    assert factor == 1.0
    assert allocation.weight <= allocation.base_weight


def test_an_unmeasured_portfolio_vol_is_neutral_not_a_free_scale_up() -> None:
    snapshot = make_snapshot(realised_portfolio_vol=0.0)
    allocation = _allocate([make_candidate()], snapshot).allocations[0]
    factors = [r.factor for r in allocation.reasons if r.step == "volatility_target"]
    assert factors == []  # factor was exactly 1.0, so nothing was recorded


def test_margin_utilisation_reduces_and_then_blocks() -> None:
    light = make_snapshot(account=make_account(100_000.0, margin_used=5_000.0))
    heavy = make_snapshot(account=make_account(100_000.0, margin_used=45_000.0))
    over = make_snapshot(account=make_account(100_000.0, margin_used=90_000.0))
    assert (
        _allocate([make_candidate()], heavy).allocations[0].weight
        < _allocate([make_candidate()], light).allocations[0].weight
    )
    assert _allocate([make_candidate()], over).allocations[0].dropped


def test_drawdown_derisks_linearly_and_then_stops_allocating() -> None:
    config = ConstructionConfig(drawdown_derisk_start_pct=5.0, drawdown_zero_pct=20.0)
    def snap(equity):
        return make_snapshot(account=make_account(equity, peak_equity=100_000.0))

    shallow = _allocate([make_candidate()], snap(97_000.0), config=config)
    mid = _allocate([make_candidate()], snap(88_000.0), config=config)
    deep = _allocate([make_candidate()], snap(75_000.0), config=config)
    assert shallow.allocations[0].weight > mid.allocations[0].weight
    assert deep.allocations[0].dropped


def test_regime_scales_the_allocation() -> None:
    trend = _allocate([make_candidate()], make_snapshot(regime="trend"))
    crisis = _allocate([make_candidate()], make_snapshot(regime="crisis"))
    assert crisis.allocations[0].weight < trend.allocations[0].weight


def test_an_unknown_regime_is_treated_conservatively() -> None:
    unknown = _allocate([make_candidate()], make_snapshot(regime="something_new"))
    trend = _allocate([make_candidate()], make_snapshot(regime="trend"))
    assert unknown.allocations[0].weight < trend.allocations[0].weight


# ------------------------------------------------------- the total budget


def test_the_total_budget_caps_the_sum_across_candidates() -> None:
    """Twenty simultaneous candidates must not spend twenty risk budgets."""
    config = ConstructionConfig(max_total_weight=2.0, min_weight_to_trade=0.0)
    candidates = [
        make_candidate(instrument=sym, strategy_id=f"s{i}")
        for i, sym in enumerate(
            ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]
        )
    ]
    result = _allocate(candidates, make_snapshot(regime="trend"), config=config)
    assert result.total_weight <= config.max_total_weight + 1e-9


def test_scaling_to_the_total_budget_is_recorded_as_a_reason() -> None:
    """Every per-candidate step is now down-only (construction_v2), so the only
    way the aggregate can exceed the budget is an allocator that over-allocates
    its BASE weights. The total cap is what catches it, and says so."""

    class _Greedy(EqualRiskAllocator):
        name = "greedy_test_allocator"

        def base_weights(self, candidates, snapshot, config):
            return {c.key: 1.0 for c in candidates}

    config = ConstructionConfig(
        max_total_weight=1.0,
        max_weight_per_candidate=1.0,
        min_weight_to_trade=0.0,
    )
    candidates = [
        make_candidate(instrument="EURUSD", strategy_id="a"),
        make_candidate(instrument="USDJPY", strategy_id="b"),
    ]
    snapshot = make_snapshot(regime="trend")
    result = PortfolioConstructor(_Greedy(), config).allocate(candidates, snapshot)

    steps = {r.step for a in result.accepted for r in a.reasons}
    assert "total_budget" in steps
    assert result.notes
    assert result.total_weight <= config.max_total_weight + 1e-9


def test_a_weight_below_the_minimum_is_dropped_rather_than_traded_tiny() -> None:
    config = ConstructionConfig(min_weight_to_trade=0.9)
    allocation = _allocate([make_candidate(confidence=0.2)], config=config).allocations[0]
    assert allocation.dropped
    assert "below_min_weight" in allocation.drop_reason


def test_weight_for_returns_zero_for_an_unknown_signal() -> None:
    assert _allocate([make_candidate()]).weight_for("nope") == 0.0


# -------------------------------------------------------- determinism


def test_allocation_is_independent_of_candidate_ordering() -> None:
    candidates = [
        make_candidate(instrument="EURUSD", strategy_id="a"),
        make_candidate(instrument="USDJPY", strategy_id="b"),
        make_candidate(instrument="AUDUSD", strategy_id="c"),
    ]
    snapshot = make_snapshot()
    forward = _allocate(candidates, snapshot)
    backward = _allocate(list(reversed(candidates)), snapshot)
    assert [a.signal_id for a in forward.allocations] == [
        a.signal_id for a in backward.allocations
    ]
    assert [a.weight for a in forward.allocations] == [
        a.weight for a in backward.allocations
    ]


# ------------------------------------------------- input validation


def test_a_zero_volatility_candidate_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="annualised_vol"):
        make_candidate(vol=0.0)


def test_health_outside_zero_to_one_is_refused() -> None:
    with pytest.raises(ValueError):
        make_candidate(health=1.5)


def test_a_correlation_matrix_must_be_square_and_bounded() -> None:
    with pytest.raises(ValueError):
        CorrelationMatrix(labels=("A", "B"), matrix=((1.0,),))
    with pytest.raises(ValueError):
        CorrelationMatrix(labels=("A",), matrix=((1.5,),))


def test_a_correlation_matrix_is_symmetric_and_unit_diagonal() -> None:
    corr = CorrelationMatrix.from_mapping({("A", "B"): 0.4})
    assert corr.get("A", "B") == corr.get("B", "A") == 0.4
    assert corr.get("A", "A") == 1.0


def test_an_incoherent_config_is_refused() -> None:
    with pytest.raises(ValueError):
        ConstructionConfig(max_total_weight=0.0)
    with pytest.raises(ValueError):
        ConstructionConfig(correlation_soft_cap=0.9, correlation_hard_cap=0.5)
    with pytest.raises(ValueError):
        ConstructionConfig(drawdown_derisk_start_pct=20.0, drawdown_zero_pct=10.0)


def test_snapshot_exposes_drawdown_and_margin_utilisation() -> None:
    snapshot = make_snapshot(
        account=make_account(80_000.0, peak_equity=100_000.0, margin_used=20_000.0)
    )
    assert snapshot.drawdown_pct == pytest.approx(20.0)
    assert snapshot.margin_utilisation == pytest.approx(0.25)
    assert isinstance(snapshot.as_of, pd.Timestamp)
