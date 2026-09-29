"""Entry locks: session-bar arithmetic, stop-streak semantics, scopes, restart.

Every expectation below is worked out by hand in the test's docstring or
comment, against the interbank week (closed Friday 17:00 to Sunday 17:00
America/New_York, the sim session calendar: 22:00 UTC in winter, which is
every January case below, and 21:00 UTC under daylight saving). ``fiboki.backtest.locks`` is the ONE implementation the engine and the
risk gateway share, so an arithmetic slip here is a slip everywhere.
"""
from __future__ import annotations

import itertools

import pandas as pd
import pytest

from fiboki.backtest import locks as locks_mod
from fiboki.backtest.locks import (
    ClosedTrade,
    LedgerLockView,
    LockBook,
    LockPolicy,
    LockPolicyUnknown,
    LockRegistration,
    LockScope,
    LockStateUnavailable,
    SessionBarClock,
    StopStreakRule,
    UnknownSessionError,
    closes_from_intents,
    timeframe_for_interval,
)
from fiboki.core.enums import Direction, ExecutionMode, Timeframe


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


H1 = Timeframe.H1


def close(
    at: str, reason: str | None = "stop_loss", *, instrument: str = "EURUSD", sid: str = "s1"
) -> ClosedTrade:
    return ClosedTrade(strategy_id=sid, instrument=instrument, exit_time=T(at), exit_reason=reason)


def reg(cooldown: int = 0, streak: StopStreakRule | None = None, tf=H1) -> LockRegistration:
    return LockRegistration(LockPolicy(cooldown, streak), tf)


# 2024-01-05 is a Friday; 2024-01-07 a Sunday; 2024-01-08 a Monday.


# --------------------------------------------------------------------------
# The session clock
# --------------------------------------------------------------------------


class TestSessionClock:
    def test_h1_weekend_does_not_count(self) -> None:
        """Friday 21:00 is the last H1 slot of the week and Sunday 22:00 the first.

        So Fri 21:00 -> k, Sun 22:00 -> k+1, Sun 23:00 -> k+2, Mon 00:00 -> k+3,
        Mon 01:00 -> k+4. Fifty wall-clock hours, four session bars.
        """
        c = SessionBarClock.for_instrument("EURUSD", "H1")
        k = c.index(T("2024-01-05 21:00"))
        assert c.index(T("2024-01-07 22:00")) == k + 1
        assert c.index(T("2024-01-07 23:00")) == k + 2
        assert c.index(T("2024-01-08 00:00")) == k + 3
        assert c.index(T("2024-01-08 01:00")) == k + 4
        assert c.index(T("2024-01-05 20:00")) == k - 1

    def test_a_timestamp_inside_the_closure_shares_fridays_index(self) -> None:
        c = SessionBarClock.for_instrument("EURUSD", "H1")
        k = c.index(T("2024-01-05 21:00"))
        for inside in ("2024-01-05 22:00", "2024-01-06 12:00", "2024-01-07 21:59"):
            assert c.index(T(inside)) == k

    def test_a_full_week_has_120_h1_session_bars(self) -> None:
        c = SessionBarClock.for_instrument("EURUSD", "H1")
        assert c.bars_between(T("2024-01-08 00:00"), T("2024-01-15 00:00")) == 120

    def test_h4_counts_the_half_open_friday_and_sunday_slots(self) -> None:
        """Slots align to Monday 00:00. Fri 20:00-24:00 is half open, so it
        counts; so does Sun 20:00-24:00. Monday to Friday is 5 x 6 = 30 slots,
        plus the Sunday 20:00 slot: 31 per week. The Sunday slot is the one a
        feed stamping its first bar of the week at 20:00 or 21:00 prints."""
        c = SessionBarClock.for_instrument("EURUSD", "H4")
        k = c.index(T("2024-01-05 21:00"))
        assert c.index(T("2024-01-07 21:00")) == k + 1
        assert c.index(T("2024-01-08 01:00")) == k + 2
        assert c.bars_between(T("2024-01-08 00:00"), T("2024-01-15 00:00")) == 31

    def test_d1_week_is_monday_to_friday(self) -> None:
        c = SessionBarClock.for_instrument("EURUSD", "D1")
        fri = c.index(T("2024-01-05"))
        assert c.index(T("2024-01-06")) == fri  # Saturday: closed
        assert c.index(T("2024-01-07 22:00")) == fri  # 2 of 24 hours open: closed
        assert c.index(T("2024-01-08")) == fri + 1

    def test_index_and_energy_use_the_same_weekend(self) -> None:
        for sym in ("US500", "WTIUSD"):
            try:
                c = SessionBarClock.for_instrument(sym, "H1")
            except KeyError:  # pragma: no cover - symbol not registered here
                pytest.skip(f"{sym} not registered")
            k = c.index(T("2024-01-05 21:00"))
            assert c.index(T("2024-01-07 22:00")) == k + 1

    def test_the_week_is_anchored_to_17_00_new_york_not_a_utc_constant(self) -> None:
        """The SIM session calendar's week: Friday 17:00 to Sunday 17:00 New York.

        Summer (EDT, 2024-07-05 is a Friday): closes 21:00 UTC, reopens Sunday
        21:00 UTC. Fri 20:00 -> k, Fri 21:00 is closed (shares k), Sun 21:00 ->
        k+1, Mon 00:00 -> k+4. The old fixed 22:00 UTC calendar counted Fri
        21:00 as a bar and refused Sun 21:00, which trades.
        """
        c = SessionBarClock.for_instrument("EURUSD", "H1")
        k = c.index(T("2024-07-05 20:00"))
        assert c.index(T("2024-07-05 21:00")) == k, "closed: after 17:00 New York"
        assert c.index(T("2024-07-07 20:59")) == k
        assert c.index(T("2024-07-07 21:00")) == k + 1, "open: 17:00 New York"
        assert c.index(T("2024-07-08 00:00")) == k + 4
        assert c.bars_between(T("2024-07-08 00:00"), T("2024-07-15 00:00")) == 120

    def test_it_agrees_with_the_sim_session_calendar_slot_by_slot(self) -> None:
        """Parity with ``sim.fills.FxSessionCalendar``: every H1 slot the fill
        simulator calls open advances the clock by one, every closed one does
        not, across both 2024 daylight-saving weekends."""
        from fiboki.core.instruments import get as get_instrument
        from fiboki.sim.fills import FxSessionCalendar

        cal, eur = FxSessionCalendar(), get_instrument("EURUSD")
        c = SessionBarClock.for_instrument("EURUSD", "H1")
        for start, end in (("2024-03-07", "2024-03-12"), ("2024-10-31", "2024-11-05"),
                           ("2024-07-04", "2024-07-09"), ("2024-01-04", "2024-01-09")):
            hours = pd.date_range(T(start), T(end), freq="1h", inclusive="left")
            for prev, cur in itertools.pairwise(hours):
                step = c.index(cur) - c.index(prev)
                assert step == (1 if cal.is_open(eur, cur) else 0), cur

    def test_h4_summer_week_drops_the_friday_slot_and_keeps_sunday(self) -> None:
        """EDT: Fri 20:00-24:00 UTC is one hour open (does not count), Sun
        20:00-24:00 three hours open (counts). 30 slots, winter's 31 less one."""
        c = SessionBarClock.for_instrument("EURUSD", "H4")
        assert c.bars_between(T("2024-07-08 00:00"), T("2024-07-15 00:00")) == 30
        k = c.index(T("2024-07-05 16:00"))
        assert c.index(T("2024-07-05 20:00")) == k
        assert c.index(T("2024-07-07 20:00")) == k + 1

    def test_naive_timestamps_are_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            SessionBarClock.for_instrument("EURUSD", "H1").index(pd.Timestamp("2024-01-05"))

    def test_an_unknown_calendar_is_refused_not_defaulted(self, monkeypatch) -> None:
        monkeypatch.setattr(locks_mod, "SESSION_CLOSURES", {"index": ()})
        with pytest.raises(UnknownSessionError, match="EURUSD"):
            SessionBarClock.for_instrument("EURUSD", "H1")

    def test_timeframe_for_interval(self) -> None:
        assert timeframe_for_interval(pd.Timedelta(hours=4)) is Timeframe.H4
        with pytest.raises(ValueError):
            timeframe_for_interval(pd.Timedelta(minutes=7))


# --------------------------------------------------------------------------
# Cooldown
# --------------------------------------------------------------------------


class TestCooldown:
    def test_friday_close_expires_on_monday_counted_in_session_bars(self) -> None:
        """THE golden case. A 4-bar H1 cooldown armed by a close on Friday 21:00.

        Locked decision bars: Fri 21:00 (k), Sun 22:00 (k+1), Sun 23:00 (k+2),
        Mon 00:00 (k+3). First free decision bar: Mon 01:00 (k+4).

        freqtrade's wall-clock lock (4 x 60 minutes) would have expired at
        Saturday 01:00 and let a Sunday 22:00 signal straight through.
        """
        book = LockBook(default=reg(cooldown=4))
        armed = book.on_close(close("2024-01-05 21:00", "take_profit"))
        assert [a.rule for a in armed] == ["cooldown"]
        for bar in ("2024-01-05 21:00", "2024-01-07 22:00", "2024-01-07 23:00",
                    "2024-01-08 00:00"):
            assert book.is_locked("EURUSD", "s1", T(bar)) is not None, bar
        assert book.is_locked("EURUSD", "s1", T("2024-01-08 01:00")) is None
        wall_clock_expiry = T("2024-01-05 21:00") + pd.Timedelta(hours=4)
        assert wall_clock_expiry < T("2024-01-07 22:00")

    def test_cooldown_counts_n_whole_bars_after_the_closing_bar(self) -> None:
        book = LockBook(default=reg(cooldown=2))
        book.on_close(close("2024-01-09 10:00", "time_stop"))
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 10:00"))
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 11:00"))
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 12:00")) is None

    def test_zero_cooldown_arms_nothing(self) -> None:
        book = LockBook(default=reg(cooldown=0))
        assert book.on_close(close("2024-01-09 10:00")) == ()
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 10:00")) is None

    def test_cooldown_is_per_strategy_and_instrument(self) -> None:
        book = LockBook(default=reg(cooldown=5))
        book.on_close(close("2024-01-09 10:00", "take_profit"))
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 11:00"))
        assert book.is_locked("GBPUSD", "s1", T("2024-01-09 11:00")) is None
        assert book.is_locked("EURUSD", "s2", T("2024-01-09 11:00")) is None

    def test_every_exit_reason_arms_the_cooldown(self) -> None:
        for reason in ("stop_loss", "take_profit", "time_stop", "trailing_stop",
                       "opposite_signal", "risk_halt", None):
            book = LockBook(default=reg(cooldown=1))
            book.on_close(close("2024-01-09 10:00", reason))
            assert book.is_locked("EURUSD", "s1", T("2024-01-09 10:00")), reason

    def test_a_lock_never_reaches_back_before_its_close(self) -> None:
        book = LockBook(default=reg(cooldown=10))
        book.on_close(close("2024-01-09 10:00"))
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 09:00")) is None

    def test_an_unregistered_strategy_arms_nothing(self) -> None:
        book = LockBook(registrations={"other": reg(cooldown=5)})
        assert book.on_close(close("2024-01-09 10:00")) == ()


# --------------------------------------------------------------------------
# Stop streak
# --------------------------------------------------------------------------


def streak(n=2, lookback=10, lock=6, scope=LockScope.INSTRUMENT) -> StopStreakRule:
    return StopStreakRule(n_stops=n, lookback_bars=lookback, lock_bars=lock, scope=scope)


class TestStopStreak:
    def test_counts_only_stop_outs(self) -> None:
        """TP, time stop, trailing stop, breakeven and reversal exits are not
        stop-outs, however many of them there are."""
        book = LockBook(default=reg(streak=streak(n=2)))
        for i, reason in enumerate(
            ("take_profit", "time_stop", "trailing_stop", "breakeven", "opposite_signal")
        ):
            assert book.on_close(close(f"2024-01-09 {10 + i}:00", reason)) == ()
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 15:00")) is None
        book.on_close(close("2024-01-09 15:00", "stop_loss"))
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 16:00")) is None
        armed = book.on_close(close("2024-01-09 16:00", "stop_loss"))
        assert [a.rule for a in armed] == ["stop_streak"]
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 16:00")).rule == "stop_streak"

    def test_an_unknown_exit_reason_counts_as_a_stop_out(self) -> None:
        book = LockBook(default=reg(streak=streak(n=2)))
        book.on_close(close("2024-01-09 10:00", None))
        assert book.on_close(close("2024-01-09 11:00", None))

    def test_lookback_window_and_lock_length(self) -> None:
        """Stops at bars b and b+10 with lookback 10: the window for the second
        is (b, b+10], which excludes b. At b+9 it would include it."""
        book = LockBook(default=reg(streak=streak(n=2, lookback=10, lock=6)))
        book.on_close(close("2024-01-09 00:00"))
        assert book.on_close(close("2024-01-09 10:00")) == ()

        book = LockBook(default=reg(streak=streak(n=2, lookback=10, lock=6)))
        book.on_close(close("2024-01-09 00:00"))
        (lock,) = book.on_close(close("2024-01-09 09:00"))
        assert lock.bars == 6
        # locked for decision bars 09:00 .. 14:00, free at 15:00
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 14:00"))
        assert book.is_locked("EURUSD", "s1", T("2024-01-09 15:00")) is None

    def test_lookback_is_counted_in_session_bars_across_a_weekend(self) -> None:
        """Fri 20:00 is k-1 and Mon 00:00 is k+3: four session bars apart, 52
        wall-clock hours apart. A 5-bar lookback sees both."""
        book = LockBook(default=reg(streak=streak(n=2, lookback=5, lock=3)))
        book.on_close(close("2024-01-05 20:00"))
        assert book.on_close(close("2024-01-08 00:00"))

    def test_a_streak_is_consumed_by_the_lock_it_arms(self) -> None:
        book = LockBook(default=reg(streak=streak(n=2, lookback=100, lock=2)))
        book.on_close(close("2024-01-09 00:00"))
        assert book.on_close(close("2024-01-09 01:00"))
        # One more stop inside the lookback does NOT re-arm on its own ...
        assert book.on_close(close("2024-01-09 05:00")) == ()
        # ... a fresh pair does.
        assert book.on_close(close("2024-01-09 06:00"))


class TestScopes:
    def test_instrument_scope(self) -> None:
        book = LockBook(default=reg(streak=streak(scope=LockScope.INSTRUMENT)))
        book.on_close(close("2024-01-09 10:00", instrument="EURUSD"))
        assert book.on_close(close("2024-01-09 11:00", instrument="GBPUSD")) == ()
        book.on_close(close("2024-01-09 12:00", instrument="EURUSD"))
        at = T("2024-01-09 13:00")
        assert book.is_locked("EURUSD", "s1", at)
        assert book.is_locked("GBPUSD", "s1", at) is None
        assert book.is_locked("EURUSD", "s2", at) is None

    def test_strategy_scope(self) -> None:
        book = LockBook(default=reg(streak=streak(scope=LockScope.STRATEGY)))
        book.on_close(close("2024-01-09 10:00", instrument="EURUSD"))
        # a different strategy's stop-out does not count toward s1's streak
        book.on_close(close("2024-01-09 10:30", instrument="EURUSD", sid="s2"))
        assert book.is_locked("USDJPY", "s1", T("2024-01-09 11:00")) is None
        book.on_close(close("2024-01-09 11:00", instrument="GBPUSD"))
        at = T("2024-01-09 12:00")
        assert book.is_locked("USDJPY", "s1", at).scope is LockScope.STRATEGY
        assert book.is_locked("USDJPY", "s2", at) is None

    def test_global_scope_counts_and_locks_every_strategy(self) -> None:
        regs = {
            "s1": reg(streak=streak(scope=LockScope.GLOBAL)),
            "s2": reg(),
        }
        book = LockBook(registrations=regs)
        book.on_close(close("2024-01-09 10:00", sid="s2", instrument="GBPUSD"))
        assert book.on_close(close("2024-01-09 11:00", sid="s2", instrument="USDJPY"))
        at = T("2024-01-09 12:00")
        for sid in ("s1", "s2", "never_seen"):
            assert book.is_locked("AUDUSD", sid, at).scope is LockScope.GLOBAL


# --------------------------------------------------------------------------
# Restart recovery
# --------------------------------------------------------------------------


LEDGER = [
    close("2024-01-05 19:00", "take_profit"),
    close("2024-01-05 20:00", "stop_loss"),
    close("2024-01-05 21:00", "stop_loss"),
    close("2024-01-08 03:00", "time_stop", instrument="GBPUSD"),
    close("2024-01-08 09:00", "stop_loss", instrument="GBPUSD"),
    close("2024-01-08 10:00", "stop_loss"),
]
POLICY = reg(cooldown=2, streak=streak(n=2, lookback=6, lock=5))
PROBES = pd.date_range("2024-01-05 18:00", "2024-01-08 20:00", freq="h", tz="UTC")


def _incremental_answers() -> list[tuple[str, str, str | None]]:
    book = LockBook(registrations={"s1": POLICY})
    pending = list(LEDGER)
    out = []
    for probe in PROBES:
        while pending and pending[0].exit_time <= probe:
            book.on_close(pending.pop(0))
        for sym in ("EURUSD", "GBPUSD"):
            lock = book.is_locked(sym, "s1", probe)
            out.append((probe.isoformat(), sym, None if lock is None else lock.code))
    return out


class TestRestart:
    def test_rebuild_from_the_ledger_equals_the_uninterrupted_book(self) -> None:
        """A process that restarts at EVERY bar and rebuilds from the ledger
        gives the same answer, bar by bar, as one that never stopped."""
        view = LedgerLockView({"s1": POLICY}, lambda: list(LEDGER))
        rebuilt = []
        for probe in PROBES:
            for sym in ("EURUSD", "GBPUSD"):
                lock = view.lock_for(sym, "s1", probe, declared=True, timeframe="H1")
                rebuilt.append((probe.isoformat(), sym, None if lock is None else lock.code))
        incremental = _incremental_answers()
        assert rebuilt == incremental
        assert any(code for _, _, code in incremental), "fixture armed no lock"

    def test_the_ledger_is_replayed_in_time_order(self) -> None:
        a = LockBook.rebuild(LEDGER, registrations={"s1": POLICY})
        b = LockBook.rebuild(list(reversed(LEDGER)), registrations={"s1": POLICY})
        assert [x.code for x in a.history] == [x.code for x in b.history]

    def test_a_close_after_the_decision_bar_is_not_seen(self) -> None:
        view = LedgerLockView({"s1": reg(cooldown=3)}, lambda: [close("2024-01-09 12:00")])
        assert view.lock_for("EURUSD", "s1", T("2024-01-09 11:00"), declared=True) is None
        assert view.lock_for("EURUSD", "s1", T("2024-01-09 12:00"), declared=True)

    def test_an_unreadable_ledger_is_unavailable_not_unlocked(self) -> None:
        def broken():
            raise OSError("disk went away")

        view = LedgerLockView({"s1": POLICY}, broken)
        with pytest.raises(LockStateUnavailable, match="disk went away"):
            view.lock_for("EURUSD", "s1", T("2024-01-09 11:00"), declared=False)

    def test_a_declaring_strategy_without_a_policy_is_refused(self) -> None:
        view = LedgerLockView({}, lambda: [])
        with pytest.raises(LockPolicyUnknown):
            view.lock_for("EURUSD", "s1", T("2024-01-09 11:00"), declared=True)
        assert view.lock_for("EURUSD", "s1", T("2024-01-09 11:00"), declared=False) is None

    def test_a_timeframe_mismatch_is_refused(self) -> None:
        view = LedgerLockView({"s1": POLICY}, lambda: [])
        with pytest.raises(LockStateUnavailable, match="timeframe"):
            view.lock_for("EURUSD", "s1", T("2024-01-09 11:00"), declared=True,
                          timeframe="H4")

    def test_the_intent_ledger_survives_a_restart(self, tmp_path) -> None:
        """Closing intents written by ExecutionService.close, read back by a NEW
        store instance on the same file, rebuild the cooldown exactly."""
        from fiboki.broker.execution_service import (
            IntentState,
            JsonlIntentStore,
            OrderIntent,
        )

        path = tmp_path / "intents.jsonl"
        store = JsonlIntentStore(path)
        at = T("2024-01-05 21:00")
        base = dict(
            plan_id="pos-1", signal_id="", strategy_id="s1", instrument="EURUSD",
            direction=Direction.SHORT, size=1000.0, mode=ExecutionMode.PAPER,
            created_at=at, updated_at=at, extra={"closing": True, "position_id": "pos-1"},
        )
        store.write(OrderIntent(client_ref="FBK-CLOSE-pos-1", state=IntentState.PENDING, **base))
        store.write(OrderIntent(client_ref="FBK-CLOSE-pos-1", state=IntentState.CLOSED, **base))
        # An entry intent and a rejected close are not closes.
        store.write(OrderIntent(client_ref="FBK-plan-2", state=IntentState.FILLED,
                                **{**base, "extra": {}}))
        store.write(OrderIntent(client_ref="FBK-CLOSE-pos-3", state=IntentState.REJECTED,
                                **{**base, "plan_id": "pos-3"}))
        del store

        restarted = JsonlIntentStore(path)
        closes = closes_from_intents(restarted.all())
        assert [(c.instrument, c.exit_time, c.exit_reason) for c in closes] == [
            ("EURUSD", at, None)
        ]
        view = LedgerLockView(
            {"s1": reg(cooldown=4)}, lambda: closes_from_intents(JsonlIntentStore(path).all())
        )
        assert view.lock_for("EURUSD", "s1", T("2024-01-08 00:00"), declared=True)
        assert view.lock_for("EURUSD", "s1", T("2024-01-08 01:00"), declared=True) is None


def test_trade_rows_convert() -> None:
    from fiboki.core.contracts import Trade
    from fiboki.core.enums import ExitReason

    trade = Trade(
        instrument="EURUSD", direction=Direction.LONG, size=1.0, entry_price=1.1,
        exit_price=1.09, entry_time=T("2024-01-09 08:00"), exit_time=T("2024-01-09 10:00"),
        exit_reason=ExitReason.STOP_LOSS, gross_pnl=-1.0, spread_cost=0.0, commission=0.0,
        slippage_cost=0.0, financing_cost=0.0, net_pnl=-1.0, account_ccy="USD",
        strategy_id="s1",
    )
    closed = ClosedTrade.of(trade)
    assert closed == ClosedTrade("s1", "EURUSD", T("2024-01-09 10:00"), "stop_loss")
    assert closed.is_stop_out


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        StopStreakRule(n_stops=1, lookback_bars=5, lock_bars=5)
    with pytest.raises(ValueError):
        StopStreakRule(n_stops=2, lookback_bars=0, lock_bars=5)
    with pytest.raises(ValueError):
        StopStreakRule(n_stops=2, lookback_bars=5, lock_bars=0)
    with pytest.raises(ValueError):
        LockPolicy(cooldown_bars_after_close=-1)
    assert not LockPolicy().active
