"""The DSL's exit vocabulary, executed: partials, trails, breakeven, time, cooldown.

Every monetary assertion in this file is hand-calculated in the docstring above
it, in the style of ``tests/golden/test_golden_pnl.py``. Where a number is
arrived at by arithmetic, the arithmetic is written out so a reader can check it
with a calculator rather than by re-running the code that produced it.

Fixtures are deliberately boring: a zero-spread, zero-slippage, zero-commission
profile, financing switched off, a constant USD->GBP rate of 0.80, and EURUSD
whose contract size is 1.0. A golden test whose fixture is itself clever cannot
be verified by hand.

What was wrong before these tests existed
-----------------------------------------
``backtest/engine.py`` read ``signal.take_profit_prices[0]`` and nothing else.
It had no handling for trailing stops, ``move_stop_to_breakeven_at_r``,
``max_bars_in_trade``, ``cooldown_bars_after_exit``,
``allow_reversal_on_opposite_signal`` or ``EventRestriction``, and the per-leg
``allocation`` fractions the DSL has always carried were discarded. The measured
consequence: ``donchian_breakout_atr``, which declares NO take-profit and relies
entirely on an ATR chandelier, produced SIX trades in thirteen years of XAUUSD
H4 because the trail never executed and every position ran to its hard stop.
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    FixedSizeSizer,
    PrecomputedSignals,
    _plan_legs,
    _TakeProfitLeg,
    run_backtest,
)
from fiboki.backtest.exits import (
    EventBlackout,
    ExitPolicy,
    ReversalMode,
    TrailKind,
    TrailSpec,
)
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction, ExitReason
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.sim.fills import ExitFill, IntrabarPolicy
from tests.helpers_exec import ConstantFx, flat_profile, make_frame

FX = ConstantFx({("USD", "GBP"): 0.80})
SIZE = 10_000.0


def _signal(
    ts: str,
    price: float,
    stop: float,
    targets: tuple[float, ...] = (),
    allocations: tuple[float, ...] = (),
    direction: Direction = Direction.LONG,
    instrument: str = "EURUSD",
) -> Signal:
    return Signal(
        strategy_id="exits",
        instrument=instrument,
        timeframe="H4",
        direction=direction,
        bar_time=pd.Timestamp(ts, tz="UTC"),
        reference_price=price,
        stop_price=stop,
        take_profit_prices=targets,
        take_profit_allocations=allocations,
    )


def _run(
    frame: pd.DataFrame,
    signals: list[Signal],
    *,
    policy: ExitPolicy | None = None,
    exit_series: pd.DataFrame | None = None,
    size: float = SIZE,
    strategy=None,
    **cfg_kw,
):
    cfg = BacktestConfig(
        initial_balance=cfg_kw.pop("initial_balance", 100_000.0),
        account_ccy="GBP",
        profile=cfg_kw.pop("profile", flat_profile()),
        charge_financing=cfg_kw.pop("charge_financing", False),
        strategy_id="exits",
        **cfg_kw,
    )
    return run_backtest(
        data={"EURUSD": frame},
        config=cfg,
        strategy=strategy if strategy is not None else PrecomputedSignals(signals),
        sizer=FixedSizeSizer(size),
        fx=FX,
        exit_policy=policy,
        exit_series=None if exit_series is None else {"EURUSD": exit_series},
    )


def _series(frame: pd.DataFrame, **columns: float) -> pd.DataFrame:
    """A constant-valued auxiliary series aligned to ``frame``'s index."""
    return pd.DataFrame({k: [v] * len(frame) for k, v in columns.items()}, index=frame.index)


# ==========================================================================
# 1. Multi-leg take profits with partial exits
# ==========================================================================


@pytest.mark.golden
def test_two_leg_partial_exit_closes_each_leg_and_reports_one_trade():
    """
    HAND-CALCULATED. Long 10,000 EURUSD filled at 1.1000, stop 1.0900, two
    take-profit legs: 40% at 1.1100 and 60% at 1.1200. GBP account, USD->GBP =
    0.80, contract size 1.0, zero spread/slippage/commission/financing.

    Leg sizes
        leg 1: 0.40 * 10,000 = 4,000
        leg 2: the allocations sum to 1.00, so the last leg takes the exact
               remainder: 10,000 - 4,000 = 6,000

    Leg 1 fires on the 08:00 bar (high 1.1150 >= 1.1100; the open 1.1040 is not
    already through it, so it fills AT the level, no price improvement):
        gross = (1.1100 - 1.1000) * 4,000 * 1.0 * 0.80
              = 0.0100 * 4,000 * 0.80 = 32.00 GBP

    Leg 2 fires on the 12:00 bar (high 1.1250 >= 1.1200, open 1.1100 not
    through):
        gross = (1.1200 - 1.1000) * 6,000 * 1.0 * 0.80
              = 0.0200 * 6,000 * 0.80 = 96.00 GBP

    The position therefore contributes ONE Trade row:
        size       = 10,000
        gross_pnl  = 32.00 + 96.00 = 128.00 GBP
        exit_price = (1.1100*4,000 + 1.1200*6,000) / 10,000
                   = (4,440 + 6,720) / 10,000 = 1.11600
        check:     (1.1160 - 1.1000) * 10,000 * 0.80 = 128.00 GBP  ✓
        exit_time  = the 12:00 bar, when the LAST piece left
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0990, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1150, 1.1030, 1.1100),
        ("2024-01-02 12:00", 1.1100, 1.1250, 1.1090, 1.1200),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100, 1.1200), (0.4, 0.6))],
    )

    assert len(res.exit_legs) == 2
    first, second = res.exit_legs
    assert first.size == pytest.approx(4_000.0)
    assert first.final is False
    assert first.exit_price == pytest.approx(1.1100)
    assert first.gross_pnl == pytest.approx(32.0, abs=1e-9)
    assert first.exit_time == pd.Timestamp("2024-01-02 08:00", tz="UTC")

    assert second.size == pytest.approx(6_000.0)
    assert second.final is True
    assert second.gross_pnl == pytest.approx(96.0, abs=1e-9)

    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.size == pytest.approx(10_000.0)
    assert trade.gross_pnl == pytest.approx(128.0, abs=1e-9)
    assert trade.net_pnl == pytest.approx(128.0, abs=1e-9)
    assert trade.exit_price == pytest.approx(1.1160, abs=1e-12)
    assert trade.exit_time == pd.Timestamp("2024-01-02 12:00", tz="UTC")
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert res.partial_exits == 1


@pytest.mark.golden
def test_allocation_remainder_rides_to_a_later_exit():
    """
    HAND-CALCULATED. The ``macd_ema_trend_hybrid`` shape: allocations 0.4 and
    0.3, which sum to 0.7. The remaining 30% is NOT reallocated — it rides.

    Leg sizes
        leg 1: 0.40 * 10,000 = 4,000  at 1.1100
        leg 2: 0.30 * 10,000 = 3,000  at 1.1200
        remainder: 10,000 - 7,000 = 3,000, still open

    Legs 1 and 2 fire on the 08:00 and 12:00 bars exactly as above:
        leg 1 gross = 0.0100 * 4,000 * 0.80 =  32.00
        leg 2 gross = 0.0200 * 3,000 * 0.80 =  48.00

    The remainder is closed at END_OF_DATA on the final bar's close of 1.1150:
        leg 3 gross = (1.1150 - 1.1000) * 3,000 * 0.80
                    = 0.0150 * 3,000 * 0.80 = 36.00

    One Trade:
        gross_pnl  = 32.00 + 48.00 + 36.00 = 116.00 GBP
        exit_price = (1.1100*4,000 + 1.1200*3,000 + 1.1150*3,000) / 10,000
                   = (4,440 + 3,360 + 3,345) / 10,000 = 1.11450
        check:     (1.1145 - 1.1000) * 10,000 * 0.80 = 116.00 GBP  ✓
        exit_reason = END_OF_DATA, because that is how the LAST piece left
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0990, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1150, 1.1030, 1.1100),
        ("2024-01-02 12:00", 1.1100, 1.1250, 1.1090, 1.1200),
        ("2024-01-02 16:00", 1.1200, 1.1210, 1.1140, 1.1150),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100, 1.1200), (0.4, 0.3))],
    )

    assert [leg.size for leg in res.exit_legs] == pytest.approx([4_000.0, 3_000.0, 3_000.0])
    assert [leg.final for leg in res.exit_legs] == [False, False, True]
    assert res.exit_legs[2].exit_reason == ExitReason.END_OF_DATA.value

    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.gross_pnl == pytest.approx(116.0, abs=1e-9)
    assert trade.exit_price == pytest.approx(1.1145, abs=1e-12)
    assert trade.exit_reason is ExitReason.END_OF_DATA
    assert res.partial_exits == 2


@pytest.mark.golden
def test_a_leg_and_the_stop_in_one_bar_respect_the_intrabar_policy():
    """
    HAND-CALCULATED. The 08:00 bar reaches BOTH the 1.1100 first target and the
    1.0950 stop. Without tick data the ordering is unknowable, so the configured
    :class:`IntrabarPolicy` decides — and it must decide for the partial exactly
    as it decides for a full one.

    Under STOP_FIRST (the default, and the conservative assumption) the whole
    10,000 closes at 1.0950 and the first leg never fires:
        gross = (1.0950 - 1.1000) * 10,000 * 0.80 = -0.0050 * 10,000 * 0.80
              = -40.00 GBP, one leg, reason STOP_LOSS.

    Under TARGET_FIRST the 4,000 leg fills at 1.1100 first (gross 32.00 GBP) and
    the remaining 6,000 is stopped at 1.0950 on the SAME bar:
        gross = 32.00 + (1.0950 - 1.1000) * 6,000 * 0.80
              = 32.00 - 24.00 = 8.00 GBP, two legs, reason STOP_LOSS.

    The gap between -40.00 and +8.00 on one bar is the size of the assumption
    V1 made silently by testing its take-profit branch first.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0990, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1150, 1.0900, 1.1000),
    ])
    signal = _signal("2024-01-02 00:00", 1.1000, 1.0950, (1.1100, 1.1200), (0.4, 0.6))

    pessimistic = _run(frame, [signal])
    assert len(pessimistic.exit_legs) == 1
    assert pessimistic.trades[0].exit_reason is ExitReason.STOP_LOSS
    assert pessimistic.trades[0].gross_pnl == pytest.approx(-40.0, abs=1e-9)

    optimistic = _run(frame, [signal], intrabar_policy=IntrabarPolicy.TARGET_FIRST)
    assert len(optimistic.exit_legs) == 2
    assert optimistic.exit_legs[0].size == pytest.approx(4_000.0)
    assert optimistic.exit_legs[0].gross_pnl == pytest.approx(32.0, abs=1e-9)
    assert optimistic.trades[0].exit_reason is ExitReason.STOP_LOSS
    assert optimistic.trades[0].gross_pnl == pytest.approx(8.0, abs=1e-9)


def test_a_signal_with_no_allocations_keeps_the_pre_multi_leg_behaviour():
    """A hand-built Signal that names targets but no allocations closes in full
    at the FIRST one. That is what every such signal meant before the engine
    could scale out, and restating those results silently would be worse than
    not implementing scale-outs at all."""
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0990, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1150, 1.1030, 1.1100),
        ("2024-01-02 12:00", 1.1100, 1.1250, 1.1090, 1.1200),
    ])
    res = _run(frame, [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100, 1.1200))])
    assert len(res.exit_legs) == 1
    assert res.exit_legs[0].size == pytest.approx(10_000.0)
    assert res.trades[0].exit_price == pytest.approx(1.1100)


def test_leg_ledger_reconciles_with_the_trade_ledger():
    """``sum(leg.net_pnl) == sum(trade.net_pnl)``, exactly.

    The whole justification for emitting one Trade per POSITION rather than one
    per fill is that nothing is lost: the per-fill detail is beside it and the
    two add up. If this ever fails, the ledger has become two ledgers.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0990, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1150, 1.1030, 1.1100),
        ("2024-01-02 12:00", 1.1100, 1.1250, 1.1090, 1.1200),
        ("2024-01-02 16:00", 1.1200, 1.1210, 1.1140, 1.1150),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100, 1.1200), (0.4, 0.3))],
        profile=flat_profile(spread_pips=1.2),
    )
    assert sum(leg.net_pnl for leg in res.exit_legs) == pytest.approx(
        sum(t.net_pnl for t in res.trades), abs=1e-9
    )
    for trade in res.trades:
        assert trade.net_pnl == pytest.approx(
            trade.gross_pnl
            - trade.spread_cost
            - trade.commission
            - trade.slippage_cost
            - trade.financing_cost,
            abs=1e-9,
        )
    # And the entry-leg spread was charged ONCE across the three exits, not once
    # per exit: a scaled-out position must not pay to open three times.
    assert res.costs.entry_leg_spread == pytest.approx(
        0.5 * 1.2 * 0.0001 * 10_000.0 * 0.80, abs=1e-9
    )


# ==========================================================================
# 2. Trailing stops
# ==========================================================================


TRAIL_FRAME_PREFIX = [
    ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),   # signal bar
    ("2024-01-02 04:00", 1.1000, 1.1020, 1.0995, 1.1010),   # entry at open 1.1000
    ("2024-01-02 08:00", 1.1010, 1.1100, 1.1000, 1.1090),
    ("2024-01-02 12:00", 1.1090, 1.1095, 1.1050, 1.1060),
]
CHANDELIER = TrailSpec(
    kind=TrailKind.ATR_CHANDELIER, value=2.0, atr_column="atr", activate_after_r=0.0
)


@pytest.mark.golden
def test_chandelier_trail_ratchets_and_never_loosens():
    """
    HAND-CALCULATED. Long 10,000 at 1.1000, initial stop 1.0900, constant ATR
    of 0.0040, a 2x ATR chandelier => a trail distance of 0.0080.

    The trail is computed from the bar that has just CLOSED and takes effect
    from the NEXT bar, so it can never fill on the bar that created it.

      04:00 (entry bar): extreme = high 1.1020
                         candidate = 1.1020 - 0.0080 = 1.0940 > 1.0900 -> stop 1.0940
      08:00: low 1.1000 > 1.0940, not stopped.
                         extreme = 1.1100
                         candidate = 1.1100 - 0.0080 = 1.1020 > 1.0940 -> stop 1.1020
      12:00: low 1.1050 > 1.1020, not stopped.
                         high 1.1095 < 1.1100 so the extreme does NOT move
                         candidate = 1.1020, not an improvement -> stop STAYS 1.1020
                         (a trail that followed the CLOSE would have loosened to
                          1.1060 - 0.0080 = 1.0980 here; the ratchet forbids it)
      16:00: open 1.1060 is not through the stop, low 1.1000 <= 1.1020 -> fills
             AT 1.1020, which is the level the ratchet preserved.

        gross = (1.1020 - 1.1000) * 10,000 * 1.0 * 0.80
              = 0.0020 * 10,000 * 0.80 = 16.00 GBP
    """
    frame = make_frame([*TRAIL_FRAME_PREFIX,
        ("2024-01-02 16:00", 1.1060, 1.1065, 1.1000, 1.1010),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(trailing=CHANDELIER),
        exit_series=_series(frame, atr=0.0040),
    )
    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.exit_price == pytest.approx(1.1020, abs=1e-12)
    assert trade.exit_reason is ExitReason.TRAILING_STOP
    assert trade.gross_pnl == pytest.approx(16.0, abs=1e-9)
    assert trade.exit_time == pd.Timestamp("2024-01-02 16:00", tz="UTC")


@pytest.mark.golden
def test_chandelier_trail_fills_through_a_gap_at_the_open():
    """
    HAND-CALCULATED. Identical to the ratchet case up to 12:00, so the stop
    stands at 1.1020. The 16:00 bar then OPENS at 1.0800, far below it.

    A stop that filled at its level through a gap was V1's free gift on every
    gap, and it is why V1's tail risk looked survivable. The trail gets no
    exemption from that rule: the fill is at min(stop, open) = 1.0800.

        gross = (1.0800 - 1.1000) * 10,000 * 1.0 * 0.80
              = -0.0200 * 10,000 * 0.80 = -160.00 GBP

    Had the fill been granted at the trailed level of 1.1020 it would have been
    +16.00 GBP, a 176.00 GBP swing on one bar.
    """
    frame = make_frame([*TRAIL_FRAME_PREFIX,
        ("2024-01-02 16:00", 1.0800, 1.0850, 1.0750, 1.0800),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(trailing=CHANDELIER),
        exit_series=_series(frame, atr=0.0040),
    )
    trade = res.trades[0]
    assert trade.exit_price == pytest.approx(1.0800, abs=1e-12)
    assert trade.exit_reason is ExitReason.TRAILING_STOP
    assert trade.gross_pnl == pytest.approx(-160.0, abs=1e-9)


@pytest.mark.golden
def test_fixed_distance_trail_and_activate_after_r():
    """
    HAND-CALCULATED. Long 10,000 at 1.1000, initial stop 1.0900, so the risk per
    unit is 0.0100 and 1R = 0.0100 of favourable excursion.

    A fixed-distance trail of 0.0050 that only ACTIVATES after 1R:

      04:00: MFE = 1.1020 - 1.1000 = 0.0020 -> 0.20R < 1R, trail inert,
             stop stays at 1.0900
      08:00: MFE = 1.1100 - 1.1000 = 0.0100 -> 1.00R >= 1R, trail engages
             extreme 1.1100 -> candidate 1.1100 - 0.0050 = 1.1050 -> stop 1.1050
      12:00: low 1.1050 <= 1.1050 -> stopped AT 1.1050

        gross = (1.1050 - 1.1000) * 10,000 * 0.80
              = 0.0050 * 10,000 * 0.80 = 40.00 GBP

    With ``activate_after_r = 0`` instead, the 04:00 bar would already have
    pulled the stop to 1.1020 - 0.0050 = 1.0970 — a different, and in this case
    worse, trade. Both are asserted, because "the activation threshold is read"
    is exactly the kind of claim that rots into a no-op.
    """
    frame = make_frame([*TRAIL_FRAME_PREFIX])
    late = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(
            trailing=TrailSpec(
                kind=TrailKind.FIXED_DISTANCE, value=0.0050, activate_after_r=1.0
            )
        ),
    )
    assert late.trades[0].exit_price == pytest.approx(1.1050, abs=1e-12)
    assert late.trades[0].exit_reason is ExitReason.TRAILING_STOP
    assert late.trades[0].gross_pnl == pytest.approx(40.0, abs=1e-9)
    assert late.trades[0].exit_time == pd.Timestamp("2024-01-02 12:00", tz="UTC")

    early = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(
            trailing=TrailSpec(
                kind=TrailKind.FIXED_DISTANCE, value=0.0050, activate_after_r=0.0
            )
        ),
    )
    # 04:00 sets 1.0970; 08:00's low of 1.1000 does not reach it; 08:00 then
    # ratchets to 1.1050 and 12:00's low of 1.1050 takes it. Same exit, but the
    # stop was live a bar earlier, which a lower low on 08:00 would have proved.
    assert early.trades[0].exit_price == pytest.approx(1.1050, abs=1e-12)

    dipped = make_frame([
        TRAIL_FRAME_PREFIX[0],
        TRAIL_FRAME_PREFIX[1],
        ("2024-01-02 08:00", 1.1010, 1.1100, 1.0960, 1.1090),
        TRAIL_FRAME_PREFIX[3],
    ])
    late_survives = _run(
        dipped,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(
            trailing=TrailSpec(
                kind=TrailKind.FIXED_DISTANCE, value=0.0050, activate_after_r=1.0
            )
        ),
    )
    early_stops = _run(
        dipped,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(
            trailing=TrailSpec(
                kind=TrailKind.FIXED_DISTANCE, value=0.0050, activate_after_r=0.0
            )
        ),
    )
    # The 08:00 low of 1.0960 is below the eager trail's 1.0970 but above the
    # patient one's stop, which is still the original 1.0900.
    assert early_stops.trades[0].exit_price == pytest.approx(1.0970, abs=1e-12)
    assert early_stops.trades[0].exit_time == pd.Timestamp("2024-01-02 08:00", tz="UTC")
    assert late_survives.trades[0].exit_time == pd.Timestamp("2024-01-02 12:00", tz="UTC")


def test_a_trail_with_no_series_is_refused_rather_than_silently_inert():
    """A chandelier whose ATR column is absent would never move the stop, and a
    strategy that silently never trails is the exact defect this work fixes."""
    frame = make_frame(TRAIL_FRAME_PREFIX)
    with pytest.raises(KeyError, match="trails on column"):
        _run(
            frame,
            [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
            policy=ExitPolicy(trailing=CHANDELIER),
        )


def test_a_misaligned_exit_series_is_refused():
    frame = make_frame(TRAIL_FRAME_PREFIX)
    shifted = _series(frame, atr=0.0040)
    shifted.index = shifted.index + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="indexed differently"):
        _run(
            frame,
            [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
            policy=ExitPolicy(trailing=CHANDELIER),
            exit_series=shifted,
        )


def test_a_short_position_trails_downwards_only():
    """The mirror image. A short's stop may only ever fall."""
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1005, 1.0980, 1.0990),
        ("2024-01-02 08:00", 1.0990, 1.0995, 1.0900, 1.0910),
        ("2024-01-02 12:00", 1.0910, 1.0960, 1.0905, 1.0950),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.1100, direction=Direction.SHORT)],
        policy=ExitPolicy(
            trailing=TrailSpec(kind=TrailKind.FIXED_DISTANCE, value=0.0050)
        ),
    )
    # 04:00: extreme 1.0980 -> stop 1.1030. 08:00: extreme 1.0900 -> stop 1.0950.
    # 12:00: high 1.0960 >= 1.0950 -> stopped at 1.0950.
    #   gross = (1.1000 - 1.0950) * 10,000 * 0.80 = 40.00 GBP
    assert res.trades[0].exit_price == pytest.approx(1.0950, abs=1e-12)
    assert res.trades[0].gross_pnl == pytest.approx(40.0, abs=1e-9)
    assert res.trades[0].exit_reason is ExitReason.TRAILING_STOP


# ==========================================================================
# 3. Breakeven
# ==========================================================================


@pytest.mark.golden
def test_breakeven_moves_the_stop_to_entry_at_the_declared_r():
    """
    HAND-CALCULATED. Long 10,000 at 1.1000, stop 1.0900 => risk 0.0100 per unit,
    ``move_stop_to_breakeven_at_r = 1.0``.

      04:00: MFE = 1.1050 - 1.1000 = 0.0050 -> 0.50R, below 1R, stop stays 1.0900
      08:00: MFE = 1.1120 - 1.1000 = 0.0120 -> 1.20R, at or above 1R,
             stop moves to the entry mid of 1.1000
      12:00: open 1.1100 not through, low 1.0990 <= 1.1000 -> fills AT 1.1000

        gross = (1.1000 - 1.1000) * 10,000 * 0.80 = 0.00 GBP

    Zero is the whole point of a breakeven stop, so the test is only meaningful
    against the counterfactual: with the rule switched off the same bars leave
    the position open to the end of the data and it exits at 1.1000's successor
    close of 1.0950 for -40.00 GBP.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0995, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1120, 1.1030, 1.1100),
        ("2024-01-02 12:00", 1.1100, 1.1105, 1.0990, 1.1000),
        ("2024-01-02 16:00", 1.1000, 1.1005, 1.0940, 1.0950),
    ])
    signal = [_signal("2024-01-02 00:00", 1.1000, 1.0900)]

    protected = _run(frame, signal, policy=ExitPolicy(breakeven_at_r=1.0))
    assert protected.trades[0].exit_price == pytest.approx(1.1000, abs=1e-12)
    assert protected.trades[0].net_pnl == pytest.approx(0.0, abs=1e-9)
    # BREAKEVEN, not TRAILING_STOP: no trail is declared on this policy at all,
    # so the old label was simply false. See ``ExitReason.BREAKEVEN``.
    assert protected.trades[0].exit_reason is ExitReason.BREAKEVEN
    assert protected.trades[0].exit_time == pd.Timestamp("2024-01-02 12:00", tz="UTC")

    unprotected = _run(frame, signal)
    assert unprotected.trades[0].exit_reason is ExitReason.END_OF_DATA
    assert unprotected.trades[0].net_pnl == pytest.approx(-40.0, abs=1e-9)


def test_a_trail_that_takes_over_from_breakeven_reports_the_trail():
    """Breakeven first, trail second: the level in force came from the trail.

    Long 10,000 at 1.1000, stop 1.0900 => 0.0100 risk. ``breakeven_at_r=0.5``
    and a 0.0050 fixed-distance trail with no activation threshold.

      04:00: MFE = 1.1060 - 1.1000 = 0.0060 -> 0.60R. Breakeven proposes
             1.1000; the trail proposes 1.1060 - 0.0050 = 1.1010, which is
             better, so 1.1010 is what is placed and the trail is what moved it.
      08:00: low 1.0995 <= 1.1010 -> stopped out at 1.1010.

    The exit reason must be TRAILING_STOP. Reporting BREAKEVEN here would
    credit the breakeven rule with a level it did not set.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1060, 1.0995, 1.1050),
        ("2024-01-02 08:00", 1.1050, 1.1055, 1.0995, 1.1000),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(
            breakeven_at_r=0.5,
            trailing=TrailSpec(kind=TrailKind.FIXED_DISTANCE, value=0.0050),
        ),
    )
    assert res.trades[0].exit_price == pytest.approx(1.1010, abs=1e-12)
    assert res.trades[0].exit_reason is ExitReason.TRAILING_STOP


def test_a_stop_that_never_moved_is_still_a_plain_stop():
    """A declared breakeven rule that never fires changes no label at all."""
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1005, 1.0890, 1.0900),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(breakeven_at_r=5.0),
    )
    assert res.trades[0].exit_reason is ExitReason.STOP_LOSS


def test_breakeven_is_a_persisted_value_readers_can_round_trip():
    """The enum value is what lands in a stored trade, and it must come back."""
    assert ExitReason.BREAKEVEN.value == "breakeven"
    assert ExitReason("breakeven") is ExitReason.BREAKEVEN
    # Every previously stored value still resolves: adding a member widens the
    # vocabulary, it does not invalidate anything already written.
    for value in (
        "stop_loss", "take_profit", "trailing_stop", "time_stop",
        "opposite_signal", "invalidation", "session_close", "risk_halt",
        "end_of_data", "margin_call",
    ):
        assert ExitReason(value).value == value


def test_breakeven_below_the_current_stop_is_not_applied():
    """A stop already better than entry is never dragged back to entry. The
    rule may only ever improve the level, like every other stop move here."""
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1200, 1.0995, 1.1190),
        ("2024-01-02 08:00", 1.1190, 1.1195, 1.1000, 1.1010),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(
            breakeven_at_r=1.0,
            trailing=TrailSpec(kind=TrailKind.FIXED_DISTANCE, value=0.0050),
        ),
    )
    # 04:00 pushes the trail to 1.1200 - 0.0050 = 1.1150, which is far above the
    # 1.1000 entry; breakeven must not pull it back down.
    assert res.trades[0].exit_price == pytest.approx(1.1150, abs=1e-12)


# ==========================================================================
# 4. Time stop and cooldown
# ==========================================================================


@pytest.mark.golden
def test_time_stop_closes_at_the_close_of_the_nth_bar():
    """
    HAND-CALCULATED. ``max_bars_in_trade = 3``. The position fills at the 04:00
    bar's open of 1.1000. ``bars_held`` counts bars STRICTLY after the entry
    bar, so it reads 1 at 08:00, 2 at 12:00 and 3 at 16:00, where the time stop
    fires at that bar's close of 1.1030.

        gross = (1.1030 - 1.1000) * 10,000 * 1.0 * 0.80
              = 0.0030 * 10,000 * 0.80 = 24.00 GBP
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1020, 1.0995, 1.1010),
        ("2024-01-02 08:00", 1.1010, 1.1030, 1.1000, 1.1020),
        ("2024-01-02 12:00", 1.1020, 1.1040, 1.1010, 1.1030),
        ("2024-01-02 16:00", 1.1030, 1.1045, 1.1020, 1.1030),
        ("2024-01-02 20:00", 1.1030, 1.1050, 1.1025, 1.1040),
    ])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.2000,), (1.0,))],
        policy=ExitPolicy(max_bars_in_trade=3),
    )
    trade = res.trades[0]
    assert trade.exit_reason is ExitReason.TIME_STOP
    assert trade.bars_held == 3
    assert trade.exit_time == pd.Timestamp("2024-01-02 16:00", tz="UTC")
    assert trade.exit_price == pytest.approx(1.1030, abs=1e-12)
    assert trade.gross_pnl == pytest.approx(24.0, abs=1e-9)


def test_without_a_time_stop_the_same_position_survives_the_bar():
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1020, 1.0995, 1.1010),
        ("2024-01-02 08:00", 1.1010, 1.1030, 1.1000, 1.1020),
        ("2024-01-02 12:00", 1.1020, 1.1040, 1.1010, 1.1030),
        ("2024-01-02 16:00", 1.1030, 1.1045, 1.1020, 1.1030),
        ("2024-01-02 20:00", 1.1030, 1.1050, 1.1025, 1.1040),
    ])
    res = _run(frame, [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.2000,), (1.0,))])
    assert res.trades[0].exit_reason is ExitReason.END_OF_DATA
    assert res.trades[0].bars_held == 4


class _EveryBarLong:
    """Signals a long on every bar with enough history. Used to prove that a
    cooldown actually suppresses the re-entry the strategy is asking for."""

    def on_bar(self, ctx):
        if ctx.bar_index < 1:
            return ()
        close = float(ctx.history("EURUSD")["close"].iloc[-1])
        return (
            Signal(
                strategy_id="exits",
                instrument="EURUSD",
                timeframe="H4",
                direction=Direction.LONG,
                bar_time=ctx.timestamp,
                reference_price=close,
                stop_price=close - 0.0500,
                take_profit_prices=(close + 0.0010,),
                take_profit_allocations=(1.0,),
            ),
        )


def _rising_frame(n: int = 14) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2024-01-02 00:00", tz="UTC")
    price = 1.1000
    for k in range(n):
        o = price
        c = price + 0.0020
        rows.append(((start + pd.Timedelta(hours=4 * k)).isoformat(), o, c + 0.0005, o - 0.0005, c))
        price = c
    return make_frame(rows)


def test_cooldown_suppresses_re_entry_for_exactly_n_bars():
    """
    The strategy asks to be long on EVERY bar. Each position takes its 10-pip
    target on the next bar, so without a cooldown it is in the market
    continuously. With ``cooldown_bars_after_exit = 2``, an exit on bar i blocks
    the fills on bars i+1 and i+2, and the next entry is on bar i+3.
    """
    frame = _rising_frame()
    no_cooldown = _run(frame, [], strategy=_EveryBarLong(), size=100.0)
    cooled = _run(
        frame,
        [],
        strategy=_EveryBarLong(),
        size=100.0,
        policy=ExitPolicy(cooldown_bars_after_exit=2),
    )

    assert len(cooled.trades) < len(no_cooldown.trades)
    assert cooled.rejections.get("cooldown", 0) > 0

    entries = sorted(t.entry_time for t in cooled.trades)
    exits = {t.entry_time: t.exit_time for t in cooled.trades}
    step = pd.Timedelta(hours=4)
    for entry in entries[:-1]:
        # Exit on bar i; the next entry may not be earlier than bar i+3.
        later = [e for e in entries if e > entry]
        if later:
            assert later[0] >= exits[entry] + 3 * step


# ==========================================================================
# 5. Opposite-signal handling
# ==========================================================================


def _reversal_frame() -> pd.DataFrame:
    return make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1020, 1.0995, 1.1010),
        ("2024-01-02 08:00", 1.1010, 1.1030, 1.1000, 1.1020),
        ("2024-01-02 12:00", 1.1020, 1.1040, 1.1010, 1.1030),
        ("2024-01-02 16:00", 1.1030, 1.1045, 1.1020, 1.1040),
    ])


@pytest.mark.golden
def test_reverse_closes_at_the_next_open_and_opens_the_other_way():
    """
    HAND-CALCULATED. A long fills at the 04:00 open of 1.1000. The strategy
    signals SHORT on the 08:00 closed bar. The engine may not act at that bar's
    close — the same rule that makes an entry actionable no earlier than the
    next bar's open — so the long closes at the 12:00 OPEN of 1.1020 and the
    short opens on that same bar at the same price.

        long  gross = (1.1020 - 1.1000) * 10,000 * 0.80 = 16.00 GBP
        short gross = (1.1040 - 1.1020) * 10,000 * 0.80 * (-1) = -16.00 GBP
              (the short is closed at END_OF_DATA on the 16:00 close of 1.1040)
    """
    frame = _reversal_frame()
    signals = [
        _signal("2024-01-02 00:00", 1.1000, 1.0900),
        _signal("2024-01-02 08:00", 1.1020, 1.1500, direction=Direction.SHORT),
    ]
    res = _run(frame, signals, policy=ExitPolicy(reversal=ReversalMode.REVERSE))

    assert len(res.trades) == 2
    first, second = res.trades
    assert first.direction is Direction.LONG
    assert first.exit_reason is ExitReason.OPPOSITE_SIGNAL
    assert first.exit_time == pd.Timestamp("2024-01-02 12:00", tz="UTC")
    assert first.exit_price == pytest.approx(1.1020, abs=1e-12)
    assert first.gross_pnl == pytest.approx(16.0, abs=1e-9)

    assert second.direction is Direction.SHORT
    assert second.entry_time == pd.Timestamp("2024-01-02 12:00", tz="UTC")
    assert second.entry_price == pytest.approx(1.1020, abs=1e-12)
    assert second.gross_pnl == pytest.approx(-16.0, abs=1e-9)


def test_close_only_closes_without_reopening():
    frame = _reversal_frame()
    signals = [
        _signal("2024-01-02 00:00", 1.1000, 1.0900),
        _signal("2024-01-02 08:00", 1.1020, 1.1500, direction=Direction.SHORT),
    ]
    res = _run(frame, signals, policy=ExitPolicy(reversal=ReversalMode.CLOSE_ONLY))
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason is ExitReason.OPPOSITE_SIGNAL
    assert res.rejections.get("reversal_close_only") == 1


def test_ignore_leaves_the_position_alone_and_refuses_the_new_order():
    """The default. ``allow_reversal_on_opposite_signal = False`` does not mean
    'close'; it means the opposite signal is an ordinary order, and
    ``max_per_instrument`` then refuses it."""
    frame = _reversal_frame()
    signals = [
        _signal("2024-01-02 00:00", 1.1000, 1.0900),
        _signal("2024-01-02 08:00", 1.1020, 1.1500, direction=Direction.SHORT),
    ]
    res = _run(frame, signals, max_concurrent=2)
    assert len(res.trades) == 1
    assert res.trades[0].direction is Direction.LONG
    assert res.trades[0].exit_reason is ExitReason.END_OF_DATA
    assert res.rejections.get("max_per_instrument") == 1


# ==========================================================================
# 6. Event restrictions
# ==========================================================================


class _AlwaysBlackout:
    def in_blackout(self, instrument, ts, **kwargs) -> bool:
        return True


class _NeverBlackout:
    """What an EMPTY economic calendar looks like from here, and therefore what
    every blackout query in this repository currently answers."""

    def in_blackout(self, instrument, ts, **kwargs) -> bool:
        return False


def _blackout_run(frame, signals, blackout, events):
    cfg = BacktestConfig(
        initial_balance=100_000.0,
        account_ccy="GBP",
        profile=flat_profile(),
        charge_financing=False,
        strategy_id="exits",
    )
    return BacktestEngine(
        data={"EURUSD": frame},
        config=cfg,
        strategy=PrecomputedSignals(signals),
        sizer=FixedSizeSizer(SIZE),
        fx=FX,
        exit_policy=ExitPolicy(events=events),
        blackout=blackout,
    ).run()


def test_event_blackout_declines_the_entry():
    frame = _reversal_frame()
    signals = [_signal("2024-01-02 00:00", 1.1000, 1.0900)]
    events = EventBlackout(minutes_before=30, minutes_after=30)

    blocked = _blackout_run(frame, signals, _AlwaysBlackout(), events)
    assert blocked.trades == []
    assert blocked.rejections.get("event_blackout") == 1

    allowed = _blackout_run(frame, signals, _NeverBlackout(), events)
    assert len(allowed.trades) == 1
    assert "event_blackout" not in allowed.rejections


def test_with_no_calendar_the_blackout_is_inert():
    """THE HONEST CAVEAT, as an assertion. No calendar (and, identically, an
    empty one) means every query answers False and the backtest trades straight
    through FOMC and NFP. See ``marketstate.calendar.USER_ACTION_NOTE``."""
    frame = _reversal_frame()
    signals = [_signal("2024-01-02 00:00", 1.1000, 1.0900)]
    res = _blackout_run(
        frame, signals, None, EventBlackout(minutes_before=999, minutes_after=999)
    )
    assert len(res.trades) == 1


def test_the_blackout_is_evaluated_on_the_FILL_bar_not_the_signal_bar():
    """
    The H4 rollover case, which is what makes the anchor matter.

    Bars are stamped 17:00, 21:00, 01:00 and 05:00 UTC; the financing rollover
    is 21:00. A signal on the 17:00 bar would DEAL at the 21:00 open, which is
    exactly the hour ``avoid_rollover_hour`` exists to avoid and the hour the
    IG profile triples the spread in. A signal on the 21:00 bar deals at 01:00,
    which is clear of it.

    So the 17:00 signal must be refused and the 21:00 signal must not be.
    Anchoring on the signal bar would get both backwards.
    """
    frame = make_frame([
        ("2024-01-31 17:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-31 21:00", 1.1000, 1.1020, 1.0995, 1.1010),
        ("2024-02-01 01:00", 1.1010, 1.1030, 1.1000, 1.1020),
        ("2024-02-01 05:00", 1.1020, 1.1040, 1.1010, 1.1030),
    ])
    events = EventBlackout(avoid_rollover_hour=True)

    deals_at_rollover = _blackout_run(
        frame, [_signal("2024-01-31 17:00", 1.1000, 1.0900)], None, events
    )
    assert deals_at_rollover.trades == []
    assert deals_at_rollover.rejections.get("rollover_hour") == 1

    deals_after_rollover = _blackout_run(
        frame, [_signal("2024-01-31 21:00", 1.1010, 1.0900)], None, events
    )
    assert len(deals_after_rollover.trades) == 1
    assert deals_after_rollover.trades[0].entry_time == pd.Timestamp(
        "2024-02-01 01:00", tz="UTC"
    )
    assert "rollover_hour" not in deals_after_rollover.rejections


def test_month_end_needs_no_calendar_either():
    """Both clock rules bite with no feed loaded, unlike the event blackout."""
    frame = make_frame([
        ("2024-01-31 09:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-31 13:00", 1.1000, 1.1020, 1.0995, 1.1010),
        ("2024-02-01 01:00", 1.1010, 1.1030, 1.1000, 1.1020),
        ("2024-02-01 05:00", 1.1020, 1.1040, 1.1010, 1.1030),
    ])
    events = EventBlackout(avoid_month_end=True)

    # Signal on the 09:00 bar of the 31st deals at 13:00 on the 31st: blocked.
    blocked = _blackout_run(
        frame, [_signal("2024-01-31 09:00", 1.1000, 1.0900)], None, events
    )
    assert blocked.trades == []
    assert blocked.rejections.get("month_end") == 1

    # Signal on the 13:00 bar deals at 01:00 on the 1st: the month has turned.
    allowed = _blackout_run(
        frame, [_signal("2024-01-31 13:00", 1.1010, 1.0900)], None, events
    )
    assert len(allowed.trades) == 1
    assert "month_end" not in allowed.rejections


# ==========================================================================
# 7. Size arithmetic that cannot be reached end to end today
# ==========================================================================


def _toy_instrument(**kwargs) -> Instrument:
    """An instrument whose minimum size EXCEEDS its size step.

    No instrument in ``core/instruments.py`` is like this today — all 123
    registered instruments (every one OANDA offers, 2026-09-30) have
    ``min_size == size_step``, so a partial can
    never leave a residue below the minimum. The guard is written anyway,
    because the registry is data and a venue with a 0.5-lot minimum and a
    0.1-lot step is an ordinary thing for it to acquire.
    """
    base = dataclasses.asdict(get_instrument("EURUSD"))
    base.update(kwargs)
    return Instrument(**base)


def test_a_partial_that_would_leave_a_sub_minimum_residue_closes_the_lot():
    """
    HAND-CALCULATED. An instrument with ``min_size = 0.5`` and
    ``size_step = 0.1``. A position of 1.0 units with a single 60% leg:

        leg size = floor(1.0 * 0.6 / 0.1) * 0.1 = 0.6
        residue  = 1.0 - 0.6 = 0.4, which is BELOW the 0.5 minimum

    The residue would be a position the venue will not let you out of, so the
    leg takes the lot: 1.0 units, and the exit is final.

    With ``min_size = 0.1`` instead, 0.4 is perfectly dealable and the leg
    closes 0.6, leaving 0.4 to ride.
    """
    from fiboki.backtest.engine import _OpenPosition
    from fiboki.core.contracts import Position

    def _probe(min_size: float) -> tuple[float, bool]:
        instrument = _toy_instrument(min_size=min_size, size_step=0.1)
        legs = _plan_legs(instrument, 1.0, (1.2000,), (0.6,))
        assert legs[0].size == pytest.approx(0.6)
        position = Position(
            instrument="EURUSD",
            direction=Direction.LONG,
            size=1.0,
            entry_price=1.1000,
            entry_time=pd.Timestamp("2024-01-02", tz="UTC"),
            stop_loss=1.0900,
        )
        op = _OpenPosition(
            seq=1,
            position=position,
            instrument=instrument,
            entry_mid=1.1000,
            entry_bar_index=0,
            take_profit=1.2000,
            entry_costs_account={"spread": 0.0, "commission": 0.0, "slippage": 0.0, "premium": 0.0},
            entry_size=1.0,
            legs=legs,
        )
        fill = ExitFill(
            exited=True, reason=ExitReason.TAKE_PROFIT, mid_price=1.2000
        )
        return BacktestEngine._size_for_exit(op, fill)

    size, final = _probe(0.5)
    assert size == pytest.approx(1.0)
    assert final is True

    size, final = _probe(0.1)
    assert size == pytest.approx(0.6)
    assert final is False


def test_plan_legs_gives_the_last_leg_the_exact_remainder():
    """
    HAND-CALCULATED. 333 units of an instrument with a size step of 1.0, split
    0.5 / 0.5.

        leg 1 = floor(333 * 0.5) = floor(166.5) = 166
        leg 2 = the allocations sum to 1.0, so it takes 333 - 166 = 167

    166 + 167 = 333 exactly. Rounding each leg independently would have given
    166 + 166 = 332 and left a one-unit stub for some other rule to clear up.
    """
    instrument = get_instrument("EURUSD")
    legs = _plan_legs(instrument, 333.0, (1.1100, 1.1200), (0.5, 0.5))
    assert [leg.size for leg in legs] == [166.0, 167.0]
    assert sum(leg.size for leg in legs) == 333.0


def test_plan_legs_drops_a_leg_that_floors_to_nothing():
    instrument = get_instrument("EURUSD")  # size step 1.0
    legs = _plan_legs(instrument, 10.0, (1.1100, 1.1200), (0.05, 0.5))
    assert [leg.price for leg in legs] == [1.1200]
    assert legs == [_TakeProfitLeg(1.1200, 5.0)]


def test_rollover_hour_blocks_every_entry_on_a_series_stamped_at_that_hour():
    """The sharp edge, asserted so nobody discovers it in a report.

    ``avoid_rollover_hour`` compares whole hours. A D1 series stamped 21:00 UTC
    against the default 21:00 rollover therefore blocks EVERY entry. The run
    produces no trades and a large ``rollover_hour`` rejection count — which is
    the evidence a reader needs. Nothing silently disables the rule, because a
    restriction that switches itself off is worse than one that visibly bites.
    """
    rows = []
    for k in range(6):
        day = pd.Timestamp("2024-01-01 21:00", tz="UTC") + pd.Timedelta(days=k)
        price = 1.1000 + 0.0010 * k
        rows.append((day.isoformat(), price, price + 0.0020, price - 0.0010, price + 0.0015))
    daily = make_frame(rows)
    res = _blackout_run(
        daily,
        [_signal(rows[0][0], rows[0][4], rows[0][4] - 0.0500)],
        None,
        EventBlackout(avoid_rollover_hour=True),
    )
    assert res.trades == []
    assert res.orders_submitted == 1
    assert res.rejections.get("rollover_hour") == 1
