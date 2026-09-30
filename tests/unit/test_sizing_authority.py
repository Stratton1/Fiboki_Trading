"""The size is decided once and survives unchanged to the venue.

V1 sized the same signal three times (paper bot off fleet equity, router off
allocated capital, IG adapter off the live broker balance). These tests pin the
V2 invariant: one number, computed in one place, carried untouched.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    SIZING_POLICY_V1,
    SIZING_POLICY_V2,
    FixedFractionalSizer,
    _snap_to_step,
)
from fiboki.broker.execution_service import (
    ExecutionService,
    InMemoryIntentStore,
    IntentState,
)
from fiboki.broker.simulated_venue import SimulatedVenue, VenueConfig
from fiboki.core.enums import Direction, ExecutionMode
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import round_size
from fiboki.portfolio.sizing import (
    PortfolioSizer,
    SizingPolicy,
    SizingRejection,
    size_trade,
)
from fiboki.portfolio.sizing import (
    _snap_to_step as sizing_snap,
)
from fiboki.risk.gateway import RiskGateway
from fiboki.sim.profiles import IG_REALISTIC
from tests.exec_fixtures import NOW, make_account, make_context, make_signal


def _size(**kwargs):
    sig = kwargs.pop("signal", None) or make_signal(**kwargs.pop("signal_kwargs", {}))
    return size_trade(
        signal=sig,
        instrument=get_instrument(sig.instrument),
        account=kwargs.pop("account", None) or make_account(kwargs.pop("equity", 100_000.0)),
        fx_quote_to_account=kwargs.pop("fx", 1.0),
        policy=kwargs.pop("policy", SizingPolicy()),
        portfolio_weight=kwargs.pop("portfolio_weight", 1.0),
    )


# ---------------------------------------------------------------- the rule


#: The v1 rule, selected explicitly. The three tests below pin its arithmetic
#: (bare stop distance) and were written before fixed_fractional_v2 became the
#: default in engine_v3_realism; the v2 arithmetic is pinned in
#: tests/golden/test_golden_sizing_v2.py.
V1 = SIZING_POLICY_V1


def test_risk_fraction_produces_the_intended_loss_at_the_stop() -> None:
    sig = make_signal(reference_price=1.1000, stop_distance=0.0050)
    out = _size(
        signal=sig, equity=50_000.0, policy=SizingPolicy(risk_fraction=0.01, policy_id=V1)
    )
    plan = out.require()
    # 1% of 50,000 = 500 risked over a 0.0050 stop on a contract size of 1.
    assert plan.size == pytest.approx(100_000.0)
    assert plan.risk_amount == pytest.approx(500.0)


def test_size_is_rounded_DOWN_to_the_instrument_step() -> None:
    """Never up. Rounding up manufactures risk the rule did not intend."""
    # size_step 0.1 since 2026-09-30 (OANDA XAU_USD tradeUnitsPrecision 1,
    # tests/fixtures/oanda/practice_instruments_2026-09-30.json); it was 0.01.
    instrument = get_instrument("XAUUSD")
    sig = make_signal(instrument="XAUUSD", reference_price=2000.0, stop_distance=7.0,
                      take_profit_distance=50.0)
    out = size_trade(
        signal=sig,
        instrument=instrument,
        account=make_account(10_000.0),
        fx_quote_to_account=1.0,
        policy=SizingPolicy(risk_fraction=0.01, policy_id=V1),
    )
    plan = out.require()
    raw = 100.0 / 7.0  # 14.2857...
    assert plan.size == round_size(instrument, raw)
    # 14.2857 rounded DOWN to a 0.1 step is 14.2 (it was 14.28 at a 0.01 step).
    assert plan.size == pytest.approx(14.2)
    assert plan.size < raw, "rounding must never increase the position"


def test_recorded_risk_is_the_risk_actually_taken_after_rounding() -> None:
    instrument = get_instrument("XAUUSD")
    sig = make_signal(instrument="XAUUSD", reference_price=2000.0, stop_distance=7.0,
                      take_profit_distance=50.0)
    plan = size_trade(
        signal=sig, instrument=instrument, account=make_account(10_000.0),
        fx_quote_to_account=1.0, policy=SizingPolicy(risk_fraction=0.01, policy_id=V1),
    ).require()
    assert plan.risk_amount == pytest.approx(plan.size * 7.0)
    assert plan.risk_amount < 100.0, "rounded-down size must risk less, not more"


def test_leverage_cap_binds_and_is_recorded() -> None:
    # A very tight stop wants an enormous position; the leverage cap must bind.
    sig = make_signal(reference_price=1.1000, stop_distance=0.0001, take_profit_distance=0.0050)
    out = _size(signal=sig, equity=10_000.0, policy=SizingPolicy(risk_fraction=0.01))
    plan = out.require()
    instrument = get_instrument("EURUSD")
    max_by_leverage = 10_000.0 * instrument.retail_leverage / 1.1000
    assert plan.size <= max_by_leverage
    assert out.detail["leverage_binding"] is True
    assert plan.max_leverage_applied == instrument.retail_leverage


def test_policy_cannot_request_more_leverage_than_the_instrument_permits() -> None:
    instrument = get_instrument("EURUSD")  # retail_leverage 30
    policy = SizingPolicy(risk_fraction=0.01, max_leverage=500.0)
    assert policy.leverage_for(instrument) == instrument.retail_leverage


def test_policy_may_request_less_leverage() -> None:
    assert SizingPolicy(max_leverage=5.0).leverage_for(get_instrument("EURUSD")) == 5.0


def test_portfolio_weight_scales_the_risk_budget() -> None:
    full = _size(equity=100_000.0).require()
    half = _size(equity=100_000.0, portfolio_weight=0.5).require()
    step = get_instrument("EURUSD").size_step
    # Half the budget, then rounded DOWN to the size step -- so at most one step
    # below the exact half, never above it.
    assert full.size / 2.0 - step < half.size <= full.size / 2.0
    assert half.portfolio_weight == 0.5


# ---------------------------------------------------------- refusals


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"equity": 0.0}, SizingRejection.NON_POSITIVE_EQUITY),
        ({"fx": 0.0}, SizingRejection.INVALID_FX_RATE),
        ({"fx": float("nan")}, SizingRejection.INVALID_FX_RATE),
        ({"portfolio_weight": 0.0}, SizingRejection.ZERO_PORTFOLIO_WEIGHT),
    ],
)
def test_refusals_are_named_never_a_silent_zero(kwargs, reason) -> None:
    out = _size(**kwargs)
    assert out.plan is None
    assert out.reason == reason
    with pytest.raises(ValueError):
        out.require()


def test_a_size_that_rounds_below_the_minimum_is_refused_not_truncated() -> None:
    instrument = get_instrument("EURUSD")  # min_size 1.0, step 1.0
    sig = make_signal(reference_price=1.1000, stop_distance=0.0500, take_profit_distance=0.1000)
    out = size_trade(
        signal=sig, instrument=instrument, account=make_account(1.0),
        fx_quote_to_account=1.0, policy=SizingPolicy(risk_fraction=0.001),
    )
    assert out.plan is None
    assert out.reason in (SizingRejection.ROUNDED_TO_ZERO, SizingRejection.BELOW_MIN_SIZE)


def test_sizing_outcome_cannot_carry_both_or_neither() -> None:
    from fiboki.portfolio.sizing import SizingOutcome

    with pytest.raises(ValueError):
        SizingOutcome(None, None)


# ------------------------------------------------- parity with the engine


@pytest.mark.parametrize("symbol", ["EURUSD", "XAUUSD", "US500"])
@pytest.mark.parametrize("policy_id", [SIZING_POLICY_V1, SIZING_POLICY_V2])
def test_portfolio_sizer_matches_the_engines_fixed_fractional_sizer(symbol, policy_id) -> None:
    """The backtester and live must not be able to disagree about size.

    Under both rules, and under v2 whether the profile is named or left to the
    default (IG_REALISTIC on both sides).
    """
    instrument = get_instrument(symbol)
    ref = {"EURUSD": 1.1000, "XAUUSD": 2000.0, "US500": 5000.0}[symbol]
    stop = {"EURUSD": 0.0037, "XAUUSD": 7.3, "US500": 11.0}[symbol]
    sig = make_signal(
        instrument=symbol, reference_price=ref, stop_distance=stop,
        take_profit_distance=stop * 3,
    )
    account = make_account(83_333.0)
    for fx in (1.0, 0.7912, 1.2634):
        ours = PortfolioSizer(
            policy=SizingPolicy(risk_fraction=0.01, policy_id=policy_id)
        ).size_for(sig, instrument, account, fx)
        theirs = FixedFractionalSizer(
            risk_fraction=0.01, policy_id=policy_id, cost_profile=IG_REALISTIC
        ).size_for(sig, instrument, account, fx)
        assert ours == theirs, f"sizing authority diverged from the engine at fx={fx}"


def test_the_float_snap_helper_matches_the_engines() -> None:
    for value, step in [
        (249999.99999999997, 1.0),
        (14.279999999999999, 0.01),
        (0.30000000000000004, 0.1),
        (1234.5, 1.0),
    ]:
        assert sizing_snap(value, step) == _snap_to_step(value, step)


# --------------------------------------- the size survives to the Order


def test_plan_size_survives_unchanged_all_the_way_to_the_order() -> None:
    """The end-to-end invariant: sizing -> gateway -> Order -> venue -> fill."""
    ctx = make_context()
    plan = ctx.plan
    venue = SimulatedVenue(VenueConfig(prices={"EURUSD": 1.1000}), now=NOW)
    service = ExecutionService(
        adapter=venue,
        gateway=RiskGateway(),
        store=InMemoryIntentStore(),
        mode=ExecutionMode.PAPER,
    )

    outcome = service.submit(plan, ctx)
    assert outcome.accepted, outcome.reason

    # 1. the intent record
    assert outcome.intent.size == plan.size
    # 2. the order the venue received
    assert venue.requests[0]["size"] == plan.size
    # 3. the venue's own record
    record = venue.record_for_client_ref(outcome.intent.client_ref)
    assert record.requested_size == plan.size
    # 4. the fill
    assert outcome.ack.fill.filled_size == plan.size
    assert outcome.intent.filled_size == plan.size
    assert outcome.intent.state is IntentState.FILLED


def test_a_partial_fill_records_the_shortfall_rather_than_hiding_it() -> None:
    ctx = make_context()
    plan = ctx.plan
    venue = SimulatedVenue(
        VenueConfig(partial_fill_fraction=0.5, prices={"EURUSD": 1.1000}), now=NOW
    )
    service = ExecutionService(
        adapter=venue, gateway=RiskGateway(), store=InMemoryIntentStore(),
        mode=ExecutionMode.PAPER,
    )
    outcome = service.submit(plan, ctx)
    # The REQUESTED size is still the plan's -- the adapter did not re-decide it.
    assert venue.requests[0]["size"] == plan.size
    assert outcome.ack.fill.filled_size < plan.size
    assert outcome.ack.fill.partial is True


def test_direction_and_stop_reach_the_order_unaltered() -> None:
    sig = make_signal(direction=Direction.SHORT, reference_price=1.1000,
                      stop_distance=0.0030, take_profit_distance=0.0060)
    ctx = make_context(
        plan=None if False else __import__("tests.exec_fixtures", fromlist=["make_plan"]).make_plan(signal=sig)
    )
    venue = SimulatedVenue(VenueConfig(prices={"EURUSD": 1.1000}), now=NOW)
    service = ExecutionService(
        adapter=venue, gateway=RiskGateway(), store=InMemoryIntentStore(),
        mode=ExecutionMode.PAPER,
    )
    outcome = service.submit(ctx.plan, ctx)
    assert outcome.intent.direction is Direction.SHORT
    assert outcome.intent.stop_loss == pytest.approx(sig.stop_price)
    assert isinstance(outcome.intent.created_at, pd.Timestamp)
