"""Hand-calculated fill-model tests.

Every expected number below is derived in a comment with the arithmetic
written out. If one of these fails, the correct response is to check the
arithmetic in the comment with a calculator and then fix the CODE. Weakening
a tolerance here is how V1's fill bugs survived for eighteen months.

Conventions used throughout (see sim/fills.py for the rationale):
  * bars are MID prices
  * the half-spread is applied to the resolved mid level
  * a long deals at mid + half + slip, and closes at mid - half - slip
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.enums import Direction, ExitReason
from fiboki.core.instruments import get as get_instrument
from fiboki.sim.fills import (
    Bar,
    FillSimulator,
    FxSessionCalendar,
    IntrabarPolicy,
    RejectReason,
)
from fiboki.sim.profiles import (
    CommissionModel,
    ExecutionProfile,
    FinancingModel,
    FixedPipSpread,
    FixedPointsSlippage,
    MinStopPolicy,
    NoSlippage,
    rng_for,
)

pytestmark = pytest.mark.golden

EURUSD = get_instrument("EURUSD")
XAUUSD = get_instrument("XAUUSD")
USDJPY = get_instrument("USDJPY")

TS = pd.Timestamp("2024-01-02 08:00", tz="UTC")


def _profile(**kw) -> ExecutionProfile:
    base = {
        "name": "GOLDEN",
        "spread": FixedPipSpread(0.0),
        "slippage": NoSlippage(),
        "commission": CommissionModel(),
        "financing": FinancingModel(),
        "min_stop_policy": MinStopPolicy.ALLOW,
    }
    base.update(kw)
    return ExecutionProfile(**base)


# ==========================================================================
# 1. A long EURUSD entry with a known spread and a known commission
# ==========================================================================


def test_long_eurusd_entry_spread_and_commission():
    """
    EURUSD: pip_size = 0.0001, contract_size = 1.0

    Profile:  spread = 2.0 pips            -> 2.0 * 0.0001 = 0.0002 price
              half-spread                  -> 0.0002 / 2   = 0.0001 price
              commission = 3.00 USD per leg (flat ticket)

    Order:    BUY 10,000 units, bar opens at 1.1000 (mid)

    Dealt price  = 1.1000 + 0.0001            = 1.1001
    Spread cost  = 0.0001 * 10,000 * 1.0      = 1.00 USD
    Commission   =                              3.00 USD
    """
    prof = _profile(
        spread=FixedPipSpread(2.0),
        commission=CommissionModel(per_trade=3.00, currency="USD"),
    )
    sim = FillSimulator(prof)
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)

    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0950,
    )

    assert fill.filled
    assert fill.mid_price == pytest.approx(1.1000, abs=1e-12)
    assert fill.filled_price == pytest.approx(1.1001, abs=1e-12)
    assert fill.half_spread == pytest.approx(0.0001, abs=1e-12)
    assert fill.costs.spread_quote == pytest.approx(1.00, abs=1e-9)
    assert fill.costs.commission_amount == pytest.approx(3.00, abs=1e-9)
    assert fill.costs.commission_ccy == "USD"


def test_short_eurusd_entry_is_mirror_image():
    """
    SELL 10,000 EURUSD at mid 1.1000 with a 2.0 pip spread.

    Dealt price = 1.1000 - 0.0001 = 1.0999   (a seller receives the bid)
    Spread cost = 0.0001 * 10,000 = 1.00 USD (identical magnitude to the long)
    """
    sim = FillSimulator(_profile(spread=FixedPipSpread(2.0)))
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.SHORT, size=10_000,
        bar=bar, bar_index=1, stop=1.1050,
    )
    assert fill.filled_price == pytest.approx(1.0999, abs=1e-12)
    assert fill.costs.spread_quote == pytest.approx(1.00, abs=1e-9)


def test_entry_slippage_is_adverse_for_both_directions():
    """
    Slippage = 1.5 pips = 0.00015 price. Spread = 0.

    LONG  dealt = 1.1000 + 0.00015 = 1.10015   (pays more)
    SHORT dealt = 1.1000 - 0.00015 = 1.09985   (receives less)

    Slippage cost, both cases = 0.00015 * 10,000 = 1.50 USD
    """
    sim = FillSimulator(_profile(slippage=FixedPointsSlippage(1.5)))
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)

    long_fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0950,
    )
    short_fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.SHORT, size=10_000,
        bar=bar, bar_index=1, stop=1.1050,
    )
    assert long_fill.filled_price == pytest.approx(1.10015, abs=1e-12)
    assert short_fill.filled_price == pytest.approx(1.09985, abs=1e-12)
    assert long_fill.costs.slippage_quote == pytest.approx(1.50, abs=1e-9)
    assert short_fill.costs.slippage_quote == pytest.approx(1.50, abs=1e-9)


# ==========================================================================
# 2. XAUUSD — a different pip size, to catch pip/price confusion
# ==========================================================================


def test_xauusd_entry_uses_metal_pip_size():
    """
    XAUUSD: pip_size = 0.01, contract_size = 1.0

    Profile spread = 30 pips -> 30 * 0.01 = 0.30 price, half = 0.15

    BUY 10 oz at mid 2000.00
    Dealt price = 2000.00 + 0.15 = 2000.15
    Spread cost = 0.15 * 10 * 1.0 = 1.50 USD

    Commission = 0.20 bps of notional, min 2.00 USD:
      notional = 2000.15 * 10 = 20,001.50
      0.20 bps = 20,001.50 * 0.20 * 1e-4 = 0.40003 USD
      below the 2.00 minimum, so the charge is 2.00 USD
    """
    prof = _profile(
        spread=FixedPipSpread(30.0),
        commission=CommissionModel(bps_of_notional=0.20, minimum=2.00, currency="USD"),
    )
    sim = FillSimulator(prof)
    bar = Bar(TS, 2000.00, 2010.00, 1995.00, 2005.00)
    fill = sim.simulate_entry(
        instrument=XAUUSD, direction=Direction.LONG, size=10,
        bar=bar, bar_index=1, stop=1950.00,
    )
    assert fill.filled_price == pytest.approx(2000.15, abs=1e-9)
    assert fill.costs.spread_quote == pytest.approx(1.50, abs=1e-9)
    assert fill.costs.commission_amount == pytest.approx(2.00, abs=1e-9)


def test_usdjpy_entry_uses_jpy_pip_size():
    """
    USDJPY: pip_size = 0.01

    spread = 2 pips -> 0.02 price, half = 0.01
    BUY 10,000 at mid 150.000 -> dealt 150.010
    Spread cost = 0.01 * 10,000 = 100.00 JPY (note: JPY, not USD)
    """
    sim = FillSimulator(_profile(spread=FixedPipSpread(2.0)))
    bar = Bar(TS, 150.000, 150.500, 149.500, 150.200)
    fill = sim.simulate_entry(
        instrument=USDJPY, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=149.000,
    )
    assert fill.filled_price == pytest.approx(150.010, abs=1e-9)
    assert fill.costs.spread_quote == pytest.approx(100.00, abs=1e-6)


# ==========================================================================
# 3. Gap-aware stops and take-profits  (THE V1 BUG)
# ==========================================================================


def test_long_stop_gaps_through_and_fills_at_the_open():
    """
    THE headline V1 bug.

    Long position, stop at 1.0950.
    Bar: O = 1.0800, H = 1.0810, L = 1.0700, C = 1.0750

    The bar OPENS 150 pips below the stop. There was never a moment at which
    1.0950 could have been dealt. The fill is the OPEN, 1.0800.

    Exit mid   = min(stop, open) = min(1.0950, 1.0800) = 1.0800
    Exit dealt = 1.0800 - 0 (zero spread in this profile) = 1.0800

    V1 recorded 1.0950 here and pocketed 150 pips per gap, every gap.
    """
    sim = FillSimulator(_profile())
    bar = Bar(TS, 1.0800, 1.0810, 1.0700, 1.0750)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1200,
    )
    assert ex.exited
    assert ex.reason is ExitReason.STOP_LOSS
    assert ex.gapped is True
    assert ex.mid_price == pytest.approx(1.0800, abs=1e-12)
    assert ex.exit_price == pytest.approx(1.0800, abs=1e-12)


def test_short_stop_gaps_through_and_fills_at_the_open():
    """
    Short position, stop at 1.1050.
    Bar: O = 1.1200, H = 1.1250, L = 1.1190, C = 1.1220

    Worse-for-a-short is HIGHER, so the fill is max(1.1050, 1.1200) = 1.1200.
    """
    sim = FillSimulator(_profile())
    bar = Bar(TS, 1.1200, 1.1250, 1.1190, 1.1220)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.SHORT, size=10_000,
        bar=bar, bar_index=3, stop=1.1050, take_profit=1.0800,
    )
    assert ex.reason is ExitReason.STOP_LOSS
    assert ex.gapped is True
    assert ex.mid_price == pytest.approx(1.1200, abs=1e-12)


def test_long_take_profit_gap_grants_no_price_improvement():
    """
    Long, take-profit at 1.1050. Bar opens at 1.1200 — straight through.

    A limit order that gaps could plausibly fill at 1.1200. We refuse to
    assume it: exit mid = min(1.1050, 1.1200) = 1.1050.

    This is the SAME 'worse of level and open' rule as the stop case; applied
    to a target it removes a windfall rather than adding a loss. Symmetry of
    rule, asymmetry of effect — both conservative.
    """
    sim = FillSimulator(_profile())
    bar = Bar(TS, 1.1200, 1.1260, 1.1180, 1.1240)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1050,
    )
    assert ex.reason is ExitReason.TAKE_PROFIT
    assert ex.mid_price == pytest.approx(1.1050, abs=1e-12)
    assert ex.gapped is True


def test_short_take_profit_gap_grants_no_price_improvement():
    """Short, target 1.0950, bar opens 1.0800: exit mid = max(1.0950, 1.0800) = 1.0950."""
    sim = FillSimulator(_profile())
    bar = Bar(TS, 1.0800, 1.0830, 1.0750, 1.0790)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.SHORT, size=10_000,
        bar=bar, bar_index=3, stop=1.1050, take_profit=1.0950,
    )
    assert ex.reason is ExitReason.TAKE_PROFIT
    assert ex.mid_price == pytest.approx(1.0950, abs=1e-12)


def test_stop_touched_without_a_gap_fills_at_the_level():
    """
    Long, stop 1.0950. Bar O = 1.1000, L = 1.0900.
    The open is above the stop, so the level WAS dealable: fill at 1.0950.
    With a 2 pip spread the dealt exit is 1.0950 - 0.0001 = 1.0949.
    """
    sim = FillSimulator(_profile(spread=FixedPipSpread(2.0)))
    bar = Bar(TS, 1.1000, 1.1010, 1.0900, 1.0920)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1500,
    )
    assert ex.gapped is False
    assert ex.mid_price == pytest.approx(1.0950, abs=1e-12)
    assert ex.exit_price == pytest.approx(1.0949, abs=1e-12)


def test_neither_level_touched_means_still_open():
    sim = FillSimulator(_profile())
    bar = Bar(TS, 1.1000, 1.1010, 1.0990, 1.1005)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1050,
    )
    assert ex.exited is False


# ==========================================================================
# 4. Both levels touched in one bar
# ==========================================================================


def test_both_touched_stop_first_is_the_default():
    """
    Long, stop 1.0950, target 1.1050.
    Bar: O = 1.1000, H = 1.1060, L = 1.0940, C = 1.1000

    Both levels are inside the range and neither is gapped at the open. Without
    tick data the true ordering is UNKNOWABLE. The default assumes the stop.
    """
    sim = FillSimulator(_profile())  # policy defaults to STOP_FIRST
    bar = Bar(TS, 1.1000, 1.1060, 1.0940, 1.1000)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1050,
    )
    assert ex.reason is ExitReason.STOP_LOSS
    assert ex.both_touched is True
    assert ex.mid_price == pytest.approx(1.0950, abs=1e-12)


def test_both_touched_target_first_is_selectable_for_sensitivity():
    sim = FillSimulator(_profile(), intrabar_policy=IntrabarPolicy.TARGET_FIRST)
    bar = Bar(TS, 1.1000, 1.1060, 1.0940, 1.1000)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1050,
    )
    assert ex.reason is ExitReason.TAKE_PROFIT
    assert ex.mid_price == pytest.approx(1.1050, abs=1e-12)


def test_both_touched_proportional_uses_distance_from_the_open():
    """
    Long, O = 1.1000, stop 1.0950, target 1.1150.

      d_stop = |1.1000 - 1.0950| = 0.0050
      d_tp   = |1.1000 - 1.1150| = 0.0150
      p(stop first) = d_tp / (d_stop + d_tp) = 0.0150 / 0.0200 = 0.75

    The stop is three times closer to the open, so it is three times more
    likely to have been reached first. The draw comes from the profile seed, so
    the outcome is reproducible; here we assert the frequency over many bar
    indices matches 0.75 rather than asserting one draw.
    """
    sim = FillSimulator(_profile(), intrabar_policy=IntrabarPolicy.PROPORTIONAL)
    bar_tpl = (1.1000, 1.1160, 1.0940, 1.1000)

    stops = 0
    n = 4000
    for i in range(n):
        bar = Bar(TS, *bar_tpl)
        ex = sim.resolve_exit(
            instrument=EURUSD, direction=Direction.LONG, size=10_000,
            bar=bar, bar_index=i, stop=1.0950, take_profit=1.1150,
        )
        stops += ex.reason is ExitReason.STOP_LOSS
    frequency = stops / n
    # 4000 Bernoulli(0.75) draws: sd = sqrt(.75*.25/4000) = 0.00685, so +/-0.03
    # is well beyond 4 sigma and cannot fire by chance.
    assert frequency == pytest.approx(0.75, abs=0.03)


def test_gap_at_the_open_overrides_the_ambiguity_policy():
    """
    Even under TARGET_FIRST, a bar that OPENS through the stop must stop out:
    the open is chronologically first, so there is no ambiguity to resolve.

    Long, stop 1.0950, target 1.1050. Bar O = 1.0900, H = 1.1100, L = 1.0850.
    """
    sim = FillSimulator(_profile(), intrabar_policy=IntrabarPolicy.TARGET_FIRST)
    bar = Bar(TS, 1.0900, 1.1100, 1.0850, 1.1000)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1050,
    )
    assert ex.reason is ExitReason.STOP_LOSS
    assert ex.mid_price == pytest.approx(1.0900, abs=1e-12)


# ==========================================================================
# 5. Minimum stop distance
# ==========================================================================


def test_min_stop_distance_rejects_a_too_tight_stop():
    """
    Profile min stop distance = 4 pips = 0.0004.
    Entry mid 1.1000, stop 1.0998 -> distance 0.0002 < 0.0004 -> REJECT.
    """
    prof = _profile(min_stop_distance_pips=4.0, min_stop_policy=MinStopPolicy.REJECT)
    sim = FillSimulator(prof)
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0998,
    )
    assert fill.filled is False
    assert fill.reject_reason is RejectReason.MIN_STOP_DISTANCE


def test_min_stop_distance_widen_pushes_the_stop_out():
    """
    Same setup under WIDEN: the stop moves to 1.1000 - 0.0004 = 1.0996.
    This HONESTLY increases the risk taken relative to the sizer's intent.
    """
    prof = _profile(min_stop_distance_pips=4.0, min_stop_policy=MinStopPolicy.WIDEN)
    sim = FillSimulator(prof)
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0998,
    )
    assert fill.filled
    assert fill.effective_stop == pytest.approx(1.0996, abs=1e-12)


def test_min_stop_distance_widen_for_a_short_pushes_upwards():
    """Short at 1.1000 with a 4 pip minimum -> stop widens to 1.1004."""
    prof = _profile(min_stop_distance_pips=4.0, min_stop_policy=MinStopPolicy.WIDEN)
    sim = FillSimulator(prof)
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.SHORT, size=10_000,
        bar=bar, bar_index=1, stop=1.1002,
    )
    assert fill.effective_stop == pytest.approx(1.1004, abs=1e-12)


def test_a_stop_already_beyond_the_minimum_is_untouched():
    prof = _profile(min_stop_distance_pips=4.0, min_stop_policy=MinStopPolicy.REJECT)
    sim = FillSimulator(prof)
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0950,
    )
    assert fill.filled
    assert fill.effective_stop == pytest.approx(1.0950, abs=1e-12)


# ==========================================================================
# 6. Partial fills, rejections, minimum deal size
# ==========================================================================


def test_partial_fill_uses_the_documented_draw_order():
    """
    simulate_entry draws, in this exact order, from rng_for(seed, bar, seq):
        1. reject_draw
        2. partial_draw
        3. partial_fraction_draw

    With partial_fill_probability = 1.0 the second draw always triggers, and
    the filled fraction is
        fraction = min_frac + (1 - min_frac) * partial_fraction_draw
    rounded DOWN to the instrument's size step (EURUSD: 1.0 unit).

    The test recomputes that from rng_for so the expected value is arithmetic,
    not a magic constant copied out of a previous run.
    """
    prof = _profile(
        partial_fill_probability=1.0, partial_fill_min_fraction=0.5, seed=12345
    )
    sim = FillSimulator(prof)
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)

    rng = rng_for(12345, 7, 0)
    _reject = rng.random()
    _partial = rng.random()
    fraction_draw = rng.random()
    expected_fraction = 0.5 + 0.5 * fraction_draw
    expected_size = float(int(10_000 * expected_fraction))  # size_step = 1.0

    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=7, stop=1.0950, sequence=0,
    )
    assert fill.filled
    assert fill.partial is True
    assert fill.filled_size == pytest.approx(expected_size, abs=1e-9)
    assert fill.rejected_size == pytest.approx(10_000 - expected_size, abs=1e-9)
    # Costs scale with the size that was actually dealt, not the size requested.
    assert fill.costs.spread_quote == pytest.approx(
        fill.half_spread * expected_size, abs=1e-9
    )


def test_certain_rejection_returns_no_fill():
    sim = FillSimulator(_profile(rejection_probability=1.0))
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0950,
    )
    assert fill.filled is False
    assert fill.reject_reason is RejectReason.BROKER_REJECT
    assert fill.rejected_size == 10_000


def test_below_minimum_deal_size_is_rejected():
    """min_deal_size = 500 units; an order for 100 cannot be dealt at all."""
    sim = FillSimulator(_profile(min_deal_size=500.0))
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=100,
        bar=bar, bar_index=1, stop=1.0950,
    )
    assert fill.filled is False
    assert fill.reject_reason is RejectReason.MIN_DEAL_SIZE


def test_a_partial_fill_that_lands_below_the_minimum_is_rejected_entirely():
    """
    partial_fill_min_fraction = 0.5 on a 900-unit order can produce 450-899
    units. With min_deal_size = 900 anything partial is undealable, so the
    whole ticket must be rejected rather than half-filled below the minimum.
    """
    prof = _profile(
        partial_fill_probability=1.0,
        partial_fill_min_fraction=0.5,
        min_deal_size=900.0,
        seed=99,
    )
    sim = FillSimulator(prof)
    bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=900,
        bar=bar, bar_index=4, stop=1.0950,
    )
    assert fill.filled is False
    assert fill.reject_reason is RejectReason.MIN_DEAL_SIZE


# ==========================================================================
# 7. Stale prices and session closure
# ==========================================================================


def test_stale_price_detection_uses_the_profile_multiple():
    """
    H4 bars, stale multiple = 3.0 -> anything over 12h is stale.
    A 4h gap is normal; a 49h weekend gap is not.
    """
    sim = FillSimulator(_profile(stale_price_max_gap_multiple=3.0))
    h4 = pd.Timedelta(hours=4)
    normal = pd.Timestamp("2024-01-02 12:00", tz="UTC")
    assert sim.is_stale(pd.Timestamp("2024-01-02 08:00", tz="UTC"), normal, h4) is False
    assert sim.is_stale(pd.Timestamp("2024-01-02 00:00", tz="UTC"), normal, h4) is False
    weekend = pd.Timestamp("2024-01-07 22:00", tz="UTC")
    assert sim.is_stale(pd.Timestamp("2024-01-05 21:00", tz="UTC"), weekend, h4) is True
    assert sim.is_stale(None, normal, h4) is False


def test_a_stale_price_widens_the_spread_by_the_configured_multiple():
    """
    Spread = 2 pips -> half = 0.0001. The stale multiple here is 2.0, so the
    half-spread charged across a weekend gap is 0.0002 and the dealt price is
    1.1000 + 0.0002 = 1.1002.
    """
    sim = FillSimulator(_profile(spread=FixedPipSpread(2.0)), stale_spread_multiple=2.0)
    bar = Bar(pd.Timestamp("2024-01-07 22:00", tz="UTC"), 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0950,
        previous_time=pd.Timestamp("2024-01-05 21:00", tz="UTC"),
        bar_interval=pd.Timedelta(hours=4),
    )
    assert fill.stale is True
    assert fill.filled_price == pytest.approx(1.1002, abs=1e-12)


def test_reject_on_stale_refuses_the_fill_when_configured():
    sim = FillSimulator(_profile(), reject_on_stale=True)
    bar = Bar(pd.Timestamp("2024-01-07 22:00", tz="UTC"), 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0950,
        previous_time=pd.Timestamp("2024-01-05 21:00", tz="UTC"),
        bar_interval=pd.Timedelta(hours=4),
    )
    assert fill.filled is False
    assert fill.reject_reason is RejectReason.STALE_PRICE


def test_fx_session_calendar_closes_the_weekend():
    """
    Default FX week: closed from Friday 22:00 UTC until Sunday 22:00 UTC.
    2024-01-05 is a Friday; 2024-01-06 a Saturday; 2024-01-07 a Sunday.
    """
    cal = FxSessionCalendar()
    assert cal.is_open(EURUSD, pd.Timestamp("2024-01-05 20:00", tz="UTC")) is True
    assert cal.is_open(EURUSD, pd.Timestamp("2024-01-05 22:00", tz="UTC")) is False
    assert cal.is_open(EURUSD, pd.Timestamp("2024-01-06 12:00", tz="UTC")) is False
    assert cal.is_open(EURUSD, pd.Timestamp("2024-01-07 12:00", tz="UTC")) is False
    assert cal.is_open(EURUSD, pd.Timestamp("2024-01-07 22:00", tz="UTC")) is True
    assert cal.is_open(EURUSD, pd.Timestamp("2024-01-08 09:00", tz="UTC")) is True


def test_entry_is_refused_when_the_market_is_closed():
    sim = FillSimulator(_profile(), calendar=FxSessionCalendar())
    bar = Bar(pd.Timestamp("2024-01-06 12:00", tz="UTC"), 1.1000, 1.1020, 1.0980, 1.1010)
    fill = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=1, stop=1.0950,
    )
    assert fill.filled is False
    assert fill.reject_reason is RejectReason.MARKET_CLOSED


# ==========================================================================
# 8. Guaranteed stops
# ==========================================================================


def test_a_guaranteed_stop_removes_the_gap_and_charges_a_premium():
    """
    Long, stop 1.0950, bar opens 1.0800 (a 150 pip gap).

    Without a guaranteed stop the fill is 1.0800.
    WITH one the fill is the level, 1.0950, and the premium is charged:
        premium = 3 pips * 0.0001 * 10,000 = 0.0003 * 10,000 = 3.00 USD
    """
    prof = _profile(use_guaranteed_stops=True, guaranteed_stop_premium_pips=3.0)
    sim = FillSimulator(prof)
    bar = Bar(TS, 1.0800, 1.0810, 1.0700, 1.0750)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1500,
    )
    assert ex.mid_price == pytest.approx(1.0950, abs=1e-12)
    assert ex.gapped is False
    assert ex.costs.guaranteed_stop_premium_quote == pytest.approx(3.00, abs=1e-9)


# ==========================================================================
# 9. Costs on BOTH legs
# ==========================================================================


def test_entry_and_exit_both_produce_costs():
    """
    The structural guard against the V1 bug: an exit leg must never return a
    zero cost object when the profile charges anything.

    spread 2 pips, commission 3.00 USD flat.
    Entry leg: spread 1.00 USD + commission 3.00 USD
    Exit  leg: spread 1.00 USD + commission 3.00 USD
    Round trip: 2.00 spread + 6.00 commission = 8.00 USD
    """
    prof = _profile(
        spread=FixedPipSpread(2.0),
        commission=CommissionModel(per_trade=3.00, currency="USD"),
    )
    sim = FillSimulator(prof)
    entry_bar = Bar(TS, 1.1000, 1.1020, 1.0980, 1.1010)
    exit_bar = Bar(TS + pd.Timedelta(hours=4), 1.1010, 1.1080, 1.1000, 1.1070)

    entry = sim.simulate_entry(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=entry_bar, bar_index=1, stop=1.0950,
    )
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=exit_bar, bar_index=2, stop=1.0950, take_profit=1.1050,
    )
    assert entry.costs.spread_quote == pytest.approx(1.00, abs=1e-9)
    assert ex.costs.spread_quote == pytest.approx(1.00, abs=1e-9)
    assert entry.costs.commission_amount == pytest.approx(3.00, abs=1e-9)
    assert ex.costs.commission_amount == pytest.approx(3.00, abs=1e-9)

    round_trip_spread = entry.costs.spread_quote + ex.costs.spread_quote
    round_trip_comm = entry.costs.commission_amount + ex.costs.commission_amount
    assert round_trip_spread == pytest.approx(2.00, abs=1e-9)
    assert round_trip_comm == pytest.approx(6.00, abs=1e-9)


def test_a_take_profit_limit_does_not_slip():
    """
    A limit order fills at its price or not at all; it does not slip against
    you. A stop does. The compensating pessimism is the no-improvement rule
    tested above.
    """
    sim = FillSimulator(_profile(slippage=FixedPointsSlippage(2.0)))
    bar = Bar(TS, 1.1000, 1.1060, 1.0940, 1.1000)

    tp = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0800, take_profit=1.1050,
    )
    assert tp.reason is ExitReason.TAKE_PROFIT
    assert tp.slippage == pytest.approx(0.0, abs=1e-15)

    sl = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1500,
    )
    assert sl.reason is ExitReason.STOP_LOSS
    assert sl.slippage == pytest.approx(0.0002, abs=1e-12)
    # Long exit deals at mid - half - slip = 1.0950 - 0 - 0.0002 = 1.0948
    assert sl.exit_price == pytest.approx(1.0948, abs=1e-12)


# ==========================================================================
# 10. Latency is carried on the profile, and bars must be coherent
# ==========================================================================


def test_incoherent_bar_is_refused():
    with pytest.raises(ValueError, match="Incoherent bar"):
        Bar(TS, 1.1000, 1.0900, 1.0800, 1.0850)  # high below open


def test_negative_slippage_is_refused_at_construction():
    with pytest.raises(ValueError, match="price improvement"):
        FixedPointsSlippage(-1.0)


def test_a_stale_price_widens_the_EXIT_spread_too():
    """
    Regression. An earlier version widened the spread on entries only, which
    made the exit leg systematically cheaper than the entry leg on any
    instrument with weekend gaps -- a quieter version of the V1 bug.

    Spread 2 pips -> half 0.0001. Stale multiple 2.0 -> half 0.0002.
    Long exiting at a stop of 1.0950 after a weekend gap:
        dealt = 1.0950 - 0.0002 = 1.0948
        spread cost = 0.0002 * 10,000 = 2.00 USD  (twice the normal 1.00)
    """
    sim = FillSimulator(_profile(spread=FixedPipSpread(2.0)), stale_spread_multiple=2.0)
    bar = Bar(pd.Timestamp("2024-01-07 22:00", tz="UTC"), 1.1000, 1.1010, 1.0900, 1.0920)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1500,
        previous_time=pd.Timestamp("2024-01-05 21:00", tz="UTC"),
        bar_interval=pd.Timedelta(hours=4),
    )
    assert ex.stale is True
    assert ex.half_spread == pytest.approx(0.0002, abs=1e-15)
    assert ex.exit_price == pytest.approx(1.0948, abs=1e-12)
    assert ex.costs.spread_quote == pytest.approx(2.00, abs=1e-9)


def test_a_normal_gap_does_not_widen_the_exit_spread():
    sim = FillSimulator(_profile(spread=FixedPipSpread(2.0)), stale_spread_multiple=2.0)
    bar = Bar(pd.Timestamp("2024-01-03 12:00", tz="UTC"), 1.1000, 1.1010, 1.0900, 1.0920)
    ex = sim.resolve_exit(
        instrument=EURUSD, direction=Direction.LONG, size=10_000,
        bar=bar, bar_index=3, stop=1.0950, take_profit=1.1500,
        previous_time=pd.Timestamp("2024-01-03 08:00", tz="UTC"),
        bar_interval=pd.Timedelta(hours=4),
    )
    assert ex.stale is False
    assert ex.costs.spread_quote == pytest.approx(1.00, abs=1e-9)
