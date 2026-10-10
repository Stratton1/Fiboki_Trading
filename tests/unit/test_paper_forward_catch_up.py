"""``ForwardFeed._catch_up``: every missed bar once, no delivered bar twice.

The catch-up steps the book through the closed bars it has not seen before the
newest bar is applied. Its bounds are per INSTRUMENT: after the last bar the
book saw for that instrument, strictly before the bar the batch delivers for
it. Bounding by the batch's newest stamp instead would, for an instrument whose
latest candle lags the others (a late candle, a different session: XAUUSD beside
EURUSD and GBPUSD in the production wiring), apply that instrument's newest bar
in the catch-up AND in the main step, and the book would see it twice (financing,
bars held, trail and breakeven decided twice on one bar).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd

from fiboki.entrypoints.paper_forward import ForwardFeed
from fiboki.sim.fills import Bar
from fiboki.workers.live_worker import BarBatch


def T(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def _bar(ts: str, px: float = 1.0) -> Bar:
    return Bar(T(ts), px, px + 0.01, px - 0.01, px)


def _frame(*stamps: str) -> pd.DataFrame:
    n = len(stamps)
    return pd.DataFrame(
        {"open": [1.0] * n, "high": [1.01] * n, "low": [0.99] * n, "close": [1.0] * n},
        index=pd.DatetimeIndex([T(s) for s in stamps]),
    )


class FeedStub:
    timeframe = "H1"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def bars_between(self, sym: str, after: pd.Timestamp, before: pd.Timestamp) -> pd.DataFrame:
        f = self.frames[sym]
        return f.loc[(f.index > after) & (f.index < before)]

    def history_through(self, sym: str, at: pd.Timestamp) -> pd.DataFrame:
        f = self.frames[sym]
        return f.loc[f.index <= at]


class ManagerStub:
    def __init__(self, now: str, last: dict[str, Bar]) -> None:
        self.now = T(now)
        self.driver = SimpleNamespace(last_bars=dict(last))
        self.open_positions: list[Any] = []
        self.book = SimpleNamespace(trades=[])
        self.steps: list[tuple[pd.Timestamp, list[str], int]] = []

    def on_bar(self, bars: dict[str, Bar], *, bar_index: int, timestamp: pd.Timestamp, series: Any):
        self.steps.append((timestamp, sorted(bars), bar_index))
        for sym, bar in bars.items():
            self.driver.last_bars[sym] = bar
        self.now = timestamp
        return SimpleNamespace(advance=SimpleNamespace(entries=[], legs=[]), telemetry=[])


class JournalStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def event(self, kind: str, **fields: Any) -> None:
        self.events.append((kind, fields))

    def record_trades(self, trades: Any) -> list[Any]:
        return []


def _forward(feed: FeedStub, manager: ManagerStub, journal: JournalStub) -> ForwardFeed:
    account = SimpleNamespace(balance=0.0, equity=0.0, peak_equity=0.0)
    forward = ForwardFeed(
        feed=feed,  # type: ignore[arg-type]
        spread=SimpleNamespace(),  # type: ignore[arg-type]
        manager=manager,  # type: ignore[arg-type]
        builder=SimpleNamespace(),  # type: ignore[arg-type]
        signals=SimpleNamespace(exit_series=lambda histories: {}),  # type: ignore[arg-type]
        journal=journal,  # type: ignore[arg-type]
        kill_switch=SimpleNamespace(),  # type: ignore[arg-type]
        account=lambda: account,  # type: ignore[arg-type]
        initial_balance=0.0,
        wiring=SimpleNamespace(instruments=sorted(feed.frames), timeframe=SimpleNamespace(minutes=60)),  # type: ignore[arg-type]
    )
    forward.bar_index = 5
    return forward


def _batch(frames: dict[str, Bar]) -> BarBatch:
    return BarBatch(
        frames=frames,
        closed_instruments=frozenset(frames),
        ages={},
        timeframe="H1",
        market_closed=frozenset(),
    )


def test_a_lagging_instruments_newest_bar_is_left_to_the_main_step() -> None:
    """Down since 10:00. A has 11:00 and 12:00; B's 12:00 candle is late, so the
    batch delivers A@12:00 and B@11:00. Catch-up applies ONLY A@11:00; B@11:00
    is the batch's bar for B and the main step applies it, once."""
    feed = FeedStub({
        "A": _frame("2026-10-19 10:00", "2026-10-19 11:00", "2026-10-19 12:00"),
        "B": _frame("2026-10-19 10:00", "2026-10-19 11:00"),
    })
    manager = ManagerStub("2026-10-19 10:00", {"A": _bar("2026-10-19 10:00"), "B": _bar("2026-10-19 10:00")})
    journal = JournalStub()
    forward = _forward(feed, manager, journal)
    batch = _batch({"A": _bar("2026-10-19 12:00"), "B": _bar("2026-10-19 11:00")})

    forward._catch_up(batch, max(b.timestamp for b in batch.frames.values()))

    assert manager.steps == [(T("2026-10-19 11:00"), ["A"], 6)]
    assert forward.bar_index == 6
    (caught,) = [f for k, f in journal.events if k == "bars_caught_up"]
    assert caught["count"] == 1
    assert not [k for k, _ in journal.events if k == "bars_not_modelled"], (
        "B's 11:00 bar is delivered by this batch; it is neither missed nor late"
    )


def test_every_missed_bar_is_applied_once_in_order_with_its_own_index() -> None:
    feed = FeedStub({
        "A": _frame("2026-10-19 10:00", "2026-10-19 11:00", "2026-10-19 12:00", "2026-10-19 13:00"),
        "B": _frame("2026-10-19 10:00", "2026-10-19 11:00", "2026-10-19 12:00", "2026-10-19 13:00"),
    })
    manager = ManagerStub("2026-10-19 10:00", {"A": _bar("2026-10-19 10:00"), "B": _bar("2026-10-19 10:00")})
    forward = _forward(feed, manager, JournalStub())
    batch = _batch({"A": _bar("2026-10-19 13:00"), "B": _bar("2026-10-19 13:00")})

    forward._catch_up(batch, T("2026-10-19 13:00"))

    assert manager.steps == [
        (T("2026-10-19 11:00"), ["A", "B"], 6),
        (T("2026-10-19 12:00"), ["A", "B"], 7),
    ]
    assert forward.bar_index == 7, "the main step takes 8 for the 13:00 bar"


def test_a_bar_older_than_the_books_step_is_journalled_not_applied() -> None:
    """B's 11:00 candle was late at the 11:00 poll (the book stepped to 11:00 on A
    alone) and arrives with B's 12:00. It cannot be stepped backwards into the
    timeline: journalled as bars_not_modelled, and not applied."""
    feed = FeedStub({
        "A": _frame("2026-10-19 10:00", "2026-10-19 11:00", "2026-10-19 12:00"),
        "B": _frame("2026-10-19 10:00", "2026-10-19 11:00", "2026-10-19 12:00"),
    })
    manager = ManagerStub("2026-10-19 11:00", {"A": _bar("2026-10-19 11:00"), "B": _bar("2026-10-19 10:00")})
    journal = JournalStub()
    forward = _forward(feed, manager, journal)
    batch = _batch({"A": _bar("2026-10-19 12:00"), "B": _bar("2026-10-19 12:00")})

    forward._catch_up(batch, T("2026-10-19 12:00"))

    assert manager.steps == []
    (late,) = [f for k, f in journal.events if k == "bars_not_modelled"]
    assert late["bars"] == [
        {"instrument": "B", "bar_time": T("2026-10-19 11:00"), "reason": "older_than_book_step"}
    ]



def test_a_restored_feeds_window_shortfall_is_journalled_and_alerted_once() -> None:
    """The feed records that it could not reach its window's first bar; the
    runtime says so in the journal and alerts, then clears it so it is said once."""
    feed = FeedStub({"A": _frame("2026-10-19 10:00")})
    feed.floor_shortfall = {"A": (T("2026-10-01 00:00"), T("2026-10-05 00:00"))}  # type: ignore[attr-defined]
    journal = JournalStub()
    fired: list[Any] = []
    forward = _forward(feed, ManagerStub("2026-10-19 10:00", {}), journal)
    forward.dispatcher = SimpleNamespace(fire=lambda *a, **k: fired.append((a, k)))

    forward._report_window_shortfall()
    forward._report_window_shortfall()

    (short,) = [f for k, f in journal.events if k == "feed_window_shortfall"]
    assert short["instruments"] == {
        "A": {"window_from": T("2026-10-01 00:00"), "obtained_from": T("2026-10-05 00:00")}
    }
    assert len(fired) == 1 and fired[0][1]["dedupe_key"] == "paper_forward_feed_window_shortfall"
    assert feed.floor_shortfall == {}  # type: ignore[attr-defined]
