"""XAUUSD in a GBP account, converted by the research FX source built from stored bars.

Audit finding P1-5: research ran in USD while paper ran in GBP, and no real FX
conversion was wired anywhere. Research now runs in GBP, and
:func:`fiboki.validation.run.build_research_fx_source` builds a
:class:`~fiboki.core.money.SeriesFxSource` from the store's DAILY closes of the
GBP crosses. This golden pins the P&L arithmetic end to end, including the one
subtlety that decides whether the conversion is look-ahead free: a daily close
is indexed at the bar's CLOSE (bar open + 1 day), not at its open.

Stored GBPUSD D1 bars (the fake store below):

    bar stamped 2024-01-01 00:00, close 1.2500  -> known from 2024-01-02 00:00
    bar stamped 2024-01-02 00:00, close 1.2800  -> known from 2024-01-03 00:00

A long XAUUSD, 2.00 oz, entered at the 2024-01-02 04:00 open (mid 2000.00)
and taken out at its target 2050.00 on the 2024-01-02 08:00 bar. Frictionless
profile, so only the conversion is being tested.

    gross USD = (2050.00 - 2000.00) * 2.00 * 1.0     = 100.00 USD
    rate at 2024-01-02 08:00 (as-of, newest KNOWN)   = 1 / 1.2500 = 0.80 GBP per USD
    gross GBP = 100.00 * 0.80                        =  80.00 GBP

Had the daily bar been indexed at its open, the 2024-01-02 bar's close of
1.2800 -- not printed until midnight -- would have been used at 08:00:
100.00 / 1.28 = 78.125 GBP. That number would be look-ahead.
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
from fiboki.core.enums import Direction, ExitReason, Timeframe
from fiboki.validation.run import build_research_fx_source
from tests.helpers_exec import flat_profile, make_frame

pytestmark = pytest.mark.golden


class _Version:
    def __init__(self, version_id: str) -> None:
        self.version_id = version_id


class _FakeStore:
    """Only what build_research_fx_source reads: ``read_latest(sym, tf, kind=)``."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[tuple[str, Timeframe]] = []

    def read_latest(self, symbol, timeframe, *, kind=None):
        self.calls.append((symbol, timeframe))
        if symbol not in self.frames:
            raise LookupError(f"no {symbol}")
        return self.frames[symbol], _Version(f"ds_{symbol.lower()}_d1")


def _gbpusd_daily() -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC")],
        name="timestamp",
    )
    return pd.DataFrame(
        {"open": [1.25, 1.25], "high": [1.26, 1.29], "low": [1.24, 1.24],
         "close": [1.2500, 1.2800]},
        index=idx,
    )


def test_xauusd_pnl_in_a_gbp_account() -> None:
    store = _FakeStore({"GBPUSD": _gbpusd_daily()})
    fx, label = build_research_fx_source(store, quote_currencies=("USD",))
    assert store.calls == [("GBPUSD", Timeframe.D1)]
    assert "GBPUSD@ds_gbpusd_d1" in label

    frame = make_frame(
        [
            ("2024-01-02 00:00", 2000.0, 2000.0, 2000.0, 2000.0),
            ("2024-01-02 04:00", 2000.0, 2010.0, 1995.0, 2005.0),
            ("2024-01-02 08:00", 2005.0, 2055.0, 2004.0, 2050.0),
            ("2024-01-02 12:00", 2050.0, 2051.0, 2049.0, 2050.0),
        ]
    )
    signal = Signal(
        strategy_id="golden_fx", instrument="XAUUSD", timeframe="H4",
        direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-01-02 00:00", tz="UTC"),
        reference_price=2000.0, stop_price=1950.0, take_profit_prices=(2050.0,),
    )
    result = run_backtest(
        data={"XAUUSD": frame},
        config=BacktestConfig(
            initial_balance=10_000.0, account_ccy="GBP", profile=flat_profile(),
            strategy_id="golden_fx", charge_financing=False,
        ),
        strategy=PrecomputedSignals([signal]),
        sizer=FixedSizeSizer(2.0),
        fx=fx,
    )
    (trade,) = result.trades
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert trade.entry_price == 2000.0
    assert trade.exit_price == 2050.0
    assert trade.fx_rate_used == pytest.approx(0.80, abs=1e-15)
    assert trade.gross_pnl == pytest.approx(80.00, abs=1e-9)
    assert trade.net_pnl == pytest.approx(80.00, abs=1e-9)
    assert trade.account_ccy == "GBP"


def test_the_close_is_not_known_before_the_bar_ends() -> None:
    """At 2024-01-02 23:59 only the 01-01 close exists; at 01-03 00:00 the 01-02 one."""
    fx, _ = build_research_fx_source(
        _FakeStore({"GBPUSD": _gbpusd_daily()}), quote_currencies=("USD",)
    )
    before = fx.rate("USD", "GBP", pd.Timestamp("2024-01-02 23:59", tz="UTC"))
    after = fx.rate("USD", "GBP", pd.Timestamp("2024-01-03 00:00", tz="UTC"))
    assert before == pytest.approx(1 / 1.25, abs=1e-15)
    assert after == pytest.approx(1 / 1.28, abs=1e-15)
