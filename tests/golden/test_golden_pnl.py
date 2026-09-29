"""Hand-calculated end-to-end P&L tests through the backtest engine.

The arithmetic for every expected figure is written out in the docstring of
its test. The decomposition the engine guarantees is::

    net_pnl == gross_pnl - spread_cost - commission - slippage_cost - financing_cost

with ``gross_pnl`` the MID-to-MID move converted to the account currency.
Every test below is checkable with a calculator and nothing else.

Account currency is GBP throughout, with deliberately round conversion rates
(USD->GBP = 0.80, JPY->GBP = 0.0055) so a conversion error cannot hide inside a
plausible-looking number — which is exactly how V1 reported USD figures as GBP
for eighteen months.
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
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction, ExitReason
from fiboki.sim.fills import IntrabarPolicy
from fiboki.sim.profiles import (
    CommissionModel,
    FinancingModel,
    rng_for,
)
from tests.helpers_exec import ConstantFx, flat_profile, make_frame

pytestmark = pytest.mark.golden

FX = ConstantFx({("USD", "GBP"): 0.80, ("JPY", "GBP"): 0.0055})


def _run(frame, signal, *, profile, size, account_ccy="GBP", **cfg_kw):
    cfg = BacktestConfig(
        initial_balance=100_000.0,
        account_ccy=account_ccy,
        profile=profile,
        strategy_id="golden",
        **cfg_kw,
    )
    return run_backtest(
        data={signal.instrument: frame},
        config=cfg,
        strategy=PrecomputedSignals([signal]),
        sizer=FixedSizeSizer(size),
        fx=FX,
    )


def _assert_decomposition(trade):
    """The identity that the V1 exit-leg bug would break."""
    expected = (
        trade.gross_pnl
        - trade.spread_cost
        - trade.commission
        - trade.slippage_cost
        - trade.financing_cost
    )
    assert trade.net_pnl == pytest.approx(expected, abs=1e-9)


# ==========================================================================
# 1. Long EURUSD, known spread and commission, GBP account
# ==========================================================================


def test_golden_long_eurusd_gbp_account():
    """
    EURUSD, pip 0.0001, contract_size 1.0. Account GBP. USD->GBP = 0.80.

    Bars (H4):
      00:00  O 1.1000  H 1.1010  L 1.0990  C 1.1000   <- signal bar
      04:00  O 1.1000  H 1.1060  L 1.0995  C 1.1050   <- entry at open, TP hit
      08:00, 12:00  (padding so the equity curve has several points)

    Signal: LONG, stop 1.0950, take-profit 1.1050. Size 10,000 units.
    Profile: spread 2.0 pips, commission 3.00 USD flat per leg, no slippage,
             no financing, zero latency.

    ENTRY  (04:00 open, mid 1.1000)
      half-spread   = (2.0 * 0.0001) / 2      = 0.0001
      dealt         = 1.1000 + 0.0001         = 1.1001
      spread cost   = 0.0001 * 10,000 * 1.0   = 1.00 USD
      commission    =                           3.00 USD

    EXIT   (same bar; high 1.1060 >= target 1.1050, open 1.1000 did not gap)
      exit mid      =                           1.1050
      dealt         = 1.1050 - 0.0001         = 1.1049
      spread cost   =                           1.00 USD
      commission    =                           3.00 USD

    P&L IN USD
      gross  = (1.1050 - 1.1000) * 10,000     = 50.00
      spread = 1.00 + 1.00                    =  2.00
      comm   = 3.00 + 3.00                    =  6.00
      net    = 50.00 - 2.00 - 6.00            = 42.00

    P&L IN GBP (x 0.80)
      gross  = 50.00 * 0.80                   = 40.00
      spread =  2.00 * 0.80                   =  1.60
      comm   =  6.00 * 0.80                   =  4.80
      net    = 40.00 - 1.60 - 4.80            = 33.60

    Cross-check against dealt prices:
      (1.1049 - 1.1001) * 10,000 = 48.00 USD, minus 6.00 commission = 42.00 USD.
      Identical, as it must be.

    MAE/MFE (account currency, measured from the entry MID 1.1000)
      MAE = (1.0995 - 1.1000) * 10,000 * 0.80 = -4.00
      MFE = (1.1060 - 1.1000) * 10,000 * 0.80 = +48.00
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1060, 1.0995, 1.1050),
        ("2024-01-02 08:00", 1.1050, 1.1060, 1.1040, 1.1050),
        ("2024-01-02 12:00", 1.1050, 1.1060, 1.1040, 1.1050),
    ])
    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0950, take_profit_prices=(1.1050,),
    )
    profile = flat_profile(
        spread_pips=2.0,
        commission=CommissionModel(per_trade=3.00, currency="USD"),
    )
    res = _run(frame, sig, profile=profile, size=10_000)

    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason is ExitReason.TAKE_PROFIT
    assert t.entry_time == pd.Timestamp("2024-01-02 04:00", tz="UTC")
    assert t.entry_price == pytest.approx(1.1000, abs=1e-12)
    assert t.exit_price == pytest.approx(1.1050, abs=1e-12)

    assert t.gross_pnl == pytest.approx(40.00, abs=1e-9)
    assert t.spread_cost == pytest.approx(1.60, abs=1e-9)
    assert t.commission == pytest.approx(4.80, abs=1e-9)
    assert t.slippage_cost == pytest.approx(0.00, abs=1e-12)
    assert t.financing_cost == pytest.approx(0.00, abs=1e-12)
    assert t.net_pnl == pytest.approx(33.60, abs=1e-9)
    assert t.fx_rate_used == pytest.approx(0.80, abs=1e-12)
    assert t.account_ccy == "GBP"
    _assert_decomposition(t)

    assert t.max_adverse_excursion == pytest.approx(-4.00, abs=1e-9)
    assert t.max_favourable_excursion == pytest.approx(48.00, abs=1e-9)

    # The cost breakdown must show BOTH legs, equally.
    assert res.costs.entry_leg_spread == pytest.approx(0.80, abs=1e-9)
    assert res.costs.exit_leg_spread == pytest.approx(0.80, abs=1e-9)
    assert res.costs.spread == pytest.approx(1.60, abs=1e-9)
    assert res.costs.commission == pytest.approx(4.80, abs=1e-9)


# ==========================================================================
# 2. The entry must come from the NEXT bar, never the signal bar
# ==========================================================================


def test_entry_price_is_the_next_bars_open_not_the_signal_bars_close():
    """
    The signal bar closes at 1.1000; the next bar opens at 1.0900. If the
    engine had filled at the signal bar's close it would record 1.1000.

    Entry mid must be 1.0900 -- the first price available AFTER the decision.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.0900, 1.1100, 1.0890, 1.1080),
        ("2024-01-02 08:00", 1.1080, 1.1090, 1.1070, 1.1080),
    ])
    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0800, take_profit_prices=(1.1050,),
    )
    res = _run(frame, sig, profile=flat_profile(), size=10_000)
    assert res.trades[0].entry_price == pytest.approx(1.0900, abs=1e-12)


# ==========================================================================
# 3. A JPY pair: quote-currency conversion with a separate commission currency
# ==========================================================================


def test_golden_usdjpy_conversion_with_usd_commission():
    """
    USDJPY: pip 0.01, quote currency JPY. Account GBP.
      JPY -> GBP = 0.0055
      USD -> GBP = 0.80      (the COMMISSION currency, deliberately different)

    Bars:
      00:00  O 150.000  H 150.100  L 149.900  C 150.000   <- signal
      04:00  O 150.000  H 151.200  L 149.950  C 151.000   <- entry, TP hit

    LONG 10,000 units, stop 149.000, target 151.000.
    Profile: spread 2.0 pips -> 0.02 price, half 0.01
             commission 2.50 USD flat per leg

    ENTRY   mid 150.000, dealt 150.000 + 0.01 = 150.010
            spread = 0.01 * 10,000 = 100.00 JPY
    EXIT    mid 151.000, dealt 151.000 - 0.01 = 150.990
            spread = 100.00 JPY

    IN JPY
      gross  = (151.000 - 150.000) * 10,000 = 10,000.00 JPY
      spread = 100 + 100                    =    200.00 JPY

    IN GBP
      gross      = 10,000.00 * 0.0055       = 55.00
      spread     =    200.00 * 0.0055       =  1.10
      commission = (2.50 + 2.50) * 0.80     =  4.00
      net        = 55.00 - 1.10 - 4.00      = 49.90

    If the engine had converted commission at the JPY rate it would report
    5.00 * 0.0055 = 0.0275 instead of 4.00 -- a 145x understatement. That is
    the class of error this test exists to catch.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 150.000, 150.100, 149.900, 150.000),
        ("2024-01-02 04:00", 150.000, 151.200, 149.950, 151.000),
        ("2024-01-02 08:00", 151.000, 151.100, 150.900, 151.000),
    ])
    sig = Signal(
        strategy_id="golden", instrument="USDJPY", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=150.000, stop_price=149.000, take_profit_prices=(151.000,),
    )
    profile = flat_profile(
        spread_pips=2.0,
        commission=CommissionModel(per_trade=2.50, currency="USD"),
    )
    res = _run(frame, sig, profile=profile, size=10_000)

    t = res.trades[0]
    assert t.gross_pnl == pytest.approx(55.00, abs=1e-9)
    assert t.spread_cost == pytest.approx(1.10, abs=1e-9)
    assert t.commission == pytest.approx(4.00, abs=1e-9)
    assert t.net_pnl == pytest.approx(49.90, abs=1e-9)
    assert t.fx_rate_used == pytest.approx(0.0055, abs=1e-15)
    _assert_decomposition(t)


# ==========================================================================
# 4. XAUUSD
# ==========================================================================


def test_golden_xauusd_long():
    """
    XAUUSD: pip 0.01, contract_size 1.0, quote USD. Account GBP, USD->GBP 0.80.

    Bars:
      00:00  O 2000.00  H 2002.00  L 1998.00  C 2000.00  <- signal
      04:00  O 2000.00  H 2025.00  L 1999.00  C 2020.00  <- entry, TP hit

    LONG 10 oz, stop 1950.00, target 2020.00.
    Profile spread 30 pips -> 30 * 0.01 = 0.30, half = 0.15

    ENTRY  mid 2000.00, dealt 2000.15, spread = 0.15 * 10 = 1.50 USD
    EXIT   mid 2020.00, dealt 2019.85, spread = 1.50 USD

    IN USD
      gross  = (2020.00 - 2000.00) * 10 = 200.00
      spread = 1.50 + 1.50              =   3.00
      net    = 197.00

    IN GBP (x 0.80)
      gross = 160.00 ; spread = 2.40 ; net = 157.60
    """
    frame = make_frame([
        ("2024-01-02 00:00", 2000.00, 2002.00, 1998.00, 2000.00),
        ("2024-01-02 04:00", 2000.00, 2025.00, 1999.00, 2020.00),
        ("2024-01-02 08:00", 2020.00, 2021.00, 2019.00, 2020.00),
    ])
    sig = Signal(
        strategy_id="golden", instrument="XAUUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=2000.00, stop_price=1950.00, take_profit_prices=(2020.00,),
    )
    res = _run(frame, sig, profile=flat_profile(spread_pips=30.0), size=10)

    t = res.trades[0]
    assert t.size == pytest.approx(10.0, abs=1e-12)
    assert t.gross_pnl == pytest.approx(160.00, abs=1e-9)
    assert t.spread_cost == pytest.approx(2.40, abs=1e-9)
    assert t.net_pnl == pytest.approx(157.60, abs=1e-9)
    _assert_decomposition(t)


# ==========================================================================
# 5. A short trade
# ==========================================================================


def test_golden_short_eurusd():
    """
    SHORT 10,000 EURUSD. Account GBP, USD->GBP 0.80. Spread 2.0 pips.

    Bars:
      00:00  O 1.1000  H 1.1010  L 1.0990  C 1.1000  <- signal
      04:00  O 1.1000  H 1.1005  L 1.0940  C 1.0960  <- entry, target 1.0950 hit

    ENTRY  mid 1.1000, dealt 1.1000 - 0.0001 = 1.0999  (a seller gets the bid)
           spread = 0.0001 * 10,000 = 1.00 USD
    EXIT   mid 1.0950, dealt 1.0950 + 0.0001 = 1.0951  (a buyer pays the offer)
           spread = 1.00 USD

    IN USD
      gross = (1.0950 - 1.1000) * (-1) * 10,000 = +50.00
      spread = 2.00
      net = 48.00
      cross-check from dealt: (1.0999 - 1.0951) * 10,000 = 48.00  OK

    IN GBP: gross 40.00, spread 1.60, net 38.40

    MAE/MFE for a SHORT (favourable = price falling):
      MFE = (1.1000 - 1.0940) * 10,000 * 0.80 = +48.00
      MAE = (1.1000 - 1.1005) * 10,000 * 0.80 =  -4.00
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1005, 1.0940, 1.0960),
        ("2024-01-02 08:00", 1.0960, 1.0970, 1.0950, 1.0960),
    ])
    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.SHORT,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.1050, take_profit_prices=(1.0950,),
    )
    res = _run(frame, sig, profile=flat_profile(spread_pips=2.0), size=10_000)

    t = res.trades[0]
    assert t.direction is Direction.SHORT
    assert t.gross_pnl == pytest.approx(40.00, abs=1e-9)
    assert t.spread_cost == pytest.approx(1.60, abs=1e-9)
    assert t.net_pnl == pytest.approx(38.40, abs=1e-9)
    assert t.max_favourable_excursion == pytest.approx(48.00, abs=1e-9)
    assert t.max_adverse_excursion == pytest.approx(-4.00, abs=1e-9)
    _assert_decomposition(t)


# ==========================================================================
# 6. A gap straight through the stop
# ==========================================================================


def test_golden_gap_through_stop_costs_far_more_than_the_stop_level():
    """
    LONG 10,000 EURUSD entered at mid 1.1000 with a stop at 1.0950.
    The following bar OPENS at 1.0800 -- 150 pips below the stop.

    Exit mid = min(1.0950, 1.0800) = 1.0800

    IN USD
      gross  = (1.0800 - 1.1000) * 10,000 = -200.00
      spread = 1.00 + 1.00                =    2.00
      net    = -202.00

    IN GBP: gross -160.00, spread 1.60, net -161.60

    Had the stop been honoured at its level (the V1 behaviour):
      gross = (1.0950 - 1.1000) * 10,000 * 0.80 = -40.00 GBP
    so the real loss is 4.0x the loss V1 would have recorded. Over a sample
    with many gaps this is the difference between a survivable drawdown and a
    blown account.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1020, 1.0990, 1.1000),
        ("2024-01-02 08:00", 1.0800, 1.0810, 1.0700, 1.0750),
        ("2024-01-02 12:00", 1.0750, 1.0760, 1.0740, 1.0750),
    ])
    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0950, take_profit_prices=(1.2000,),
    )
    res = _run(frame, sig, profile=flat_profile(spread_pips=2.0), size=10_000)

    t = res.trades[0]
    assert t.exit_reason is ExitReason.STOP_LOSS
    assert t.exit_price == pytest.approx(1.0800, abs=1e-12)
    assert t.gross_pnl == pytest.approx(-160.00, abs=1e-9)
    assert t.net_pnl == pytest.approx(-161.60, abs=1e-9)
    _assert_decomposition(t)

    stop_level_loss = (1.0950 - 1.1000) * 10_000 * 0.80  # -40.00 GBP
    assert t.gross_pnl == pytest.approx(stop_level_loss * 4.0, abs=1e-9)


# ==========================================================================
# 7. A bar that touches both the stop and the target
# ==========================================================================


@pytest.mark.parametrize(
    ("policy", "expected_exit_mid", "expected_net"),
    [
        # STOP_FIRST : exit 1.0950 -> gross (1.0950-1.1000)*10000*0.80 = -40.00
        #              spread 1.60 -> net -41.60
        (IntrabarPolicy.STOP_FIRST, 1.0950, -41.60),
        # TARGET_FIRST: exit 1.1050 -> gross (1.1050-1.1000)*10000*0.80 = +40.00
        #              spread 1.60 -> net  38.40
        (IntrabarPolicy.TARGET_FIRST, 1.1050, 38.40),
    ],
)
def test_golden_both_touched_policy_changes_the_result_by_80_gbp(
    policy, expected_exit_mid, expected_net
):
    """
    One bar, both levels inside its range, no gap at the open:
      04:00  O 1.1000  H 1.1060  L 1.0940  C 1.1000
      stop 1.0950, target 1.1050

    The two assumptions differ by 38.40 - (-41.60) = 80.00 GBP on a single
    trade. That is the size of the thing V1 chose silently. It is now a
    parameter, and this test is the measurement.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1060, 1.0940, 1.1000),
        ("2024-01-02 08:00", 1.1000, 1.1010, 1.0990, 1.1000),
    ])
    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0950, take_profit_prices=(1.1050,),
    )
    res = _run(
        frame, sig, profile=flat_profile(spread_pips=2.0), size=10_000,
        intrabar_policy=policy,
    )
    t = res.trades[0]
    assert t.exit_price == pytest.approx(expected_exit_mid, abs=1e-12)
    assert t.net_pnl == pytest.approx(expected_net, abs=1e-9)
    _assert_decomposition(t)


# ==========================================================================
# 8. Financing over three rollovers, one of them Wednesday's triple
# ==========================================================================


def test_golden_financing_over_three_nights():
    """
    A position opened 2024-01-02 00:00 UTC and closed 2024-01-05 00:00 UTC
    crosses the 21:00 UTC rollover on 01-02 (Tue), 01-03 (Wed) and 01-04 (Thu).

    UPDATED DELIBERATELY for engine_v3_realism (audit P2-7). Under the old
    calendar-night rule that was three nights (3.30 USD, 2.64 GBP). Spot FX
    rolls T+2, so Wednesday's rollover carries the weekend and is charged three
    nights: Tue 1 + Wed 3 + Thu 1 = FIVE nights (backtest/position.py
    ``financing_nights``). The old figure was the bug, not the arithmetic.

    Prices are held flat at 1.1000 for the whole hold (except the final bar's
    high, which reaches the target) so the notional is constant and the
    arithmetic is exact.

    FinancingModel(annual_bps_long = 365.0, basis_days = 365.0):
      annual rate   = 365 * 1e-4                    = 0.0365
      nightly rate  = 0.0365 / 365                  = 0.0001   (1 bp per night)
      notional      = 1.1000 * 10,000 * 1.0         = 11,000.00 USD
      per night     = 11,000.00 * 0.0001            =      1.10 USD
      five nights   = 1.10 * (1 + 3 + 1)            =      5.50 USD
      in GBP        = 5.50 * 0.80                   =      4.40

    Exit on the final bar at the target 1.1050:
      gross USD = (1.1050 - 1.1000) * 10,000        =     50.00
      gross GBP = 50.00 * 0.80                      =     40.00
      net  GBP  = 40.00 - 4.40                      =     35.60
    (spread and commission are zero in this profile so financing is isolated)
    """
    rows = []
    ts = pd.Timestamp("2024-01-01 20:00", tz="UTC")
    end = pd.Timestamp("2024-01-05 00:00", tz="UTC")
    while ts < end:
        rows.append((ts.strftime("%Y-%m-%d %H:%M"), 1.1000, 1.1000, 1.1000, 1.1000))
        ts += pd.Timedelta(hours=4)
    rows.append((end.strftime("%Y-%m-%d %H:%M"), 1.1000, 1.1050, 1.1000, 1.1000))
    frame = make_frame(rows)

    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-01 20:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0950, take_profit_prices=(1.1050,),
    )
    profile = flat_profile(
        financing=FinancingModel(annual_bps_long=365.0, basis_days=365.0),
    )
    res = _run(frame, sig, profile=profile, size=10_000)

    t = res.trades[0]
    assert t.entry_time == pd.Timestamp("2024-01-02 00:00", tz="UTC")
    assert t.exit_time == pd.Timestamp("2024-01-05 00:00", tz="UTC")
    assert t.financing_cost == pytest.approx(4.40, abs=1e-9)
    assert t.gross_pnl == pytest.approx(40.00, abs=1e-9)
    assert t.net_pnl == pytest.approx(35.60, abs=1e-9)
    _assert_decomposition(t)
    assert res.costs.financing == pytest.approx(4.40, abs=1e-9)


def test_golden_short_financing_uses_the_short_rate():
    """
    Same Tue-Wed-Thu hold, SHORT, with annual_bps_short = 730.0 (2 bp/night):
      per night    = 11,000.00 * 0.0002          = 2.20 USD
      five nights  = 2.20 * (1 + 3 + 1)          = 11.00 USD -> 11.00 * 0.80 = 8.80 GBP

    (UPDATED DELIBERATELY for engine_v3_realism, P2-7: Wednesday's rollover is
    the FX triple. Under the old calendar-night rule this was 5.28 GBP.)
    A long/short differential that the engine ignored would report 4.40 here.
    """
    rows = []
    ts = pd.Timestamp("2024-01-01 20:00", tz="UTC")
    end = pd.Timestamp("2024-01-05 00:00", tz="UTC")
    while ts < end:
        rows.append((ts.strftime("%Y-%m-%d %H:%M"), 1.1000, 1.1000, 1.1000, 1.1000))
        ts += pd.Timedelta(hours=4)
    rows.append((end.strftime("%Y-%m-%d %H:%M"), 1.1000, 1.1000, 1.0950, 1.1000))
    frame = make_frame(rows)

    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.SHORT,
        bar_time=pd.Timestamp("2024-01-01 20:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.1050, take_profit_prices=(1.0950,),
    )
    profile = flat_profile(
        financing=FinancingModel(annual_bps_long=365.0, annual_bps_short=730.0, basis_days=365.0),
    )
    res = _run(frame, sig, profile=profile, size=10_000)
    assert res.trades[0].financing_cost == pytest.approx(8.80, abs=1e-9)


# ==========================================================================
# 9. A partial fill flows through to P&L
# ==========================================================================


def test_golden_partial_fill_scales_pnl_exactly():
    """
    The engine numbers its orders deterministically: the signal emitted on bar
    0 becomes pending order sequence 1, filled on bar index 1. So the draws are
    rng_for(seed, bar_index=1, sequence=1) in the documented order
    (reject, partial, fraction).

      fraction    = 0.5 + 0.5 * fraction_draw
      filled size = floor(10,000 * fraction)          (EURUSD size step = 1.0)

    With spread 2.0 pips and USD->GBP 0.80 the expected P&L is
      gross  = (1.1050 - 1.1000) * filled * 0.80
      spread = 0.0001 * filled * 0.80 * 2 legs
      net    = gross - spread
    all of which scale linearly in the filled size -- the point of the test.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1060, 1.0995, 1.1050),
        ("2024-01-02 08:00", 1.1050, 1.1060, 1.1040, 1.1050),
    ])
    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0950, take_profit_prices=(1.1050,),
    )
    profile = flat_profile(
        spread_pips=2.0,
        partial_fill_probability=1.0,
        partial_fill_min_fraction=0.5,
        seed=4242,
    )
    res = _run(frame, sig, profile=profile, size=10_000)

    rng = rng_for(4242, 1, 1)
    rng.random()  # reject draw
    rng.random()  # partial draw
    fraction = 0.5 + 0.5 * rng.random()
    expected_size = float(int(10_000 * fraction))

    t = res.trades[0]
    assert t.size == pytest.approx(expected_size, abs=1e-9)
    assert 5_000 <= t.size < 10_000

    expected_gross = 0.0050 * expected_size * 0.80
    expected_spread = 0.0001 * expected_size * 0.80 * 2
    assert t.gross_pnl == pytest.approx(expected_gross, abs=1e-9)
    assert t.spread_cost == pytest.approx(expected_spread, abs=1e-9)
    assert t.net_pnl == pytest.approx(expected_gross - expected_spread, abs=1e-9)
    _assert_decomposition(t)


# ==========================================================================
# 10. The both-leg guarantee, stated as an invariant
# ==========================================================================


def test_exit_leg_spread_equals_entry_leg_spread_under_a_flat_model():
    """
    With a flat spread and a constant size, the two legs must cost the same.
    V1 would report entry_leg_spread > 0 and exit_leg_spread == 0.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1060, 1.0995, 1.1050),
        ("2024-01-02 08:00", 1.1050, 1.1060, 1.1040, 1.1050),
    ])
    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0950, take_profit_prices=(1.1050,),
    )
    res = _run(frame, sig, profile=flat_profile(spread_pips=2.0), size=10_000)
    assert res.costs.exit_leg_spread > 0.0
    assert res.costs.exit_leg_spread == pytest.approx(res.costs.entry_leg_spread, abs=1e-12)


def test_engine_refuses_to_run_without_an_fx_source():
    """The V1 'silently assume 1.0' failure mode must be unreachable."""
    from fiboki.backtest.engine import BacktestEngine

    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1060, 1.0995, 1.1050),
    ])
    with pytest.raises(ValueError, match="FxRateSource"):
        BacktestEngine(
            data={"EURUSD": frame},
            config=BacktestConfig(initial_balance=1000.0),
            strategy=PrecomputedSignals([]),
            sizer=FixedSizeSizer(1.0),
            fx=None,
        )


def test_identity_fx_source_refuses_a_mismatched_account_currency():
    """
    A GBP account trading a USD-quoted instrument cannot be priced by
    IdentityFxSource. It must raise, not return 1.0.
    """
    from fiboki.core.money import IdentityFxSource

    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1060, 1.0995, 1.1050),
        ("2024-01-02 08:00", 1.1050, 1.1060, 1.1040, 1.1050),
    ])
    sig = Signal(
        strategy_id="golden", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0950, take_profit_prices=(1.1050,),
    )
    cfg = BacktestConfig(initial_balance=100_000.0, account_ccy="GBP", profile=flat_profile())
    with pytest.raises(ValueError, match="IdentityFxSource"):
        run_backtest(
            data={"EURUSD": frame}, config=cfg,
            strategy=PrecomputedSignals([sig]),
            sizer=FixedSizeSizer(10_000), fx=IdentityFxSource(),
        )
