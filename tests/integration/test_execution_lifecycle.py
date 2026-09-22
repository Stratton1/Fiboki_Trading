"""The order lifecycle: idempotent, crash-survivable, reconcilable.

Each test here corresponds to a specific V1 failure with file:line evidence in
the audit. They are integration tests because the failures were all in the
seams: between writing a record and dispatching, between an internal id and a
broker id, between a process dying and a process starting.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.broker.base import BrokerUnavailable
from fiboki.broker.execution_service import (
    ExecutionService,
    IdempotencyViolation,
    InMemoryIntentStore,
    IntentState,
    JsonlIntentStore,
    client_ref_for,
)
from fiboki.broker.mode_guard import ModeGuard
from fiboki.broker.simulated_venue import SimulatedVenue, VenueConfig
from fiboki.core.enums import ExecutionMode
from fiboki.risk.gateway import ExitContext, InMemoryAttemptRecorder, RiskGateway
from fiboki.risk.killswitch import KillSwitch, KillSwitchMode, RequestKind
from tests.exec_fixtures import (
    NOW,
    healthy_market,
    healthy_venue,
    make_context,
    make_plan,
    make_signal,
)

PRICES = {"EURUSD": 1.1000, "GBPUSD": 1.2700}


def build(
    *,
    venue_config: VenueConfig | None = None,
    store=None,
    kill_switch: KillSwitch | None = None,
    recorder=None,
    tmp_path=None,
):
    cfg = venue_config or VenueConfig(prices=PRICES)
    if not cfg.prices:
        cfg.prices = dict(PRICES)
    venue = SimulatedVenue(cfg, now=NOW)
    gateway = RiskGateway(
        kill_switch=kill_switch or KillSwitch(),
        recorder=recorder or InMemoryAttemptRecorder(),
    )
    if store is None:
        store = (
            JsonlIntentStore(tmp_path / "intents.jsonl")
            if tmp_path is not None
            else InMemoryIntentStore()
        )
    service = ExecutionService(
        adapter=venue,
        gateway=gateway,
        store=store,
        mode_guard=ModeGuard(env={}),
        mode=ExecutionMode.PAPER,
    )
    return service, venue, gateway, store


# ------------------------------------------------------------ happy path


def test_a_clean_submission_fills_and_persists_the_broker_reference() -> None:
    service, venue, _, store = build()
    ctx = make_context()
    outcome = service.submit(ctx.plan, ctx)

    assert outcome.accepted
    intent = store.get(client_ref_for(ctx.plan))
    assert intent.state is IntentState.FILLED
    assert intent.broker_ref and intent.broker_ref.startswith("SIMDEAL-")
    assert intent.broker_ref == venue.positions()[0].venue_ref


def test_the_pending_record_is_written_BEFORE_dispatch(tmp_path) -> None:
    """The whole point: the record exists even if the process dies next."""
    service, venue, _, store = build(tmp_path=tmp_path)
    ctx = make_context()
    seen: list[str] = []

    original = venue.place_order

    def observing(order):
        # At the moment of dispatch, what does durable storage already say?
        reloaded = JsonlIntentStore(tmp_path / "intents.jsonl")
        record = reloaded.get(order.client_ref)
        seen.append(record.state.value if record else "MISSING")
        return original(order)

    venue.place_order = observing
    service.submit(ctx.plan, ctx)
    assert seen == ["pending"], (
        "the intent was not durably PENDING at the instant of dispatch; a crash "
        "here would leave a real position with no record"
    )


# ------------------------------------------------------------ idempotency


def test_a_duplicate_client_ref_is_refused_locally() -> None:
    """A filled plan may not be resubmitted; doing so would double the position."""
    service, venue, _, _ = build()
    ctx = make_context()
    service.submit(ctx.plan, ctx)
    with pytest.raises(IdempotencyViolation, match="already"):
        service.submit(ctx.plan, ctx)
    assert len(venue.requests) == 1


def test_a_non_terminal_intent_refuses_resubmission() -> None:
    """Explicitly the stated rule: non-terminal client_refs are refused."""
    service, _, _, store = build(
        venue_config=VenueConfig(prices=PRICES, ack_then_fail=True)
    )
    ctx = make_context()
    service.submit(ctx.plan, ctx)
    assert store.get(client_ref_for(ctx.plan)).state is IntentState.UNKNOWN
    with pytest.raises(IdempotencyViolation):
        service.submit(ctx.plan, ctx)


@pytest.mark.parametrize(
    "state", [IntentState.REJECTED, IntentState.CANCELLED, IntentState.BLOCKED]
)
def test_only_states_that_provably_never_reached_the_book_allow_a_retry(state) -> None:
    from dataclasses import replace

    service, venue, _, store = build(
        venue_config=VenueConfig(prices=PRICES, reject_after=1)
    )
    ctx = make_context()
    service.submit(ctx.plan, ctx)
    ref = client_ref_for(ctx.plan)
    store.write(replace(store.get(ref), state=state))
    assert state.blocks_resubmission is False
    # The venue still refuses (reject_after=1), but the SERVICE let it through,
    # which is the behaviour under test.
    service.submit(ctx.plan, ctx)
    assert len(venue.requests) == 2


def test_the_client_ref_is_deterministic_in_the_plan() -> None:
    plan = make_plan()
    assert client_ref_for(plan) == client_ref_for(plan)
    assert client_ref_for(plan) != client_ref_for(make_plan())


def test_the_venue_also_refuses_a_duplicate_client_ref() -> None:
    """Belt and braces: the key must work at the venue, not only in our store."""
    service, venue, _, _ = build()
    ctx = make_context()
    service.submit(ctx.plan, ctx)
    from fiboki.broker.base import DuplicateClientRef
    from fiboki.core.contracts import Order
    from fiboki.core.enums import OrderType

    replay = Order(
        plan_id=ctx.plan.plan_id,
        instrument=ctx.plan.instrument,
        direction=ctx.plan.direction,
        size=ctx.plan.size,
        order_type=OrderType.MARKET,
        mode=ExecutionMode.PAPER,
        client_ref=client_ref_for(ctx.plan),
    )
    with pytest.raises(DuplicateClientRef):
        venue.place_order(replay)


def test_a_duplicate_at_the_venue_becomes_UNKNOWN_not_rejected() -> None:
    service, venue, _, store = build()
    ctx = make_context()
    service.submit(ctx.plan, ctx)

    # Force the local record into a retryable state so the local guard does not
    # fire, leaving only the venue's duplicate detection.
    from dataclasses import replace

    ref = client_ref_for(ctx.plan)
    store.write(replace(store.get(ref), state=IntentState.CANCELLED))
    outcome = service.submit(ctx.plan, ctx)
    assert not outcome.accepted
    assert store.get(ref).state is IntentState.UNKNOWN


def test_a_terminal_intent_does_not_block_a_genuinely_new_plan() -> None:
    service, _, _, _ = build()
    for _ in range(3):
        ctx = make_context(plan=make_plan())
        assert service.submit(ctx.plan, ctx).accepted


# -------------------------------------------------- crash mid order


def test_crash_mid_order_leaves_a_recoverable_PENDING_record(tmp_path) -> None:
    """The venue took the order; the response never arrived; we then died."""
    service, venue, _, store = build(
        venue_config=VenueConfig(prices=PRICES, ack_then_fail=True), tmp_path=tmp_path
    )
    ctx = make_context()
    outcome = service.submit(ctx.plan, ctx)

    assert not outcome.accepted
    ref = client_ref_for(ctx.plan)

    # The venue really does hold the position.
    assert len(venue.positions()) == 1
    assert len(venue.lost_responses) == 1

    # And a *fresh process* reading durable storage finds a non-terminal record.
    reloaded = JsonlIntentStore(tmp_path / "intents.jsonl")
    record = reloaded.get(ref)
    assert record is not None
    assert record.state is IntentState.UNKNOWN
    assert record.state.needs_reconciliation
    assert not record.state.terminal
    assert record.broker_ref is None  # we never learned it -- that is the point


def test_an_unconfirmed_order_is_never_recorded_as_a_rejection() -> None:
    service, _, _, store = build(
        venue_config=VenueConfig(prices=PRICES, disconnect_after=1)
    )
    ctx = make_context()
    outcome = service.submit(ctx.plan, ctx)
    assert not outcome.accepted
    state = store.get(client_ref_for(ctx.plan)).state
    assert state is IntentState.UNKNOWN
    assert state is not IntentState.REJECTED


def test_a_positive_venue_refusal_IS_terminal() -> None:
    service, _, _, store = build(venue_config=VenueConfig(prices=PRICES, reject_after=1))
    ctx = make_context()
    outcome = service.submit(ctx.plan, ctx)
    assert not outcome.accepted
    intent = store.get(client_ref_for(ctx.plan))
    assert intent.state is IntentState.REJECTED
    assert intent.state.terminal


# ------------------------------------------------------ reconciliation


def test_reconciliation_is_clean_on_a_healthy_system() -> None:
    """V1's could not be, ever: it compared a uuid4 against a dealId."""
    service, _, _, _ = build()
    for _ in range(4):
        ctx = make_context(plan=make_plan())
        assert service.submit(ctx.plan, ctx).accepted

    report = service.reconcile(now=NOW)
    assert report.clean, report.summary()
    assert report.still_unknown == ()
    assert report.orphan_broker_refs == ()
    assert report.size_mismatches == ()


def test_reconciliation_recovers_the_broker_ref_after_a_lost_response(tmp_path) -> None:
    service, venue, _, _ = build(
        venue_config=VenueConfig(prices=PRICES, ack_then_fail=True, ack_then_fail_count=1),
        tmp_path=tmp_path,
    )
    ctx = make_context()
    service.submit(ctx.plan, ctx)
    ref = client_ref_for(ctx.plan)

    # A brand-new process, as after a restart.
    store = JsonlIntentStore(tmp_path / "intents.jsonl")
    fresh = ExecutionService(
        adapter=venue, gateway=RiskGateway(), store=store,
        mode_guard=ModeGuard(env={}), mode=ExecutionMode.PAPER,
    )
    assert store.get(ref).broker_ref is None

    report = fresh.reconcile(now=NOW)
    assert ref in report.resolved
    recovered = store.get(ref)
    assert recovered.broker_ref == venue.positions()[0].venue_ref
    assert recovered.state is IntentState.FILLED


def test_reconciliation_is_keyed_on_the_broker_reference() -> None:
    """Once known, matching uses the venue's key space and not ours."""
    service, venue, _, store = build()
    ctx = make_context()
    service.submit(ctx.plan, ctx)
    intent = store.get(client_ref_for(ctx.plan))
    assert intent.broker_ref in {a.broker_ref for a in venue.orders()}
    # Our internal ids are NOT in the venue's key space -- exactly V1's bug.
    assert intent.plan_id not in {a.broker_ref for a in venue.orders()}
    assert service.reconcile(now=NOW).clean


def test_a_venue_position_with_no_local_record_is_reported_as_an_orphan() -> None:
    service, venue, _, _ = build()
    ctx = make_context()
    service.submit(ctx.plan, ctx)

    # Simulate a position opened by something outside our records.
    from fiboki.core.contracts import Position
    from fiboki.core.enums import Direction

    venue._positions["SIMDEAL-99999999"] = Position(
        instrument="GBPUSD", direction=Direction.LONG, size=1000.0,
        entry_price=1.27, entry_time=NOW, stop_loss=1.26,
        venue_ref="SIMDEAL-99999999",
    )
    report = service.reconcile(now=NOW)
    assert not report.clean
    assert "SIMDEAL-99999999" in report.orphan_broker_refs


def test_a_pending_order_the_venue_never_saw_is_resolved_as_cancelled() -> None:
    service, venue, _, store = build(
        venue_config=VenueConfig(prices=PRICES, disconnect_after=1)
    )
    ctx = make_context()
    service.submit(ctx.plan, ctx)
    ref = client_ref_for(ctx.plan)
    from dataclasses import replace

    store.write(replace(store.get(ref), state=IntentState.PENDING))
    venue.reconnect()

    report = service.reconcile(now=NOW)
    assert store.get(ref).state is IntentState.CANCELLED
    assert ref in report.resolved


def test_reconciliation_survives_an_unreachable_venue() -> None:
    service, venue, _, _ = build()
    ctx = make_context()
    service.submit(ctx.plan, ctx)

    def boom():
        raise BrokerUnavailable("venue down")

    venue.orders = boom
    report = service.reconcile(now=NOW)
    assert not report.clean
    assert any("venue_unreachable" in e for e in report.errors)


# ------------------------------------------------------------- recovery


def test_recovery_rebuilds_open_positions_with_their_refs_and_stops(tmp_path) -> None:
    service, venue, _, _ = build(tmp_path=tmp_path)
    contexts = [make_context(plan=make_plan()) for _ in range(3)]
    for ctx in contexts:
        assert service.submit(ctx.plan, ctx).accepted

    # A fresh process, as after a worker restart.
    store = JsonlIntentStore(tmp_path / "intents.jsonl")
    restarted = ExecutionService(
        adapter=venue, gateway=RiskGateway(), store=store,
        mode_guard=ModeGuard(env={}), mode=ExecutionMode.PAPER,
    )
    report = restarted.recover(now=NOW)

    assert len(report.positions) == 3
    assert report.orphans == ()
    for recovered in report.positions:
        assert recovered.broker_ref.startswith("SIMDEAL-")
        assert recovered.has_local_record
        assert recovered.stop_loss and recovered.stop_loss > 0
        assert recovered.position.venue_ref == recovered.broker_ref
    assert report.reconciliation.clean


def test_recovery_reports_a_venue_position_we_cannot_explain() -> None:
    service, venue, _, _ = build()
    from fiboki.core.contracts import Position
    from fiboki.core.enums import Direction

    venue._positions["SIMDEAL-42424242"] = Position(
        instrument="EURUSD", direction=Direction.SHORT, size=500.0,
        entry_price=1.10, entry_time=NOW, stop_loss=1.11,
        venue_ref="SIMDEAL-42424242",
    )
    report = service.recover(now=NOW)
    assert len(report.orphans) == 1
    assert report.orphans[0].broker_ref == "SIMDEAL-42424242"


def test_recovery_restores_a_stop_the_venue_did_not_report(tmp_path) -> None:
    service, venue, _, _ = build(tmp_path=tmp_path)
    ctx = make_context()
    service.submit(ctx.plan, ctx)

    # A venue that forgets the attached stop on reconnect.
    position = venue.positions()[0]
    position.stop_loss = 0.0

    store = JsonlIntentStore(tmp_path / "intents.jsonl")
    restarted = ExecutionService(
        adapter=venue, gateway=RiskGateway(), store=store,
        mode_guard=ModeGuard(env={}), mode=ExecutionMode.PAPER,
    )
    report = restarted.recover(now=NOW)
    assert report.positions[0].stop_loss == pytest.approx(ctx.plan.signal.stop_price)


# ---------------------------------------------------------- kill switch


def test_a_paused_kill_switch_blocks_new_orders_at_the_service() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="wide spreads", at=NOW)
    service, venue, _, store = build(kill_switch=ks)
    ctx = make_context(request_kind=RequestKind.OPEN)
    outcome = service.submit(ctx.plan, ctx)

    assert not outcome.accepted
    assert outcome.blocked_by_risk
    assert "kill_switch_pause_blocks_new_risk" in outcome.decision.reasons
    assert venue.requests == [], "a blocked order must never reach the venue"
    assert store.get(client_ref_for(ctx.plan)).state is IntentState.BLOCKED


def test_flatten_closes_every_position_through_the_normal_order_path() -> None:
    """V1's kill switch abandoned open positions. This one does not."""
    ks = KillSwitch()
    service, venue, gateway, store = build(kill_switch=ks)
    for symbol in ("EURUSD", "GBPUSD", "EURUSD"):
        sig = make_signal(instrument=symbol, reference_price=PRICES[symbol],
                          stop_distance=0.0030)
        ctx = make_context(plan=make_plan(signal=sig))
        assert service.submit(ctx.plan, ctx).accepted
    assert len(venue.positions()) == 3

    ks.activate(KillSwitchMode.FLATTEN, operator="joe", reason="incident", at=NOW,
                positions_open=3)
    intents = ks.flatten_orders(venue.positions())
    assert len(intents) == 3

    def context_factory(position):
        return ExitContext(
            instrument=position.instrument,
            strategy_id=position.strategy_id,
            size=position.size,
            now=NOW,
            mode=ExecutionMode.PAPER,
            position_id=position.position_id,
            market=healthy_market(instrument=position.instrument),
            venue=healthy_venue(),
            request_kind=RequestKind.CLOSE,
            extra={"reason": "kill_switch_flatten"},
        )

    outcomes = service.flatten(venue.positions(), context_factory=context_factory)
    assert all(o.accepted for o in outcomes), [o.reason for o in outcomes]
    assert venue.positions() == ()
    closed = [i for i in store.all() if i.state is IntentState.CLOSED]
    assert len(closed) == 3


def test_a_flatten_continues_past_an_individual_failure() -> None:
    ks = KillSwitch()
    service, venue, _, _ = build(kill_switch=ks)
    for symbol in ("EURUSD", "GBPUSD"):
        sig = make_signal(instrument=symbol, reference_price=PRICES[symbol],
                          stop_distance=0.0030)
        ctx = make_context(plan=make_plan(signal=sig))
        service.submit(ctx.plan, ctx)
    ks.activate(KillSwitchMode.FLATTEN, operator="joe", reason="x", at=NOW)

    positions = list(venue.positions())
    positions[0].venue_ref = None  # unclosable

    def context_factory(position):
        return ExitContext(
            instrument=position.instrument, strategy_id=position.strategy_id,
            size=position.size, now=NOW, mode=ExecutionMode.PAPER,
            position_id=position.position_id,
            market=healthy_market(instrument=position.instrument),
            venue=healthy_venue(), request_kind=RequestKind.CLOSE,
        )

    outcomes = service.flatten(positions, context_factory=context_factory)
    assert [o.accepted for o in outcomes].count(True) == 1
    assert [o.accepted for o in outcomes].count(False) == 1


# -------------------------------------------------------- mode isolation


def test_the_service_refuses_to_submit_in_an_unauthorised_mode() -> None:
    venue = SimulatedVenue(VenueConfig(prices=PRICES), now=NOW)
    service = ExecutionService(
        adapter=venue,
        gateway=RiskGateway(),
        store=InMemoryIntentStore(),
        mode_guard=ModeGuard(env={}),
        venue_url="https://api-fxtrade.oanda.com/",
        mode=ExecutionMode.LIVE,
    )
    ctx = make_context(mode=ExecutionMode.LIVE)
    outcome = service.submit(ctx.plan, ctx)
    assert not outcome.accepted
    assert "mode_guard_blocked" in outcome.reason
    assert venue.requests == []


def test_the_service_cannot_be_constructed_without_a_gateway() -> None:
    venue = SimulatedVenue(VenueConfig(prices=PRICES), now=NOW)
    with pytest.raises(ValueError, match="RiskGateway"):
        ExecutionService(adapter=venue, gateway=None, store=InMemoryIntentStore())


# ---------------------------------------------------------- durability


def test_the_intent_store_is_append_only_and_replays(tmp_path) -> None:
    path = tmp_path / "intents.jsonl"
    service, _, _, store = build(tmp_path=tmp_path)
    ctx = make_context()
    service.submit(ctx.plan, ctx)

    history = JsonlIntentStore(path).history()
    states = [h.state for h in history]
    assert states[0] is IntentState.PENDING
    assert states[-1] is IntentState.FILLED
    assert len(history) >= 2, "intermediate states must not be overwritten in place"

    replayed = JsonlIntentStore(path)
    assert replayed.get(client_ref_for(ctx.plan)).state is IntentState.FILLED


def test_intent_rows_round_trip_through_json(tmp_path) -> None:
    service, _, _, _ = build(tmp_path=tmp_path)
    ctx = make_context()
    service.submit(ctx.plan, ctx)
    for intent in JsonlIntentStore(tmp_path / "intents.jsonl").history():
        assert intent.to_json()
        assert isinstance(intent.created_at, pd.Timestamp)
        assert intent.created_at.tzinfo is not None
