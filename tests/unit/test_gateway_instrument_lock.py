"""The gateway's ``instrument_lock`` check: named, decision-bar, fail-closed.

What these tests pin:

* the check is asked about the signal's DECISION bar, never ``ctx.now``, so it
  agrees with the backtester at a lock's boundary;
* a lock-declaring strategy is refused by a gateway that cannot establish lock
  state -- no lock source, an unreadable ledger, no registered policy -- rather
  than trading unlocked;
* a restarted gateway reading the durable ledger refuses exactly what the one
  before the restart would have;
* an exit is never refused by a lock.
"""
from __future__ import annotations

from dataclasses import replace

import pandas as pd

from fiboki.backtest.locks import (
    ClosedTrade,
    LedgerLockView,
    LockPolicy,
    LockRegistration,
    LockScope,
    StopStreakRule,
    closes_from_intents,
)
from fiboki.broker.execution_service import IntentState, JsonlIntentStore, OrderIntent
from fiboki.core.enums import Direction, ExecutionMode, Timeframe
from fiboki.risk.gateway import ExitContext, InMemoryAttemptRecorder, RiskGateway
from fiboki.risk.killswitch import RequestKind
from fiboki.strategy.dsl import LOCKS_DECLARED_FEATURE
from tests.exec_fixtures import (
    NOW,
    healthy_market,
    healthy_venue,
    make_context,
    make_plan,
    make_signal,
)

#: NOW is Monday 2024-06-03 12:00 UTC. The signal's decision bar is the H1 bar
#: that opened at 11:00 and has just closed; the wall clock is a moment later.
BAR = NOW - pd.Timedelta(hours=1)
REG = {"sid": LockRegistration(LockPolicy(cooldown_bars_after_close=2), Timeframe.H1)}


def _signal(*, declared: bool = True, sid: str = "sid", bar: pd.Timestamp = BAR,
            instrument: str = "EURUSD"):
    sig = make_signal(strategy_id=sid, bar_time=bar, instrument=instrument)
    return replace(sig, features={LOCKS_DECLARED_FEATURE: 1.0} if declared else {})


def _decide(gateway: RiskGateway, signal) -> tuple[bool, tuple[str, ...]]:
    decision = gateway.evaluate(make_context(make_plan(signal)))
    assert "instrument_lock" in decision.checks_run
    return decision.allowed, decision.reasons


def _lock_reasons(reasons: tuple[str, ...]) -> list[str]:
    return [r for r in reasons if r.startswith("instrument_lock")]


def test_without_a_lock_source_an_undeclaring_signal_passes() -> None:
    allowed, reasons = _decide(RiskGateway(recorder=InMemoryAttemptRecorder()),
                               _signal(declared=False))
    assert allowed, reasons


def test_without_a_lock_source_a_declaring_signal_is_refused() -> None:
    allowed, reasons = _decide(RiskGateway(recorder=InMemoryAttemptRecorder()), _signal())
    assert not allowed
    assert _lock_reasons(reasons) == ["instrument_lock_state_unavailable:no_lock_source"]


def test_a_lock_in_force_blocks_and_names_itself() -> None:
    ledger = [ClosedTrade("sid", "EURUSD", BAR - pd.Timedelta(hours=1), "take_profit")]
    gw = RiskGateway(locks=LedgerLockView(REG, lambda: ledger))
    allowed, reasons = _decide(gw, _signal())
    assert not allowed
    (reason,) = _lock_reasons(reasons)
    assert reason.startswith("instrument_lock:cooldown:instrument:sid:EURUSD:")
    # Two bars after the closing bar, it has expired.
    assert _decide(gw, _signal(bar=BAR + pd.Timedelta(hours=1)))[0]
    # Another instrument and another strategy were never locked.
    assert _decide(gw, _signal(instrument="GBPUSD"))[0]
    assert _decide(gw, _signal(sid="other", declared=False))[0]


def test_the_decision_bar_is_the_signal_bar_not_the_wall_clock() -> None:
    """A 1-bar cooldown armed by a close on the decision bar itself. On the
    decision bar it is in force; one bar later -- which is where ``ctx.now``
    points -- it is not. The gateway must refuse."""
    reg = {"sid": LockRegistration(LockPolicy(cooldown_bars_after_close=1), Timeframe.H1)}
    ledger = [ClosedTrade("sid", "EURUSD", BAR, "stop_loss")]
    gw = RiskGateway(locks=LedgerLockView(reg, lambda: ledger))
    allowed, reasons = _decide(gw, _signal())
    assert not allowed and _lock_reasons(reasons)


def test_an_unreadable_ledger_blocks() -> None:
    def broken():
        raise OSError("ledger unavailable")

    for declared in (True, False):
        gw = RiskGateway(locks=LedgerLockView(REG, broken))
        allowed, reasons = _decide(gw, _signal(declared=declared))
        assert not allowed
        (reason,) = _lock_reasons(reasons)
        assert reason.startswith("instrument_lock_state_unavailable:")
        assert "ledger unavailable" in reason


def test_a_declaring_strategy_with_no_registered_policy_blocks() -> None:
    gw = RiskGateway(locks=LedgerLockView({}, lambda: []))
    allowed, reasons = _decide(gw, _signal())
    assert not allowed
    assert "no lock policy is registered" in _lock_reasons(reasons)[0]


def test_a_global_streak_locks_a_strategy_that_declares_nothing() -> None:
    regs = {
        "sid": LockRegistration(
            LockPolicy(stop_streak=StopStreakRule(2, 10, 5, LockScope.GLOBAL)), Timeframe.H1
        )
    }
    ledger = [
        ClosedTrade("other", "USDJPY", BAR - pd.Timedelta(hours=2), "stop_loss"),
        ClosedTrade("other", "GBPUSD", BAR - pd.Timedelta(hours=1), "stop_loss"),
    ]
    gw = RiskGateway(locks=LedgerLockView(regs, lambda: ledger))
    allowed, reasons = _decide(gw, _signal(sid="other", declared=False, instrument="AUDUSD"))
    assert not allowed
    assert _lock_reasons(reasons)[0].startswith("instrument_lock:stop_streak:global:")


def test_a_restarted_gateway_reads_the_lock_back_from_the_intent_ledger(tmp_path) -> None:
    """Friday close on the last bar of a SUMMER week, 4-bar cooldown; the
    gateway process is then replaced. The new one, with nothing but the JSONL
    intent ledger, refuses Sunday 21:00, 22:00 and 23:00 and permits Monday
    00:00.

    New York is on daylight saving on 2024-05-31, so the interbank week (the
    sim session calendar, 17:00 New York) closes at 21:00 UTC on Friday and
    reopens at 21:00 UTC on Sunday: Fri 20:00 is session bar k, Sun 21:00 k+1,
    Sun 22:00 k+2, Sun 23:00 k+3, Mon 00:00 k+4. (Under the old fixed 22:00 UTC
    calendar Fri 21:00 counted as a bar and Sun 21:00 did not, an hour wrong at
    both ends for most of the year.)"""
    path = tmp_path / "intents.jsonl"
    closed_at = pd.Timestamp("2024-05-31 20:00", tz="UTC")  # a Friday, last summer bar
    store = JsonlIntentStore(path)
    store.write(
        OrderIntent(
            client_ref="FBK-CLOSE-p1", plan_id="p1", signal_id="", strategy_id="sid",
            instrument="EURUSD", direction=Direction.SHORT, size=1000.0,
            mode=ExecutionMode.PAPER, state=IntentState.CLOSED, created_at=closed_at,
            updated_at=closed_at, extra={"closing": True, "position_id": "p1"},
        )
    )
    del store

    reg = {"sid": LockRegistration(LockPolicy(cooldown_bars_after_close=4), Timeframe.H1)}
    restarted = RiskGateway(
        locks=LedgerLockView(reg, lambda: closes_from_intents(JsonlIntentStore(path).all()))
    )

    def decide(bar: str) -> bool:
        ts = pd.Timestamp(bar, tz="UTC")
        now = ts + pd.Timedelta(hours=1)
        sig = replace(make_signal(strategy_id="sid", bar_time=ts),
                      features={LOCKS_DECLARED_FEATURE: 1.0})
        ctx = make_context(make_plan(sig), now=now,
                           market=healthy_market(now=now, instrument="EURUSD"))
        return restarted.evaluate(ctx).allowed

    assert not decide("2024-06-02 21:00")  # Sunday, first bar of a summer week
    assert not decide("2024-06-02 23:00")
    assert decide("2024-06-03 00:00")


def test_an_exit_never_runs_the_lock_check() -> None:
    def broken():
        raise OSError("no ledger")

    gw = RiskGateway(locks=LedgerLockView(REG, broken))
    assert "instrument_lock" not in RiskGateway.EXIT_CHECKS
    decision = gw.evaluate_exit(
        ExitContext(
            instrument="EURUSD", strategy_id="sid", size=1000.0, now=NOW,
            mode=ExecutionMode.PAPER, market=healthy_market(), venue=healthy_venue(),
            request_kind=RequestKind.CLOSE,
        )
    )
    assert decision.allowed, decision.reasons
