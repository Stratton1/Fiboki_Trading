"""Weekend noise: amend/close refused only because the market is closed is ONE INFO, not CRITICALs.

Before (Wave 3O item 9): every bar that re-sent a close or a protective amend
the gateway refused with ``market_closed`` raised ``close_not_realised`` /
``amend_not_realised`` through ``VenuePositionManager._raise_unrealised``,
which the paper runtime maps to RECONCILIATION_DIVERGENCE (CRITICAL), repeated
whenever the dispatcher's 15-minute window allowed: a weekend of CRITICALs for
a refusal that is the gateway working as designed.

Now, on the REAL ExecutionService and RiskGateway: a market-closed refusal is
recorded on the cycle and notified once per position and instruction, as an
INFO ``deferred_until_market_open``; it is not an unrealised intent; the same
instruction failing once the market is OPEN escalates exactly as before; one
that succeeds clears the deferral, so the next closed market notifies again.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from fiboki.backtest.exits import ExitPolicy
from fiboki.backtest.position import BookConfig, PositionBook
from fiboki.broker.base import PositionNotFound
from fiboki.broker.execution_service import ExecutionService, InMemoryIntentStore
from fiboki.broker.position_manager import AmendKind, ManagerCycle, VenuePositionManager
from fiboki.core.contracts import Position
from fiboki.core.enums import Direction, ExecutionMode, Provenance
from fiboki.core.money import IdentityFxSource
from fiboki.obs.alerts import AlertDispatcher, AlertEvent, MemoryChannel, Severity
from fiboki.risk.gateway import ExitContext, MarketView, RiskGateway, VenueView
from fiboki.risk.killswitch import RequestKind
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.fills import FillSimulator, IntrabarPolicy
from fiboki.sim.profiles import OANDA_REALISTIC

NOW = pd.Timestamp("2026-10-17T22:00:00Z")  # Saturday morning in Asia; FX closed


class FlatVenue:
    """A venue that holds nothing: a close that reaches it is already flat."""

    venue_name = "test"
    name = "test"

    def close_position(self, position: Any, *, client_ref: str, reason: str = "") -> Any:
        raise PositionNotFound(position.venue_ref or "")


class Market:
    def __init__(self) -> None:
        self.open = False
        self.connected = True


def _manager(market: Market, alerts: list[tuple[str, str, dict]]) -> VenuePositionManager:
    profile = OANDA_REALISTIC
    book = PositionBook(
        sim=FillSimulator(profile=profile, intrabar_policy=IntrabarPolicy.STOP_FIRST),
        fx=IdentityFxSource(),
        config=BookConfig(account_ccy="USD", max_concurrent=1, max_per_instrument=1, strategy_id="t",
                          provenance=Provenance.PAPER),
        policy=ExitPolicy(),
        initial_balance=100_000.0,
        latency_bars=profile.latency_bars,
        financing_profile=profile.financing,
    )
    execution = ExecutionService(adapter=FlatVenue(), gateway=RiskGateway(limits=PAPER_LIMITS),
                                 store=InMemoryIntentStore(), mode=ExecutionMode.PAPER)

    def context(position: Position, kind: str) -> ExitContext:
        return ExitContext(
            instrument=position.instrument, strategy_id="t", size=position.size, now=NOW,
            mode=ExecutionMode.PAPER, position_id=position.position_id, limits=PAPER_LIMITS,
            market=MarketView(mid_price=1.1, spread_price=0.00012, quote_time=NOW, last_bar_time=NOW,
                              market_open=market.open),
            venue=VenueView(connected=market.connected, score=1.0 if market.connected else 0.0,
                            message="" if market.connected else "down"),
            request_kind=RequestKind.CLOSE if kind == "close" else RequestKind.PROTECTIVE_AMEND,
        )

    return VenuePositionManager(
        execution=execution, book=book, context_factory=context, account_ccy="USD",
        alert=lambda key, message, detail: alerts.append((key, message, detail)),
        clock=lambda: NOW, sleeper=lambda _s: None,
    )


def _position() -> Position:
    return Position(instrument="EURUSD", direction=Direction.LONG, size=10_000.0, entry_price=1.1,
                    entry_time=NOW - pd.Timedelta(hours=8), stop_loss=1.095, venue_ref="PFWD-1",
                    position_id="pos-1")


def _close(manager: VenuePositionManager, position: Position) -> tuple[bool, ManagerCycle]:
    cycle = ManagerCycle(at=NOW, advance=None)  # type: ignore[arg-type]
    applied = manager._instruct_close(position=position, size=position.size, final=True, kind=AmendKind.CLOSE,
                                      reason="stop_loss", cycle=cycle)
    return applied, cycle


def test_a_close_refused_for_a_closed_market_is_one_info_not_repeated_criticals():
    market, alerts = Market(), []
    manager, pos = _manager(market, alerts), _position()
    for _ in range(48):  # every retry over a weekend
        applied, cycle = _close(manager, pos)
        assert not applied and cycle.divergences == [] and cycle.alerts
    assert len(alerts) == 1
    key, message, detail = alerts[0]
    assert key == "deferred:close_not_realised:PFWD-1" and detail["deferred_until_market_open"]
    assert "deferred until the market opens" in message
    assert manager.unrealised_intents == [] and "close_not_realised:PFWD-1" in manager.deferred


def test_a_refusal_once_the_market_is_open_still_escalates():
    market, alerts = Market(), []
    manager, pos = _manager(market, alerts), _position()
    _close(manager, pos)
    market.open, market.connected = True, False  # open, but the venue is down
    applied, cycle = _close(manager, pos)
    assert not applied and [d.kind for d in cycle.divergences] == ["close_not_realised"]
    assert alerts[-1][0] == "close_not_realised:PFWD-1"
    assert "deferred_until_market_open" not in alerts[-1][2]
    assert manager.deferred == {}


def test_success_clears_the_deferral_so_the_next_closed_market_notifies_again():
    market, alerts = Market(), []
    manager, pos = _manager(market, alerts), _position()
    _close(manager, pos)
    market.open = True
    applied, _ = _close(manager, pos)
    assert applied and manager.deferred == {}
    market.open = False
    _close(manager, pos)
    assert [a[0] for a in alerts] == ["deferred:close_not_realised:PFWD-1"] * 2


def test_the_paper_runtime_routes_a_deferral_to_info_and_a_divergence_to_critical():
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel])
    # The exact mapping compose_runtime installs (paper_forward.py).
    def sink(key: str, message: str, detail: dict) -> None:
        dispatcher.fire(
            AlertEvent.INSTRUCTION_DEFERRED if detail.get("deferred_until_market_open")
            else AlertEvent.RECONCILIATION_DIVERGENCE,
            message, source="paper-forward", dedupe_key=key,
        )

    market, alerts = Market(), []
    manager, pos = _manager(market, alerts), _position()
    manager.alert = sink
    for _ in range(10):
        _close(manager, pos)
    market.open, market.connected = True, False
    _close(manager, pos)
    sent = [(a.event, a.severity) for a in channel.sent]
    assert sent == [
        (AlertEvent.INSTRUCTION_DEFERRED, Severity.INFO),
        (AlertEvent.RECONCILIATION_DIVERGENCE, Severity.CRITICAL),
    ]
