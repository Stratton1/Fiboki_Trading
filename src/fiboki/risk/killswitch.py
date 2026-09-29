"""The kill switch, with semantics stated rather than assumed.

V1's kill switch blocked new orders and abandoned open positions. Nobody wrote
down which of the two things it was supposed to do, so it did the first one and
the operator believed it did the second. In a fast market that is the worst
possible combination: you stop hedging and keep the exposure.

V2 forces the choice at activation time. There are exactly two modes and they
mean exactly this:

``PAUSE``
    No NEW risk. Existing positions are left open and KEEP THEIR STOPS. Adding
    to a position, opening a new one, or increasing size is refused. Closing,
    reducing, and stop/target amendments that reduce risk are still permitted,
    because a pause that prevents you from getting flat is a trap.

``FLATTEN``
    Close everything NOW at market, and refuse everything except the closing
    orders themselves. :meth:`KillSwitch.flatten_orders` enumerates the closing
    intents; the execution service dispatches them through the normal ordering
    path so they are recorded, idempotent and recoverable like any other order.

Both modes:
    * are activated with a named operator and a reason,
    * append an immutable record to a journal,
    * and require an EXPLICIT operator deactivation. There is no timeout, no
      auto-reset and no "it clears when the market calms down". A switch that
      turns itself off is not a kill switch.

One journal, re-read before every decision (audit F, P0-1): the operator's
journal is ``fiboki.core.paths.resolve_paths(env).killswitch_journal``
(:meth:`KillSwitch.from_paths`), the same file for the CLI, the API and every
worker, and :meth:`KillSwitch.refresh` re-reads it whenever it changed, so an
activation from another process binds this one's very next decision.
"""
from __future__ import annotations

import json
import tempfile
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.contracts import Position
from fiboki.core.durable import durable_append, read_payloads, touch_durable
from fiboki.core.paths import FibokiPaths

__all__ = [
    "FileKillSwitchJournal",
    "FlattenIntent",
    "InMemoryKillSwitchJournal",
    "KillSwitch",
    "KillSwitchEvent",
    "KillSwitchJournal",
    "KillSwitchMode",
    "KillSwitchState",
    "RequestKind",
]


class KillSwitchMode(str, Enum):
    PAUSE = "pause"
    FLATTEN = "flatten"


class RequestKind(str, Enum):
    """What an order is trying to do, from the kill switch's point of view."""

    OPEN = "open"
    INCREASE = "increase"
    REDUCE = "reduce"
    CLOSE = "close"
    #: A stop/target amendment that strictly reduces risk.
    PROTECTIVE_AMEND = "protective_amend"

    @property
    def adds_risk(self) -> bool:
        return self in (RequestKind.OPEN, RequestKind.INCREASE)


@dataclass(frozen=True, slots=True)
class KillSwitchEvent:
    """One immutable journal row. Append-only: rows are never edited or removed."""

    action: str  # "activate" | "deactivate"
    mode: KillSwitchMode | None
    operator: str
    reason: str
    at: pd.Timestamp
    positions_open: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        d = asdict(self)
        d["mode"] = self.mode.value if self.mode else None
        d["at"] = self.at.isoformat()
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_json(line: str) -> KillSwitchEvent:
        d = json.loads(line)
        return KillSwitchEvent(
            action=d["action"],
            mode=KillSwitchMode(d["mode"]) if d.get("mode") else None,
            operator=d["operator"],
            reason=d["reason"],
            at=pd.Timestamp(d["at"]),
            positions_open=int(d.get("positions_open", 0)),
            extra=dict(d.get("extra") or {}),
        )


class KillSwitchJournal:
    """Append-only store of activations and deactivations."""

    #: Does an activation written here survive the process and reach every
    #: other process? Only a durable journal may guard anything but a backtest.
    durable: bool = False

    def append(self, event: KillSwitchEvent) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def events(self) -> list[KillSwitchEvent]:  # pragma: no cover - interface
        raise NotImplementedError

    def fingerprint(self) -> Hashable:
        """Cheap token that changes whenever the journal may have changed.

        :meth:`KillSwitch.refresh` compares it before every decision and
        replays only when it moved.
        """
        return None


class InMemoryKillSwitchJournal(KillSwitchJournal):
    """Process-local journal. Legitimate for BACKTEST and unit tests ONLY.

    An activation written here is invisible to every other process, which is
    why :class:`fiboki.risk.gateway.RiskGateway` refuses one outside BACKTEST.
    """

    durable = False

    def __init__(self) -> None:
        self._events: list[KillSwitchEvent] = []

    def append(self, event: KillSwitchEvent) -> None:
        self._events.append(event)

    def events(self) -> list[KillSwitchEvent]:
        return list(self._events)

    def fingerprint(self) -> Hashable:
        return ("memory", len(self._events))


class FileKillSwitchJournal(KillSwitchJournal):
    """Self-verifying JSON-lines journal, made durable on every append.

    The switch's state must survive the crash that made you hit it, so each
    append goes through :func:`fiboki.core.durable.durable_append`: an
    exclusive lock, a CRC-framed line, ``F_FULLFSYNC`` on macOS (plain
    ``fsync`` elsewhere) and a directory ``fsync`` when the file is created.
    An unflushed kill switch is not a kill switch.

    A torn final line (a crash mid-append) is quarantined to
    ``<file>.torn-<ts>`` and reported CRITICAL rather than raising, so the
    switch can still be READ after the crash. Unframed lines written before
    framing existed are still accepted.
    """

    durable = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        touch_durable(self.path)

    def append(self, event: KillSwitchEvent) -> None:
        durable_append(self.path, event.to_json())

    def events(self) -> list[KillSwitchEvent]:
        return [KillSwitchEvent.from_json(line) for line in read_payloads(self.path)]

    def fingerprint(self) -> Hashable:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return ("missing",)
        return (st.st_ino, st.st_size, st.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    active: bool
    mode: KillSwitchMode | None
    operator: str | None
    reason: str | None
    since: pd.Timestamp | None

    @property
    def blocks_new_risk(self) -> bool:
        return self.active

    @property
    def requires_flatten(self) -> bool:
        return self.active and self.mode is KillSwitchMode.FLATTEN


@dataclass(frozen=True, slots=True)
class FlattenIntent:
    """A single close-everything instruction produced by FLATTEN mode."""

    position_id: str
    instrument: str
    size: float
    strategy_id: str
    venue_ref: str | None
    reason: str


class KillSwitch:
    """Operator-controlled halt. State is recovered from the journal on startup."""

    def __init__(self, journal: KillSwitchJournal | None = None) -> None:
        self.journal = journal or InMemoryKillSwitchJournal()
        self._fingerprint: Hashable = self.journal.fingerprint()
        self._state = self._replay()

    @classmethod
    def at_path(cls, path: str | Path) -> KillSwitch:
        """A switch over the durable journal at ``path``."""
        return cls(FileKillSwitchJournal(path))

    @classmethod
    def from_paths(cls, paths: FibokiPaths) -> KillSwitch:
        """THE operator kill switch: the journal every process resolves.

        Built from :func:`fiboki.core.paths.resolve_paths`, which is what the
        CLI, the API settings and the worker compositions use, so an
        activation from any one of them is seen by all of them.
        """
        return cls.at_path(paths.killswitch_journal)

    # ------------------------------------------------------------- state

    def _replay(self) -> KillSwitchState:
        state = KillSwitchState(False, None, None, None, None)
        for ev in self.journal.events():
            if ev.action == "activate":
                state = KillSwitchState(True, ev.mode, ev.operator, ev.reason, ev.at)
            elif ev.action == "deactivate":
                state = KillSwitchState(False, None, None, None, None)
        return state

    def refresh(self) -> KillSwitchState:
        """Re-read the journal if it changed since this instance last looked.

        The check is a ``stat`` (inode, size, mtime), so it is cheap enough to
        run before every decision, and it is what makes an activation written
        by ANOTHER process (``fiboki killswitch pause`` in a terminal, the API)
        bind this process's very next order. Before this existed the journal
        was replayed once, in ``__init__``, and a running worker kept trading
        through an activation until it was restarted.
        """
        current = self.journal.fingerprint()
        if current != self._fingerprint:
            self._state = self._replay()
            self._fingerprint = current
        return self._state

    @property
    def durable(self) -> bool:
        return bool(getattr(self.journal, "durable", False))

    @property
    def state(self) -> KillSwitchState:
        return self.refresh()

    @property
    def active(self) -> bool:
        return self.refresh().active

    @property
    def mode(self) -> KillSwitchMode | None:
        return self.refresh().mode

    def _append(self, event: KillSwitchEvent) -> KillSwitchState:
        self.journal.append(event)
        # Replay rather than assume: the journal, not this object, is the state.
        self._state = self._replay()
        self._fingerprint = self.journal.fingerprint()
        return self._state

    # -------------------------------------------------------- transitions

    def activate(
        self,
        mode: KillSwitchMode,
        *,
        operator: str,
        reason: str,
        at: pd.Timestamp | None = None,
        positions_open: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> KillSwitchState:
        """Halt trading. ``mode`` is mandatory and has no default, on purpose.

        Escalating PAUSE -> FLATTEN is allowed and journalled. De-escalating
        FLATTEN -> PAUSE is refused: once the decision to get flat has been
        taken, softening it must go through an explicit deactivation so that
        two separate operator actions are on the record.
        """
        if not isinstance(mode, KillSwitchMode):
            raise TypeError("KillSwitch.activate requires an explicit KillSwitchMode")
        if not operator or not reason:
            raise ValueError("Kill switch activation requires an operator and a reason")
        # Another process may have escalated since this instance last looked.
        self.refresh()
        if (
            self._state.active
            and self._state.mode is KillSwitchMode.FLATTEN
            and mode is KillSwitchMode.PAUSE
        ):
            raise ValueError(
                "Refusing to downgrade FLATTEN to PAUSE. Deactivate explicitly "
                "first; a single call must never quietly re-arm the book."
            )
        ts = at or pd.Timestamp.now(tz="UTC")
        ev = KillSwitchEvent(
            action="activate",
            mode=mode,
            operator=operator,
            reason=reason,
            at=ts,
            positions_open=positions_open,
            extra=dict(extra or {}),
        )
        return self._append(ev)

    def deactivate(
        self,
        *,
        operator: str,
        reason: str,
        at: pd.Timestamp | None = None,
        extra: dict[str, Any] | None = None,
    ) -> KillSwitchState:
        """Re-arm trading. Requires an explicit operator action; never automatic."""
        self.refresh()
        if not self._state.active:
            raise ValueError("Kill switch is not active; nothing to deactivate")
        if not operator or not reason:
            raise ValueError("Kill switch deactivation requires an operator and a reason")
        ts = at or pd.Timestamp.now(tz="UTC")
        ev = KillSwitchEvent(
            action="deactivate",
            mode=self._state.mode,
            operator=operator,
            reason=reason,
            at=ts,
            extra=dict(extra or {}),
        )
        return self._append(ev)

    # ----------------------------------------------------------- queries

    def allows(self, kind: RequestKind) -> tuple[bool, str]:
        """Does the switch permit a request of this kind? Returns ``(ok, reason)``.

        Refreshes from the journal first, so the answer reflects an activation
        written by any process up to this call.
        """
        self.refresh()
        if not self._state.active:
            return True, "kill_switch_inactive"
        mode = self._state.mode
        if mode is KillSwitchMode.PAUSE:
            if kind.adds_risk:
                return False, "kill_switch_pause_blocks_new_risk"
            # Reducing, closing and protective amendments stay open. A pause
            # that stops you getting flat is a trap, not a control.
            return True, "kill_switch_pause_permits_risk_reduction"
        if mode is KillSwitchMode.FLATTEN:
            if kind in (RequestKind.CLOSE, RequestKind.REDUCE):
                return True, "kill_switch_flatten_permits_closing"
            return False, "kill_switch_flatten_blocks_non_closing"
        return False, "kill_switch_unknown_mode"  # pragma: no cover - defensive

    def flatten_orders(
        self, positions: Iterable[Position], *, reason: str | None = None
    ) -> tuple[FlattenIntent, ...]:
        """Enumerate the closing intents FLATTEN mode implies.

        Deterministically ordered by instrument then position id so a restart
        mid-flatten resumes in the same sequence. Returns empty unless the
        switch is active in FLATTEN mode -- this method cannot be used to
        abandon positions by accident, and cannot be used to flatten while
        merely paused.
        """
        self.refresh()
        if not self._state.active or self._state.mode is not KillSwitchMode.FLATTEN:
            return ()
        why = reason or f"kill_switch_flatten:{self._state.reason}"
        ordered: Sequence[Position] = sorted(
            positions, key=lambda p: (p.instrument, p.position_id)
        )
        return tuple(
            FlattenIntent(
                position_id=p.position_id,
                instrument=p.instrument,
                size=p.size,
                strategy_id=p.strategy_id,
                venue_ref=p.venue_ref,
                reason=why,
            )
            for p in ordered
        )

    def history(self) -> list[KillSwitchEvent]:
        return self.journal.events()


def temp_journal() -> FileKillSwitchJournal:  # pragma: no cover - test convenience
    return FileKillSwitchJournal(Path(tempfile.mkdtemp()) / "killswitch.jsonl")
