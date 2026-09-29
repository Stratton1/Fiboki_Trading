"""The economic calendar reaches the paper gateway, and a blackout blocks an entry.

Before this, ``build_replay_session`` built its ``RiskContextBuilder`` with no
``event_source``, so the gateway's ``event_blackout`` check ran on every order
against an empty tuple and could never fire; ``summary.json`` said
``wired_into_gateway: false``. These tests replay a Donchian-style breakout
that fires on the bar stamped at a real US Non-Farm Payrolls release in the
committed official calendar (2024-03-08 13:30 UTC) and prove:

* with the default calendar the entry reaches the gateway and is BLOCKED with
  the gateway's own ``event_blackout`` reason, and nothing reaches the venue;
* the identical bars with an explicitly empty calendar let the entry through,
  so the block above is the calendar and not some other check;
* the same breakout two days earlier, with no release near it, is allowed with
  the calendar wired, so the calendar does not block indiscriminately;
* a replay the calendar does not cover is refused unless explicitly allowed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.broker.paper import PaperConfig
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction
from fiboki.core.money import IdentityFxSource
from fiboki.marketstate.calendar import (
    CalendarError,
    EconomicEvent,
    ImpactLevel,
    InMemoryEconomicCalendar,
    load_official_calendar,
)
from fiboki.portfolio.sizing import SizingPolicy
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.profiles import IG_REALISTIC
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import LiveWorkerConfig
from fiboki.workers.runtime import build_replay_session, calendar_event_source

NFP = pd.Timestamp("2024-03-08 13:30", tz="UTC")
QUIET = pd.Timestamp("2024-03-06 13:30", tz="UTC")
LOOKBACK = 20


class DonchianBreakout:
    """Close above the prior ``LOOKBACK`` bars' high: long. CLOSED bars only."""

    def __init__(self) -> None:
        self.emitted: list[pd.Timestamp] = []

    def __call__(self, symbol: str, history: pd.DataFrame, now: pd.Timestamp) -> list[Signal]:
        if len(history) <= LOOKBACK + 1:
            return []
        window = history.iloc[-(LOOKBACK + 1) : -1]
        close = float(history["close"].iloc[-1])
        if close <= float(window["high"].max()):
            return []
        self.emitted.append(now)
        risk = close * 0.002
        return [
            Signal(
                strategy_id="calendar_breakout",
                instrument=symbol,
                timeframe="M15",
                direction=Direction.LONG,
                bar_time=now,
                reference_price=close,
                stop_price=close - risk,
                take_profit_prices=(close + 2 * risk,),
            )
        ]


def _frame(breakout_at: pd.Timestamp) -> pd.DataFrame:
    """Flat M15 bars with exactly one upside breakout, on ``breakout_at``."""
    index = pd.date_range(breakout_at - pd.Timedelta(hours=10), periods=60, freq="15min")
    close = np.full(len(index), 1.1000)
    high = close + 0.0002
    low = close - 0.0002
    at = index.get_loc(breakout_at)
    close[at:] = 1.1050
    high[at] = 1.1055
    high[at + 1 :] = 1.1053
    low[at:] = 1.1047
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(high, np.maximum(open_, close)),
            "low": np.minimum(low, np.minimum(open_, close)),
            "close": close,
        },
        index=index,
    )


def _run(frame: pd.DataFrame, store: WorkerStore, **kwargs):
    source = DonchianBreakout()
    session = build_replay_session(
        frames={"EURUSD": frame},
        signal_source=source,
        store=store,
        paper_config=PaperConfig(
            initial_balance=100_000.0,
            account_ccy="USD",
            profile=IG_REALISTIC,
            strategy_id="calendar_breakout",
        ),
        sizing_policy=SizingPolicy(risk_fraction=0.005),
        fx=IdentityFxSource(),
        timeframe="M15",
        limits=PAPER_LIMITS,
        worker_config=LiveWorkerConfig(
            max_cycles=len(frame),
            idle_sleep_seconds=0.0,
            busy_sleep_seconds=0.0,
            reconcile_every_cycles=1000,
            lifecycle_every_cycles=1000,
            lease_name=f"calendar-{id(frame)}",
        ),
        **kwargs,
    )
    session.worker.run(install_signals=False)
    return session, source


@pytest.fixture()
def store(tmp_path):
    with WorkerStore.sqlite_at(tmp_path / "workers.sqlite") as handle:
        yield handle


def test_the_official_calendar_holds_the_release_this_test_relies_on() -> None:
    names = [
        e.name
        for e in load_official_calendar().all_events()
        if e.event_time == NFP and e.currency == "USD"
    ]
    assert names == ["US Non-Farm Payrolls"]


def test_an_entry_inside_the_nfp_blackout_is_blocked_by_the_gateway(store) -> None:
    session, source = _run(_frame(NFP), store)

    assert source.emitted == [NFP], "the breakout must fire on the release bar and only there"
    summary = session.summary()
    assert summary["economic_calendar"]["wired_into_gateway"] is True
    assert summary["economic_calendar"]["covered"] is True
    assert summary["economic_calendar"]["n_events"] > 0

    attempts = session.telemetry()
    assert len(attempts) == 1
    reasons = list(session.gateway.recorder.attempts[0].decision.reasons)
    assert f"event_blackout:{NFP.isoformat()}" in reasons, reasons
    assert summary["block_reasons"].get("event_blackout") == 1
    assert summary["accepted"] == 0 and summary["trades"] == 0
    assert not session.broker.book.open


def test_the_same_bars_without_a_calendar_let_the_entry_through(store) -> None:
    """The control: remove only the calendar and the identical entry passes."""
    session, source = _run(
        _frame(NFP),
        store,
        calendar=InMemoryEconomicCalendar.empty(),
        allow_empty_calendar=True,
    )
    assert source.emitted == [NFP]
    summary = session.summary()
    assert summary["economic_calendar"]["wired_into_gateway"] is False
    assert summary["gateway_blocked"] == 0, summary["block_reasons"]
    assert summary["accepted"] == 1


def test_a_breakout_away_from_any_release_is_allowed_with_the_calendar_wired(store) -> None:
    session, source = _run(_frame(QUIET), store)
    assert source.emitted == [QUIET]
    summary = session.summary()
    assert summary["economic_calendar"]["wired_into_gateway"] is True
    assert summary["gateway_blocked"] == 0, summary["block_reasons"]
    assert summary["accepted"] == 1


def test_an_uncovered_replay_is_refused_unless_explicitly_allowed(store) -> None:
    old = _frame(pd.Timestamp("2015-03-06 13:30", tz="UTC"))
    with pytest.raises(CalendarError, match="does not span"):
        _run(old, store)

    session, _ = _run(old, store, allow_empty_calendar=True)
    record = session.summary()["economic_calendar"]
    # Still applied wherever it has events; the refusal is what was lifted.
    assert record["wired_into_gateway"] is True
    assert record["covered"] is False
    assert record["allow_empty_calendar"] is True
    assert "does not span" in record["coverage_error"]


def test_an_event_without_a_fixed_time_blacks_out_its_whole_window() -> None:
    """A BoJ-style event spans [event_time, window_end]; inside it, 'now' blocks."""
    start = pd.Timestamp("2024-03-19 02:00", tz="UTC")
    event = EconomicEvent(
        event_time=start,
        currency="JPY",
        name="BoJ Policy Rate",
        impact=ImpactLevel.HIGH,
        time_known=False,
        window_end=start + pd.Timedelta(hours=3),
    )
    source = calendar_event_source(
        InMemoryEconomicCalendar([event]), horizon_minutes=PAPER_LIMITS.event_blackout_minutes
    )
    inside = start + pd.Timedelta(hours=2)
    assert source("USDJPY", inside) == (inside,)
    after = event.window_end + pd.Timedelta(minutes=10)
    assert source("USDJPY", after) == (event.window_end,)
    assert source("USDJPY", event.window_end + pd.Timedelta(minutes=16)) == ()
    assert source("EURUSD", inside) == (), "JPY news is not EURUSD news"
