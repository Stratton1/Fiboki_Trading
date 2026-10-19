"""Per-bot pause / resume / drain: a durable control ledger the paper runtime obeys.

What it is for
--------------
The kill switch is fleet-wide and blunt: PAUSE stops every bot's new entries,
FLATTEN closes every position. An operator who wants ONE bot to stop opening
positions (a strategy behaving oddly, a news week on one instrument, a review
pending) had no tool short of stopping the process, which abandons nothing
today only because the state snapshot restores it, and which leaves no record
of who did it or why.

The states
----------
``active``    the bot trades as its wiring says.
``paused``    no NEW entries. Open positions keep being managed: stops, trails,
              targets, time stops and reversal closes run exactly as before,
              because they are the book's own exit rules. Nothing is closed.
``draining``  as ``paused``, and when the book is flat (no open position, no
              entry the venue filled that the book has still to model, no close
              the venue has still to confirm) the runtime records
              ``drain_complete`` and the bot becomes ``stopped``.
``stopped``   flat and accepting no entries. The process keeps polling and
              marking bars, so the journal has no gap; ``resume`` makes it
              ``active`` again.

Transitions (anything else is refused, with the reason, and the refusal is
audited by the API like an allowed act)::

    active   --pause-->  paused      --resume--> active
    active   --drain-->  draining    --resume--> active
    paused   --drain-->  draining    --pause-->  paused
    draining --drain_complete (runtime only)--> stopped --resume--> active

Every record carries the operator, a REQUIRED reason (8 to 500 characters, the
kill switch's bounds) and the UTC time. The ledger is ONE append-only file
(``core.paths.FibokiPaths.bot_controls``) written through
:func:`fiboki.core.durable.durable_append`, so it survives a restart, a torn
final line is quarantined rather than fatal, and two writers (the API and a
runtime recording ``drain_complete``) are serialised by the same lock. The
state of a bot is the replay of its records; nothing is cached across
processes, so a restarted runtime is paused exactly when its bot is.

What a control can NOT do
-------------------------
It cannot add risk: no state permits more than ``active``. It cannot close a
position or move a stop. It is not a kill switch and does not replace one; the
kill switch is read independently and wins.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fiboki.core.durable import durable_append, read_payloads

__all__ = [
    "CONTROL_SCHEMA",
    "REASON_MAX",
    "REASON_MIN",
    "RUNTIME_ACTOR",
    "BotControlLedger",
    "BotControlState",
    "BotState",
    "ControlAction",
    "ControlRecord",
    "ControlRefused",
    "forward_session_id",
]

CONTROL_SCHEMA = "fiboki.bot_control/1"
#: The kill switch's reason bounds (``api/routers/system.KillSwitchRequest``).
REASON_MIN = 8
REASON_MAX = 500
#: The actor of a record the paper runtime writes itself (``drain_complete``).
RUNTIME_ACTOR = "paper-runtime"


def forward_session_id(wiring_version: str) -> str:
    """The session id a forward paper runtime writes under (``forward-<version>``)."""
    return f"forward-{wiring_version}"


class ControlAction(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    DRAIN = "drain"
    #: Written by the runtime, never by an operator: the drain reached flat.
    DRAIN_COMPLETE = "drain_complete"


class BotState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DRAINING = "draining"
    STOPPED = "stopped"

    @property
    def blocks_entries(self) -> bool:
        return self is not BotState.ACTIVE


#: (state, action) -> next state. Absent pairs are refused.
_TRANSITIONS: dict[tuple[BotState, ControlAction], BotState] = {
    (BotState.ACTIVE, ControlAction.PAUSE): BotState.PAUSED,
    (BotState.ACTIVE, ControlAction.DRAIN): BotState.DRAINING,
    (BotState.PAUSED, ControlAction.RESUME): BotState.ACTIVE,
    (BotState.PAUSED, ControlAction.DRAIN): BotState.DRAINING,
    (BotState.DRAINING, ControlAction.RESUME): BotState.ACTIVE,
    (BotState.DRAINING, ControlAction.PAUSE): BotState.PAUSED,
    (BotState.DRAINING, ControlAction.DRAIN_COMPLETE): BotState.STOPPED,
    (BotState.STOPPED, ControlAction.RESUME): BotState.ACTIVE,
}

#: Which actions an operator may request from each state (for the UI and API).
OPERATOR_ACTIONS = (ControlAction.PAUSE, ControlAction.RESUME, ControlAction.DRAIN)


class ControlRefused(ValueError):
    """The requested transition is not allowed from the bot's current state."""

    def __init__(self, message: str, *, state: BotState, action: str) -> None:
        super().__init__(message)
        self.state = state
        self.action = action


@dataclass(frozen=True, slots=True)
class ControlRecord:
    session_id: str
    action: ControlAction
    state_before: BotState
    state_after: BotState
    actor: str
    actor_role: str
    reason: str
    at: datetime
    correlation_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["schema"] = CONTROL_SCHEMA
        row["action"] = self.action.value
        row["state_before"] = self.state_before.value
        row["state_after"] = self.state_after.value
        row["at"] = self.at.astimezone(UTC).isoformat()
        return row

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ControlRecord:
        at = datetime.fromisoformat(str(row["at"]).replace("Z", "+00:00"))
        return cls(
            session_id=str(row["session_id"]),
            action=ControlAction(str(row["action"])),
            state_before=BotState(str(row["state_before"])),
            state_after=BotState(str(row["state_after"])),
            actor=str(row.get("actor", "")),
            actor_role=str(row.get("actor_role", "")),
            reason=str(row.get("reason", "")),
            at=at if at.tzinfo else at.replace(tzinfo=UTC),
            correlation_id=str(row.get("correlation_id", "")),
            detail=dict(row.get("detail") or {}),
        )


@dataclass(frozen=True, slots=True)
class BotControlState:
    """One bot's control state, replayed from its records."""

    session_id: str
    state: BotState
    #: The record that put the bot in this state; ``None`` for a bot never controlled.
    last: ControlRecord | None
    #: How many records this bot has (the runtime uses it to journal new ones once).
    records: int

    @property
    def blocks_entries(self) -> bool:
        return self.state.blocks_entries

    def allowed_actions(self) -> list[str]:
        return [a.value for a in OPERATOR_ACTIONS if (self.state, a) in _TRANSITIONS]

    def summary(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "since": None if self.last is None else self.last.at.isoformat(),
            "actor": None if self.last is None else self.last.actor,
            "reason": None if self.last is None else self.last.reason,
            "records": self.records,
        }


def _validate_reason(reason: str) -> str:
    text = (reason or "").strip()
    if not REASON_MIN <= len(text) <= REASON_MAX:
        raise ValueError(
            f"a control needs a reason of {REASON_MIN} to {REASON_MAX} characters; "
            f"got {len(text)}. A bot paused with no recorded reason is an unexplained "
            "outage later."
        )
    return text


class BotControlLedger:
    """The append-only control file. Replayed on every read; never cached across processes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- reads ---------------------------------------------------------------

    def records(self, session_id: str | None = None) -> list[ControlRecord]:
        if not self.path.exists():
            return []
        out: list[ControlRecord] = []
        for payload in read_payloads(self.path):
            # A line this code cannot read raises (KeyError/ValueError): it is
            # never silently a no-op, because a skipped PAUSE is a bot trading.
            record = ControlRecord.from_row(json.loads(payload))
            if session_id is None or record.session_id == session_id:
                out.append(record)
        return out

    def state(self, session_id: str) -> BotControlState:
        return _replay(session_id, self.records(session_id))

    def states(self) -> dict[str, BotControlState]:
        by: dict[str, list[ControlRecord]] = {}
        for record in self.records():
            by.setdefault(record.session_id, []).append(record)
        return {sid: _replay(sid, rows) for sid, rows in by.items()}

    def signature(self) -> tuple[int, int] | None:
        """(size, mtime_ns) of the file: a cheap "has anything changed?" probe."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return (st.st_size, st.st_mtime_ns)

    # -- writes ----------------------------------------------------------------

    def request(
        self,
        session_id: str,
        action: ControlAction | str,
        *,
        actor: str,
        actor_role: str,
        reason: str,
        at: datetime | None = None,
        correlation_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> ControlRecord:
        """An OPERATOR's request. Refused unless the transition is allowed."""
        act = ControlAction(action)
        if act is ControlAction.DRAIN_COMPLETE:
            raise ControlRefused(
                "drain_complete is recorded by the paper runtime when the book is flat; "
                "an operator cannot declare a bot flat.",
                state=self.state(session_id).state,
                action=act.value,
            )
        if not actor:
            raise ValueError("a control must name the operator who requested it")
        return self._append(
            session_id, act, actor=actor, actor_role=actor_role,
            reason=_validate_reason(reason), at=at, correlation_id=correlation_id,
            detail=detail,
        )

    def complete_drain(
        self, session_id: str, *, at: datetime | None = None, detail: dict[str, Any] | None = None
    ) -> ControlRecord:
        """The RUNTIME's record that a draining bot is flat. Refused unless draining."""
        return self._append(
            session_id,
            ControlAction.DRAIN_COMPLETE,
            actor=RUNTIME_ACTOR,
            actor_role="runtime",
            reason="the book is flat: no open position, no pending entry, no unconfirmed close",
            at=at,
            detail=detail,
        )

    def _append(
        self,
        session_id: str,
        action: ControlAction,
        *,
        actor: str,
        actor_role: str,
        reason: str,
        at: datetime | None,
        correlation_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> ControlRecord:
        if not session_id:
            raise ValueError("a control must name the bot (session id)")
        current = self.state(session_id).state
        nxt = _TRANSITIONS.get((current, action))
        if nxt is None:
            raise ControlRefused(
                f"{session_id} is {current.value}; {action.value} is not allowed from "
                f"{current.value}. Allowed: "
                + (", ".join(a.value for a in OPERATOR_ACTIONS if (current, a) in _TRANSITIONS) or "none"),
                state=current,
                action=action.value,
            )
        stamp = (at or datetime.now(tz=UTC)).astimezone(UTC)
        record = ControlRecord(
            session_id=session_id,
            action=action,
            state_before=current,
            state_after=nxt,
            actor=actor,
            actor_role=actor_role,
            reason=reason,
            at=stamp,
            correlation_id=correlation_id,
            detail=dict(detail or {}),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        durable_append(self.path, json.dumps(record.as_row(), sort_keys=True, default=str))
        return record


def _replay(session_id: str, rows: Iterable[ControlRecord]) -> BotControlState:
    state = BotState.ACTIVE
    last: ControlRecord | None = None
    n = 0
    for record in rows:
        n += 1
        # The file is the authority: the state is the last record's state_after,
        # as written. Transitions are validated when a record is APPENDED.
        state = record.state_after
        last = record
    return BotControlState(session_id=session_id, state=state, last=last, records=n)
