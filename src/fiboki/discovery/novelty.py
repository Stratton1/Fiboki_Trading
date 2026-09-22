"""Novelty: asked BEFORE compute is spent, not after it is wasted.

The expensive failure of an automated search is not a bad candidate. It is a
competent search rediscovering, every few weeks, an idea that was tried and
rejected, because nothing in the system remembers. V1 had no memory at all, so
its cost was paid repeatedly and invisibly.

:mod:`fiboki.research.memory` already answers "does the ledger know anything
about this?" This module turns that answer into a DECISION a campaign can act
on, on three keys and in this order:

1. **Content hash.** The identical document. There is nothing to learn from
   running it again on the same data.
2. **Structure hash** (``fiboki.research.structure``). The same rules over the
   same indicators with different numbers -- the reparameterised rediscovery,
   which is how a search actually re-finds things and which a content hash
   cannot see.
3. **Weighted structural similarity and free-text recall.** Nearby work, which
   is not a reason to skip but is a reason to read before running.

The decision, and what makes it overridable
-------------------------------------------
A rediscovery is skipped. The one thing that overrides the skip is MATERIALLY
DIFFERENT REASONING, and the module is opinionated about what counts:

* a dataset version that did not exist when the prior attempts ran -- new data
  is new evidence, and the module detects this itself rather than taking a
  caller's word for it;
* an explicit, written reason supplied by the caller, which is recorded
  verbatim on the verdict so a reader can judge it.

New parameter values are NOT materially different reasoning. That is what a
reparameterisation is, and admitting it as an override would make the whole
mechanism decorative.

A skip is a result
------------------
:class:`NoveltyVerdict` carries the prior attempts, their outcomes, the rungs
they died at and a recommendation in words. The campaign writes the skip to the
ledger, so "we did not run this, and here is exactly why" is part of the record
rather than an absence in it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from fiboki.research.experiment import Experiment, ExperimentLedger, Outcome
from fiboki.research.memory import RecallResult, Relation, ResearchMemory
from fiboki.research.structure import fingerprint

__all__ = [
    "NoveltyDecision",
    "NoveltyIndex",
    "NoveltyVerdict",
    "PriorAttempt",
]


class NoveltyDecision(str, Enum):
    """What a campaign should do with this proposal."""

    PROCEED = "proceed"
    PROCEED_WITH_CAUTION = "proceed_with_caution"
    SKIP_REDISCOVERY = "skip_rediscovery"

    @property
    def should_run(self) -> bool:
        return self is not NoveltyDecision.SKIP_REDISCOVERY


@dataclass(frozen=True, slots=True)
class PriorAttempt:
    """One earlier experiment that bears on this proposal."""

    experiment_id: str
    strategy_id: str
    relation: str
    similarity: float
    outcome: str
    rejected_at_rung: str = ""
    reason: str = ""
    conclusion: str = ""
    dataset_version_id: str = ""
    created_at: str = ""

    @property
    def short_id(self) -> str:
        return self.experiment_id[:12] if self.experiment_id else "?"

    @property
    def is_rediscovery(self) -> bool:
        return self.relation in (
            Relation.EXACT.value,
            Relation.REPARAMETERISATION.value,
        )

    def describe(self) -> str:
        where = f" at {self.rejected_at_rung}" if self.rejected_at_rung else ""
        tail = self.conclusion or self.reason
        return (
            f"{self.short_id} [{self.relation} {self.similarity:.2f}] "
            f"{self.strategy_id or '-'} -> {self.outcome}{where}"
            + (f" -- {tail}" if tail else "")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "strategy_id": self.strategy_id,
            "relation": self.relation,
            "similarity": round(float(self.similarity), 6),
            "outcome": self.outcome,
            "rejected_at_rung": self.rejected_at_rung,
            "reason": self.reason,
            "conclusion": self.conclusion,
            "dataset_version_id": self.dataset_version_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PriorAttempt:
        return cls(
            experiment_id=str(raw.get("experiment_id", "")),
            strategy_id=str(raw.get("strategy_id", "")),
            relation=str(raw.get("relation", "")),
            similarity=float(raw.get("similarity", 0.0)),
            outcome=str(raw.get("outcome", "")),
            rejected_at_rung=str(raw.get("rejected_at_rung", "")),
            reason=str(raw.get("reason", "")),
            conclusion=str(raw.get("conclusion", "")),
            dataset_version_id=str(raw.get("dataset_version_id", "")),
            created_at=str(raw.get("created_at", "")),
        )

    @classmethod
    def from_experiment(
        cls, experiment: Experiment, *, relation: str, similarity: float
    ) -> PriorAttempt:
        return cls(
            experiment_id=experiment.id,
            strategy_id=experiment.strategy_id,
            relation=relation,
            similarity=float(similarity),
            outcome=experiment.outcome.value,
            rejected_at_rung=experiment.rejected_at_rung,
            reason=experiment.rejection_reason,
            conclusion=experiment.conclusion,
            dataset_version_id=experiment.dataset_version_id,
            created_at=experiment.created_at.isoformat()
            if isinstance(experiment.created_at, datetime)
            else str(experiment.created_at),
        )


@dataclass(frozen=True, slots=True)
class NoveltyVerdict:
    """What the ledger already knows, and what to do about it."""

    query: str
    decision: NoveltyDecision
    prior_attempts: tuple[PriorAttempt, ...] = ()
    recommendation: str = ""
    override_reason: str = ""
    content_hash: str = ""
    structure_hash: str = ""
    similarity_threshold: float = 0.75
    dataset_version_id: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def is_novel(self) -> bool:
        """No exact duplicate and no reparameterisation in the ledger."""
        return not self.rediscoveries

    @property
    def should_run(self) -> bool:
        return self.decision.should_run

    @property
    def rediscoveries(self) -> tuple[PriorAttempt, ...]:
        return tuple(p for p in self.prior_attempts if p.is_rediscovery)

    @property
    def outcomes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.prior_attempts:
            counts[p.outcome] = counts.get(p.outcome, 0) + 1
        return dict(sorted(counts.items()))

    def narrative(self) -> str:
        """One sentence an operator can act on, naming the prior experiments.

        The shape the brief asks for::

            <what was proposed> was tested in E-1241, E-1282 and E-1309;
            <what happened>; do not repeat without materially different
            reasoning.
        """
        if not self.prior_attempts:
            return f"{self.query}: nothing in the ledger resembles this."
        ids = _join([p.short_id for p in self.prior_attempts])
        what_happened = _what_happened(self.prior_attempts)
        if self.decision is NoveltyDecision.SKIP_REDISCOVERY:
            tail = "do not repeat without materially different reasoning."
        elif self.decision is NoveltyDecision.PROCEED_WITH_CAUTION:
            tail = (
                "proceeding anyway because "
                + (self.override_reason or "the reasoning is materially different")
                + "."
            )
        else:
            tail = "related but not a rediscovery; read the prior work before running."
        return f"{self.query} was tested in {ids}; {what_happened}; {tail}"

    def describe(self) -> str:
        lines = [self.narrative(), f"decision: {self.decision.value}"]
        if self.recommendation:
            lines.append(self.recommendation)
        lines.extend(f"  - {p.describe()}" for p in self.prior_attempts)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "decision": self.decision.value,
            "is_novel": self.is_novel,
            "should_run": self.should_run,
            "recommendation": self.recommendation,
            "override_reason": self.override_reason,
            "content_hash": self.content_hash,
            "structure_hash": self.structure_hash,
            "similarity_threshold": float(self.similarity_threshold),
            "dataset_version_id": self.dataset_version_id,
            "narrative": self.narrative(),
            "outcomes": self.outcomes,
            "prior_attempts": [p.to_dict() for p in self.prior_attempts],
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> NoveltyVerdict:
        return cls(
            query=str(raw.get("query", "")),
            decision=NoveltyDecision(raw["decision"]),
            prior_attempts=tuple(
                PriorAttempt.from_dict(p) for p in raw.get("prior_attempts", ())
            ),
            recommendation=str(raw.get("recommendation", "")),
            override_reason=str(raw.get("override_reason", "")),
            content_hash=str(raw.get("content_hash", "")),
            structure_hash=str(raw.get("structure_hash", "")),
            similarity_threshold=float(raw.get("similarity_threshold", 0.75)),
            dataset_version_id=str(raw.get("dataset_version_id", "")),
            diagnostics=dict(raw.get("diagnostics", {})),
        )


def _join(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _what_happened(attempts: Sequence[PriorAttempt]) -> str:
    """Summarise the prior outcomes in the words the ledger recorded."""
    sentences: list[str] = []
    seen: set[str] = set()
    for attempt in attempts:
        text = (attempt.conclusion or attempt.reason).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            sentences.append(text.rstrip("."))
        if len(sentences) >= 3:
            break
    if sentences:
        return "; ".join(sentences)
    counts: dict[str, int] = {}
    for attempt in attempts:
        counts[attempt.outcome] = counts.get(attempt.outcome, 0) + 1
    return ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))


# --------------------------------------------------------------------------
# The index
# --------------------------------------------------------------------------


class NoveltyIndex:
    """Decides whether a proposal is worth compute, against the whole history."""

    def __init__(
        self,
        ledger: ExperimentLedger,
        *,
        similarity_threshold: float = 0.75,
        keyword_threshold: float = 0.5,
        max_matches: int = 10,
    ) -> None:
        self.ledger = ledger
        self.memory = ResearchMemory(
            ledger,
            similarity_threshold=similarity_threshold,
            keyword_threshold=keyword_threshold,
            max_matches=max_matches,
        )
        self.similarity_threshold = float(similarity_threshold)

    def assess(
        self,
        document: Any,
        *,
        rationale: str = "",
        dataset_version_id: str = "",
        override_reason: str = "",
        label: str = "",
        exclude_tags: Sequence[str] = (),
    ) -> NoveltyVerdict:
        """Prior attempts, their outcomes, and a recommendation.

        ``dataset_version_id`` is not decoration: if every prior attempt ran on
        a DIFFERENT dataset version, the proposal is being asked of evidence
        none of them saw, and that is materially different reasoning without
        anyone having to assert it.

        ``exclude_tags`` drops ledger rows carrying any of those tags from the
        comparison. A campaign passes its OWN id: a campaign that is resumed
        after a crash would otherwise find the rows it wrote before the crash,
        conclude that every one of its candidates is an exact duplicate, and
        skip the whole population -- turning a resume into a silent no-op. Rows
        from any OTHER campaign are prior work and stay in.
        """
        recall = self.memory.recall(document, text=rationale)
        fp = fingerprint(document) if document is not None else None
        excluded = frozenset(str(t) for t in exclude_tags)
        matches = [
            m
            for m in recall.matches
            if not (excluded and excluded.intersection(m.experiment.tags))
        ]
        proposal_blind = _blind_spot(document)
        downgraded = 0
        attempts_list: list[PriorAttempt] = []
        for m in matches:
            relation = m.relation.value
            similarity = m.similarity
            if relation == Relation.REPARAMETERISATION.value:
                prior_blind = _blind_spot(m.experiment.strategy_document)
                if prior_blind is not None and prior_blind != proposal_blind:
                    # The structural fingerprint has NO token for the session or
                    # event restriction, so two documents that differ only in
                    # WHEN they are allowed to deal share a structure hash. That
                    # is a blind spot, not a rediscovery: "the same rules, London
                    # only" is a different piece of research from "the same
                    # rules, unrestricted". Demoted to a variant, which is worth
                    # reading before running but is not a reason to skip.
                    relation = Relation.STRUCTURAL_VARIANT.value
                    similarity = min(similarity, 0.95)
                    downgraded += 1
            attempts_list.append(
                PriorAttempt.from_experiment(
                    m.experiment, relation=relation, similarity=similarity
                )
            )
        attempts = tuple(attempts_list)
        query = label or _label_for(document, rationale)
        rediscoveries = tuple(p for p in attempts if p.is_rediscovery)

        diagnostics: dict[str, Any] = {
            "n_matches": len(attempts),
            "n_rediscoveries": len(rediscoveries),
            "n_excluded_by_tag": len(recall.matches) - len(matches),
            "n_demoted_by_blind_spot": downgraded,
            "blind_spot_digest": proposal_blind,
            "excluded_tags": sorted(excluded),
            "memory_recommendation": recall.recommendation(),
            "relations": sorted({p.relation for p in attempts}),
        }

        if not rediscoveries:
            decision = NoveltyDecision.PROCEED
            recommendation = (
                recall.recommendation()
                if attempts
                else (
                    "NOVEL: nothing in the ledger resembles this. Proceed, and "
                    "record the result whichever way it goes."
                )
            )
        else:
            fresh_data, note = _is_fresh_dataset(rediscoveries, dataset_version_id)
            diagnostics["dataset_note"] = note
            if fresh_data:
                decision = NoveltyDecision.PROCEED_WITH_CAUTION
                override_reason = override_reason or note
                recommendation = (
                    "REDISCOVERY ON NEW DATA: "
                    + note
                    + " The prior attempts still describe what to expect, so read "
                    "them: "
                    + "; ".join(p.describe() for p in rediscoveries[:3])
                )
            elif override_reason.strip():
                decision = NoveltyDecision.PROCEED_WITH_CAUTION
                recommendation = (
                    "REDISCOVERY OVERRIDDEN: the caller supplied a reason -- "
                    f"{override_reason.strip()} -- which is recorded on this verdict "
                    "so a reader can judge whether it is materially different "
                    "reasoning or a wish."
                )
            else:
                decision = NoveltyDecision.SKIP_REDISCOVERY
                recommendation = recall.recommendation()

        return NoveltyVerdict(
            query=query,
            decision=decision,
            prior_attempts=attempts,
            recommendation=recommendation,
            override_reason=override_reason.strip(),
            content_hash=fp.content_hash if fp else "",
            structure_hash=fp.structure_hash if fp else "",
            similarity_threshold=self.similarity_threshold,
            dataset_version_id=dataset_version_id,
            diagnostics=diagnostics,
        )

    def recall(self, document: Any = None, *, text: str = "") -> RecallResult:
        """The raw research-memory answer, for a caller that wants the detail."""
        return self.memory.recall(document, text=text)

    def outcomes_for(self, document: Any) -> dict[str, int]:
        return self.memory.outcomes_for_structure(document)


#: Fields a strategy document declares that ``research.structure`` has NO token
#: for, and that a mutation operator can change. Compared PRESENCE-AWARE rather
#: than value-by-value: "restricted to one window" and "restricted to a
#: different window" are the same idea at different numbers, which is exactly
#: what a reparameterisation is, while "restricted" and "unrestricted" are not.
def _blind_spot(document: Any) -> str | None:
    """A digest of the semantic fields the structural fingerprint cannot see.

    ``None`` when there is no document to read -- in which case the relation is
    left alone, so a missing record makes the campaign MORE likely to skip, not
    less. Over-skipping costs a research opportunity; under-skipping inflates
    the trial count, which only makes the deflation harder. Both errors are
    safe; this one is the cheaper of the two to explain.
    """
    if document is None:
        return None
    if hasattr(document, "model_dump"):
        data = document.model_dump(mode="json")
    elif isinstance(document, Mapping):
        data = dict(document)
    else:  # pragma: no cover - a caller passing something else
        return None
    sessions = data.get("sessions")
    events = data.get("events") or {}
    if sessions is None:
        session_part = "sessions:none"
    else:
        windows = sessions.get("windows") or ()
        weekdays = sessions.get("weekdays") or ()
        session_part = (
            f"sessions:windows={len(windows)},weekdays={len(weekdays)},"
            f"weekend_block={bool(sessions.get('block_bars_before_weekend'))}"
        )
    event_part = (
        f"events:blackout={bool(events.get('block_minutes_before')) or bool(events.get('block_minutes_after'))},"
        f"tags={len(events.get('blocked_event_tags') or ())},"
        f"month_end={bool(events.get('avoid_month_end'))},"
        f"rollover={bool(events.get('avoid_rollover_hour'))}"
    )
    return f"{session_part}|{event_part}"


def _label_for(document: Any, rationale: str) -> str:
    if rationale.strip():
        return rationale.strip().rstrip(".")
    strategy_id = getattr(document, "strategy_id", "") or "this proposal"
    mutation = getattr(document, "mutation", None)
    if mutation is not None and str(getattr(mutation, "description", "") or ""):
        return str(mutation.description).rstrip(".")
    return str(strategy_id)


def _is_fresh_dataset(
    rediscoveries: Iterable[PriorAttempt], dataset_version_id: str
) -> tuple[bool, str]:
    """True when no prior rediscovery ran on the dataset version now proposed."""
    if not dataset_version_id:
        return False, "no dataset version was supplied, so freshness cannot be claimed"
    seen = {p.dataset_version_id for p in rediscoveries if p.dataset_version_id}
    if not seen:
        return (
            False,
            "the prior attempts record no dataset version, so it cannot be shown "
            "that this data is new to them",
        )
    if dataset_version_id in seen:
        return (
            False,
            f"the prior attempts already ran on dataset version {dataset_version_id}",
        )
    return (
        True,
        f"every prior attempt ran on {_join(sorted(seen))}, none of which is the "
        f"proposed {dataset_version_id}",
    )


def promoted_priors(verdict: NoveltyVerdict) -> tuple[PriorAttempt, ...]:
    """Prior attempts that were PROMOTED. Re-running one produces no new evidence."""
    return tuple(p for p in verdict.prior_attempts if p.outcome == Outcome.PROMOTED.value)
