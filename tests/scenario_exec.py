"""A single non-trivial backtest scenario, shared by the determinism and
look-ahead tests.

It is deliberately the HARDEST case the engine supports, because a determinism
test on an easy case proves nothing:

* two instruments with different pip sizes, contract specs and price scales,
  on a shared timeline;
* the SEVERE_STRESS profile, so probabilistic slippage, partial fills,
  rejections and one bar of execution latency are all live;
* several concurrent positions, so ordering effects are exposed;
* longs and shorts;
* a GBP account against USD-quoted instruments, so every figure passes through
  a currency conversion.

The price data is generated from a fixed seed so the scenario is reproducible
across processes without shipping a data file.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fiboki.backtest.engine import BacktestConfig, FixedFractionalSizer
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction
from fiboki.sim.profiles import SEVERE_STRESS

SPECS = (
    # symbol,   start price, per-bar vol (fraction)
    ("EURUSD", 1.1000, 0.0016),
    ("XAUUSD", 1850.0, 0.0042),
)


def synthetic_data(n_bars: int = 1400, seed: int = 20240101) -> dict[str, pd.DataFrame]:
    """Reproducible random-walk OHLC on an H4 grid, one frame per instrument."""
    out: dict[str, pd.DataFrame] = {}
    idx = pd.date_range("2021-01-04 00:00", periods=n_bars, freq="4h", tz="UTC")
    idx.name = "timestamp"
    for k, (symbol, start, vol) in enumerate(SPECS):
        rng = np.random.default_rng([seed, k])
        steps = rng.normal(0.0, vol, size=n_bars)
        close = start * np.exp(np.cumsum(steps))
        open_ = np.empty(n_bars)
        open_[0] = start
        open_[1:] = close[:-1]
        wick = np.abs(rng.normal(0.0, vol * 0.9, size=n_bars)) * close
        high = np.maximum(open_, close) + wick
        low = np.minimum(open_, close) - wick
        out[symbol] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close}, index=idx
        )
    return out


class BreakoutStrategy:
    """A plain Donchian breakout. Reads ONLY ``ctx.history``, which ends at now.

    The strategy is not the point of these tests — being a realistic consumer
    of the engine's context is. It emits both directions, on both instruments,
    frequently enough to build a meaningful ledger.
    """

    def __init__(self, lookback: int = 20, stop_fraction: float = 0.5, rr: float = 1.5) -> None:
        self.lookback = lookback
        self.stop_fraction = stop_fraction
        self.rr = rr

    def on_bar(self, ctx) -> list[Signal]:
        signals: list[Signal] = []
        for symbol in ctx.instruments:            # sorted: determinism
            hist = ctx.history(symbol)
            if len(hist) < self.lookback + 2:
                continue
            window = hist.iloc[-(self.lookback + 1) : -1]
            close = float(hist["close"].iloc[-1])
            hi = float(window["high"].max())
            lo = float(window["low"].min())
            span = hi - lo
            if span <= 0 or close <= 0:
                continue
            stop_dist = span * self.stop_fraction
            if stop_dist <= 0 or stop_dist >= close:
                continue
            if close > hi:
                signals.append(
                    Signal(
                        strategy_id="breakout", instrument=symbol, timeframe="H4",
                        direction=Direction.LONG, bar_time=ctx.timestamp,
                        reference_price=close,
                        stop_price=close - stop_dist,
                        take_profit_prices=(close + stop_dist * self.rr,),
                    )
                )
            elif close < lo:
                signals.append(
                    Signal(
                        strategy_id="breakout", instrument=symbol, timeframe="H4",
                        direction=Direction.SHORT, bar_time=ctx.timestamp,
                        reference_price=close,
                        stop_price=close + stop_dist,
                        take_profit_prices=(close - stop_dist * self.rr,),
                    )
                )
        return signals


class ConstantUsdGbp:
    """USD->GBP = 0.79, and nothing else. Explicit and process-independent."""

    def rate(self, from_ccy: str, to_ccy: str, when: pd.Timestamp) -> float:
        f, t = from_ccy.upper(), to_ccy.upper()
        if f == t:
            return 1.0
        if (f, t) == ("USD", "GBP"):
            return 0.79
        if (f, t) == ("GBP", "USD"):
            return 1.0 / 0.79
        raise KeyError(f"No rate for {f}->{t}")


def build_config() -> BacktestConfig:
    return BacktestConfig(
        initial_balance=50_000.0,
        account_ccy="GBP",
        profile=SEVERE_STRESS,
        max_concurrent=3,
        max_per_instrument=1,
        charge_financing=True,
        strategy_id="breakout",
    )


def run_scenario(data: dict[str, pd.DataFrame] | None = None):
    from fiboki.backtest.engine import run_backtest

    return run_backtest(
        data=data if data is not None else synthetic_data(),
        config=build_config(),
        strategy=BreakoutStrategy(),
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=ConstantUsdGbp(),
    )


def run_tight_scenario(config: BacktestConfig | None = None, data: dict | None = None):
    """A variant whose stop and target BOTH sit inside a typical single bar.

    The main scenario's levels are half a 20-bar range apart, so no bar ever
    touches both and the intrabar policy is never exercised. This variant uses
    8% of the range, which makes roughly a fifth of its exits ambiguous — the
    case where STOP_FIRST vs TARGET_FIRST actually decides the answer.
    """
    from fiboki.backtest.engine import run_backtest

    return run_backtest(
        data=data if data is not None else synthetic_data(),
        config=config or build_config(),
        strategy=BreakoutStrategy(lookback=20, stop_fraction=0.08, rr=1.0),
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=ConstantUsdGbp(),
    )


# ==========================================================================
# The exit-vocabulary scenario
# ==========================================================================
#
# The scenario above predates the engine's ability to honour anything but a
# stop and a first target, so it exercises none of the exit vocabulary. This
# one does, deliberately all at once, because the determinism risks live in the
# interactions: a trail reading an auxiliary series, a position closing in
# pieces, a time stop firing on a bar that also touched a level, a cooldown
# keyed on a bar index, and a reversal that closes one position and opens
# another inside the same bar.


class ScaleOutBreakout(BreakoutStrategy):
    """The breakout, but asking for the whole exit vocabulary.

    Two take-profit legs at 1R and 2.5R closing 40% and 30%, with 30% left to
    ride into the trail, the time stop or the end of the data — the shape
    ``macd_ema_trend_hybrid`` declares and that nothing has ever executed.
    """

    def on_bar(self, ctx):
        out = []
        for signal in super().on_bar(ctx):
            risk = signal.stop_distance
            sign = 1 if signal.direction is Direction.LONG else -1
            out.append(
                Signal(
                    strategy_id=signal.strategy_id,
                    instrument=signal.instrument,
                    timeframe=signal.timeframe,
                    direction=signal.direction,
                    bar_time=signal.bar_time,
                    reference_price=signal.reference_price,
                    stop_price=signal.stop_price,
                    take_profit_prices=(
                        signal.reference_price + sign * 1.0 * risk,
                        signal.reference_price + sign * 2.5 * risk,
                    ),
                    take_profit_allocations=(0.4, 0.3),
                )
            )
        return out


def exit_vocabulary_policy():
    """Trail, breakeven, time stop, cooldown and reversal, all live at once."""
    from fiboki.backtest.exits import ExitPolicy, ReversalMode, TrailKind, TrailSpec

    return ExitPolicy(
        trailing=TrailSpec(
            kind=TrailKind.ATR_CHANDELIER,
            value=3.0,
            activate_after_r=1.0,
            atr_column="atr_14",
        ),
        breakeven_at_r=1.0,
        max_bars_in_trade=40,
        cooldown_bars_after_exit=2,
        reversal=ReversalMode.REVERSE,
    )


def exit_series_for(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """A real Wilder ATR per instrument, indexed identically to its bars."""
    from fiboki.indicators.volatility import ATR

    atr = ATR(14)
    return {sym: atr.compute(frame)[[atr.name]] for sym, frame in data.items()}


def run_exit_vocabulary_scenario(
    config: BacktestConfig | None = None, data: dict | None = None
):
    from fiboki.backtest.engine import run_backtest

    bars = data if data is not None else synthetic_data()
    return run_backtest(
        data=bars,
        config=config or build_config(),
        # A SHORT lookback on purpose: the 20-bar breakout almost never reverses
        # while a position is open, so the reversal path would go untested and
        # the scenario would quietly cover four of the five exit reasons. At 6
        # bars the ledger contains stop, trailing, time, opposite-signal and
        # end-of-data exits, plus cooldown rejections.
        strategy=ScaleOutBreakout(lookback=6, stop_fraction=0.4, rr=1.0),
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=ConstantUsdGbp(),
        exit_policy=exit_vocabulary_policy(),
        exit_series=exit_series_for(bars),
    )
