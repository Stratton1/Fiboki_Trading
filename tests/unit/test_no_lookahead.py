"""Look-ahead tests: mutate the future, assert the past is unchanged.

The principle
-------------
A backtest is free of look-ahead if and only if replacing every bar after time
T with completely different data leaves every decision made at or before T
untouched. That is a property, not a code review, and it is checkable.

These tests mutate future bars violently (up to +/- 25% per bar, which is
absurd for H4 FX and therefore impossible to pass by luck) and assert that the
trades that had already closed are byte-identical, cost for cost.

Complementary structural tests assert that ``BarContext`` cannot hand a
strategy a future row at all, and that the engine rejects a signal dated
anywhere other than the bar it was emitted on.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.scenario_exec import (
    BreakoutStrategy,
    ConstantUsdGbp,
    build_config,
    run_scenario,
    synthetic_data,
)

CUT = 800  # bar index after which the future is replaced


def _shock_future(
    data: dict[str, pd.DataFrame], cut: int, seed: int = 7
) -> dict[str, pd.DataFrame]:
    """Multiply every bar after ``cut`` by a large random factor.

    All four prices in a row are scaled by the SAME positive factor, so each
    bar stays internally coherent (low <= open, close <= high) while being
    numerically unrecognisable.
    """
    out: dict[str, pd.DataFrame] = {}
    for k, (symbol, frame) in enumerate(sorted(data.items())):
        rng = np.random.default_rng([seed, k])
        copy = frame.copy()
        n_future = len(copy) - (cut + 1)
        factors = 1.0 + rng.uniform(-0.25, 0.25, size=n_future)
        values = copy.to_numpy(dtype=np.float64)
        values[cut + 1 :, :] *= factors[:, None]
        out[symbol] = pd.DataFrame(values, index=copy.index, columns=copy.columns)
    return out


def _closed_by(result, cutoff: pd.Timestamp):
    return [t for t in result.trades if t.exit_time <= cutoff]


def _row(t):
    """Everything about a trade except its random UUID."""
    return (
        t.instrument, t.direction.value, t.size, t.entry_price, t.exit_price,
        t.entry_time, t.exit_time, t.exit_reason.value, t.gross_pnl,
        t.spread_cost, t.commission, t.slippage_cost, t.financing_cost,
        t.net_pnl, t.bars_held, t.max_adverse_excursion, t.max_favourable_excursion,
    )


def test_replacing_the_future_leaves_closed_trades_identical():
    data = synthetic_data()
    cutoff = data["EURUSD"].index[CUT]

    baseline = run_scenario(data)
    shocked = run_scenario(_shock_future(data, CUT))

    base_closed = _closed_by(baseline, cutoff)
    shock_closed = _closed_by(shocked, cutoff)

    assert len(base_closed) > 10, (
        f"Only {len(base_closed)} trades closed before the cut; the test would "
        "be vacuous. Move the cut later or lengthen the scenario."
    )
    assert len(base_closed) == len(shock_closed)
    for a, b in zip(base_closed, shock_closed, strict=True):
        assert _row(a) == _row(b), f"Trade closed at {a.exit_time} changed when the future changed"


def test_the_future_shock_really_does_change_the_later_ledger():
    """Guards against the previous test passing because nothing happened."""
    data = synthetic_data()
    baseline = run_scenario(data)
    shocked = run_scenario(_shock_future(data, CUT))
    assert baseline.ledger_sha256() != shocked.ledger_sha256()
    assert baseline.final_equity != pytest.approx(shocked.final_equity, rel=1e-9)


def test_equity_curve_before_the_cut_is_unchanged():
    """Mark-to-market equity is also a decision output, so it must be pinned."""
    data = synthetic_data()
    cutoff = data["EURUSD"].index[CUT]

    baseline = run_scenario(data).equity_curve.loc[:cutoff]
    shocked = run_scenario(_shock_future(data, CUT)).equity_curve.loc[:cutoff]

    assert len(baseline) == len(shocked) > 100
    pd.testing.assert_frame_equal(baseline, shocked, check_exact=True)


@pytest.mark.parametrize("cut", [200, 500, 1100])
def test_the_property_holds_at_several_cut_points(cut):
    data = synthetic_data()
    cutoff = data["EURUSD"].index[cut]
    baseline = run_scenario(data)
    shocked = run_scenario(_shock_future(data, cut, seed=cut))
    base_closed = _closed_by(baseline, cutoff)
    shock_closed = _closed_by(shocked, cutoff)
    assert len(base_closed) == len(shock_closed)
    assert [_row(t) for t in base_closed] == [_row(t) for t in shock_closed]


def test_mutating_only_the_final_bar_changes_nothing_that_already_closed():
    data = synthetic_data()
    baseline = run_scenario(data)

    tampered = {k: v.copy() for k, v in data.items()}
    for frame in tampered.values():
        frame.iloc[-1] = frame.iloc[-1] * 1.20

    changed = run_scenario(tampered)
    # Everything except trades still open into the last bar must be identical.
    last_ts = data["EURUSD"].index[-1]
    base_closed = [t for t in baseline.trades if t.exit_time < last_ts]
    changed_closed = [t for t in changed.trades if t.exit_time < last_ts]
    assert [_row(t) for t in base_closed] == [_row(t) for t in changed_closed]


# --------------------------------------------------------------------------
# Structural guards
# --------------------------------------------------------------------------


def test_bar_context_never_exposes_a_future_row():
    """The slice ``iloc[:cursor+1]`` is the mechanism; this asserts it holds."""
    from fiboki.backtest.engine import FixedFractionalSizer, run_backtest

    seen: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    class Spy(BreakoutStrategy):
        def on_bar(self, ctx):
            for symbol in ctx.instruments:
                hist = ctx.history(symbol)
                if len(hist):
                    seen.append((ctx.timestamp, hist.index[-1]))
            return super().on_bar(ctx)

    run_backtest(
        data=synthetic_data(400),
        config=build_config(),
        strategy=Spy(),
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=ConstantUsdGbp(),
    )
    assert seen, "the spy never saw a bar"
    assert all(last <= now for now, last in seen), (
        "BarContext.history returned a row dated after the current bar"
    )


def test_a_signal_dated_in_the_future_is_rejected_loudly():
    """A strategy that fabricates a bar_time must fail fast, not be absorbed."""
    from fiboki.backtest.engine import (
        BacktestConfig,
        FixedSizeSizer,
        run_backtest,
    )
    from fiboki.core.contracts import Signal
    from fiboki.core.enums import Direction
    from tests.helpers_exec import ConstantFx, flat_profile, make_frame

    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1060, 1.0995, 1.1050),
        ("2024-01-02 08:00", 1.1050, 1.1060, 1.1040, 1.1050),
    ])

    class Cheat:
        def on_bar(self, ctx):
            if ctx.bar_index != 0:
                return ()
            return (
                Signal(
                    strategy_id="cheat", instrument="EURUSD", timeframe="H4",
                    direction=Direction.LONG,
                    bar_time=pd.Timestamp("2024-01-02 08:00", tz="UTC"),  # the future
                    reference_price=1.1000, stop_price=1.0950,
                ),
            )

    with pytest.raises(ValueError, match="look-ahead"):
        run_backtest(
            data={"EURUSD": frame},
            config=BacktestConfig(
                initial_balance=10_000.0, account_ccy="GBP", profile=flat_profile()
            ),
            strategy=Cheat(),
            sizer=FixedSizeSizer(1_000),
            fx=ConstantFx({("USD", "GBP"): 0.8}),
        )


def test_execution_latency_pushes_the_fill_further_into_the_future():
    """Zero latency fills on the NEXT bar; one bar of latency on the one after.

    A single signal on bar 0 is enough to pin this down exactly, which the
    aggregate scenario cannot do.
    """
    import dataclasses

    from fiboki.backtest.engine import (
        BacktestConfig,
        FixedSizeSizer,
        PrecomputedSignals,
        run_backtest,
    )
    from fiboki.core.contracts import Signal
    from fiboki.core.enums import Direction
    from tests.helpers_exec import ConstantFx, flat_profile, make_frame

    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),   # signal bar
        ("2024-01-02 04:00", 1.2000, 1.2010, 1.1990, 1.2000),   # zero-latency entry
        ("2024-01-02 08:00", 1.3000, 1.3010, 1.2990, 1.3000),   # one-bar-latency entry
        ("2024-01-02 12:00", 1.3000, 1.3010, 1.2990, 1.3000),
    ])
    sig = Signal(
        strategy_id="lat", instrument="EURUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=1.1000, stop_price=1.0500, take_profit_prices=(2.0000,),
    )

    def _entry_price(latency: int) -> float:
        profile = dataclasses.replace(flat_profile(), latency_bars=latency)
        res = run_backtest(
            data={"EURUSD": frame},
            config=BacktestConfig(
                initial_balance=100_000.0, account_ccy="GBP", profile=profile
            ),
            strategy=PrecomputedSignals([sig]),
            sizer=FixedSizeSizer(1_000),
            fx=ConstantFx({("USD", "GBP"): 0.8}),
        )
        assert len(res.trades) == 1
        return res.trades[0].entry_price

    assert _entry_price(0) == pytest.approx(1.2000, abs=1e-12)
    assert _entry_price(1) == pytest.approx(1.3000, abs=1e-12)
