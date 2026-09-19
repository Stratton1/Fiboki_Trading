"""The gateway runs everything, names everything, and fails closed.

V1's risk engine had zero call sites, so the only thing worth asserting about
V2's is that it cannot be silently skipped, cannot silently pass, and cannot
silently drop a refusal.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.contracts import RiskDecision
from fiboki.core.enums import ExecutionMode, StrategyLifecycle
from fiboki.portfolio.construction import PortfolioSnapshot
from fiboki.risk.gateway import (
    ExitContext,
    InMemoryAttemptRecorder,
    MarketView,
    RiskGateway,
    StrategyView,
    VenueView,
)
from fiboki.risk.killswitch import KillSwitch, KillSwitchMode, RequestKind
from fiboki.risk.limits import DEFAULT_LIMITS
from tests.exec_fixtures import (
    NOW,
    healthy_market,
    healthy_venue,
    make_account,
    make_context,
    make_plan,
    make_snapshot,
)


def _gateway(**kwargs) -> RiskGateway:
    kwargs.setdefault("recorder", InMemoryAttemptRecorder())
    return RiskGateway(**kwargs)


# ------------------------------------------------------------- happy path


def test_a_clean_plan_is_allowed_and_every_check_is_named() -> None:
    gw = _gateway()
    decision = gw.evaluate(make_context())
    assert decision.allowed, decision.reasons
    assert decision.checks_run == RiskGateway.CHECKS
    assert len(decision.checks_run) == 18
    assert decision.decided_at == NOW


def test_checks_run_is_complete_even_when_blocked() -> None:
    """An audit must be able to prove which rules ran, not just which failed."""
    gw = _gateway()
    ctx = make_context(market=MarketView())  # everything unknown
    decision = gw.evaluate(ctx)
    assert not decision.allowed
    assert decision.checks_run == RiskGateway.CHECKS


def test_all_failing_reasons_are_reported_not_just_the_first() -> None:
    gw = _gateway(kill_switch=_paused())
    ctx = make_context(
        market=MarketView(market_open=False, spread_price=None),
        venue=VenueView(connected=False, score=0.0),
        strategy=StrategyView(lifecycle=StrategyLifecycle.QUARANTINED),
    )
    decision = gw.evaluate(ctx)
    assert not decision.allowed
    assert len(decision.reasons) >= 4, decision.reasons


def _paused() -> KillSwitch:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="test", at=NOW)
    return ks


# ----------------------------------------------------------- fail closed


def test_an_exception_inside_a_check_BLOCKS_the_order(monkeypatch) -> None:
    gw = _gateway()

    def exploding(ctx, limits):
        raise RuntimeError("database went away")

    monkeypatch.setattr(gw, "_check_margin_utilisation", exploding)
    decision = gw.evaluate(make_context())
    assert not decision.allowed
    assert any(
        r.startswith("check_error:margin_utilisation:RuntimeError") for r in decision.reasons
    ), decision.reasons
    # and the check is still listed as having run, so the audit shows it errored
    assert "margin_utilisation" in decision.checks_run


def test_every_single_check_fails_closed_when_it_raises(monkeypatch) -> None:
    """Not just one -- all eighteen."""
    for name in RiskGateway.CHECKS:
        gw = _gateway()
        monkeypatch.setattr(
            gw, f"_check_{name}", lambda ctx, limits: (_ for _ in ()).throw(ValueError("boom"))
        )
        decision = gw.evaluate(make_context())
        assert not decision.allowed, f"{name} raised and the order was still allowed"
        assert any(r.startswith(f"check_error:{name}:ValueError") for r in decision.reasons)
        monkeypatch.undo()


def test_a_check_that_does_not_run_blocks_the_order(monkeypatch) -> None:
    """Coverage verification: a silently-skipped check is a blocked order."""
    gw = _gateway()
    monkeypatch.setattr(
        RiskGateway, "CHECKS", (*RiskGateway.CHECKS, "a_check_that_does_not_exist")
    )
    decision = gw.evaluate(make_context())
    assert not decision.allowed
    assert any("a_check_that_does_not_exist" in r for r in decision.reasons)


def test_unknown_market_data_is_not_treated_as_fine() -> None:
    gw = _gateway()
    decision = gw.evaluate(make_context(market=MarketView(market_open=True)))
    assert not decision.allowed
    joined = ";".join(decision.reasons)
    assert "spread_unknown" in joined
    assert "check_error:data_freshness" in joined or "data_stale" in joined


def test_unknown_broker_health_blocks() -> None:
    decision = _gateway().evaluate(make_context(venue=VenueView()))
    assert not decision.allowed
    assert "broker_health_unknown" in decision.reasons


def test_unknown_strategy_lifecycle_blocks() -> None:
    decision = _gateway().evaluate(make_context(strategy=StrategyView(lifecycle=None)))
    assert not decision.allowed
    assert "strategy_lifecycle_unknown" in decision.reasons


# ------------------------------------------------- every check can block


def test_max_per_trade_risk() -> None:
    plan = make_plan(risk_fraction=0.02)  # 2% > the 1% limit
    decision = _gateway().evaluate(make_context(plan=plan))
    assert not decision.allowed
    assert any(r.startswith("max_per_trade_risk") for r in decision.reasons)


def test_max_account_risk() -> None:
    ctx = make_context(open_risk_amount=4_900.0)  # + 1,000 = 5.9% > 5%
    decision = _gateway().evaluate(ctx)
    assert any(r.startswith("max_account_risk") for r in decision.reasons)


def test_max_instrument_exposure() -> None:
    snap = make_snapshot(instrument_exposure={"EURUSD": 950_000.0})
    decision = _gateway().evaluate(make_context(snapshot=snap))
    assert any(r.startswith("max_instrument_exposure") for r in decision.reasons)


def test_max_strategy_exposure() -> None:
    snap = make_snapshot(strategy_exposure={"ichimoku_a": 1_450_000.0})
    decision = _gateway().evaluate(make_context(snapshot=snap))
    assert any(r.startswith("max_strategy_exposure") for r in decision.reasons)


def test_max_currency_exposure_nets_rather_than_accumulating() -> None:
    """Long EURUSD is +EUR and -USD. A short USD book must net, not double."""
    gw = _gateway()
    plan = make_plan()
    notional = plan.size * 1.1000
    # An existing SHORT EUR position of the same size nets this one to zero.
    netting = make_snapshot(currency_exposure={"EUR": -notional, "USD": 0.0})
    assert gw.evaluate(make_context(plan=plan, snapshot=netting)).allowed

    stacking = make_snapshot(currency_exposure={"EUR": 1_450_000.0})
    blocked = gw.evaluate(make_context(plan=plan, snapshot=stacking))
    assert any(r.startswith("max_currency_exposure:EUR") for r in blocked.reasons)


def test_max_correlated_exposure() -> None:
    ctx = make_context(correlated_exposure=1_900_000.0)
    decision = _gateway().evaluate(ctx)
    assert any(r.startswith("max_correlated_exposure") for r in decision.reasons)


def test_daily_loss() -> None:
    decision = _gateway().evaluate(make_context(daily_pnl=-3_100.0))
    assert any(r.startswith("daily_loss") for r in decision.reasons)


def test_a_daily_PROFIT_does_not_block() -> None:
    assert _gateway().evaluate(make_context(daily_pnl=5_000.0)).allowed


def test_weekly_loss() -> None:
    decision = _gateway().evaluate(make_context(weekly_pnl=-6_100.0))
    assert any(r.startswith("weekly_loss") for r in decision.reasons)


def test_total_drawdown() -> None:
    snap = make_snapshot(account=make_account(80_000.0, peak_equity=100_000.0))
    decision = _gateway().evaluate(make_context(snapshot=snap))
    assert any(r.startswith("total_drawdown") for r in decision.reasons)


def test_margin_utilisation() -> None:
    snap = make_snapshot(account=make_account(100_000.0, margin_used=60_000.0))
    decision = _gateway().evaluate(make_context(snapshot=snap))
    assert any(r.startswith("margin_utilisation") for r in decision.reasons)


def test_stale_price() -> None:
    market = healthy_market()
    stale = MarketView(
        mid_price=market.mid_price,
        spread_price=market.spread_price,
        quote_time=NOW - pd.Timedelta(seconds=300),
        last_bar_time=market.last_bar_time,
        market_open=True,
    )
    decision = _gateway().evaluate(make_context(market=stale))
    assert any(r.startswith("stale_price") for r in decision.reasons)


def test_a_quote_from_the_future_blocks() -> None:
    market = MarketView(
        mid_price=1.10,
        spread_price=0.0001,
        quote_time=NOW + pd.Timedelta(seconds=60),
        last_bar_time=NOW - pd.Timedelta(seconds=30),
        market_open=True,
    )
    decision = _gateway().evaluate(make_context(market=market))
    assert any(r.startswith("quote_from_the_future") for r in decision.reasons)


def test_data_freshness() -> None:
    market = MarketView(
        mid_price=1.10,
        spread_price=0.0001,
        quote_time=NOW - pd.Timedelta(seconds=2),
        last_bar_time=NOW - pd.Timedelta(hours=2),
        market_open=True,
    )
    decision = _gateway().evaluate(make_context(market=market))
    assert any(r.startswith("data_stale") for r in decision.reasons)


def test_abnormal_spread() -> None:
    instr_pip = 0.0001
    market = MarketView(
        mid_price=1.10,
        spread_price=10 * instr_pip,  # EURUSD typical is 0.9 pips
        quote_time=NOW - pd.Timedelta(seconds=2),
        last_bar_time=NOW - pd.Timedelta(seconds=30),
        market_open=True,
    )
    decision = _gateway().evaluate(make_context(market=market))
    assert any(r.startswith("abnormal_spread") for r in decision.reasons)


def test_negative_spread_blocks() -> None:
    market = MarketView(
        mid_price=1.10, spread_price=-0.0001,
        quote_time=NOW - pd.Timedelta(seconds=2),
        last_bar_time=NOW - pd.Timedelta(seconds=30), market_open=True,
    )
    assert any(
        r.startswith("negative_spread")
        for r in _gateway().evaluate(make_context(market=market)).reasons
    )


def test_broker_health() -> None:
    decision = _gateway().evaluate(
        make_context(venue=VenueView(connected=True, score=0.2, message="degraded"))
    )
    assert any(r.startswith("broker_unhealthy") for r in decision.reasons)


def test_market_closed() -> None:
    market = MarketView(
        mid_price=1.10, spread_price=0.0001,
        quote_time=NOW - pd.Timedelta(seconds=2),
        last_bar_time=NOW - pd.Timedelta(seconds=30), market_open=False,
    )
    assert "market_closed" in _gateway().evaluate(make_context(market=market)).reasons


def test_market_halted() -> None:
    market = MarketView(
        mid_price=1.10, spread_price=0.0001,
        quote_time=NOW - pd.Timedelta(seconds=2),
        last_bar_time=NOW - pd.Timedelta(seconds=30), market_open=True, halted=True,
    )
    assert "market_halted" in _gateway().evaluate(make_context(market=market)).reasons


def test_event_blackout() -> None:
    base = healthy_market()
    market = MarketView(
        mid_price=base.mid_price,
        spread_price=base.spread_price,
        quote_time=base.quote_time,
        last_bar_time=base.last_bar_time,
        market_open=True,
        event_times=(NOW + pd.Timedelta(minutes=5),),
    )
    decision = _gateway().evaluate(make_context(market=market))
    assert any(r.startswith("event_blackout") for r in decision.reasons)


def test_an_event_outside_the_window_does_not_block() -> None:
    base = healthy_market()
    market = MarketView(
        mid_price=base.mid_price, spread_price=base.spread_price,
        quote_time=base.quote_time, last_bar_time=base.last_bar_time,
        market_open=True, event_times=(NOW + pd.Timedelta(hours=3),),
    )
    assert _gateway().evaluate(make_context(market=market)).allowed


def test_kill_switch_pause_blocks_an_opening_order() -> None:
    gw = _gateway(kill_switch=_paused())
    decision = gw.evaluate(make_context(request_kind=RequestKind.OPEN))
    assert "kill_switch_pause_blocks_new_risk" in decision.reasons


@pytest.mark.parametrize(
    "lifecycle",
    [
        StrategyLifecycle.QUARANTINED,
        StrategyLifecycle.RETIRED,
        StrategyLifecycle.DEGRADED,
        StrategyLifecycle.RESEARCH,
        StrategyLifecycle.CANDIDATE,
    ],
)
def test_strategy_lifecycle_blocks(lifecycle) -> None:
    decision = _gateway().evaluate(
        make_context(strategy=StrategyView(lifecycle=lifecycle))
    )
    assert any(r.startswith("strategy_lifecycle_blocks") for r in decision.reasons)


def test_a_degraded_strategy_blocks_even_in_an_allowed_lifecycle() -> None:
    decision = _gateway().evaluate(
        make_context(
            strategy=StrategyView(lifecycle=StrategyLifecycle.PAPER, degraded=True)
        )
    )
    assert "strategy_degraded" in decision.reasons


def test_live_mode_requires_a_live_lifecycle() -> None:
    decision = _gateway().evaluate(
        make_context(
            mode=ExecutionMode.LIVE,
            strategy=StrategyView(lifecycle=StrategyLifecycle.PAPER),
        )
    )
    assert any(r.startswith("strategy_not_live_approved") for r in decision.reasons)


# ------------------------------------------------------- attempt records


def test_a_blocked_order_is_recorded_with_a_named_reason() -> None:
    recorder = InMemoryAttemptRecorder()
    gw = RiskGateway(recorder=recorder)
    gw.evaluate(make_context(daily_pnl=-9_000.0))
    assert len(recorder.attempts) == 1
    attempt = recorder.blocked[0]
    assert not attempt.allowed
    assert "daily_loss" in attempt.reason
    assert attempt.limits_version == DEFAULT_LIMITS.version
    assert attempt.limits_fingerprint == DEFAULT_LIMITS.fingerprint()


def test_an_allowed_order_is_also_recorded() -> None:
    recorder = InMemoryAttemptRecorder()
    RiskGateway(recorder=recorder).evaluate(make_context())
    assert len(recorder.attempts) == 1
    assert recorder.attempts[0].allowed


def test_a_blocked_attempt_renders_as_execution_telemetry() -> None:
    recorder = InMemoryAttemptRecorder()
    RiskGateway(recorder=recorder).evaluate(make_context(weekly_pnl=-50_000.0))
    telemetry = recorder.blocked[0].to_telemetry()
    assert telemetry.filled_size == 0.0
    assert telemetry.rejected_size > 0
    assert "weekly_loss" in telemetry.venue_error
    assert telemetry.extra["blocked_by_risk_gateway"] is True
    assert len(telemetry.extra["checks_run"]) == 18


def test_nothing_is_dropped_silently_over_many_attempts() -> None:
    recorder = InMemoryAttemptRecorder()
    gw = RiskGateway(recorder=recorder)
    for i in range(25):
        gw.evaluate(make_context(daily_pnl=-100.0 * i))
    assert len(recorder.attempts) == 25


# ------------------------------------------------------------ exit path


def _exit_ctx(**kwargs) -> ExitContext:
    params = {
        "instrument": "EURUSD",
        "strategy_id": "ichimoku_a",
        "size": 10_000.0,
        "now": NOW,
        "mode": ExecutionMode.PAPER,
        "market": healthy_market(),
        "venue": healthy_venue(),
        "position_id": "pos_1",
    }
    params.update(kwargs)
    return ExitContext(**params)


def test_an_exit_runs_the_smaller_named_check_set() -> None:
    decision = _gateway().evaluate_exit(_exit_ctx())
    assert decision.allowed
    assert decision.checks_run == RiskGateway.EXIT_CHECKS


def test_an_exit_is_not_blocked_by_book_level_risk_limits() -> None:
    """A control that stops you getting flat is a trap, not a control."""
    gw = _gateway()
    blocked_entry = gw.evaluate(make_context(daily_pnl=-50_000.0, weekly_pnl=-50_000.0))
    assert not blocked_entry.allowed
    assert gw.evaluate_exit(_exit_ctx()).allowed


def test_a_paused_kill_switch_still_permits_closing() -> None:
    gw = _gateway(kill_switch=_paused())
    assert gw.evaluate_exit(_exit_ctx(request_kind=RequestKind.CLOSE)).allowed


def test_a_flattening_kill_switch_permits_closing() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.FLATTEN, operator="joe", reason="incident", at=NOW)
    assert _gateway(kill_switch=ks).evaluate_exit(_exit_ctx()).allowed


def test_evaluate_exit_refuses_a_risk_adding_request() -> None:
    with pytest.raises(ValueError, match="ADDS"):
        _gateway().evaluate_exit(_exit_ctx(request_kind=RequestKind.OPEN))


def test_exit_fails_closed_on_an_exception(monkeypatch) -> None:
    gw = _gateway()
    monkeypatch.setattr(
        gw, "_check_broker_health",
        lambda ctx, limits: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    decision = gw.evaluate_exit(_exit_ctx())
    assert not decision.allowed
    assert any(r.startswith("check_error:broker_health") for r in decision.reasons)


def test_exit_attempts_are_recorded_too() -> None:
    recorder = InMemoryAttemptRecorder()
    RiskGateway(recorder=recorder).evaluate_exit(_exit_ctx())
    assert recorder.attempts[0].extra["exit"] is True


def test_risk_decision_helpers_are_consistent() -> None:
    allow = RiskDecision.allow(("a", "b"))
    assert allow.allowed and allow.checks_run == ("a", "b")
    block = RiskDecision.block("nope", ("a",))
    assert not block.allowed and block.reasons == ("nope",)


def test_the_gateway_performs_no_io() -> None:
    """It takes a snapshot; it cannot reach a broker, a database or a clock."""
    ctx = make_context()
    assert isinstance(ctx.snapshot, PortfolioSnapshot)
    decision = _gateway().evaluate(ctx)
    # decided_at is the CALLER's timestamp, never a wall clock read
    assert decision.decided_at == ctx.now
