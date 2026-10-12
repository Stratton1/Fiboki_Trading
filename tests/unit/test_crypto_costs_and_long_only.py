"""Spot crypto: the cost model, the profile guard, and long-only in the risk layer.

* ``BpsOfMidSpread`` and ``BpsSlippage`` are proportions of price, and a missing
  price RAISES rather than reading as zero;
* ``COINBASE_UK_SPOT`` charges 60 bps of notional per leg in GBP and no
  financing; ``stop_out_cost_per_unit`` adds that commission for SPOT only, and
  is unchanged for every CFD (so no stored CFD result moves);
* a CFD profile on spot crypto (and the reverse) is refused by the engine;
* a SHORT entry on a non-shortable instrument is refused by the engine
  (``short_not_permitted``) and by the gateway (``direction_permitted``), and
  neither blocks closing a long.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    BacktestConfig,
    FixedSizeSizer,
    PrecomputedSignals,
    run_backtest,
)
from fiboki.backtest.exits import ExitPolicy, ReversalMode
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction
from fiboki.core.instruments import get as get_instrument
from fiboki.risk.gateway import MarketView, RiskGateway
from fiboki.sim.profiles import (
    COINBASE_UK_SPOT,
    COINBASE_UK_SPOT_STRESS,
    IDEALISED_RESEARCH,
    OANDA_REALISTIC,
    BpsOfMidSpread,
    BpsSlippage,
    ProfileMismatch,
    assert_profile_covers,
    expected_slippage_price,
    get_profile,
    rng_for,
)
from tests.exec_fixtures import NOW, make_context, make_plan, make_signal
from tests.helpers_exec import ConstantFx, make_frame

BTC = get_instrument("BTCGBP")
EUR = get_instrument("EURUSD")


# ------------------------------------------------------------------ cost model


def test_bps_spread_is_a_proportion_of_mid_with_a_floor() -> None:
    model = BpsOfMidSpread(multiplier=1.0, floor_bps=2.0)
    # BTCGBP's recorded median is 0.823 bps, under the 2 bps floor.
    assert model.spread_price(BTC, 3, 60_000.0) == pytest.approx(2.0e-4 * 60_000.0)
    assert model.spread_price(BTC, 3, 150.0) == pytest.approx(2.0e-4 * 150.0)
    wide = BpsOfMidSpread(multiplier=3.0, floor_bps=0.0)
    assert wide.spread_price(BTC, 3, 60_000.0) == pytest.approx(3 * 0.823e-4 * 60_000.0)


def test_bps_spread_refuses_a_cfd_and_a_missing_price() -> None:
    with pytest.raises(ValueError, match="typical_spread_bps"):
        BpsOfMidSpread().spread_price(EUR, 3, 1.1)
    with pytest.raises(ValueError, match="positive mid"):
        BpsOfMidSpread().spread_price(BTC, 3, 0.0)


def test_bps_slippage_and_its_expectation_need_the_price() -> None:
    slip = BpsSlippage(bps=5.0)
    assert slip.slippage_price(BTC, Direction.LONG, 60_000.0, rng_for(1, 2)) == pytest.approx(30.0)
    assert expected_slippage_price(slip, BTC, 60_000.0) == pytest.approx(30.0)
    with pytest.raises(ValueError, match="needs the price"):
        expected_slippage_price(slip, BTC)
    with pytest.raises(ValueError, match="price improvement"):
        BpsSlippage(bps=-1.0)


def test_the_coinbase_profile_is_spot_costs() -> None:
    p = get_profile("COINBASE_UK_SPOT")
    assert p is COINBASE_UK_SPOT
    # 60 bps of notional per leg, in the quote currency (GBP for these pairs).
    assert p.commission.commission(BTC, 0.5, 60_000.0) == pytest.approx(0.006 * 30_000.0)
    assert p.commission.currency_for(BTC) == "GBP"
    assert p.financing.nightly_charge(BTC, Direction.LONG, 1.0, 60_000.0) == 0.0
    assert p.min_stop_distance_price(BTC) == 0.0
    assert COINBASE_UK_SPOT_STRESS.commission.bps_of_notional > p.commission.bps_of_notional


def test_stop_out_cost_includes_the_spot_commission_and_leaves_cfds_alone() -> None:
    """Hand arithmetic, BTCGBP at GBP 60,000 under COINBASE_UK_SPOT:

    spread  = max(0.823, 2.0) bps of 60,000      = 12.0
    slip    = 5 bps of 60,000 per fill, two fills = 2 * 30.0 = 60.0
    fee     = 60 bps of 60,000 per leg, two legs  = 2 * 360.0 = 720.0
    total   = 792.0 GBP per BTC of size.
    """
    assert COINBASE_UK_SPOT.stop_out_cost_per_unit(BTC, 12, 60_000.0) == pytest.approx(792.0)
    # A CFD keeps the historical rule: spread + 2 * E[slippage], no commission.
    spread = OANDA_REALISTIC.spread_price(EUR, 12, 1.1)
    slip = OANDA_REALISTIC.expected_slippage_price(EUR)
    assert OANDA_REALISTIC.stop_out_cost_per_unit(EUR, 12, 1.1) == float(spread + 2.0 * slip)


def test_a_profile_must_model_the_product() -> None:
    assert_profile_covers(COINBASE_UK_SPOT, BTC)
    assert_profile_covers(IDEALISED_RESEARCH, BTC)
    assert_profile_covers(OANDA_REALISTIC, EUR)
    with pytest.raises(ProfileMismatch, match="spot crypto"):
        assert_profile_covers(OANDA_REALISTIC, BTC)
    with pytest.raises(ProfileMismatch, match="models spot crypto"):
        assert_profile_covers(COINBASE_UK_SPOT, EUR)


# ------------------------------------------------------------------- engine


def _btc_frame() -> pd.DataFrame:
    rows = []
    price = 60_000.0
    for i in range(12):
        ts = (pd.Timestamp("2026-10-03T00:00Z") + pd.Timedelta(hours=4 * i)).isoformat()
        rows.append((ts, price, price * 1.004, price * 0.996, price * 1.001))
        price *= 1.001
    return make_frame(rows)


def _sig(ts: str, direction: Direction, price: float = 60_000.0) -> Signal:
    stop = price * (0.97 if direction is Direction.LONG else 1.03)
    return Signal(
        strategy_id="crypto_test", instrument="BTCGBP", timeframe="H4", direction=direction,
        bar_time=pd.Timestamp(ts, tz="UTC"), reference_price=price, stop_price=stop,
    )


def _run(signals: list[Signal], *, profile=COINBASE_UK_SPOT, policy: ExitPolicy | None = None):
    cfg = BacktestConfig(initial_balance=100_000.0, account_ccy="GBP", profile=profile,
                         strategy_id="crypto_test")
    return run_backtest(
        data={"BTCGBP": _btc_frame()}, config=cfg, strategy=PrecomputedSignals(signals),
        sizer=FixedSizeSizer(0.1), fx=ConstantFx({}), exit_policy=policy,
    )


def test_the_engine_refuses_a_cfd_profile_on_spot_crypto() -> None:
    with pytest.raises(ProfileMismatch):
        _run([], profile=OANDA_REALISTIC)


def test_the_engine_refuses_a_short_entry_on_spot_crypto() -> None:
    result = _run([_sig("2026-10-03T08:00Z", Direction.SHORT)])
    assert result.trades == []
    assert result.rejections.get("short_not_permitted") == 1
    assert result.orders_submitted == 0


def test_a_short_signal_still_closes_a_long_and_opens_nothing() -> None:
    signals = [_sig("2026-10-03T04:00Z", Direction.LONG), _sig("2026-10-03T16:00Z", Direction.SHORT)]
    result = _run(signals, policy=ExitPolicy(reversal=ReversalMode.REVERSE))
    assert [t.direction for t in result.trades] == [Direction.LONG]
    assert result.trades[0].exit_reason.value == "opposite_signal"
    assert result.rejections.get("short_not_permitted") == 1
    # The long paid 60 bps on each leg: commission is ~1.2% of the entry notional.
    t = result.trades[0]
    assert t.commission == pytest.approx(0.006 * t.size * (t.entry_price + t.exit_price), rel=1e-6)
    assert t.financing_cost == 0.0


def test_a_long_entry_is_unaffected() -> None:
    result = _run([_sig("2026-10-03T08:00Z", Direction.LONG)])
    assert len(result.trades) == 1 and "short_not_permitted" not in result.rejections


# ------------------------------------------------------------------ gateway


def _crypto_context(direction: Direction, *, spread_bps: float = 1.0, mid: float | None = 60_000.0):
    signal = make_signal(instrument="BTCGBP", direction=direction, reference_price=60_000.0,
                         stop_distance=1_800.0, take_profit_distance=None)
    plan = make_plan(signal, equity=100_000.0, risk_fraction=0.005)
    market = MarketView(
        mid_price=mid,
        spread_price=None if mid is None else spread_bps * 1e-4 * mid,
        quote_time=NOW - pd.Timedelta(seconds=2),
        last_bar_time=NOW - pd.Timedelta(seconds=30),
        market_open=True, regime="trend", event_times=(),
    )
    return make_context(plan, market=market)


def test_the_gateway_refuses_a_short_entry_on_spot_crypto() -> None:
    decision = RiskGateway().evaluate(_crypto_context(Direction.SHORT))
    assert not decision.allowed
    assert "short_not_permitted:BTCGBP:coinbase" in decision.reasons
    assert "direction_permitted" in decision.checks_run
    assert "direction_permitted" not in RiskGateway.EXIT_CHECKS


def test_the_gateway_allows_a_long_and_a_cfd_short() -> None:
    long_ok = RiskGateway().evaluate(_crypto_context(Direction.LONG))
    assert not [r for r in long_ok.reasons if r.startswith("short_not_permitted")]
    cfd_short = RiskGateway().evaluate(make_context(make_plan(make_signal(direction=Direction.SHORT))))
    assert cfd_short.allowed, cfd_short.reasons


def test_abnormal_spread_is_measured_in_bps_of_mid_for_spot() -> None:
    # 0.823 bps typical; DEFAULT_LIMITS max_spread_multiple is 3.
    calm = RiskGateway().evaluate(_crypto_context(Direction.LONG, spread_bps=2.0))
    assert not [r for r in calm.reasons if r.startswith("abnormal_spread")], calm.reasons
    wide = RiskGateway().evaluate(_crypto_context(Direction.LONG, spread_bps=5.0))
    assert any(r.startswith("abnormal_spread") for r in wide.reasons)
    # The verdict is a multiple of bps, so it is the same at a 400x lower price.
    cheap = RiskGateway().evaluate(_crypto_context(Direction.LONG, spread_bps=5.0, mid=150.0))
    assert any(r.startswith("abnormal_spread") for r in cheap.reasons)
    unknown = RiskGateway().evaluate(_crypto_context(Direction.LONG, mid=None))
    assert "spread_unknown" in unknown.reasons or "spread_reference_mid_unknown" in unknown.reasons
