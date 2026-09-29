"""fixed_fractional_v2: the risk per unit includes the costs of being stopped out.

Audit finding P1-16. A position stopped exactly at its level does not lose the
stop distance; with both-leg costs (``sim/fills.py``) it loses

    stop_distance + half_spread (entry) + half_spread (exit)
                  + E[slippage] (market entry) + E[slippage] (stop exit)

so a sizer that divides the risk budget by the bare stop distance risks MORE
than its stated fraction on every trade. Every number below is worked by hand.

IG_REALISTIC, EURUSD (typical spread 0.9 pips, pip 0.0001):

* spread at an ordinary hour = max(0.9 * 1.0, floor 0.6) = 0.9 pips = 0.00009
* E[slippage] per fill = p * mean(1..3 ticks) * tick = 0.30 * 2 * 0.4 = 0.24 pips
  = 0.000024; two fills = 0.000048
* cost per unit = 0.00009 + 0.000048 = 0.000138
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    SIZING_POLICY_V1,
    SIZING_POLICY_V2,
    FixedFractionalSizer,
    expected_entry_time,
    stop_out_cost_per_unit,
)
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction
from fiboki.core.instruments import get as get_instrument
from fiboki.portfolio.sizing import SizingPolicy, size_trade
from fiboki.risk.gateway import RiskGateway
from fiboki.sim.profiles import IG_REALISTIC, expected_slippage_price
from tests.exec_fixtures import make_account, make_context, make_snapshot

pytestmark = pytest.mark.golden

EURUSD = get_instrument("EURUSD")


def _signal(*, stop_distance: float, bar_time: str = "2024-06-03 12:00") -> Signal:
    return Signal(
        strategy_id="golden_v2",
        instrument="EURUSD",
        timeframe="H1",
        direction=Direction.LONG,
        bar_time=pd.Timestamp(bar_time, tz="UTC"),
        reference_price=1.1000,
        stop_price=1.1000 - stop_distance,
        take_profit_prices=(1.1000 + 3 * stop_distance,),
    )


def test_expected_slippage_of_the_ig_profile() -> None:
    """0.30 * (1 + 3) / 2 * 0.4 pips = 0.24 pips = 0.000024 on EURUSD."""
    assert expected_slippage_price(IG_REALISTIC.slippage, EURUSD) == pytest.approx(0.000024, abs=1e-15)


def test_the_entry_is_expected_at_the_next_bar_open() -> None:
    """An H1 signal on the 12:00 bar is acted on at the 13:00 open."""
    sig = _signal(stop_distance=0.0005)
    assert expected_entry_time(sig) == pd.Timestamp("2024-06-03 13:00", tz="UTC")


def test_cost_per_unit_at_an_ordinary_hour() -> None:
    """0.9 pips spread + 2 * 0.24 pips slippage = 1.38 pips = 0.000138."""
    sig = _signal(stop_distance=0.0005)
    assert stop_out_cost_per_unit(IG_REALISTIC, EURUSD, sig) == pytest.approx(0.000138, abs=1e-15)


def test_cost_per_unit_inside_the_rollover_window() -> None:
    """Signal on the 20:00 bar enters at 21:00, inside IG's (21, 23) x3 window:
    spread 0.9 * 3 = 2.7 pips; + 0.48 pips slippage = 3.18 pips = 0.000318."""
    sig = _signal(stop_distance=0.0005, bar_time="2024-06-03 20:00")
    assert stop_out_cost_per_unit(IG_REALISTIC, EURUSD, sig) == pytest.approx(0.000318, abs=1e-15)


def test_v2_size_on_a_five_pip_stop() -> None:
    """
    Equity 50,000 GBP, risk 1% = 500, fx 1.0 (the arithmetic, not a real rate).

      v1: risk per unit = 0.0005                     -> 500 / 0.0005   = 1,000,000 units
      v2: risk per unit = 0.0005 + 0.000138 = 0.000638 -> 500 / 0.000638 = 783,699.06
                                                        -> rounded DOWN = 783,699 units

    Leverage cap 50,000 * 30 / 1.1 = 1,363,636 units: not binding.
    Realised loss at the stop, v1: 1,000,000 * 0.000638 = 638.00 = 1.276% of
    equity, i.e. 27.6% above the stated 1%. v2: 783,699 * 0.000638 = 499.999962.
    """
    sig = _signal(stop_distance=0.0005)
    account = make_account(50_000.0)
    v1 = size_trade(
        signal=sig, instrument=EURUSD, account=account, fx_quote_to_account=1.0,
        policy=SizingPolicy(risk_fraction=0.01, policy_id=SIZING_POLICY_V1),
    ).require()
    v2 = size_trade(
        signal=sig, instrument=EURUSD, account=account, fx_quote_to_account=1.0,
        policy=SizingPolicy(risk_fraction=0.01, policy_id=SIZING_POLICY_V2),
    ).require()
    assert v1.size == 1_000_000.0
    assert v2.size == 783_699.0
    assert v1.risk_amount == pytest.approx(500.0, abs=1e-6)
    assert v2.risk_amount == pytest.approx(783_699 * 0.000638, abs=1e-6)
    assert v2.risk_amount <= 500.0
    assert v1.size * 0.000638 == pytest.approx(638.0, abs=1e-6)
    assert v2.sizing_basis == "fixed_fractional_v2:IG_REALISTIC"
    assert v1.sizing_basis == "fixed_fractional_v1"


def test_the_engine_sizer_agrees_to_the_unit() -> None:
    sig = _signal(stop_distance=0.0005)
    size = FixedFractionalSizer(risk_fraction=0.01).size_for(
        sig, EURUSD, make_account(50_000.0), 1.0
    )
    assert size == 783_699.0


def test_the_default_policy_is_v2() -> None:
    assert SizingPolicy().policy_id == SIZING_POLICY_V2
    assert FixedFractionalSizer().policy_id == SIZING_POLICY_V2


def test_the_gateway_checks_the_same_risk_amount_the_sizer_recorded() -> None:
    """``max_per_trade_risk`` reads ``plan.risk_amount``: under v2 that is the
    stop-out loss INCLUDING costs, 499.999962 of 50,000 = 0.999999924%, which passes
    the 1% limit. A v1 plan records 500.00 while the same stop-out actually loses
    638.00 -- the understatement the gateway could not see."""
    sig = _signal(stop_distance=0.0005)
    account = make_account(50_000.0)
    plan = size_trade(
        signal=sig, instrument=EURUSD, account=account, fx_quote_to_account=1.0,
        policy=SizingPolicy(risk_fraction=0.01),
    ).require()
    ctx = make_context(plan, snapshot=make_snapshot(equity=50_000.0))
    decision = RiskGateway().evaluate(ctx)
    assert not any(r.startswith("max_per_trade_risk") for r in decision.reasons), decision.reasons
    assert plan.risk_amount == pytest.approx(499.999962, abs=1e-9)
    assert plan.risk_amount / 50_000.0 * 100.0 == pytest.approx(0.999999924, abs=1e-12)
