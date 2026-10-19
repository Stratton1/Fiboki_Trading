"""Risk of ruin and position size (ops/risk_calc.py): the platform's own sizing, seeded ruin."""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.contracts import AccountState, Signal
from fiboki.core.enums import Direction
from fiboki.core.instruments import get as get_instrument
from fiboki.ops.risk_calc import SizeRequest, position_sizes, risk_of_ruin
from fiboki.portfolio.sizing import SizingPolicy, size_trade
from fiboki.sim.profiles import get_profile

R = [1.5, -1.0, -1.0, 2.2, -1.0, 0.4, -1.0, 3.0, -1.0, -1.0, 1.1, -0.6] * 10


def test_sizes_are_exactly_size_trade_s_and_the_leverage_cap_binds():
    rows = position_sizes(
        [SizeRequest("EURUSD", 1.1000, 20.0), SizeRequest("EURUSD", 1.1000, 0.1)],
        account_size=100_000.0, account_ccy="USD", risk_fraction=0.01,
        policy_id="fixed_fractional_v2", profile_name="OANDA_REALISTIC",
    )
    inst = get_instrument("EURUSD")
    direct = size_trade(
        signal=Signal(strategy_id="x", instrument="EURUSD", timeframe="H4", direction=Direction.LONG,
                      bar_time=pd.Timestamp("2026-01-01T00:00:00Z"), reference_price=1.1,
                      stop_price=1.1 - 20 * inst.pip_size),
        instrument=inst, account=AccountState(balance=100_000.0, equity=100_000.0, currency="USD"),
        fx_quote_to_account=1.0,
        policy=SizingPolicy(risk_fraction=0.01, policy_id="fixed_fractional_v2", cost_profile=get_profile("OANDA_REALISTIC")),
    )
    assert rows[0].size == direct.plan.size and rows[0].risk_amount == direct.plan.risk_amount
    assert not rows[0].leverage_binding and rows[0].rejection is None
    assert rows[1].leverage_binding, "a 0.1 pip stop is capped by the instrument's retail leverage"
    assert rows[1].notional <= 100_000.0 * rows[1].leverage_cap + 1e-6


def test_a_cross_currency_instrument_needs_a_rate_and_unknowns_are_named():
    rows = position_sizes(
        [SizeRequest("USDJPY", 150.0, 30.0), SizeRequest("USDJPY", 150.0, 30.0, 1 / 150.0), SizeRequest("NOPE", 1.0, 1.0)],
        account_size=50_000.0, account_ccy="USD", risk_fraction=0.005,
        policy_id="fixed_fractional_v2", profile_name="OANDA_REALISTIC",
    )
    assert rows[0].size is None and rows[0].rejection.startswith("fx_rate_required")
    assert rows[1].size is not None and rows[1].size > 0
    assert rows[2].rejection == "unknown_instrument"


def test_risk_of_ruin_is_seeded_and_rises_with_risk():
    a = risk_of_ruin(R, risk_fraction=0.02, ruin_drawdown_pct=30.0, n_paths=2_000)
    b = risk_of_ruin(R, risk_fraction=0.02, ruin_drawdown_pct=30.0, n_paths=2_000)
    assert a == b
    low = risk_of_ruin(R, risk_fraction=0.005, ruin_drawdown_pct=30.0, n_paths=2_000)
    high = risk_of_ruin(R, risk_fraction=0.05, ruin_drawdown_pct=30.0, n_paths=2_000)
    assert low.probability <= a.probability <= high.probability
    assert high.probability > low.probability
    assert a.horizon_trades == len(R) and a.max_dd_p50 <= a.max_dd_p95 <= a.max_dd_p99
    with pytest.raises(ValueError):
        risk_of_ruin([1.0], risk_fraction=0.01, ruin_drawdown_pct=50)
