"""Order lifecycle that survives a crash mid-order.

The V1 failures this exists to close, in order
----------------------------------------------
1. **Nothing was written before dispatch.** A crash between the HTTP request
   and the response left a real position with zero local record. Forever.
2. **No order idempotency.** A retry could double a position.
3. **Broker deal ids lived only in memory.** A worker restart orphaned real
   positions permanently: we could see them at the venue and had no way to
   associate them with anything, including our own stops.
4. **Reconciliation compared an internal ``uuid4`` against the broker's
   ``dealId``** -- two key spaces that never intersect. It could not produce a
   clean result even on a perfectly healthy system, so its output was ignored,
   so it may as well not have existed.
5. **An unconfirmed order was treated as a rejection.** The single most
   dangerous simplification available: it assumes the safe outcome on exactly
   the occasions when the outcome is unknown.

The protocol
------------
::

    gateway decision  ->  write PENDING intent (fsynced)  ->  dispatch
                                                               |
                        ack: persist broker_ref immediately  <--+
                        rejection: terminal REJECTED
                        anything else: UNKNOWN, reconcile

**The PENDING record is written and flushed to durable storage before the
dispatch call is made.** If the process dies at the worst possible instant,
the record exists, the client reference in it is the one the venue was given,
and :meth:`ExecutionService.reconcile` can find the position again.

**Reconciliation is keyed on the BROKER reference.** The one exception is an
intent that never learned its broker reference because the response was lost;
for those, and only those, the venue is searched by the ``client_ref`` we sent
in order to *learn* the broker reference, which is then persisted and used for
everything thereafter. That is what the client reference is for.

**An unconfirmed order is UNKNOWN, never a rejection.** UNKNOWN is a
non-terminal state: it blocks a duplicate submission of the same plan and keeps
appearing in reconciliation reports until a human or the venue resolves it.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from fiboki.broker.base import (
    AmendNotSupported,
    BrokerAdapter,
    BrokerError,
    BrokerRejected,
    BrokerUnavailable,
    DuplicateClientRef,
    OrderAck,
    OrderStatus,
    PositionNotFound,
)
from fiboki.broker.mode_guard import ModeGuard
from fiboki.core.contracts import (
    Order,
    Position,
    RiskDecision,
    TradePlan,
)
from fiboki.core.durable import durable_append, read_payloads, touch_durable
from fiboki.core.enums import Direction, ExecutionMode, OrderType
from fiboki.risk.gateway import ExitContext, RiskContext, RiskGateway

__all__ = [
    "AmendOutcome",
    "ExecutionService",
    "IdempotencyViolation",
    "InMemoryIntentStore",
    "IntentState",
    "IntentStore",
    "JsonlIntentStore",
    "OrderIntent",
    "ReconciliationReport",
    "RecoveredPosition",
    "RecoveryReport",
    "SubmitOutcome",
    "amend_client_ref",
    "client_ref_for",
]


class IdempotencyViolation(RuntimeError):
    """A plan whose client reference is already live was submitted again."""


class IntentState(str, Enum):
    #: Written BEFORE dispatch. If you find one of these after a restart, an
    #: order may or may not have reached the venue -- reconcile it.
    PENDING = "pending"
    #: Dispatched; outcome genuinely unconfirmed. NOT a rejection.
    UNKNOWN = "unknown"
    #: Venue accepted; broker reference persisted.
    ACKED = "acked"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    #: Position subsequently closed.
    CLOSED = "closed"
    #: Blocked by the risk gateway or mode guard; never reached a venue.
    BLOCKED = "blocked"

    @property
    def terminal(self) -> bool:
        return self in (
            IntentState.FILLED,
            IntentState.REJECTED,
            IntentState.CANCELLED,
            IntentState.CLOSED,
            IntentState.BLOCKED,
        )

    @property
    def needs_reconciliation(self) -> bool:
        return self in (IntentState.PENDING, IntentState.UNKNOWN, IntentState.ACKED)

    @property
    def blocks_resubmission(self) -> bool:
        """May the same plan be submitted again from this state?

        Every non-terminal state blocks, because the order may be live. So do
        FILLED and CLOSED, which are terminal but mean "this plan already
        reached the book" -- resubmitting would double a real position.

        Only REJECTED, CANCELLED and BLOCKED permit a retry, because in each of
        those the order provably never became a position: the venue refused it,
        reconciliation proved the venue never saw it, or it never left us.
        """
        return self is not IntentState.REJECTED and self not in (
            IntentState.CANCELLED,
            IntentState.BLOCKED,
        )


def client_ref_for(plan: TradePlan) -> str:
    """The idempotency key. Deterministic in the plan, so a retry collides.

    A random key per attempt would make every retry a new order, which is the
    bug rather than the fix. Deriving it from ``plan_id`` means submitting the
    same plan twice is detectable both locally and at the venue.
    """
    return f"FBK-{plan.plan_id}"


def amend_client_ref(
    position_id: str, stop_loss: float | None, take_profit: float | None
) -> str:
    """The idempotency key for an amendment. Deterministic in the DESIRED STATE.

    This is the one design decision the whole client-side position manager
    rests on. An entry's key is derived from the plan, so submitting the same
    plan twice collides and is refused -- which is right, because a second
    entry would be a second position. An amendment is not like that: re-issuing
    "put the stop at 1.0940" is *the same instruction*, and a manager that
    could not safely re-issue it would have to choose between losing a trail
    step to one lost HTTP response and risking a double-apply.

    Deriving the key from the levels removes the choice. Re-issuing levels
    already applied collides with the record of the earlier application and is
    skipped without a network call; a DIFFERENT level is a different key and a
    genuinely new instruction. Idempotency is therefore a property of the key
    space rather than of anybody remembering to check.
    """
    def _level(value: float | None) -> str:
        return "none" if value is None else f"{float(value):.10f}"

    return f"FBK-AMEND-{position_id}-{_level(stop_loss)}-{_level(take_profit)}"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """The durable record of one order, from before dispatch to terminal state."""

    client_ref: str
    plan_id: str
    signal_id: str
    strategy_id: str
    instrument: str
    direction: Direction
    size: float
    mode: ExecutionMode
    state: IntentState
    created_at: pd.Timestamp
    updated_at: pd.Timestamp
    venue: str = ""
    order_id: str = ""
    broker_ref: str | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    filled_size: float = 0.0
    filled_price: float | None = None
    last_error: str = ""
    limits_version: str = ""
    dispatch_attempts: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["mode"] = self.mode.value
        d["state"] = self.state.value
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_json(line: str) -> OrderIntent:
        d = json.loads(line)
        return OrderIntent(
            client_ref=d["client_ref"],
            plan_id=d["plan_id"],
            signal_id=d.get("signal_id", ""),
            strategy_id=d.get("strategy_id", ""),
            instrument=d["instrument"],
            direction=Direction(d["direction"]),
            size=float(d["size"]),
            mode=ExecutionMode(d["mode"]),
            state=IntentState(d["state"]),
            created_at=pd.Timestamp(d["created_at"]),
            updated_at=pd.Timestamp(d["updated_at"]),
            venue=d.get("venue", ""),
            order_id=d.get("order_id", ""),
            broker_ref=d.get("broker_ref"),
            stop_loss=d.get("stop_loss"),
            take_profit=d.get("take_profit"),
            filled_size=float(d.get("filled_size", 0.0)),
            filled_price=d.get("filled_price"),
            last_error=d.get("last_error", ""),
            limits_version=d.get("limits_version", ""),
            dispatch_attempts=int(d.get("dispatch_attempts", 0)),
            extra=dict(d.get("extra") or {}),
        )


class IntentStore(Protocol):
    def write(self, intent: OrderIntent) -> None: ...
    def get(self, client_ref: str) -> OrderIntent | None: ...
    def all(self) -> tuple[OrderIntent, ...]: ...


class InMemoryIntentStore:
    """Non-durable store. Legitimate for tests and backtests ONLY.

    A paper or broker-touching deployment must use :class:`JsonlIntentStore`
    (or an equivalent durable store), because the entire value of writing the
    intent before dispatch is that it outlives the process.
    """

    def __init__(self) -> None:
        self._current: dict[str, OrderIntent] = {}
        self.history: list[OrderIntent] = []

    def write(self, intent: OrderIntent) -> None:
        self._current[intent.client_ref] = intent
        self.history.append(intent)

    def get(self, client_ref: str) -> OrderIntent | None:
        return self._current.get(client_ref)

    def all(self) -> tuple[OrderIntent, ...]:
        return tuple(self._current[k] for k in sorted(self._current))


class JsonlIntentStore:
    """Append-only, durable, self-verifying JSON-lines store. Last line per client_ref wins.

    Append-only because the audit question is "what did we believe, when?", not
    "what do we believe now". Every write goes through
    :func:`fiboki.core.durable.durable_append` -- a CRC-framed line, flushed
    with ``F_FULLFSYNC`` on macOS (plain ``fsync`` does not reach the platter
    there) and a directory ``fsync`` when the file is created -- because the
    crash we are defending against is exactly the one that eats the page cache.

    A torn final line (the process died mid-write) is quarantined to
    ``<file>.torn-<ts>`` and reported CRITICAL on replay instead of making the
    store unopenable. That is safe by construction: :meth:`write` returns only
    after the flush and the PENDING intent is written BEFORE dispatch, so a
    torn intent is one whose dispatch never started. Lines written before
    framing existed are still read.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        touch_durable(self.path)
        self._current: dict[str, OrderIntent] = {}
        self._replay()

    def _replay(self) -> None:
        for line in read_payloads(self.path):
            intent = OrderIntent.from_json(line)
            self._current[intent.client_ref] = intent

    def write(self, intent: OrderIntent) -> None:
        durable_append(self.path, intent.to_json())
        self._current[intent.client_ref] = intent

    def get(self, client_ref: str) -> OrderIntent | None:
        return self._current.get(client_ref)

    def all(self) -> tuple[OrderIntent, ...]:
        return tuple(self._current[k] for k in sorted(self._current))

    def history(self) -> tuple[OrderIntent, ...]:
        return tuple(OrderIntent.from_json(line) for line in read_payloads(self.path))


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubmitOutcome:
    accepted: bool
    intent: OrderIntent | None
    decision: RiskDecision | None
    ack: OrderAck | None = None
    reason: str = ""

    @property
    def blocked_by_risk(self) -> bool:
        return self.decision is not None and not self.decision.allowed


@dataclass(frozen=True, slots=True)
class AmendOutcome:
    """The result of one venue instruction that is not an entry.

    ``skipped`` means the venue already holds these levels and nothing was
    sent. It is reported separately from ``accepted`` because "we did not need
    to move it" and "we moved it" are different facts about the system, and a
    manager that could not tell them apart would count no-ops as evidence its
    amendments were reaching the venue.
    """

    accepted: bool
    intent: OrderIntent | None
    decision: RiskDecision | None
    ack: OrderAck | None = None
    reason: str = ""
    attempts: int = 0
    skipped: bool = False

    @property
    def blocked_by_risk(self) -> bool:
        return self.decision is not None and not self.decision.allowed


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    at: pd.Timestamp
    checked: int
    resolved: tuple[str, ...] = ()
    still_unknown: tuple[str, ...] = ()
    orphan_broker_refs: tuple[str, ...] = ()
    size_mismatches: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """A healthy system reconciles clean. V1's could not, by construction."""
        return not (
            self.still_unknown or self.orphan_broker_refs or self.size_mismatches or self.errors
        )

    def summary(self) -> str:
        return (
            f"reconcile@{self.at.isoformat()} checked={self.checked} "
            f"resolved={len(self.resolved)} unknown={len(self.still_unknown)} "
            f"orphans={len(self.orphan_broker_refs)} mismatches={len(self.size_mismatches)} "
            f"clean={self.clean}"
        )


@dataclass(frozen=True, slots=True)
class RecoveredPosition:
    position: Position
    intent: OrderIntent | None
    broker_ref: str
    has_local_record: bool
    stop_loss: float | None


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    at: pd.Timestamp
    positions: tuple[RecoveredPosition, ...]
    unresolved_intents: tuple[OrderIntent, ...]
    reconciliation: ReconciliationReport

    @property
    def orphans(self) -> tuple[RecoveredPosition, ...]:
        return tuple(p for p in self.positions if not p.has_local_record)


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------


class ExecutionService:
    """The only thing in the codebase that constructs an ``Order``.

    That is not a coincidence, it is the design: making the gateway mandatory
    means making the ``Order`` constructor unreachable from anywhere that has
    not already obtained a :class:`~fiboki.core.contracts.RiskDecision`.
    ``tests/unit/test_no_gateway_bypass.py`` walks the AST of ``src/`` and fails
    if any other module constructs one.
    """

    def __init__(
        self,
        *,
        adapter: BrokerAdapter,
        gateway: RiskGateway,
        store: IntentStore,
        mode_guard: ModeGuard | None = None,
        venue_url: str = "",
        mode: ExecutionMode | None = None,
    ) -> None:
        if gateway is None:
            raise ValueError(
                "ExecutionService requires a RiskGateway. There is no bypass "
                "constructor and there will not be one: V1's risk engine had "
                "zero call sites because it was optional."
            )
        self.adapter = adapter
        self.gateway = gateway
        self.store = store
        self.mode_guard = mode_guard or ModeGuard()
        self.venue_url = venue_url
        self.mode = mode or adapter.mode

    # ------------------------------------------------------------- submit

    def submit(self, plan: TradePlan, context: RiskContext) -> SubmitOutcome:
        """Place one order for ``plan``. Never sizes; never bypasses the gateway."""
        now = context.now
        client_ref = client_ref_for(plan)

        # -- 0. mode isolation ------------------------------------------
        guard = self.mode_guard.check(
            self.mode,
            strategy_id=plan.signal.strategy_id,
            venue_url=self.venue_url,
            now=now,
        )
        if not guard.allowed:
            reason = f"mode_guard_blocked:{list(guard.failed_controls)}"
            self._write(
                self._new_intent(plan, client_ref, now, IntentState.BLOCKED, last_error=reason)
            )
            return SubmitOutcome(False, self.store.get(client_ref), None, None, reason)

        # -- 1. idempotency ---------------------------------------------
        existing = self.store.get(client_ref)
        if existing is not None and existing.state.blocks_resubmission:
            raise IdempotencyViolation(
                f"client_ref {client_ref!r} is already {existing.state.value}"
                f" (broker_ref={existing.broker_ref!r}). Refusing to place a second "
                "order for the same plan; resolve the existing one first."
            )

        # -- 2. THE MANDATORY RISK GATEWAY ------------------------------
        decision = self.gateway.evaluate(context)
        if not decision.allowed:
            # The gateway already recorded the attempt with named reasons. The
            # intent is written too, so "why didn't it trade?" is answerable
            # from the order store alone.
            blocked = self._new_intent(
                plan, client_ref, now, IntentState.BLOCKED,
                last_error=";".join(decision.reasons),
                limits_version=(context.limits.version if context.limits else ""),
            )
            self._write(blocked)
            return SubmitOutcome(False, blocked, decision, None, "risk_gateway_blocked")

        # -- 3. construct the order (sized ONCE, upstream) --------------
        order = Order(
            plan_id=plan.plan_id,
            instrument=plan.instrument,
            direction=plan.direction,
            size=plan.size,  # NOT recomputed. Ever.
            order_type=OrderType.MARKET,
            mode=self.mode,
            client_ref=client_ref,
            stop_loss=plan.signal.stop_price,
            # ONE target for the venue -- that is all a venue can hold -- plus
            # the whole ladder for the position manager behind it. Truncating to
            # the first leg here is what made a paper run of a scale-out
            # document a run of a different document.
            take_profit=(
                plan.signal.take_profit_prices[0] if plan.signal.take_profit_prices else None
            ),
            take_profit_prices=tuple(plan.signal.take_profit_prices),
            take_profit_allocations=tuple(plan.signal.take_profit_allocations),
            created_at=now,
        )

        # -- 4. WRITE PENDING BEFORE DISPATCH ---------------------------
        intent = self._new_intent(
            plan, client_ref, now, IntentState.PENDING,
            order_id=order.order_id,
            limits_version=(context.limits.version if context.limits else ""),
        )
        self._write(intent)

        # -- 5. dispatch -------------------------------------------------
        intent = replace(intent, dispatch_attempts=intent.dispatch_attempts + 1)
        try:
            ack = self.adapter.place_order(order)
        except BrokerRejected as exc:
            # A positive refusal. This one IS terminal.
            self._write(
                replace(
                    intent,
                    state=IntentState.REJECTED,
                    updated_at=now,
                    last_error=f"broker_rejected:{exc}",
                )
            )
            return SubmitOutcome(False, self.store.get(client_ref), decision, None, str(exc))
        except DuplicateClientRef as exc:
            # The venue already has it. That is the idempotency key working, not
            # a failure: the order exists there and we must go and find it.
            self._write(
                replace(
                    intent,
                    state=IntentState.UNKNOWN,
                    updated_at=now,
                    last_error=f"duplicate_client_ref:{exc}",
                )
            )
            return SubmitOutcome(False, self.store.get(client_ref), decision, None, str(exc))
        except BrokerUnavailable as exc:
            # UNKNOWN, NOT rejected. The venue may well have the order.
            self._write(
                replace(
                    intent,
                    state=IntentState.UNKNOWN,
                    updated_at=now,
                    last_error=f"broker_unavailable:{exc}",
                )
            )
            return SubmitOutcome(False, self.store.get(client_ref), decision, None, str(exc))
        except BrokerError as exc:
            self._write(
                replace(
                    intent,
                    state=IntentState.UNKNOWN,
                    updated_at=now,
                    last_error=f"broker_error:{type(exc).__name__}:{exc}",
                )
            )
            return SubmitOutcome(False, self.store.get(client_ref), decision, None, str(exc))

        # -- 6. persist the broker reference IMMEDIATELY ----------------
        updated = self._apply_ack(intent, ack, now)
        self._write(updated)
        return SubmitOutcome(ack.status is not OrderStatus.REJECTED, updated, decision, ack, "")

    # -------------------------------------------------------------- close

    def close(self, position: Position, context: ExitContext) -> SubmitOutcome:
        """Close ``position``. Also goes through the gateway -- exits are orders too.

        The kill switch's FLATTEN mode reaches the venue through here, so a
        flatten is recorded, idempotent and recoverable exactly like an entry.
        V1's kill switch had no such path, which is why it abandoned positions.
        """
        now = context.now
        client_ref = f"FBK-CLOSE-{position.position_id}"
        decision = self.gateway.evaluate_exit(context)
        if not decision.allowed:
            return SubmitOutcome(False, None, decision, None, "risk_gateway_blocked_exit")

        existing = self.store.get(client_ref)
        if existing is not None and existing.state.blocks_resubmission:
            raise IdempotencyViolation(
                f"close for {position.position_id} is already {existing.state.value}"
            )

        intent = OrderIntent(
            client_ref=client_ref,
            plan_id=position.position_id,
            signal_id="",
            strategy_id=position.strategy_id,
            instrument=position.instrument,
            direction=position.direction.opposite,
            size=position.size,
            mode=self.mode,
            state=IntentState.PENDING,
            created_at=now,
            updated_at=now,
            venue=self.adapter.venue_name,
            broker_ref=position.venue_ref,
            extra={"closing": True, "position_id": position.position_id},
        )
        self._write(intent)
        try:
            ack = self.adapter.close_position(
                position, client_ref=client_ref, reason=context.extra.get("reason", "")
            )
        except PositionNotFound as exc:
            # The venue has no such position. For a CLOSE that is the state we
            # wanted: the position is flat, which is what we were asking for.
            # Recording it as a rejection would make a manager retry an
            # instruction that can never succeed and then alert on a book that
            # is exactly right.
            self._write(
                replace(
                    intent,
                    state=IntentState.CLOSED,
                    updated_at=now,
                    last_error=f"already_flat:{exc}",
                )
            )
            return SubmitOutcome(
                True, self.store.get(client_ref), decision, None, f"already_flat:{exc}"
            )
        except BrokerRejected as exc:
            self._write(replace(intent, state=IntentState.REJECTED, updated_at=now,
                                last_error=f"broker_rejected:{exc}"))
            return SubmitOutcome(False, self.store.get(client_ref), decision, None, str(exc))
        except BrokerError as exc:
            self._write(replace(intent, state=IntentState.UNKNOWN, updated_at=now,
                                last_error=f"{type(exc).__name__}:{exc}"))
            return SubmitOutcome(False, self.store.get(client_ref), decision, None, str(exc))

        closed = replace(
            intent,
            state=IntentState.CLOSED,
            updated_at=now,
            broker_ref=ack.broker_ref or intent.broker_ref,
            filled_size=ack.fill.filled_size if ack.fill else 0.0,
            filled_price=ack.fill.filled_price if ack.fill else None,
        )
        self._write(closed)
        return SubmitOutcome(True, closed, decision, ack, "")

    # ------------------------------------------------- venue-side management

    def amend(
        self,
        position: Position,
        *,
        stop_loss: float | None,
        take_profit: float | None,
        context: ExitContext,
        retry: Any = None,
        sleeper: Any = None,
        reason: str = "",
    ) -> AmendOutcome:
        """Move the ONE stop and ONE limit the venue holds for ``position``.

        This is the only route to an adapter's ``amend_position``. Nothing in
        ``workers/`` or the position manager calls an adapter directly, for the
        same reason nothing constructs an ``Order``: the durable record, the
        gateway decision and the idempotency key are not optional extras that a
        caller may remember to add, they are the path.

        Four things happen here that do not happen if you call the adapter:

        1. the risk gateway's EXIT check set runs, so a kill switch in FLATTEN
           mode cannot be bypassed by dressing an instruction up as an amend;
        2. an intent is written and FSYNCED BEFORE dispatch, so a crash between
           deciding and dispatching leaves evidence;
        3. the instruction is idempotent -- see :func:`amend_client_ref` -- so
           re-issuing levels the venue already holds costs nothing and sends
           nothing;
        4. it is RETRIED with backoff. An entry is never retried, because a
           retry that reached the venue twice doubles a position. An amendment
           is idempotent, so the calculus inverts: not retrying is the risk.

        A retry is exhausted rather than swallowed. The caller gets
        ``accepted=False`` and the last error, and is expected to alert --
        :class:`~fiboki.broker.position_manager.VenuePositionManager` does.
        """
        now = context.now
        client_ref = amend_client_ref(position.position_id, stop_loss, take_profit)

        decision = self.gateway.evaluate_exit(context)
        if not decision.allowed:
            return AmendOutcome(
                False, None, decision, None, "risk_gateway_blocked_amend", 0, False
            )

        existing = self.store.get(client_ref)
        if existing is not None and existing.state is IntentState.FILLED:
            # These exact levels have already been applied at the venue. Not an
            # error and not a no-op to hide: a reported skip.
            return AmendOutcome(True, existing, decision, None, "already_applied", 0, True)

        intent = OrderIntent(
            client_ref=client_ref,
            plan_id=position.position_id,
            signal_id="",
            strategy_id=position.strategy_id,
            instrument=position.instrument,
            direction=position.direction,
            size=position.size,
            mode=self.mode,
            state=IntentState.PENDING,
            created_at=now,
            updated_at=now,
            venue=self.adapter.venue_name,
            broker_ref=position.venue_ref,
            stop_loss=stop_loss,
            take_profit=take_profit,
            extra={"amend": True, "position_id": position.position_id, "reason": reason},
        )
        self._write(intent)

        attempts = int(getattr(retry, "attempts", 1) or 1)
        last_error = ""
        for attempt in range(1, attempts + 1):
            intent = replace(intent, dispatch_attempts=attempt, updated_at=now)
            try:
                ack = self.adapter.amend_position(
                    position,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    client_ref=client_ref,
                    reason=reason,
                )
            except PositionNotFound as exc:
                # Nothing to amend. Terminal, and NOT retryable: the position is
                # gone and no amount of backoff brings it back.
                self._write(
                    replace(
                        intent,
                        state=IntentState.CLOSED,
                        updated_at=now,
                        last_error=f"already_flat:{exc}",
                    )
                )
                return AmendOutcome(
                    False,
                    self.store.get(client_ref),
                    decision,
                    None,
                    f"already_flat:{exc}",
                    attempt,
                    False,
                )
            except AmendNotSupported as exc:
                # The venue cannot express this at all. Retrying is pointless
                # and would turn a structural limit into a latency spike.
                self._write(
                    replace(
                        intent,
                        state=IntentState.REJECTED,
                        updated_at=now,
                        last_error=f"amend_not_supported:{exc}",
                    )
                )
                return AmendOutcome(
                    False,
                    self.store.get(client_ref),
                    decision,
                    None,
                    f"amend_not_supported:{exc}",
                    attempt,
                    False,
                )
            except (BrokerRejected, BrokerUnavailable, BrokerError) as exc:
                last_error = f"{type(exc).__name__}:{exc}"
                state = (
                    IntentState.REJECTED
                    if isinstance(exc, BrokerRejected)
                    else IntentState.UNKNOWN
                )
                self._write(
                    replace(intent, state=state, updated_at=now, last_error=last_error)
                )
                if attempt >= attempts:
                    break
                if sleeper is not None and retry is not None:
                    sleeper(retry.backoff(attempt))
                continue

            applied = replace(
                intent,
                state=IntentState.FILLED,
                updated_at=now,
                broker_ref=ack.broker_ref or intent.broker_ref,
                order_id=intent.order_id or ack.order_id,
                last_error="",
            )
            self._write(applied)
            return AmendOutcome(True, applied, decision, ack, "", attempt, False)

        return AmendOutcome(
            False, self.store.get(client_ref), decision, None, last_error, attempts, False
        )

    def close_partial(
        self,
        position: Position,
        *,
        size: float,
        context: ExitContext,
        reason: str = "",
    ) -> AmendOutcome:
        """Close ``size`` of ``position``, leaving the remainder open.

        This is how a scale-out leg beyond the first reaches the venue. Unlike
        an amendment it is NOT retried: a partial close is not idempotent --
        two of them close twice the size -- so an unconfirmed one is UNKNOWN
        and is resolved by reconciliation, exactly like an unconfirmed entry.
        The asymmetry is the point.
        """
        now = context.now
        client_ref = f"FBK-PARTIAL-{position.position_id}-{float(size):.10f}"
        decision = self.gateway.evaluate_exit(context)
        if not decision.allowed:
            return AmendOutcome(
                False, None, decision, None, "risk_gateway_blocked_partial", 0, False
            )

        existing = self.store.get(client_ref)
        if existing is not None and existing.state.blocks_resubmission:
            raise IdempotencyViolation(
                f"a partial close of {size} on {position.position_id} is already "
                f"{existing.state.value}. Closing it twice would close twice the "
                "size; resolve the existing one first."
            )

        intent = OrderIntent(
            client_ref=client_ref,
            plan_id=position.position_id,
            signal_id="",
            strategy_id=position.strategy_id,
            instrument=position.instrument,
            direction=position.direction.opposite,
            size=float(size),
            mode=self.mode,
            state=IntentState.PENDING,
            created_at=now,
            updated_at=now,
            venue=self.adapter.venue_name,
            broker_ref=position.venue_ref,
            extra={
                "partial_close": True,
                "position_id": position.position_id,
                "reason": reason,
            },
        )
        self._write(intent)
        try:
            ack = self.adapter.close_partial(
                position, size=float(size), client_ref=client_ref, reason=reason
            )
        except PositionNotFound as exc:
            self._write(
                replace(
                    intent,
                    state=IntentState.CLOSED,
                    updated_at=now,
                    last_error=f"already_flat:{exc}",
                )
            )
            return AmendOutcome(
                True, self.store.get(client_ref), decision, None,
                f"already_flat:{exc}", 1, True,
            )
        except BrokerRejected as exc:
            self._write(
                replace(intent, state=IntentState.REJECTED, updated_at=now,
                        last_error=f"broker_rejected:{exc}")
            )
            return AmendOutcome(
                False, self.store.get(client_ref), decision, None, str(exc), 1, False
            )
        except BrokerError as exc:
            self._write(
                replace(intent, state=IntentState.UNKNOWN, updated_at=now,
                        last_error=f"{type(exc).__name__}:{exc}")
            )
            return AmendOutcome(
                False, self.store.get(client_ref), decision, None, str(exc), 1, False
            )

        done = replace(
            intent,
            state=IntentState.FILLED,
            updated_at=now,
            broker_ref=ack.broker_ref or intent.broker_ref,
            filled_size=ack.fill.filled_size if ack.fill else float(size),
            filled_price=ack.fill.filled_price if ack.fill else None,
        )
        self._write(done)
        return AmendOutcome(True, done, decision, ack, "", 1, False)

    def flatten(
        self,
        positions: Iterable[Position],
        *,
        context_factory,
    ) -> tuple[SubmitOutcome, ...]:
        """Close every supplied position, continuing past individual failures.

        A flatten that aborts on the first error leaves a half-flat book, which
        is often worse than either extreme. Failures are returned, not raised.
        """
        out: list[SubmitOutcome] = []
        for pos in positions:
            try:
                out.append(self.close(pos, context_factory(pos)))
            except Exception as exc:
                out.append(SubmitOutcome(False, None, None, None, f"{type(exc).__name__}:{exc}"))
        return tuple(out)

    # -------------------------------------------------------- reconcile

    def reconcile(self, *, now: pd.Timestamp | None = None) -> ReconciliationReport:
        """Compare our record with the venue's, keyed on the BROKER reference."""
        now = now or pd.Timestamp.now(tz="UTC")
        resolved: list[str] = []
        still_unknown: list[str] = []
        mismatches: list[str] = []
        errors: list[str] = []

        try:
            venue_orders = self.adapter.orders()
            venue_positions = self.adapter.positions()
        except Exception as exc:
            return ReconciliationReport(
                at=now, checked=0, errors=(f"venue_unreachable:{type(exc).__name__}:{exc}",)
            )

        by_broker_ref = {a.broker_ref: a for a in venue_orders if a.broker_ref}
        by_client_ref = {a.client_ref: a for a in venue_orders if a.client_ref}

        outstanding = [i for i in self.store.all() if i.state.needs_reconciliation]
        for intent in outstanding:
            ack: OrderAck | None = None
            if intent.broker_ref:
                # The normal path: OUR key space and THEIRS are the same key
                # space, because we stored theirs.
                ack = by_broker_ref.get(intent.broker_ref)
            if ack is None:
                # The recovery path, and the only legitimate use of the client
                # reference here: learn the broker reference we never received.
                ack = by_client_ref.get(intent.client_ref)

            if ack is None:
                if intent.state is IntentState.PENDING:
                    # Written before dispatch, and the venue has never heard of
                    # it. Safe to call dead; no position can exist.
                    self._write(
                        replace(
                            intent,
                            state=IntentState.CANCELLED,
                            updated_at=now,
                            last_error="reconciled:venue_has_no_record_of_pending_order",
                        )
                    )
                    resolved.append(intent.client_ref)
                else:
                    still_unknown.append(intent.client_ref)
                continue

            updated = self._apply_ack(intent, ack, now)
            if (updated.broker_ref and not intent.broker_ref) or updated.state is not intent.state:
                resolved.append(intent.client_ref)
            self._write(updated)

            if updated.state.needs_reconciliation:
                still_unknown.append(intent.client_ref)

            venue_size = float(ack.raw.get("filled_size", updated.filled_size) or 0.0)
            if (
                ack.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
                and venue_size > 0
                and abs(venue_size - updated.filled_size) > 1e-9
            ):
                mismatches.append(
                    f"{intent.client_ref}:local={updated.filled_size} venue={venue_size}"
                )

        known_refs = {i.broker_ref for i in self.store.all() if i.broker_ref}
        orphans = tuple(
            sorted(p.venue_ref for p in venue_positions if p.venue_ref and p.venue_ref not in known_refs)
        )

        return ReconciliationReport(
            at=now,
            checked=len(outstanding),
            resolved=tuple(sorted(set(resolved))),
            still_unknown=tuple(sorted(set(still_unknown))),
            orphan_broker_refs=orphans,
            size_mismatches=tuple(sorted(mismatches)),
            errors=tuple(errors),
        )

    # ---------------------------------------------------------- recovery

    def recover(self, *, now: pd.Timestamp | None = None) -> RecoveryReport:
        """Startup recovery: reconcile first, then rebuild the open book.

        Positions come back carrying their broker reference and their stop, so
        a restarted worker can manage them rather than staring at them.
        """
        now = now or pd.Timestamp.now(tz="UTC")
        report = self.reconcile(now=now)

        by_ref: dict[str, OrderIntent] = {
            i.broker_ref: i for i in self.store.all() if i.broker_ref
        }
        recovered: list[RecoveredPosition] = []
        try:
            venue_positions = self.adapter.positions()
        except Exception:
            venue_positions = ()

        for pos in venue_positions:
            ref = pos.venue_ref or ""
            intent = by_ref.get(ref)
            stop = pos.stop_loss or (intent.stop_loss if intent else None)
            if intent is not None and pos.stop_loss in (None, 0.0) and intent.stop_loss:
                pos.stop_loss = float(intent.stop_loss)
            if intent is not None and not pos.strategy_id:
                pos.strategy_id = intent.strategy_id
            recovered.append(
                RecoveredPosition(
                    position=pos,
                    intent=intent,
                    broker_ref=ref,
                    has_local_record=intent is not None,
                    stop_loss=stop,
                )
            )

        unresolved = tuple(
            i for i in self.store.all() if i.state.needs_reconciliation and not i.broker_ref
        )
        return RecoveryReport(now, tuple(recovered), unresolved, report)

    # ---------------------------------------------------------- internals

    def _write(self, intent: OrderIntent) -> None:
        self.store.write(intent)

    def _new_intent(
        self,
        plan: TradePlan,
        client_ref: str,
        now: pd.Timestamp,
        state: IntentState,
        *,
        order_id: str = "",
        last_error: str = "",
        limits_version: str = "",
    ) -> OrderIntent:
        return OrderIntent(
            client_ref=client_ref,
            plan_id=plan.plan_id,
            signal_id=plan.signal.signal_id,
            strategy_id=plan.signal.strategy_id,
            instrument=plan.instrument,
            direction=plan.direction,
            size=plan.size,
            mode=self.mode,
            state=state,
            created_at=now,
            updated_at=now,
            venue=self.adapter.venue_name,
            order_id=order_id,
            stop_loss=plan.signal.stop_price,
            take_profit=(
                plan.signal.take_profit_prices[0] if plan.signal.take_profit_prices else None
            ),
            last_error=last_error,
            limits_version=limits_version,
            extra={
                "risk_amount": plan.risk_amount,
                "sizing_basis": plan.sizing_basis,
                # THE WHOLE LADDER, durably. ``take_profit`` above is the one
                # target a venue can hold; these are every leg the document
                # declared. After a restart they are the only record of what the
                # position was supposed to do -- the manager's in-memory book
                # died with the process -- so a recovered position can have its
                # intent re-derived instead of merely being stared at.
                "take_profit_prices": list(plan.signal.take_profit_prices),
                "take_profit_allocations": list(plan.signal.take_profit_allocations),
            },
        )

    @staticmethod
    def _apply_ack(intent: OrderIntent, ack: OrderAck, now: pd.Timestamp) -> OrderIntent:
        state = {
            OrderStatus.FILLED: IntentState.FILLED,
            OrderStatus.PARTIALLY_FILLED: IntentState.FILLED,
            OrderStatus.ACCEPTED: IntentState.ACKED,
            OrderStatus.REJECTED: IntentState.REJECTED,
            OrderStatus.CANCELLED: IntentState.CANCELLED,
            OrderStatus.UNKNOWN: IntentState.UNKNOWN,
        }[ack.status]
        filled_size = ack.fill.filled_size if ack.fill else float(
            ack.raw.get("filled_size", 0.0) or 0.0
        )
        filled_price = ack.fill.filled_price if ack.fill else ack.raw.get("price")
        return replace(
            intent,
            state=state,
            updated_at=now,
            broker_ref=ack.broker_ref or intent.broker_ref,
            order_id=intent.order_id or ack.order_id,
            filled_size=filled_size or intent.filled_size,
            filled_price=filled_price if filled_price is not None else intent.filled_price,
            last_error=ack.message if ack.status is OrderStatus.REJECTED else intent.last_error,
        )
