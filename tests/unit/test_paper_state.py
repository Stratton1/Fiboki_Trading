"""The paper-forward state snapshot, piece by piece.

``tests/integration/test_paper_forward_restore.py`` proves the whole runtime
restores to a run byte-identical to one that never restarted. This file proves
the parts it rests on:

* the snapshot record is atomic and self-verifying (CRC), and a flipped byte
  is refused, never half-read;
* :func:`fiboki.entrypoints.paper_state.assess` restores only a record whose
  schema, wiring sha256, strategy hashes and limits version all match and
  whose watermark equals the ledgers' current length;
* the book, the manager and the paper venue round-trip EXACTLY, every field
  of every position included (a new field that the encoder does not know
  fails here rather than vanishing across a restart), and a restored manager
  makes the same decisions over the same later bars as the original;
* a restored venue continues its reference sequence;
* the forward reconciler does not report a close the manager is itself
  re-sending, and still reports one it is not.
"""
from __future__ import annotations

import json
import math
from dataclasses import fields
from pathlib import Path

import pandas as pd
import pytest

from fiboki.backtest.exits import ExitPolicy, ReversalMode, TrailKind, TrailSpec
from fiboki.backtest.position import (
    BookConfig,
    ManagedPosition,
    PositionBook,
    managed_to_state,
)
from fiboki.broker.execution_service import ExecutionService, InMemoryIntentStore
from fiboki.broker.position_manager import IntentDivergence, VenuePositionManager
from fiboki.core.contracts import Order, Position
from fiboki.core.durable import FrameError, read_record, write_record_atomic
from fiboki.core.enums import Direction, ExecutionMode, OrderType, Provenance
from fiboki.core.money import IdentityFxSource
from fiboki.entrypoints import paper_state
from fiboki.entrypoints.paper_forward import ForwardPositionReconciler
from fiboki.entrypoints.paper_venue import ForwardPaperVenue
from fiboki.risk.gateway import ExitContext, MarketView, RiskGateway, VenueView
from fiboki.risk.killswitch import RequestKind
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.fills import Bar, FillSimulator, IntrabarPolicy
from fiboki.sim.profiles import OANDA_REALISTIC

SYMBOL = "EURUSD"
START = pd.Timestamp("2026-10-19 01:00", tz="UTC")
H1 = pd.Timedelta(hours=1)


# ---------------------------------------------------------------- the record


def test_a_record_round_trips_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "snap.json"
    write_record_atomic(path, '{"a":1}')
    write_record_atomic(path, '{"a":2,"b":[1.5,null]}')
    assert read_record(path) == '{"a":2,"b":[1.5,null]}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["snap.json"]


@pytest.mark.parametrize(
    "damage",
    [
        lambda t: t.replace('"a":2', '"a":3'),  # a changed value
        lambda t: t[:-1],  # no terminating newline
        lambda t: t + t,  # two records
        lambda t: t.replace('{"_crc32":"', '{"crc":"'),  # not framed
    ],
)
def test_a_damaged_record_is_refused(tmp_path: Path, damage) -> None:
    path = tmp_path / "snap.json"
    write_record_atomic(path, '{"a":2}')
    path.write_text(damage(path.read_text()))
    with pytest.raises(FrameError):
        read_record(path)


# ---------------------------------------------------------------- assessment


STAMPS = {"wiring_sha256": "w" * 64, "strategy_hashes": ["s" * 64], "limits_version": "lim_v1"}


def _ledgers(tmp: Path) -> dict[str, Path]:
    trades, intents = tmp / "trades.jsonl", tmp / "intents.jsonl"
    trades.write_text("")
    intents.write_text("")
    return {"trades_path": trades, "intents_path": intents}


def _snapshot(tmp: Path, ledgers: dict[str, Path], **override) -> Path:
    body = {
        **paper_state.stamps(**STAMPS),
        "watermark": paper_state.watermark(ledgers["trades_path"], ledgers["intents_path"]),
        "state": {"anything": 1},
        **override,
    }
    path = tmp / paper_state.SNAPSHOT_FILE
    paper_state.write(path, body)
    return path


def test_a_matching_snapshot_is_restorable(tmp_path: Path) -> None:
    ledgers = _ledgers(tmp_path)
    path = _snapshot(tmp_path, ledgers)
    found = paper_state.assess(path, **STAMPS, **ledgers)
    assert found.restorable and found.state == {"anything": 1}


def test_no_snapshot_is_absent(tmp_path: Path) -> None:
    found = paper_state.assess(tmp_path / "nope.json", **STAMPS, **_ledgers(tmp_path))
    assert found.status == "absent" and not found.restorable


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"wiring_sha256": "x" * 64}, "wiring_sha256"),
        ({"strategy_content_hashes": ["t" * 64]}, "strategy_content_hashes"),
        ({"limits_version": "lim_v2"}, "limits_version"),
        ({"schema": "fiboki.paper_forward.state/0"}, "schema"),
    ],
)
def test_any_stamp_that_differs_is_a_mismatch(tmp_path: Path, override, field) -> None:
    ledgers = _ledgers(tmp_path)
    path = _snapshot(tmp_path, ledgers, **override)
    found = paper_state.assess(path, **STAMPS, **ledgers)
    assert found.status == "mismatch" and field in found.reason


def test_a_snapshot_the_ledgers_have_outgrown_is_stale(tmp_path: Path) -> None:
    ledgers = _ledgers(tmp_path)
    path = _snapshot(tmp_path, ledgers)
    from fiboki.core.durable import durable_append

    durable_append(ledgers["trades_path"], '{"trade_id":"t1"}')
    found = paper_state.assess(path, **STAMPS, **ledgers)
    assert found.status == "stale" and "ledgers grew" in found.reason


def test_a_torn_ledger_tail_written_after_the_snapshot_is_not_counted(tmp_path: Path) -> None:
    """The dead process began a ledger write and never finished it: that record
    never happened, so the snapshot is still the latest truth."""
    ledgers = _ledgers(tmp_path)
    path = _snapshot(tmp_path, ledgers)
    with ledgers["intents_path"].open("a") as handle:
        handle.write('{"_crc32":"00000000","half')
    found = paper_state.assess(path, **STAMPS, **ledgers)
    assert found.restorable, found.reason


def test_a_corrupt_snapshot_is_refused_and_can_be_kept_aside(tmp_path: Path) -> None:
    ledgers = _ledgers(tmp_path)
    path = _snapshot(tmp_path, ledgers)
    path.write_text(path.read_text().replace('"anything":1', '"anything":2'))
    found = paper_state.assess(path, **STAMPS, **ledgers)
    assert found.status == "corrupt" and "crc32" in found.reason
    kept = paper_state.quarantine(path, lambda: START)
    assert kept is not None and kept.exists() and not path.exists()


# ------------------------------------------------- book, manager and venue


def _context(now_fn):
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
                mid_price=1.1, spread_price=0.00012, quote_time=now, last_bar_time=now,
                market_open=True,
            ),
            venue=VenueView(connected=True, score=1.0),
            request_kind=(
                RequestKind.PROTECTIVE_AMEND if kind == "amend"
                else RequestKind.REDUCE if kind == "reduce" else RequestKind.CLOSE
            ),
        )

    return factory


POLICY = ExitPolicy(
    allocations=(0.4, 0.3, 0.3),
    trailing=TrailSpec(kind=TrailKind.ATR_CHANDELIER, value=2.0, atr_column="atr",
                       activate_after_r=0.5),
    breakeven_at_r=0.8,
    max_bars_in_trade=40,
    cooldown_bars_after_exit=2,
    reversal=ReversalMode.REVERSE,
)


def _rig():
    clock = {"now": START}
    venue = ForwardPaperVenue(
        profile=OANDA_REALISTIC, account=lambda: None, clock=lambda: clock["now"],
        strategy_id="unit", ref_prefix="PFWD-S0007",
    )
    book = PositionBook(
        sim=FillSimulator(profile=OANDA_REALISTIC, intrabar_policy=IntrabarPolicy.STOP_FIRST),
        fx=IdentityFxSource(),
        config=BookConfig(account_ccy="USD", max_concurrent=3, max_per_instrument=2,
                          charge_financing=True, strategy_id="unit",
                          provenance=Provenance.PAPER),
        policy=POLICY,
        initial_balance=100_000.0,
        latency_bars=OANDA_REALISTIC.latency_bars,
        financing_profile=OANDA_REALISTIC.financing,
    )
    execution = ExecutionService(
        adapter=venue, gateway=RiskGateway(limits=PAPER_LIMITS),
        store=InMemoryIntentStore(), mode=ExecutionMode.PAPER,
    )
    manager = VenuePositionManager(
        execution=execution, book=book, context_factory=_context(lambda: clock["now"]),
        fx=IdentityFxSource(), account_ccy="USD", latency_bars=OANDA_REALISTIC.latency_bars,
        sleeper=lambda _s: None,
    )
    manager.set_bar_interval(SYMBOL, H1)
    return manager, venue, clock


def _bar(i: int, drift: float = 0.0004) -> Bar:
    base = 1.1000 + drift * i
    return Bar(START + i * H1, base, base + 0.0011, base - 0.0006, base + 0.0003)


def _step(manager, venue, clock, i: int, drift: float = 0.0004) -> None:
    b = _bar(i, drift)
    clock["now"] = b.timestamp + H1
    venue.observe_closes({SYMBOL: b.timestamp + H1}, {SYMBOL: b.close})
    manager.on_bar({SYMBOL: b}, bar_index=i, timestamp=b.timestamp,
                   series={SYMBOL: {"atr": 0.0012}})


def _deal(manager, venue, clock, *, ref: str, direction=Direction.LONG, size=10_000.0):
    price = venue.marks[SYMBOL][1]
    sign = direction.sign
    order = Order(
        plan_id=f"p-{ref}", instrument=SYMBOL, direction=direction, size=size,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref=ref,
        stop_loss=price - sign * 0.0030, take_profit=price + sign * 0.0020,
        take_profit_prices=(price + sign * 0.0020, price + sign * 0.0040, price + sign * 0.0070),
        take_profit_allocations=(0.4, 0.3, 0.3), created_at=clock["now"],
    )
    ack = venue.place_order(order)
    manager.adopt(order, ack, strategy_id="unit")


def _busy_manager():
    """A manager with an open, scaled-out, trailed position, a pending entry, a
    scheduled reversal and a cooldown: every kind of state at once."""
    manager, venue, clock = _rig()
    _step(manager, venue, clock, 0)
    _deal(manager, venue, clock, ref="c1")
    for i in range(1, 9):
        _step(manager, venue, clock, i)
    _deal(manager, venue, clock, ref="c2")  # pending in the book until next bar
    manager.book.schedule_reversal(SYMBOL, Direction.SHORT, index=manager.driver.bar_index)
    manager.book.cooldown_until["GBPUSD"] = 42
    manager.book.bump("unit_rejection")
    return manager, venue, clock


def _json(state) -> str:
    return json.dumps(state, sort_keys=True)


def test_the_busy_state_really_is_busy() -> None:
    manager, venue, _clock = _busy_manager()
    (op,) = manager.book.open
    assert op.next_leg >= 1 and op.stop_trailed and op.position.bars_held > 0
    assert manager.book.pending and manager.book.pending_closes
    assert manager._pending_orders and len(venue.positions()) == 2


def test_every_managed_position_and_position_field_is_carried() -> None:
    """A field the encoder does not know would silently reset across a restart."""
    manager, _venue, _clock = _busy_manager()
    encoded = managed_to_state(manager.book.open[0])
    assert set(encoded) == {f.name for f in fields(ManagedPosition)}
    assert set(encoded["position"]) == {f.name for f in fields(Position)}


def test_book_manager_and_venue_round_trip_exactly() -> None:
    manager, venue, _clock = _busy_manager()
    m_state = json.loads(json.dumps(manager.export_state()))
    v_state = json.loads(json.dumps(venue.export_state()))
    fresh, fresh_venue, _ = _rig()
    fresh.restore_state(m_state)
    fresh_venue.restore_state(v_state)
    assert _json(fresh.export_state()) == _json(manager.export_state())
    assert _json(fresh_venue.export_state()) == _json(venue.export_state())


def test_nan_trail_anchor_survives_the_round_trip() -> None:
    manager, _venue, _clock = _busy_manager()
    manager.book.open[0].extreme = float("nan")
    fresh, _v, _c = _rig()
    fresh.restore_state(json.loads(json.dumps(manager.export_state())))
    assert math.isnan(fresh.book.open[0].extreme)


def test_a_restored_manager_makes_the_same_decisions_over_the_same_bars() -> None:
    manager, venue, clock = _busy_manager()
    fresh, fresh_venue, fresh_clock = _rig()
    fresh.restore_state(json.loads(json.dumps(manager.export_state())))
    fresh_venue.restore_state(json.loads(json.dumps(venue.export_state())))
    fresh_clock["now"] = clock["now"]
    # Turn the market: trail, scale-outs, the reversal, stops.
    for i in range(9, 40):
        _step(manager, venue, clock, i, drift=-0.0006 if i > 14 else 0.0004)
        _step(fresh, fresh_venue, fresh_clock, i, drift=-0.0006 if i > 14 else 0.0004)

    def ledger(m):
        return [
            {f.name: getattr(t, f.name) for f in fields(t) if f.name != "trade_id"}
            for t in m.book.trades
        ]

    assert manager.book.trades, "the later bars must close something"
    assert ledger(fresh) == ledger(manager)
    assert fresh.book.balance == manager.book.balance
    assert _json(fresh.export_state()["book"]) == _json(manager.export_state()["book"])


def test_restore_refuses_a_manager_or_venue_that_has_run() -> None:
    manager, venue, _clock = _busy_manager()
    state = manager.export_state()
    with pytest.raises(ValueError, match="fresh"):
        manager.restore_state(state)
    with pytest.raises(ValueError, match="fresh"):
        venue.restore_state(venue.export_state())


def test_a_restored_venue_continues_its_reference_sequence() -> None:
    manager, venue, clock = _busy_manager()
    fresh, fresh_venue, _ = _rig()
    fresh_venue.ref_prefix = "PFWD-S0099"  # what a new session would have chosen
    fresh_venue.restore_state(venue.export_state())
    assert fresh_venue.ref_prefix == "PFWD-S0007"
    assert fresh_venue._next_ref() == venue._next_ref()
    from fiboki.broker.base import DuplicateClientRef

    order = Order(
        plan_id="p", instrument=SYMBOL, direction=Direction.LONG, size=1000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="c1",
        stop_loss=1.0, created_at=clock["now"],
    )
    with pytest.raises(DuplicateClientRef):
        fresh_venue.place_order(order)


# ------------------------------------------------------- the forward reconciler


def test_the_forward_reconciler_does_not_report_a_close_being_re_sent() -> None:
    manager, venue, _clock = _rig()
    held = Position(instrument=SYMBOL, direction=Direction.LONG, size=1000.0,
                    entry_price=1.1, entry_time=START, stop_loss=1.09, venue_ref="R1")
    other = Position(instrument=SYMBOL, direction=Direction.LONG, size=1000.0,
                     entry_price=1.1, entry_time=START, stop_loss=1.09, venue_ref="R2")
    manager.pending_closes["R1"] = (held, "stop_loss")

    def fake_reconcile(*, repair: bool = False):
        return [
            IntentDivergence(kind="close_pending", position_id="a", broker_ref="R1",
                             instrument=SYMBOL),
            IntentDivergence(kind="close_pending", position_id="b", broker_ref="R2",
                             instrument=SYMBOL),
            IntentDivergence(kind="unmanaged_at_venue", position_id=other.position_id,
                             broker_ref="R2", instrument=SYMBOL),
        ]

    manager.reconcile = fake_reconcile  # type: ignore[method-assign]
    found = ForwardPositionReconciler(manager).reconcile()
    assert [(d.kind, d.broker_ref) for d in found] == [
        ("close_pending", "R2"), ("unmanaged_at_venue", "R2")
    ]
