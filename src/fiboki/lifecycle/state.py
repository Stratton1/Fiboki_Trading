"""The lifecycle state machine: who may move a strategy where, and on what evidence.

``StrategyLifecycle`` has existed in :mod:`fiboki.core.enums` since the beginning.
The risk gateway blocks seven of its states and portfolio construction allocates
to six, both tested -- and yet **nothing owned a transition**. No module decided
that a strategy was now PAPER rather than CANDIDATE, nothing refused a promotion
that skipped a stage, and nothing wrote down who decided or why. The enum was
consumed and unowned, which is the shape of a control that looks present and is
not. This module is the owner.

Three properties, each of which is a defect prevented
-----------------------------------------------------
**Promotion cannot skip a stage.** ``LEGAL_TRANSITIONS`` is data, not ``if``
statements, and it contains exactly the adjacent pairs up the ladder. CANDIDATE
to DEMO is not a fast path, it is a ``IllegalTransition``. A demotion, by
contrast, may drop several rungs at once: getting out is always allowed to be
faster than getting in.

**Nothing automated can reach LIVE.** Two independent controls. The transition
table marks ``APPROVED -> LIVE`` as requiring a human authorisation record; and
:meth:`LifecycleStateMachine.transition` refuses *any* transition whose target is
LIVE unless the acting party is a human AND a matching
:class:`HumanAuthorisation` is among the evidence. The second check does not
consult the table, so a future edit that loosened the table would still not open
a path. An :class:`Actor` of kind ``AUTOMATED_RULE`` cannot construct a valid
authorisation for itself: the authorisation records a named human, and the
machine checks the actor's kind separately.

**The history cannot be tidied.** Every transition is appended to a hash-chained
log. Each record commits to its predecessor's hash, so editing any earlier row
invalidates every row after it, and :meth:`TransitionLog.verify` names the first
broken link. This is the same reasoning as the experiment ledger: the value of
the record is precisely that it contains the decisions nobody liked.

What this module does NOT do
----------------------------
It does not decide whether a promotion is *deserved* -- that is
:mod:`fiboki.lifecycle.promotion`, which evaluates versioned criteria and hands
back a decision. It does not compute degradation, and it performs no I/O beyond
the log it is given. It is a machine over data, which is what makes it
exhaustively testable.

A note on a field this module deliberately does not add
--------------------------------------------------------
:class:`~fiboki.strategy.dsl.StrategyDocument` still has no lifecycle field, and
it should not get one: the document is content-addressed and frozen, so a
lifecycle stored on it would change the content hash every time the strategy was
promoted, and the holdout registry and the experiment ledger key on that hash.
Lifecycle is a property of the *deployment* of a document, not of the document,
and it lives here on :class:`StrategyRecord`, bound to the content hash.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fiboki.core.enums import StrategyLifecycle
from fiboki.validation.report import canonicalise

__all__ = [
    "GENESIS_HASH",
    "HEALTH_STATES",
    "LADDER",
    "LEGAL_TRANSITIONS",
    "RUNNING_STATES",
    "TERMINAL_STATES",
    "Actor",
    "ActorKind",
    "ChainVerification",
    "Evidence",
    "EvidenceKind",
    "FileTransitionLog",
    "HumanAuthorisation",
    "HumanAuthorisationRequired",
    "IllegalTransition",
    "InMemoryTransitionLog",
    "LifecycleError",
    "LifecycleStateMachine",
    "LifecycleTransition",
    "StrategyRecord",
    "TransitionKind",
    "TransitionLog",
    "TransitionRecord",
    "UnknownStrategy",
    "ladder_index",
    "transition_table",
]

GENESIS_HASH = "0" * 64
"""``previous_hash`` of the first record in a chain. Never a real digest."""


# ==========================================================================
# Errors
# ==========================================================================


class LifecycleError(RuntimeError):
    """Base class for every way a lifecycle operation can be refused."""


class IllegalTransition(LifecycleError):
    """The requested (from, to) pair is not in :data:`LEGAL_TRANSITIONS`."""

    def __init__(
        self,
        from_state: StrategyLifecycle,
        to_state: StrategyLifecycle,
        detail: str = "",
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        tail = f": {detail}" if detail else ""
        super().__init__(
            f"illegal lifecycle transition {from_state.value} -> {to_state.value}{tail}"
        )


class HumanAuthorisationRequired(LifecycleError):
    """A transition that only a named human may make was attempted without one."""


class UnknownStrategy(LifecycleError):
    """No record for this content hash. Deliberately not a default state.

    A strategy nobody registered has no lifecycle, and returning DISCOVERY here
    would make absence look like a value -- the caller would believe it had
    asked and been answered.
    """

    def __init__(self, strategy_content_hash: str) -> None:
        self.strategy_content_hash = strategy_content_hash
        super().__init__(
            f"no lifecycle record for strategy content hash "
            f"{strategy_content_hash[:12]!r}; register it before asking for its state"
        )


class TamperedLog(LifecycleError):
    """The hash chain does not verify."""


# ==========================================================================
# Actors and evidence
# ==========================================================================


class ActorKind(str, Enum):
    """Who made a transition happen.

    ``AUTOMATED_RULE`` is deliberately distinct from ``SYSTEM``: a named,
    pre-registered stopping rule that demotes a strategy is a different kind of
    thing from a migration or a replay, and an operator reading the history must
    be able to tell them apart without parsing a name.
    """

    HUMAN = "human"
    AUTOMATED_RULE = "automated_rule"
    SYSTEM = "system"

    @property
    def is_automatic(self) -> bool:
        return self is not ActorKind.HUMAN


@dataclass(frozen=True, slots=True)
class Actor:
    """A named party. "The pipeline" is not an actor; a rule name is."""

    kind: ActorKind
    name: str

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ValueError(
                "actor name is mandatory: a transition nobody is named for "
                "cannot be followed up"
            )
        if self.kind is ActorKind.AUTOMATED_RULE and ":" not in self.name:
            raise ValueError(
                "an automated rule must name itself as '<family>:<rule>' "
                f"(got {self.name!r}), so the history says WHICH rule fired"
            )

    @property
    def is_automatic(self) -> bool:
        return self.kind.is_automatic

    @classmethod
    def human(cls, name: str) -> Actor:
        return cls(ActorKind.HUMAN, name)

    @classmethod
    def rule(cls, name: str) -> Actor:
        return cls(ActorKind.AUTOMATED_RULE, name)

    @classmethod
    def system(cls, name: str) -> Actor:
        return cls(ActorKind.SYSTEM, name)

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "name": self.name}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Actor:
        return cls(ActorKind(raw["kind"]), str(raw["name"]))


class EvidenceKind(str, Enum):
    VALIDATION_REPORT = "validation_report"
    MONITOR_RESULT = "monitor_result"
    STOPPING_RULE = "stopping_rule"
    PROMOTION_EVALUATION = "promotion_evaluation"
    PRE_REGISTRATION = "pre_registration"
    HUMAN_AUTHORISATION = "human_authorisation"
    OPERATOR_NOTE = "operator_note"


@dataclass(frozen=True, slots=True)
class Evidence:
    """What justified a transition. A transition with no evidence is refused."""

    kind: EvidenceKind
    id: str
    """Report content hash, monitor result id, registration id, or a note id."""
    summary: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError(f"{self.kind.value} evidence needs an id to point at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "id": self.id,
            "summary": self.summary,
            "detail": canonicalise(dict(self.detail)),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Evidence:
        return cls(
            kind=EvidenceKind(raw["kind"]),
            id=str(raw["id"]),
            summary=str(raw.get("summary", "")),
            detail=dict(raw.get("detail", {})),
        )

    @classmethod
    def note(cls, note_id: str, summary: str, **detail: Any) -> Evidence:
        return cls(EvidenceKind.OPERATOR_NOTE, note_id, summary, detail)


@dataclass(frozen=True, slots=True)
class HumanAuthorisation:
    """A named human's written authorisation for ONE strategy into ONE state.

    Bound to the strategy content hash and to the target state on purpose. An
    authorisation that said only "Joe approves" could be replayed against a
    different strategy, or against a later, different version of the same one --
    and the content hash is exactly what distinguishes two bindings of the same
    template. The machine checks :meth:`matches` before it will act.

    This object records a decision. It is **not** a credential and confers no
    capability: only a human :class:`Actor` may present one, and the machine
    checks the actor's kind independently.
    """

    authorised_by: str
    strategy_content_hash: str
    to_state: StrategyLifecycle
    statement: str
    """Why, in the authoriser's own words. Minimum length is enforced because
    "approved" is not a reason anyone can audit two years later."""
    authorised_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    reviewed_report_hash: str = ""
    ticket: str = ""

    MIN_STATEMENT_CHARS = 24

    def __post_init__(self) -> None:
        if not str(self.authorised_by).strip():
            raise ValueError("a human authorisation must name the human")
        if not str(self.strategy_content_hash).strip():
            raise ValueError("a human authorisation must name the strategy content hash")
        if len(str(self.statement).strip()) < self.MIN_STATEMENT_CHARS:
            raise ValueError(
                "a human authorisation must state WHY in at least "
                f"{self.MIN_STATEMENT_CHARS} characters; "
                f"got {len(str(self.statement).strip())}"
            )

    def matches(self, strategy_content_hash: str, to_state: StrategyLifecycle) -> bool:
        return (
            self.strategy_content_hash == strategy_content_hash
            and self.to_state is to_state
        )

    def as_evidence(self) -> Evidence:
        return Evidence(
            kind=EvidenceKind.HUMAN_AUTHORISATION,
            id=f"auth:{self.authorised_by}:{self.to_state.value}",
            summary=self.statement,
            detail=self.to_dict(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorised_by": self.authorised_by,
            "strategy_content_hash": self.strategy_content_hash,
            "to_state": self.to_state.value,
            "statement": self.statement,
            "authorised_at": self.authorised_at.astimezone(UTC).isoformat(),
            "reviewed_report_hash": self.reviewed_report_hash,
            "ticket": self.ticket,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> HumanAuthorisation:
        return cls(
            authorised_by=str(raw["authorised_by"]),
            strategy_content_hash=str(raw["strategy_content_hash"]),
            to_state=StrategyLifecycle(raw["to_state"]),
            statement=str(raw["statement"]),
            authorised_at=datetime.fromisoformat(str(raw["authorised_at"])),
            reviewed_report_hash=str(raw.get("reviewed_report_hash", "")),
            ticket=str(raw.get("ticket", "")),
        )


def _authorisation_in(evidence: Sequence[Evidence]) -> HumanAuthorisation | None:
    for item in evidence:
        if item.kind is not EvidenceKind.HUMAN_AUTHORISATION:
            continue
        try:
            return HumanAuthorisation.from_dict(item.detail)
        except (KeyError, ValueError):
            continue
    return None


# ==========================================================================
# The transition table
# ==========================================================================

LADDER: tuple[StrategyLifecycle, ...] = (
    StrategyLifecycle.DISCOVERY,
    StrategyLifecycle.RESEARCH,
    StrategyLifecycle.VALIDATING,
    StrategyLifecycle.CANDIDATE,
    StrategyLifecycle.PAPER,
    StrategyLifecycle.SHADOW,
    StrategyLifecycle.DEMO,
    StrategyLifecycle.APPROVED,
    StrategyLifecycle.LIVE,
)
"""The promotion ladder, in order. Promotion moves exactly one step along it."""

RUNNING_STATES: tuple[StrategyLifecycle, ...] = (
    StrategyLifecycle.PAPER,
    StrategyLifecycle.SHADOW,
    StrategyLifecycle.DEMO,
    StrategyLifecycle.APPROVED,
    StrategyLifecycle.LIVE,
)
"""States in which a strategy is producing forward observations to monitor."""

HEALTH_STATES: tuple[StrategyLifecycle, ...] = (
    StrategyLifecycle.WATCH,
    StrategyLifecycle.DEGRADED,
    StrategyLifecycle.QUARANTINED,
)
"""Off-ladder states a running strategy is moved into when it misbehaves."""

TERMINAL_STATES: tuple[StrategyLifecycle, ...] = (StrategyLifecycle.RETIRED,)
"""RETIRED has no outgoing transition. Reviving an idea means a NEW content hash
and a new record, so the retired one's history stays exactly as it was."""

_PRE_RUNNING: tuple[StrategyLifecycle, ...] = (
    StrategyLifecycle.DISCOVERY,
    StrategyLifecycle.RESEARCH,
    StrategyLifecycle.VALIDATING,
    StrategyLifecycle.CANDIDATE,
)


def ladder_index(state: StrategyLifecycle) -> int:
    """Position on the ladder, or ``-1`` for an off-ladder state."""
    return LADDER.index(state) if state in LADDER else -1


class TransitionKind(str, Enum):
    REGISTRATION = "registration"
    """The one self-transition: a strategy entering the system at DISCOVERY."""
    PROMOTION = "promotion"
    DEMOTION = "demotion"
    HEALTH_ESCALATION = "health_escalation"
    RECOVERY = "recovery"
    RETIREMENT = "retirement"

    @property
    def adds_risk(self) -> bool:
        """True when the move lets the strategy trade more, or at all."""
        return self in (TransitionKind.PROMOTION, TransitionKind.RECOVERY)


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """One legal edge of the state machine, with the reason it exists."""

    from_state: StrategyLifecycle
    to_state: StrategyLifecycle
    kind: TransitionKind
    requires_human_actor: bool = False
    """The acting party's kind must be HUMAN. Every move that ADDS risk, plus
    retirement, sets this: an automated rule may take risk away and may never
    give it back."""
    requires_human_authorisation: bool = False
    """A :class:`HumanAuthorisation` matching this strategy and target state must
    be among the evidence. Set only on transitions into LIVE."""
    rationale: str = ""

    @property
    def key(self) -> tuple[StrategyLifecycle, StrategyLifecycle]:
        return (self.from_state, self.to_state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_state.value,
            "to": self.to_state.value,
            "kind": self.kind.value,
            "requires_human_actor": self.requires_human_actor,
            "requires_human_authorisation": self.requires_human_authorisation,
            "rationale": self.rationale,
        }


def _build_transitions() -> tuple[LifecycleTransition, ...]:
    out: list[LifecycleTransition] = []

    # Registration. The only self-transition, and it is not reachable through
    # `transition()` -- `register()` writes it.
    out.append(
        LifecycleTransition(
            StrategyLifecycle.DISCOVERY,
            StrategyLifecycle.DISCOVERY,
            TransitionKind.REGISTRATION,
            rationale="a strategy enters the system at DISCOVERY and nowhere else",
        )
    )

    # Promotions: ADJACENT PAIRS ONLY. This is the rule that makes the ladder a
    # ladder rather than a suggestion.
    #
    # Inside the laboratory (DISCOVERY -> RESEARCH -> VALIDATING -> CANDIDATE)
    # the research loop may promote: nothing has been allocated risk yet, and the
    # ladder in `validation/` already produces the evidence. From CANDIDATE
    # onwards every promotion puts a strategy somewhere that generates orders, so
    # every one of them requires a named human.
    for lower, upper in itertools.pairwise(LADDER):
        out.append(
            LifecycleTransition(
                lower,
                upper,
                TransitionKind.PROMOTION,
                requires_human_actor=upper in RUNNING_STATES,
                requires_human_authorisation=upper is StrategyLifecycle.LIVE,
                rationale=(
                    "each stage answers a question the previous one could not; "
                    "skipping one means promoting on evidence that was never gathered"
                ),
            )
        )

    # Demotions: ANY distance down the ladder. Getting out is allowed to be
    # faster than getting in, and an automated rule may do it.
    for i, higher in enumerate(LADDER):
        for lower in LADDER[:i]:
            out.append(
                LifecycleTransition(
                    higher,
                    lower,
                    TransitionKind.DEMOTION,
                    rationale=(
                        "a demotion may drop several stages at once: the evidence "
                        "that justified the higher stages is what has just failed"
                    ),
                )
            )

    # Health escalation off the ladder, and between the health states.
    for running in RUNNING_STATES:
        for health in HEALTH_STATES:
            out.append(
                LifecycleTransition(
                    running,
                    health,
                    TransitionKind.HEALTH_ESCALATION,
                    rationale="forward behaviour diverged from expectation",
                )
            )
    for higher, lower in (
        (StrategyLifecycle.WATCH, StrategyLifecycle.DEGRADED),
        (StrategyLifecycle.WATCH, StrategyLifecycle.QUARANTINED),
        (StrategyLifecycle.DEGRADED, StrategyLifecycle.QUARANTINED),
    ):
        out.append(
            LifecycleTransition(
                higher,
                lower,
                TransitionKind.HEALTH_ESCALATION,
                rationale="degradation deepened past the next band",
            )
        )

    # Recovery: back onto the ladder, or up the health states. ALWAYS a human.
    # This is the "explicit operator action to reverse" that the stopping rules
    # require, expressed structurally rather than as a docstring.
    for health in HEALTH_STATES:
        for running in RUNNING_STATES:
            out.append(
                LifecycleTransition(
                    health,
                    running,
                    TransitionKind.RECOVERY,
                    requires_human_actor=True,
                    requires_human_authorisation=running is StrategyLifecycle.LIVE,
                    rationale=(
                        "a halt reverses only by an explicit operator action; a "
                        "monitor that can un-halt what it halted is not a halt"
                    ),
                )
            )
    for deeper, shallower in (
        (StrategyLifecycle.DEGRADED, StrategyLifecycle.WATCH),
        (StrategyLifecycle.QUARANTINED, StrategyLifecycle.WATCH),
        (StrategyLifecycle.QUARANTINED, StrategyLifecycle.DEGRADED),
    ):
        out.append(
            LifecycleTransition(
                deeper,
                shallower,
                TransitionKind.RECOVERY,
                requires_human_actor=True,
                rationale="easing a health band is an operator decision, not a monitor's",
            )
        )

    # Health states back to the laboratory: a demotion, so no human needed.
    for health in HEALTH_STATES:
        for lab in _PRE_RUNNING:
            out.append(
                LifecycleTransition(
                    health,
                    lab,
                    TransitionKind.DEMOTION,
                    rationale="send it back to research rather than leave it parked",
                )
            )

    # Retirement from everywhere except RETIRED itself.
    for state in StrategyLifecycle:
        if state is StrategyLifecycle.RETIRED:
            continue
        out.append(
            LifecycleTransition(
                state,
                StrategyLifecycle.RETIRED,
                TransitionKind.RETIREMENT,
                requires_human_actor=True,
                rationale=(
                    "retirement ends the record; the automated path stops at "
                    "QUARANTINED so a human decides whether the idea is dead"
                ),
            )
        )

    keys = [t.key for t in out]
    if len(set(keys)) != len(keys):
        duplicates = sorted({f"{a.value}->{b.value}" for a, b in keys if keys.count((a, b)) > 1})
        raise AssertionError(f"duplicate transition edges: {duplicates}")
    return tuple(out)


LEGAL_TRANSITIONS: tuple[LifecycleTransition, ...] = _build_transitions()
"""Every legal edge, as data. Nothing else is legal."""

_BY_KEY: dict[tuple[StrategyLifecycle, StrategyLifecycle], LifecycleTransition] = {
    t.key: t for t in LEGAL_TRANSITIONS
}


def transition_table() -> tuple[dict[str, Any], ...]:
    """The table, serialised. What the README and the API both render."""
    return tuple(
        t.to_dict()
        for t in sorted(
            LEGAL_TRANSITIONS,
            key=lambda t: (t.kind.value, t.from_state.value, t.to_state.value),
        )
    )


# ==========================================================================
# The append-only, hash-chained log
# ==========================================================================


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One transition, sealed into a hash chain.

    ``record_hash`` commits to ``previous_hash``, so a record cannot be edited,
    reordered or removed without breaking every record after it.
    """

    sequence: int
    at: datetime
    strategy_id: str
    strategy_content_hash: str
    from_state: StrategyLifecycle
    to_state: StrategyLifecycle
    kind: TransitionKind
    actor: Actor
    automatic: bool
    reason: str
    evidence: tuple[Evidence, ...]
    previous_hash: str
    record_hash: str

    def payload(self) -> dict[str, Any]:
        """Everything the hash commits to. Excludes ``record_hash`` itself."""
        return {
            "sequence": int(self.sequence),
            "at": self.at.astimezone(UTC).isoformat(),
            "strategy_id": self.strategy_id,
            "strategy_content_hash": self.strategy_content_hash,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "kind": self.kind.value,
            "actor": self.actor.to_dict(),
            "automatic": bool(self.automatic),
            "reason": self.reason,
            "evidence": [e.to_dict() for e in self.evidence],
            "previous_hash": self.previous_hash,
        }

    @staticmethod
    def digest(payload: Mapping[str, Any]) -> str:
        blob = json.dumps(
            canonicalise(dict(payload)),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def recompute_hash(self) -> str:
        return self.digest(self.payload())

    @property
    def intact(self) -> bool:
        return self.record_hash == self.recompute_hash()

    @classmethod
    def seal(
        cls,
        *,
        sequence: int,
        at: datetime,
        strategy_id: str,
        strategy_content_hash: str,
        from_state: StrategyLifecycle,
        to_state: StrategyLifecycle,
        kind: TransitionKind,
        actor: Actor,
        reason: str,
        evidence: Sequence[Evidence],
        previous_hash: str,
    ) -> TransitionRecord:
        """Build a record and compute its hash. ``automatic`` is DERIVED.

        There is no way to claim a transition was manual while an automated rule
        made it: ``automatic`` comes from the actor's kind and is never an
        argument.
        """
        draft = cls(
            sequence=sequence,
            at=at,
            strategy_id=strategy_id,
            strategy_content_hash=strategy_content_hash,
            from_state=from_state,
            to_state=to_state,
            kind=kind,
            actor=actor,
            automatic=actor.is_automatic,
            reason=reason,
            evidence=tuple(evidence),
            previous_hash=previous_hash,
            record_hash="",
        )
        return replace(draft, record_hash=draft.digest(draft.payload()))

    def describe(self) -> str:
        how = f"auto/{self.actor.name}" if self.automatic else f"by {self.actor.name}"
        return (
            f"#{self.sequence} {self.at.astimezone(UTC).isoformat()} "
            f"{self.strategy_content_hash[:12]} "
            f"{self.from_state.value} -> {self.to_state.value} "
            f"({self.kind.value}, {how}): {self.reason}"
        )

    def to_dict(self) -> dict[str, Any]:
        out = self.payload()
        out["record_hash"] = self.record_hash
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TransitionRecord:
        return cls(
            sequence=int(raw["sequence"]),
            at=datetime.fromisoformat(str(raw["at"])),
            strategy_id=str(raw.get("strategy_id", "")),
            strategy_content_hash=str(raw["strategy_content_hash"]),
            from_state=StrategyLifecycle(raw["from_state"]),
            to_state=StrategyLifecycle(raw["to_state"]),
            kind=TransitionKind(raw["kind"]),
            actor=Actor.from_dict(raw["actor"]),
            automatic=bool(raw["automatic"]),
            reason=str(raw.get("reason", "")),
            evidence=tuple(Evidence.from_dict(e) for e in raw.get("evidence", [])),
            previous_hash=str(raw["previous_hash"]),
            record_hash=str(raw.get("record_hash", "")),
        )

    def to_json(self) -> str:
        return json.dumps(
            canonicalise(self.to_dict()),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """Whether the log is intact, and where it stopped being so."""

    ok: bool
    n_records: int
    broken_at: int | None = None
    reason: str = ""

    def describe(self) -> str:
        if self.ok:
            return f"chain intact over {self.n_records} record(s)"
        return f"chain BROKEN at record {self.broken_at}: {self.reason}"


class TransitionLog:
    """Append-only store. ``append``, ``records``, ``verify`` -- and no more.

    There is no ``update`` and no ``delete``, for the same reason the experiment
    ledger has none: the API not offering one is a convention, but the hash chain
    is a property of the artefact, and it is the chain that a determined tidier
    runs into.
    """

    def append(self, record: TransitionRecord) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def records(self) -> list[TransitionRecord]:  # pragma: no cover - interface
        raise NotImplementedError

    # ---------------------------------------------------------------- derived

    def head_hash(self) -> str:
        rows = self.records()
        return rows[-1].record_hash if rows else GENESIS_HASH

    def __len__(self) -> int:
        return len(self.records())

    def for_strategy(self, strategy_content_hash: str) -> list[TransitionRecord]:
        return [r for r in self.records() if r.strategy_content_hash == strategy_content_hash]

    def verify(self) -> ChainVerification:
        """Walk the chain and name the FIRST break.

        Three separate ways a log can be wrong, each checked: a record whose own
        hash does not match its payload (an edited field), a record whose
        ``previous_hash`` does not match its predecessor (a removed or reordered
        record), and a sequence that is not contiguous from zero (a truncation).
        """
        rows = self.records()
        previous = GENESIS_HASH
        for i, row in enumerate(rows):
            if row.sequence != i:
                return ChainVerification(
                    False, len(rows), i, f"sequence {row.sequence} at position {i}"
                )
            if row.previous_hash != previous:
                return ChainVerification(
                    False,
                    len(rows),
                    i,
                    f"previous_hash {row.previous_hash[:12]} does not match "
                    f"predecessor {previous[:12]}",
                )
            recomputed = row.recompute_hash()
            if recomputed != row.record_hash:
                return ChainVerification(
                    False,
                    len(rows),
                    i,
                    f"record_hash {row.record_hash[:12]} does not match the "
                    f"payload, which hashes to {recomputed[:12]}",
                )
            previous = row.record_hash
        return ChainVerification(True, len(rows))


class InMemoryTransitionLog(TransitionLog):
    def __init__(self, records: Iterable[TransitionRecord] = ()) -> None:
        self._records: list[TransitionRecord] = list(records)

    def append(self, record: TransitionRecord) -> None:
        self._records.append(record)

    def records(self) -> list[TransitionRecord]:
        return list(self._records)


class FileTransitionLog(TransitionLog):
    """JSON-lines, fsynced on every append.

    The interesting transition is the one immediately before the process died --
    an automatic demotion raised by a monitor that then crashed. An unflushed
    lifecycle log would lose exactly that row.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, record: TransitionRecord) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def records(self) -> list[TransitionRecord]:
        out: list[TransitionRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(TransitionRecord.from_dict(json.loads(line)))
        return out


# ==========================================================================
# The record bound to a strategy
# ==========================================================================


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    """A strategy content hash, its current lifecycle state, and how it got there.

    Keyed on the CONTENT hash rather than the strategy id, because two bindings
    of one template are two strategies everywhere else in this system -- the
    holdout registry refuses a second look per content hash, and the experiment
    ledger keys on it. A lifecycle keyed on the id would let a reparameterisation
    inherit a promotion it never earned.

    Immutable: :meth:`applied` returns a NEW record. The history is the chain of
    transitions, and the evidence chain is everything those transitions cited.
    """

    strategy_id: str
    strategy_content_hash: str
    state: StrategyLifecycle
    created_at: datetime
    history: tuple[TransitionRecord, ...] = ()
    pre_health_state: StrategyLifecycle | None = None
    """The ladder state this strategy was in when it last left the ladder for a
    health state. A recovery may not return it any higher than this."""

    @property
    def is_running(self) -> bool:
        return self.state in RUNNING_STATES

    @property
    def is_halted(self) -> bool:
        return self.state in HEALTH_STATES or self.state is StrategyLifecycle.RETIRED

    @property
    def ladder_position(self) -> int:
        return ladder_index(self.state)

    @property
    def last_transition(self) -> TransitionRecord | None:
        return self.history[-1] if self.history else None

    def entered_state_at(self) -> datetime:
        """When the CURRENT state was entered. The registration counts."""
        for record in reversed(self.history):
            if record.to_state is self.state:
                return record.at
        return self.created_at

    def time_in_state(self, now: datetime) -> float:
        """Seconds in the current state. Used by the elapsed-time criteria."""
        return max(0.0, (now - self.entered_state_at()).total_seconds())

    def evidence_chain(self) -> tuple[Evidence, ...]:
        """Every piece of evidence ever cited, oldest first."""
        return tuple(e for record in self.history for e in record.evidence)

    def evidence_of(self, kind: EvidenceKind) -> tuple[Evidence, ...]:
        return tuple(e for e in self.evidence_chain() if e.kind is kind)

    def applied(self, record: TransitionRecord) -> StrategyRecord:
        """This record after ``record``. Does not validate -- the machine does."""
        if record.kind is TransitionKind.REGISTRATION:
            return replace(self, history=(*self.history, record))
        leaving_ladder = self.state in LADDER and record.to_state in HEALTH_STATES
        returning = record.to_state in LADDER
        pre = self.state if leaving_ladder else (None if returning else self.pre_health_state)
        return replace(
            self,
            state=record.to_state,
            history=(*self.history, record),
            pre_health_state=pre,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_content_hash": self.strategy_content_hash,
            "state": self.state.value,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "entered_state_at": self.entered_state_at().astimezone(UTC).isoformat(),
            "pre_health_state": (
                self.pre_health_state.value if self.pre_health_state else None
            ),
            "n_transitions": len(self.history),
            "history": [r.to_dict() for r in self.history],
        }


# ==========================================================================
# The machine
# ==========================================================================


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class LifecycleStateMachine:
    """Owns the states, validates every transition, and writes the log.

    Performs no I/O of its own beyond the :class:`TransitionLog` it is handed,
    and reads no clock unless the caller omits ``at`` -- which is what makes its
    behaviour exhaustively testable rather than time-dependent.
    """

    def __init__(self, log: TransitionLog | None = None) -> None:
        self.log: TransitionLog = log if log is not None else InMemoryTransitionLog()
        self._records: dict[str, StrategyRecord] = {}
        self._replay()

    # ------------------------------------------------------------- replay

    def _replay(self) -> None:
        """Rebuild every :class:`StrategyRecord` from the log.

        Refuses to start against a broken chain. A machine that silently served
        state from a tampered log would be worse than one that would not start,
        because it would look like it was working.
        """
        verification = self.log.verify()
        if not verification.ok:
            raise TamperedLog(
                f"refusing to load lifecycle state: {verification.describe()}"
            )
        for record in self.log.records():
            if record.kind is TransitionKind.REGISTRATION:
                self._records[record.strategy_content_hash] = StrategyRecord(
                    strategy_id=record.strategy_id,
                    strategy_content_hash=record.strategy_content_hash,
                    state=StrategyLifecycle.DISCOVERY,
                    created_at=record.at,
                    history=(record,),
                )
                continue
            current = self._records.get(record.strategy_content_hash)
            if current is None:  # pragma: no cover - only a hand-built log does this
                raise TamperedLog(
                    f"transition for unregistered strategy "
                    f"{record.strategy_content_hash[:12]} at sequence {record.sequence}"
                )
            self._records[record.strategy_content_hash] = current.applied(record)

    # -------------------------------------------------------------- reads

    def is_registered(self, strategy_content_hash: str) -> bool:
        return strategy_content_hash in self._records

    def record(self, strategy_content_hash: str) -> StrategyRecord:
        try:
            return self._records[strategy_content_hash]
        except KeyError:
            raise UnknownStrategy(strategy_content_hash) from None

    def state(self, strategy_content_hash: str) -> StrategyLifecycle:
        """The current state. Raises rather than defaulting -- see :class:`UnknownStrategy`."""
        return self.record(strategy_content_hash).state

    def records(self) -> tuple[StrategyRecord, ...]:
        return tuple(self._records[k] for k in sorted(self._records))

    def in_state(self, *states: StrategyLifecycle) -> tuple[StrategyRecord, ...]:
        wanted = set(states)
        return tuple(r for r in self.records() if r.state in wanted)

    @staticmethod
    def edge(
        from_state: StrategyLifecycle, to_state: StrategyLifecycle
    ) -> LifecycleTransition | None:
        """The legal edge, or ``None``. ``None`` means illegal, not unknown."""
        if from_state is to_state:
            return None  # REGISTRATION is written by register(), never requested
        return _BY_KEY.get((from_state, to_state))

    def legal_targets(self, strategy_content_hash: str) -> tuple[StrategyLifecycle, ...]:
        current = self.state(strategy_content_hash)
        return tuple(
            sorted(
                {
                    t.to_state
                    for t in LEGAL_TRANSITIONS
                    if t.from_state is current and t.kind is not TransitionKind.REGISTRATION
                },
                key=lambda s: s.value,
            )
        )

    # ------------------------------------------------------------- writes

    def register(
        self,
        *,
        strategy_id: str,
        strategy_content_hash: str,
        actor: Actor,
        reason: str,
        evidence: Sequence[Evidence] = (),
        at: datetime | None = None,
    ) -> StrategyRecord:
        """Enter a strategy at DISCOVERY. Idempotent only in the sense of raising."""
        if strategy_content_hash in self._records:
            raise LifecycleError(
                f"strategy {strategy_content_hash[:12]} is already registered in "
                f"state {self._records[strategy_content_hash].state.value}"
            )
        if not str(strategy_content_hash).strip():
            raise ValueError("strategy_content_hash is mandatory")
        when = at or _utcnow()
        item = tuple(evidence) or (
            Evidence.note(f"registration:{strategy_content_hash[:12]}", reason),
        )
        record = TransitionRecord.seal(
            sequence=len(self.log),
            at=when,
            strategy_id=strategy_id,
            strategy_content_hash=strategy_content_hash,
            from_state=StrategyLifecycle.DISCOVERY,
            to_state=StrategyLifecycle.DISCOVERY,
            kind=TransitionKind.REGISTRATION,
            actor=actor,
            reason=reason,
            evidence=item,
            previous_hash=self.log.head_hash(),
        )
        self.log.append(record)
        created = StrategyRecord(
            strategy_id=strategy_id,
            strategy_content_hash=strategy_content_hash,
            state=StrategyLifecycle.DISCOVERY,
            created_at=when,
            history=(record,),
        )
        self._records[strategy_content_hash] = created
        return created

    def transition(
        self,
        strategy_content_hash: str,
        to_state: StrategyLifecycle,
        *,
        actor: Actor,
        reason: str,
        evidence: Sequence[Evidence],
        at: datetime | None = None,
    ) -> StrategyRecord:
        """Move a strategy. Raises on anything the table or the invariants refuse.

        The checks, in the order they run and the reason for each:

        1. The strategy is registered. An unregistered hash has no state.
        2. The edge exists in :data:`LEGAL_TRANSITIONS`.
        3. There is evidence. A transition nobody can justify is not a decision.
        4. **Target LIVE requires a human actor and a matching authorisation.**
           This check does not consult the transition table, so it survives an
           edit to the table.
        5. Edges marked ``requires_human_actor`` have one.
        6. A recovery does not return a strategy higher than it was when it left
           the ladder.
        """
        current = self.record(strategy_content_hash)
        from_state = current.state

        edge = self.edge(from_state, to_state)
        if edge is None:
            hint = ""
            if (
                from_state in LADDER
                and to_state in LADDER
                and ladder_index(to_state) > ladder_index(from_state) + 1
            ):
                hint = (
                    "promotion moves exactly one stage; the intermediate stages "
                    "gather the evidence this one would be promoting without"
                )
            elif from_state is StrategyLifecycle.RETIRED:
                hint = "RETIRED is terminal; a revived idea is a new content hash"
            raise IllegalTransition(from_state, to_state, hint)

        if not evidence:
            raise LifecycleError(
                f"{from_state.value} -> {to_state.value} needs evidence: a "
                "ValidationReport id, a monitor result, a fired rule, or an "
                "operator note. A transition nobody can justify is not a decision."
            )

        # -- the LIVE invariant, checked independently of the table ----------
        if to_state is StrategyLifecycle.LIVE:
            if actor.kind is not ActorKind.HUMAN:
                raise HumanAuthorisationRequired(
                    f"{actor.kind.value} {actor.name!r} may not move a strategy to "
                    "LIVE. Reaching LIVE requires a named human and a written "
                    "authorisation; no automated rule has, or can be given, that "
                    "authority."
                )
            auth = _authorisation_in(evidence)
            if auth is None:
                raise HumanAuthorisationRequired(
                    "a transition to LIVE requires a HumanAuthorisation among its "
                    "evidence; none was supplied"
                )
            if not auth.matches(strategy_content_hash, StrategyLifecycle.LIVE):
                raise HumanAuthorisationRequired(
                    "the supplied HumanAuthorisation names strategy "
                    f"{auth.strategy_content_hash[:12]} -> {auth.to_state.value}, "
                    f"not {strategy_content_hash[:12]} -> live. An authorisation "
                    "is bound to one strategy and one target state."
                )

        if edge.requires_human_actor and actor.kind is not ActorKind.HUMAN:
            raise HumanAuthorisationRequired(
                f"{from_state.value} -> {to_state.value} is a "
                f"{edge.kind.value} and adds risk or ends the record; it requires "
                f"a named human, not {actor.kind.value} {actor.name!r}"
            )
        if edge.requires_human_authorisation:
            auth = _authorisation_in(evidence)
            if auth is None or not auth.matches(strategy_content_hash, to_state):
                raise HumanAuthorisationRequired(
                    f"{from_state.value} -> {to_state.value} requires a matching "
                    "HumanAuthorisation among its evidence"
                )

        if edge.kind is TransitionKind.RECOVERY and current.pre_health_state is not None:
            ceiling = current.pre_health_state
            if to_state in LADDER and ladder_index(to_state) > ladder_index(ceiling):
                raise IllegalTransition(
                    from_state,
                    to_state,
                    f"this strategy left the ladder from {ceiling.value}; a "
                    "recovery may not return it higher than it was",
                )

        record = TransitionRecord.seal(
            sequence=len(self.log),
            at=at or _utcnow(),
            strategy_id=current.strategy_id,
            strategy_content_hash=strategy_content_hash,
            from_state=from_state,
            to_state=to_state,
            kind=edge.kind,
            actor=actor,
            reason=reason,
            evidence=tuple(evidence),
            previous_hash=self.log.head_hash(),
        )
        self.log.append(record)
        updated = current.applied(record)
        self._records[strategy_content_hash] = updated
        return updated

    # ------------------------------------------------------------- health

    def verify(self) -> ChainVerification:
        return self.log.verify()
