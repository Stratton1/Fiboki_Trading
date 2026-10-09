"""The venue-side position manager: amendments, retries, recovery and exposure.

``tests/integration/test_venue_position_manager.py`` proves the DECISIONS are
the backtester's. This file proves the machinery around them behaves when the
venue misbehaves, which is the part that only matters in production:

* an amendment is idempotent, and a re-issue of levels already applied sends
  nothing;
* a refused amendment is retried with backoff and, if it still cannot be
  realised, ALERTS rather than diverging silently;
* a rate limit is treated as UNKNOWN and retried, not as a refusal;
* a venue-side stop that no longer matches our intent is detected and repaired;
* a restarted worker re-derives intent for recovered positions and re-attaches
  what the venue can hold;
* ``managed_exit_exposure`` is a number, and the demo promotion gate refuses a
  policy no venue can hold unless an operator accepts it by name.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.backtest.exits import ExitPolicy, TrailKind, TrailSpec
from fiboki.backtest.position import BookConfig, PositionBook
from fiboki.broker.base import AmendNotSupported, OrderAck, OrderStatus
from fiboki.broker.execution_service import (
    ExecutionService,
    InMemoryIntentStore,
    IntentState,
    OrderIntent,
    ReconciliationReport,
    RecoveredPosition,
    RecoveryReport,
    amend_client_ref,
)
from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.broker.position_manager import (
    MANAGED_EXIT_USER_ACTION_NOTE,
    AmendKind,
    DegradedModeRefused,
    RetryPolicy,
    VenueIntent,
    VenuePositionManager,
    assess_policy,
    require_venue_realisable,
)
from fiboki.broker.simulated_venue import SimulatedVenue, VenueConfig
from fiboki.core.contracts import Order
from fiboki.core.enums import Direction, ExecutionMode, OrderType, Provenance
from fiboki.core.money import IdentityFxSource
from fiboki.risk.gateway import ExitContext, MarketView, RiskGateway, VenueView
from fiboki.risk.killswitch import KillSwitch, KillSwitchMode, RequestKind
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.fills import Bar, FillSimulator, IntrabarPolicy
from fiboki.sim.profiles import IDEALISED_RESEARCH

SYMBOL = "EURUSD"
START = pd.Timestamp("2024-01-01 00:00", tz="UTC")
ENTRY = 1.1000
STOP = 1.0970
TARGET = 1.1060


# ==========================================================================
# Harness
# ==========================================================================


def context_factory(now_fn):
    def factory(position, kind: str) -> ExitContext:
        now = now_fn()
        return ExitContext(
            instrument=position.instrument,
            strategy_id=position.strategy_id or "unit",
            size=position.size,
            now=now,
            mode=ExecutionMode.PAPER,
            position_id=position.position_id,
            limits=PAPER_LIMITS,
            market=MarketView(
                mid_price=ENTRY,
                spread_price=0.00012,
                quote_time=now,
                last_bar_time=now,
                market_open=True,
            ),
            venue=VenueView(connected=True, score=1.0),
            request_kind=(
                RequestKind.PROTECTIVE_AMEND
                if kind == "amend"
                else RequestKind.REDUCE
                if kind == "reduce"
                else RequestKind.CLOSE
            ),
        )

    return factory


def build(
    *,
    venue_config: VenueConfig | None = None,
    policy: ExitPolicy | None = None,
    retry: RetryPolicy | None = None,
    kill_switch: KillSwitch | None = None,
):
    venue = SimulatedVenue(
        venue_config or VenueConfig(prices={SYMBOL: ENTRY}),
        balance=100_000.0,
        currency="USD",
        now=START,
    )
    fx = IdentityFxSource()
    book = PositionBook(
        sim=FillSimulator(
            profile=IDEALISED_RESEARCH, intrabar_policy=IntrabarPolicy.STOP_FIRST
        ),
        fx=fx,
        config=BookConfig(
            account_ccy="USD",
            max_concurrent=4,
            max_per_instrument=4,
            charge_financing=False,
            strategy_id="unit",
            provenance=Provenance.PAPER,
        ),
        policy=policy or ExitPolicy(),
        initial_balance=100_000.0,
    )
    execution = ExecutionService(
        adapter=venue,
        gateway=RiskGateway(limits=PAPER_LIMITS, kill_switch=kill_switch),
        store=InMemoryIntentStore(),
        mode=ExecutionMode.PAPER,
    )
    alerts: list[tuple[str, str]] = []
    telemetry: list = []
    slept: list[float] = []
    manager = VenuePositionManager(
        execution=execution,
        book=book,
        context_factory=context_factory(lambda: manager.now or START),
        telemetry=telemetry.append,
        alert=lambda key, message, detail: alerts.append((key, message)),
        retry=retry or RetryPolicy(attempts=3, initial_backoff_seconds=0.25),
        sleeper=slept.append,
        fx=fx,
        account_ccy="USD",
    )
    manager.set_bar_interval(SYMBOL, pd.Timedelta(hours=1))
    return manager, venue, alerts, telemetry, slept


def bar(i: int, *, open_=ENTRY, high=None, low=None, close=None) -> Bar:
    return Bar(
        START + pd.Timedelta(hours=i),
        open_,
        high if high is not None else open_ + 0.0005,
        low if low is not None else open_ - 0.0005,
        close if close is not None else open_,
    )


def open_one(manager, venue, *, target: float | None = TARGET, ladder=(), allocs=()):
    """Bar 0, place, bar 1 fills. Returns the ManagedPosition."""
    manager.on_bar({SYMBOL: bar(0)}, bar_index=0, timestamp=bar(0).timestamp)
    order = Order(
        plan_id="p1",
        instrument=SYMBOL,
        direction=Direction.LONG,
        size=10_000.0,
        order_type=OrderType.MARKET,
        mode=ExecutionMode.PAPER,
        client_ref="c1",
        stop_loss=STOP,
        take_profit=target,
        take_profit_prices=tuple(ladder) or ((target,) if target else ()),
        take_profit_allocations=tuple(allocs),
        created_at=bar(0).timestamp,
    )
    ack = venue.place_order(order)
    manager.adopt(order, ack, strategy_id="unit")
    venue.set_time(bar(1).timestamp)
    manager.on_bar({SYMBOL: bar(1)}, bar_index=1, timestamp=bar(1).timestamp)
    assert manager.book.open, "the entry did not fill; the test would prove nothing"
    return manager.book.open[0]


def trail_to(manager, venue, managed, level: float, index: int):
    """Move the book's stop as a trail would, then let the manager push it."""
    managed.position.stop_loss = level
    managed.stop_trailed = True
    venue.set_time(bar(index).timestamp)
    return manager.on_bar(
        {SYMBOL: bar(index)}, bar_index=index, timestamp=bar(index).timestamp
    )


# ==========================================================================
# Attachment and idempotency
# ==========================================================================


def test_the_entry_order_attaches_the_stop_and_the_first_target() -> None:
    manager, venue, _alerts, _tele, _slept = build()
    open_one(manager, venue)
    held = venue.positions()[0]
    assert held.stop_loss == pytest.approx(STOP)
    assert held.take_profit_targets == [pytest.approx(TARGET)]


def test_a_trail_step_becomes_exactly_one_venue_amendment() -> None:
    manager, venue, _alerts, telemetry, _slept = build()
    managed = open_one(manager, venue)
    trail_to(manager, venue, managed, 1.0990, index=2)

    applied = [a for a in venue.amendments if a["applied"]]
    assert len(applied) == 1, venue.amendments
    assert applied[0]["stop_loss"] == pytest.approx(1.0990)
    assert venue.positions()[0].stop_loss == pytest.approx(1.0990)
    kinds = [t.kind for t in telemetry]
    assert AmendKind.TRAIL in kinds


def test_re_issuing_the_same_levels_sends_nothing() -> None:
    """Idempotency is structural: the client reference IS the desired state."""
    manager, venue, _alerts, _tele, _slept = build()
    managed = open_one(manager, venue)
    trail_to(manager, venue, managed, 1.0990, index=2)
    sent = len(venue.amendments)

    # A bar on which nothing changed. The manager must not chatter.
    venue.set_time(bar(3).timestamp)
    manager.on_bar({SYMBOL: bar(3)}, bar_index=3, timestamp=bar(3).timestamp)
    assert len(venue.amendments) == sent, (
        "the manager re-sent an amendment for levels the venue already holds"
    )


def test_the_amend_client_reference_is_the_desired_state() -> None:
    a = amend_client_ref("pos_1", 1.0990, 1.1060)
    b = amend_client_ref("pos_1", 1.0990, 1.1060)
    c = amend_client_ref("pos_1", 1.0991, 1.1060)
    assert a == b, "the same instruction must produce the same key"
    assert a != c, "a different level must be a different instruction"
    assert amend_client_ref("pos_1", None, None) != a


def test_an_amend_repeated_after_success_is_reported_as_skipped() -> None:
    """Not silently dropped: 'we did not need to move it' is a fact."""
    manager, venue, _alerts, _tele, _slept = build()
    managed = open_one(manager, venue)
    position = manager.venue_positions[str(managed.position.venue_ref)]
    ctx = context_factory(lambda: manager.now)(position, "amend")
    first = manager.execution.amend(
        position, stop_loss=1.0990, take_profit=TARGET, context=ctx
    )
    second = manager.execution.amend(
        position, stop_loss=1.0990, take_profit=TARGET, context=ctx
    )
    assert first.accepted and not first.skipped
    assert second.accepted and second.skipped
    assert second.reason == "already_applied"
    assert len([a for a in venue.amendments if a["applied"]]) == 1


# ==========================================================================
# Rejection, retry, alert
# ==========================================================================


def test_a_refused_amendment_is_retried_and_then_succeeds() -> None:
    """'Too close to market' is the amendment rejection that actually happens."""
    manager, venue, alerts, telemetry, slept = build(
        venue_config=VenueConfig(prices={SYMBOL: ENTRY}, reject_amend_count=2),
        retry=RetryPolicy(attempts=3, initial_backoff_seconds=0.25, multiplier=2.0),
    )
    managed = open_one(manager, venue)
    trail_to(manager, venue, managed, 1.0990, index=2)

    record = [t for t in telemetry if t.kind is AmendKind.TRAIL][-1]
    assert record.applied, record.error
    assert record.attempts == 3
    assert slept == [0.25, 0.5], "the backoff did not grow between attempts"
    assert alerts == [], "a retry that succeeded must not alert"
    assert venue.positions()[0].stop_loss == pytest.approx(1.0990)


def test_an_amendment_that_cannot_be_realised_alerts_and_is_recorded() -> None:
    """The failure mode this whole class exists to make impossible to miss."""
    manager, venue, alerts, telemetry, _slept = build(
        venue_config=VenueConfig(prices={SYMBOL: ENTRY}, reject_amend_after=1),
        retry=RetryPolicy(attempts=2, initial_backoff_seconds=0.0),
    )
    managed = open_one(manager, venue)
    cycle = trail_to(manager, venue, managed, 1.0990, index=2)

    record = [t for t in telemetry if t.kind is AmendKind.TRAIL][-1]
    assert not record.applied
    assert record.attempts == 2
    assert "ATTACHED_ORDER_LEVEL_DISTANCE_ERROR" in record.error

    assert cycle.divergences, "an unrealisable intent produced no divergence"
    assert cycle.divergences[0].kind == "amend_not_realised"
    assert manager.unrealised_intents, "the divergence was not retained"
    assert alerts, "no alert was raised for an intent that could not be realised"
    key, message = alerts[0]
    assert key.startswith("amend_not_realised")
    assert "NOT being managed as the backtest assumes" in message

    # AND the venue still holds the OLD stop, and our record says so. A manager
    # that optimistically cached the new level would report a trailed stop that
    # does not exist.
    assert venue.positions()[0].stop_loss == pytest.approx(STOP)
    assert manager.venue_state[str(managed.position.venue_ref)].stop_loss == STOP


def test_a_failed_amendment_is_re_attempted_on_the_next_bar() -> None:
    """Intent is not lost. The next bar re-issues it, because it is idempotent."""
    config = VenueConfig(prices={SYMBOL: ENTRY}, reject_amend_after=1)
    manager, venue, _alerts, _tele, _slept = build(
        venue_config=config, retry=RetryPolicy(attempts=1, initial_backoff_seconds=0.0)
    )
    managed = open_one(manager, venue)
    trail_to(manager, venue, managed, 1.0990, index=2)
    assert venue.positions()[0].stop_loss == pytest.approx(STOP)

    # The venue relents. Nothing else changes: the manager still wants 1.0990.
    config.reject_amend_after = None
    venue.set_time(bar(3).timestamp)
    manager.on_bar({SYMBOL: bar(3)}, bar_index=3, timestamp=bar(3).timestamp)
    assert venue.positions()[0].stop_loss == pytest.approx(1.0990), (
        "the manager forgot an intent it had failed to realise"
    )


def test_a_rate_limit_is_unknown_and_is_retried_not_refused() -> None:
    config = VenueConfig(prices={SYMBOL: ENTRY}, amend_rate_limited=True)
    manager, venue, _alerts, telemetry, slept = build(
        venue_config=config,
        retry=RetryPolicy(attempts=3, initial_backoff_seconds=0.1),
    )
    managed = open_one(manager, venue)
    trail_to(manager, venue, managed, 1.0990, index=2)
    record = [t for t in telemetry if t.kind is AmendKind.TRAIL][-1]
    assert record.attempts == 3
    assert len(slept) == 2
    intent = manager.execution.store.get(
        amend_client_ref(
            manager.venue_positions[str(managed.position.venue_ref)].position_id,
            1.0990,
            TARGET,
        )
    )
    assert intent is not None
    assert intent.state is IntentState.UNKNOWN, (
        "a rate-limited amendment was recorded as REJECTED. The venue may have "
        "applied it; the outcome is unknown and must be recorded as unknown."
    )


def test_a_market_closed_refusal_alerts_rather_than_silently_diverging() -> None:
    config = VenueConfig(prices={SYMBOL: ENTRY}, market_closed=True)
    manager, venue, alerts, _tele, _slept = build(
        venue_config=config, retry=RetryPolicy(attempts=2, initial_backoff_seconds=0.0)
    )
    managed = open_one(manager, venue)
    trail_to(manager, venue, managed, 1.0990, index=2)
    assert any("MARKET_HALTED" in m for _k, m in alerts), alerts


def test_an_adapter_that_cannot_amend_is_not_retried() -> None:
    """A structural limit is not a latency spike. One attempt, then the truth."""
    manager, venue, _alerts, _tele, slept = build()
    managed = open_one(manager, venue)
    position = manager.venue_positions[str(managed.position.venue_ref)]

    paper = PaperBroker(
        config=PaperConfig(initial_balance=1000.0), fx=IdentityFxSource()
    )
    manager.execution.adapter = paper
    ctx = context_factory(lambda: manager.now)(position, "amend")
    outcome = manager.execution.amend(
        position,
        stop_loss=1.0990,
        take_profit=TARGET,
        context=ctx,
        retry=RetryPolicy(attempts=5, initial_backoff_seconds=1.0),
        sleeper=slept.append,
    )
    assert not outcome.accepted
    assert outcome.attempts == 1
    assert "amend_not_supported" in outcome.reason
    assert slept == [], "a structurally impossible amendment was retried"
    with pytest.raises(AmendNotSupported):
        paper.amend_position(
            position, stop_loss=1.0, take_profit=None, client_ref="x"
        )


def test_the_kill_switch_still_governs_an_amendment() -> None:
    """An instruction dressed up as an amend must not bypass a FLATTEN."""
    ks = KillSwitch()
    manager, venue, _alerts, _tele, _slept = build(kill_switch=ks)
    managed = open_one(manager, venue)
    ks.activate(KillSwitchMode.FLATTEN, operator="joe", reason="test", at=START)
    before = len(venue.amendments)
    trail_to(manager, venue, managed, 1.0990, index=2)
    assert len(venue.amendments) == before, (
        "an amendment reached the venue while the kill switch was in FLATTEN"
    )


def test_the_backoff_grows_and_is_capped() -> None:
    policy = RetryPolicy(
        attempts=6, initial_backoff_seconds=1.0, multiplier=3.0, max_backoff_seconds=10.0
    )
    assert [policy.backoff(n) for n in range(1, 6)] == [1.0, 3.0, 9.0, 10.0, 10.0]
    with pytest.raises(ValueError):
        RetryPolicy(attempts=0)


# ==========================================================================
# Reconciliation
# ==========================================================================


def test_a_venue_side_stop_that_no_longer_matches_our_intent_is_detected() -> None:
    """An operator moved the stop in the broker's web UI. We must notice."""
    manager, venue, alerts, _tele, _slept = build()
    managed = open_one(manager, venue)
    ref = str(managed.position.venue_ref)

    venue._positions[ref].stop_loss = 1.0900  # somebody else's hand
    divergences = manager.reconcile(repair=False)

    assert [d.kind for d in divergences] == ["stop_mismatch"]
    found = divergences[0]
    assert found.venue_stop == pytest.approx(1.0900)
    assert found.intended_stop == pytest.approx(STOP)
    assert any(k.startswith("stop_mismatch") for k, _m in alerts)


def test_a_detected_mismatch_is_repaired_by_default() -> None:
    manager, venue, _alerts, telemetry, _slept = build()
    managed = open_one(manager, venue)
    ref = str(managed.position.venue_ref)
    venue._positions[ref].stop_loss = 1.0900

    manager.reconcile()
    assert venue.positions()[0].stop_loss == pytest.approx(STOP)
    assert any(t.kind is AmendKind.CORRECTION for t in telemetry)


def test_reconciliation_reports_a_position_the_venue_no_longer_has() -> None:
    manager, venue, _alerts, _tele, _slept = build()
    managed = open_one(manager, venue)
    venue._positions.pop(str(managed.position.venue_ref))
    divergences = manager.reconcile()
    assert [d.kind for d in divergences] == ["missing_at_venue"]


def test_reconciliation_reports_a_real_position_nobody_is_managing() -> None:
    manager, venue, _alerts, _tele, _slept = build()
    open_one(manager, venue)
    stray = Order(
        plan_id="p2", instrument=SYMBOL, direction=Direction.LONG, size=5_000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="c2",
        stop_loss=STOP, created_at=START,
    )
    venue.place_order(stray)
    divergences = manager.reconcile()
    assert [d.kind for d in divergences] == ["unmanaged_at_venue"]
    assert "nothing trails it" in divergences[0].detail


def test_silence_means_agreement() -> None:
    manager, venue, _alerts, _tele, _slept = build()
    open_one(manager, venue)
    assert manager.reconcile() == []


# ==========================================================================
# Startup recovery
# ==========================================================================


def _recovered(venue, *, ladder, allocations, stop, filled_price) -> RecoveryReport:
    position = venue.positions()[0]
    intent = OrderIntent(
        client_ref="FBK-plan-1",
        plan_id="plan-1",
        signal_id="sig-1",
        strategy_id="recovered_strategy",
        instrument=position.instrument,
        direction=position.direction,
        size=position.size,
        mode=ExecutionMode.PAPER,
        state=IntentState.FILLED,
        created_at=START,
        updated_at=START,
        venue="simulated",
        broker_ref=position.venue_ref,
        stop_loss=stop,
        take_profit=ladder[0] if ladder else None,
        filled_size=position.size,
        filled_price=filled_price,
        extra={
            "take_profit_prices": list(ladder),
            "take_profit_allocations": list(allocations),
        },
    )
    return RecoveryReport(
        at=START,
        positions=(
            RecoveredPosition(
                position=position,
                intent=intent,
                broker_ref=str(position.venue_ref),
                has_local_record=True,
                stop_loss=position.stop_loss,
            ),
        ),
        unresolved_intents=(),
        reconciliation=ReconciliationReport(at=START, checked=0),
    )


def test_startup_reconciliation_re_derives_the_ladder_and_re_attaches() -> None:
    """A restarted worker inherits real positions and an empty book."""
    manager, venue, _alerts, telemetry, _slept = build()
    # A position that exists at the venue and nowhere in this process.
    order = Order(
        plan_id="plan-1", instrument=SYMBOL, direction=Direction.LONG, size=10_000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="FBK-plan-1",
        stop_loss=STOP, take_profit=1.1020, created_at=START,
    )
    venue.place_order(order)
    manager.on_bar({SYMBOL: bar(0)}, bar_index=0, timestamp=bar(0).timestamp)
    assert manager.book.open == []

    report = _recovered(
        venue,
        ladder=(1.1020, 1.1045, 1.1090),
        allocations=(0.4, 0.3, 0.3),
        stop=STOP,
        filled_price=ENTRY,
    )
    divergences = manager.resume(report, bar_index=0, now=START)

    assert len(manager.book.open) == 1, "the recovered position was not re-managed"
    managed = manager.book.open[0]
    assert [round(leg.price, 4) for leg in managed.legs] == [1.1020, 1.1045, 1.1090], (
        "the take-profit LADDER was not re-derived from the durable intent"
    )
    assert managed.risk_per_unit == pytest.approx(abs(ENTRY - STOP)), (
        "R was re-derived from the CURRENT stop rather than the original one"
    )
    assert managed.position.strategy_id == "recovered_strategy"
    assert [d.kind for d in divergences] == ["recovered"]
    assert "bars_held restarts at zero" in divergences[0].detail
    # Nothing to re-attach: the venue already holds what we intend.
    assert [t for t in telemetry if t.kind is AmendKind.REATTACH] == []


def test_startup_reconciliation_re_attaches_a_level_the_venue_lost() -> None:
    manager, venue, _alerts, telemetry, _slept = build()
    order = Order(
        plan_id="plan-1", instrument=SYMBOL, direction=Direction.LONG, size=10_000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="FBK-plan-1",
        stop_loss=STOP, created_at=START,
    )
    venue.place_order(order)
    manager.on_bar({SYMBOL: bar(0)}, bar_index=0, timestamp=bar(0).timestamp)
    report = _recovered(
        venue, ladder=(1.1020,), allocations=(), stop=STOP, filled_price=ENTRY
    )
    manager.resume(report, bar_index=0, now=START)

    reattached = [t for t in telemetry if t.kind is AmendKind.REATTACH]
    assert reattached, "the manager did not re-attach the missing target"
    assert reattached[0].applied
    assert venue.positions()[0].take_profit_targets == [pytest.approx(1.1020)]


def test_a_trailed_stop_survives_recovery_as_a_trailed_stop() -> None:
    """The venue's stop is better than the original, so the trail HAD moved."""
    manager, venue, _alerts, _tele, _slept = build()
    order = Order(
        plan_id="plan-1", instrument=SYMBOL, direction=Direction.LONG, size=10_000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="FBK-plan-1",
        stop_loss=1.0990, take_profit=1.1020, created_at=START,
    )
    venue.place_order(order)
    manager.on_bar({SYMBOL: bar(0)}, bar_index=0, timestamp=bar(0).timestamp)
    report = _recovered(
        venue, ladder=(1.1020,), allocations=(), stop=STOP, filled_price=ENTRY
    )
    manager.resume(report, bar_index=0, now=START)
    managed = manager.book.open[0]
    assert managed.stop_trailed, (
        "a recovered position whose venue stop is better than its original one "
        "must report a subsequent stop-out as a TRAILING_STOP, not a STOP_LOSS"
    )
    assert managed.initial_stop == pytest.approx(STOP)


def test_a_recovered_position_with_no_local_record_is_named_unmanageable() -> None:
    manager, venue, alerts, _tele, _slept = build()
    order = Order(
        plan_id="orphan", instrument=SYMBOL, direction=Direction.LONG, size=1_000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="orphan",
        stop_loss=STOP, created_at=START,
    )
    venue.place_order(order)
    manager.on_bar({SYMBOL: bar(0)}, bar_index=0, timestamp=bar(0).timestamp)
    position = venue.positions()[0]
    report = RecoveryReport(
        at=START,
        positions=(
            RecoveredPosition(
                position=position, intent=None, broker_ref=str(position.venue_ref),
                has_local_record=False, stop_loss=position.stop_loss,
            ),
        ),
        unresolved_intents=(),
        reconciliation=ReconciliationReport(at=START, checked=0),
    )
    divergences = manager.resume(report, bar_index=0, now=START)
    assert [d.kind for d in divergences] == ["orphan_unmanageable"]
    assert manager.book.open == [], (
        "a position with no ladder and no original stop must NOT be invented into "
        "the book; an exit policy cannot be guessed"
    )
    assert any(k.startswith("orphan_unmanageable") for k, _m in alerts)


# ==========================================================================
# managed_exit_exposure
# ==========================================================================


def test_managed_exit_exposure_is_zero_for_a_stop_and_one_full_target() -> None:
    manager, venue, _alerts, _tele, _slept = build()
    open_one(manager, venue)
    exposure = manager.measure_managed_exit_exposure()
    assert exposure.amount == 0.0
    assert exposure.positions == 1
    assert exposure.exposed_positions == 0


def test_managed_exit_exposure_counts_the_size_behind_the_unattached_legs() -> None:
    manager, venue, _alerts, _tele, _slept = build(
        policy=ExitPolicy(allocations=(0.4, 0.3, 0.3))
    )
    open_one(
        manager, venue,
        target=1.1020, ladder=(1.1020, 1.1045, 1.1090), allocs=(0.4, 0.3, 0.3),
    )
    exposure = manager.measure_managed_exit_exposure()
    assert exposure.exposed_positions == 1
    # 60% of 10,000 units rides behind legs two and three, at |mark - stop|.
    expected = 6_000.0 * abs(ENTRY - STOP)
    assert exposure.amount == pytest.approx(expected, rel=1e-6)
    assert exposure.per_instrument[SYMBOL] == pytest.approx(expected, rel=1e-6)
    assert exposure.per_strategy["unit"] == pytest.approx(expected, rel=1e-6)


def test_managed_exit_exposure_counts_the_whole_size_with_no_target_at_all() -> None:
    manager, venue, _alerts, _tele, _slept = build(
        policy=ExitPolicy(
            trailing=TrailSpec(kind=TrailKind.ATR_CHANDELIER, value=3.0, atr_column="atr14")
        )
    )
    open_one(manager, venue, target=None)
    exposure = manager.measure_managed_exit_exposure()
    assert exposure.amount == pytest.approx(10_000.0 * abs(ENTRY - STOP), rel=1e-6)


def test_exposure_is_measured_to_the_VENUE_stop_not_ours() -> None:
    """A dead worker realises the venue's stop. That is the honest denominator."""
    manager, venue, _alerts, _tele, _slept = build(
        policy=ExitPolicy(allocations=(0.4, 0.3, 0.3))
    )
    managed = open_one(
        manager, venue,
        target=1.1020, ladder=(1.1020, 1.1045, 1.1090), allocs=(0.4, 0.3, 0.3),
    )
    # Our intent trails to 1.0990; the venue refuses to move.
    managed.position.stop_loss = 1.0990
    exposure = manager.measure_managed_exit_exposure()
    assert exposure.amount == pytest.approx(6_000.0 * abs(ENTRY - STOP), rel=1e-6), (
        "the exposure was measured to our intended stop, which a dead worker "
        "would never reach"
    )


# ==========================================================================
# The demo promotion gate
# ==========================================================================


def test_a_stop_and_one_target_is_venue_realisable() -> None:
    assessment = assess_policy(ExitPolicy(), take_profit_legs=1)
    assert assessment.realisable
    assert assessment.features == ()
    assert require_venue_realisable(
        ExitPolicy(), strategy_id="plain", take_profit_legs=1
    ).realisable


@pytest.mark.parametrize(
    ("policy", "feature"),
    [
        (ExitPolicy(allocations=(0.4, 0.3, 0.3)), "multi_leg_take_profit"),
        (
            ExitPolicy(trailing=TrailSpec(kind=TrailKind.ATR_CHANDELIER, value=3.0, atr_column="atr14")),
            "trailing_stop",
        ),
        (ExitPolicy(breakeven_at_r=1.0), "breakeven_at_r"),
        (ExitPolicy(max_bars_in_trade=40), "time_stop"),
    ],
)
def test_every_client_managed_feature_is_named(policy, feature: str) -> None:
    assessment = assess_policy(policy)
    assert not assessment.realisable
    assert any(f.startswith(feature) for f in assessment.features), assessment.features


def test_promotion_to_demo_is_refused_for_a_policy_a_venue_cannot_hold() -> None:
    with pytest.raises(DegradedModeRefused, match="managed by this process"):
        require_venue_realisable(
            ExitPolicy(trailing=TrailSpec(kind=TrailKind.ATR_CHANDELIER, value=3.0, atr_column="atr14")),
            strategy_id="donchian_breakout_atr",
        )


def test_an_operator_may_accept_the_exposure_and_must_be_named() -> None:
    policy = ExitPolicy(allocations=(0.4, 0.3, 0.3), breakeven_at_r=1.0)
    with pytest.raises(DegradedModeRefused, match="nobody signed"):
        require_venue_realisable(
            policy,
            strategy_id="macd_ema_trend_hybrid",
            accept_managed_exit_exposure=True,
        )
    accepted = require_venue_realisable(
        policy,
        strategy_id="macd_ema_trend_hybrid",
        accept_managed_exit_exposure=True,
        operator="joe",
    )
    assert not accepted.realisable
    assert "accepted by joe" in accepted.note


def test_the_user_action_note_says_what_it_has_to_say() -> None:
    note = MANAGED_EXIT_USER_ACTION_NOTE
    assert "USER ACTION REQUIRED" in note
    assert "managed_exit_exposure" in note
    assert "DEGRADED MODE" in note
    assert "accept_managed_exit_exposure" in note
    assert "ONE stop and ONE limit" in note
    assert "still optimistic" in note


# ==========================================================================
# The venue can only hold one of each
# ==========================================================================


def test_a_venue_position_never_holds_more_than_one_stop_and_one_limit() -> None:
    """The constraint, asserted against the venue rather than assumed."""
    manager, venue, _alerts, _tele, _slept = build(
        policy=ExitPolicy(allocations=(0.4, 0.3, 0.3))
    )
    managed = open_one(
        manager, venue,
        target=1.1020, ladder=(1.1020, 1.1045, 1.1090), allocs=(0.4, 0.3, 0.3),
    )
    trail_to(manager, venue, managed, 1.0990, index=2)
    held = venue.positions()[0]
    assert len(held.take_profit_targets) <= 1
    assert isinstance(held.stop_loss, float)


def test_the_venue_intent_comparison_treats_none_as_a_distinct_level() -> None:
    a = VenueIntent(stop_loss=1.0, take_profit=None)
    b = VenueIntent(stop_loss=1.0, take_profit=1.1)
    assert not a.matches(b, tolerance=1e-9)
    assert a.matches(VenueIntent(1.0, None), tolerance=1e-9)
    assert not a.matches(None, tolerance=1e-9)


def test_an_ack_with_no_broker_reference_cannot_be_managed() -> None:
    manager, venue, _alerts, _tele, _slept = build()
    manager.on_bar({SYMBOL: bar(0)}, bar_index=0, timestamp=bar(0).timestamp)
    order = Order(
        plan_id="p", instrument=SYMBOL, direction=Direction.LONG, size=1000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="cx",
        stop_loss=STOP, created_at=START,
    )
    ack = OrderAck(
        status=OrderStatus.UNKNOWN, broker_ref=None, client_ref="cx",
        order_id="o", submitted_at=START,
    )
    with pytest.raises(ValueError, match="no broker"):
        manager.adopt(order, ack)


def test_a_stray_venue_position_the_book_refused_is_closed(monkeypatch) -> None:
    """The account must never hold a position nothing is managing."""
    manager, venue, alerts, _tele, _slept = build()
    manager.book.config = BookConfig(
        account_ccy="USD", max_concurrent=1, max_per_instrument=1,
        charge_financing=False, strategy_id="unit", provenance=Provenance.PAPER,
    )
    open_one(manager, venue)
    # A second order the venue fills and the book will refuse on max_concurrent.
    order = Order(
        plan_id="p2", instrument=SYMBOL, direction=Direction.LONG, size=5_000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="c2",
        stop_loss=STOP, take_profit=TARGET, created_at=START,
    )
    venue.set_time(bar(2).timestamp)
    manager.adopt(order, venue.place_order(order), strategy_id="unit")
    assert len(venue.positions()) == 2

    venue.set_time(bar(3).timestamp)
    cycle = manager.on_bar({SYMBOL: bar(3)}, bar_index=3, timestamp=bar(3).timestamp)
    assert [d.kind for d in cycle.divergences] == ["entry_not_modelled"]
    assert len(venue.positions()) == 1, (
        "the stray position was reported but left open at the venue"
    )
    assert any(k.startswith("entry_not_modelled") for k, _m in alerts)


def test_the_book_driver_refuses_to_replay_a_bar() -> None:
    manager, _venue, _alerts, _tele, _slept = build()
    manager.on_bar({SYMBOL: bar(0)}, bar_index=0, timestamp=bar(0).timestamp)
    with pytest.raises(ValueError, match="monotonic"):
        manager.on_bar({SYMBOL: bar(0)}, bar_index=0, timestamp=bar(0).timestamp)


def test_position_matches_what_the_manager_thinks_after_a_full_close() -> None:
    manager, venue, _alerts, _tele, _slept = build()
    managed = open_one(manager, venue)
    ref = str(managed.position.venue_ref)
    from fiboki.core.enums import ExitReason

    manager.flatten({SYMBOL: bar(2)}, reason=ExitReason.RISK_HALT)
    assert manager.book.open == []
    assert venue.positions() == ()
    assert ref not in manager.venue_positions
    assert ref not in manager.venue_state


# ==========================================================================
# A refused close is kept and re-sent (paper forward, 2026-10-06)
# ==========================================================================


def _stale_on_demand(manager):
    """Wrap the context factory so a test can make the next exits see a stale quote."""
    original = manager.context_factory
    state = {"stale": False}

    def factory(position, kind):
        ctx = original(position, kind)
        if not state["stale"]:
            return ctx
        from dataclasses import replace

        old = ctx.now - pd.Timedelta(seconds=PAPER_LIMITS.max_price_age_seconds + 30)
        return replace(ctx, market=replace(ctx.market, quote_time=old, last_bar_time=old))

    manager.context_factory = factory
    return state


def test_a_close_the_gateway_refuses_is_kept_and_re_sent_until_the_venue_is_flat() -> None:
    manager, venue, alerts, telemetry, _slept = build()
    managed = open_one(manager, venue)
    ref = str(managed.position.venue_ref)
    state = _stale_on_demand(manager)

    # Bar 2 runs through the stop. The book closes; the gateway refuses the
    # venue close on a stale quote, as it did on 2026-10-06.
    state["stale"] = True
    venue.set_time(bar(2).timestamp)
    manager.on_bar(
        {SYMBOL: bar(2, low=STOP - 0.0010)}, bar_index=2, timestamp=bar(2).timestamp
    )
    assert manager.book.open == []
    assert [p.venue_ref for p in venue.positions()] == [ref], "the venue should still hold it"
    assert ref in manager.pending_closes
    assert any("re-sent every bar" in m for _k, m in alerts), alerts
    assert any(t.error == "risk_gateway_blocked_exit" for t in telemetry)

    # Reconciliation names it for what it is, not as a position nobody manages.
    assert [d.kind for d in manager.reconcile(repair=False)] == ["close_pending"]

    # Bar 3: fresh quote. The same close goes through the same gateway and lands.
    state["stale"] = False
    venue.set_time(bar(3).timestamp)
    manager.on_bar({SYMBOL: bar(3)}, bar_index=3, timestamp=bar(3).timestamp)
    assert venue.positions() == ()
    assert manager.pending_closes == {}
    assert manager.reconcile(repair=False) == []


def test_a_pending_close_is_dropped_once_the_venue_no_longer_holds_the_position() -> None:
    manager, venue, _alerts, _telemetry, _slept = build()
    managed = open_one(manager, venue)
    ref = str(managed.position.venue_ref)
    state = _stale_on_demand(manager)
    state["stale"] = True
    venue.set_time(bar(2).timestamp)
    manager.on_bar(
        {SYMBOL: bar(2, low=STOP - 0.0010)}, bar_index=2, timestamp=bar(2).timestamp
    )
    assert ref in manager.pending_closes
    venue._positions.pop(ref)  # the venue's own stop took it
    assert manager.reconcile(repair=False) == []
    assert manager.pending_closes == {}


def test_a_refused_flatten_is_also_kept_pending() -> None:
    from fiboki.core.enums import ExitReason

    manager, venue, _alerts, _telemetry, _slept = build()
    managed = open_one(manager, venue)
    ref = str(managed.position.venue_ref)
    state = _stale_on_demand(manager)
    state["stale"] = True
    manager.flatten({SYMBOL: bar(2)}, reason=ExitReason.RISK_HALT)
    assert ref in manager.pending_closes
    state["stale"] = False
    manager.flatten({SYMBOL: bar(3)}, reason=ExitReason.RISK_HALT)
    assert venue.positions() == () and manager.pending_closes == {}
