"""A hypothesis: the claim a campaign exists to attack.

A search process that starts from "what scores well?" finds whatever the sample
happened to reward. A search that starts from a stated economic mechanism can at
least be told when it is wrong, because the mechanism predicts something and the
prediction can fail. So every candidate a campaign proposes is attached to a
:class:`Hypothesis`, and a hypothesis that cannot say what would refute it is
refused at construction.

What this object insists on
---------------------------
``economic_rationale``
    WHY the effect should exist -- a mechanism, not a pattern. Long, because a
    one-line rationale is a label rather than an argument.

``prediction``
    The falsifiable consequence. Something that has a truth value once the data
    is in, and that is not merely "it will make money".

``refutation``
    THE observation that would kill it. Mandatory, and checked against the
    prediction so it cannot be the same sentence re-typed: a refutation that
    restates the prediction is not a refutation.

``evidence``
    Both directions, and at least one piece AGAINST. The five seed documents in
    ``research/strategies/`` already do this in prose -- every one of them names
    the published work that says the effect does not survive multiple-testing
    correction. Making it a field means a hypothesis cannot be filed having
    looked only at the literature that agrees with it.

``regimes`` / ``instruments`` / ``timeframes``
    WHERE the claim is supposed to hold. A hypothesis that holds everywhere
    predicts nothing, and one whose claimed domain is never written down can be
    quietly widened after the fact to cover wherever the result happened to land.

Relationship to :class:`fiboki.research.artefacts.Hypothesis`
-------------------------------------------------------------
That one is the AGENT-facing artefact: a short pre-registration an LLM role
files into the research store. This one is the CAMPAIGN-facing object: it adds
the evidence ledger, the regime claim and the lifecycle status a campaign needs
to drive proposal and to record refutation. :meth:`Hypothesis.as_artefact`
converts, so a campaign hypothesis can be filed in the agent store too rather
than creating a second parallel record of the same idea.

Persistence
-----------
Hypotheses live in the append-only experiment ledger like everything else. A
status change is not an edit -- it is a NEW row whose ``parent_experiment_id``
points at the previous one, so "this was supported in March and refuted in
September" is readable end to end, which is the only version of the record worth
keeping.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Timeframe
from fiboki.research.artefacts import Hypothesis as HypothesisArtefact
from fiboki.research.experiment import (
    ActorKind,
    Experiment,
    ExperimentDraft,
    ExperimentLedger,
    Outcome,
)

__all__ = [
    "Evidence",
    "Hypothesis",
    "HypothesisLedger",
    "HypothesisStatus",
    "load_hypotheses",
]

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

#: Tag written on every ledger row that carries a hypothesis payload.
HYPOTHESIS_TAG = "hypothesis"

#: Minimum lengths. Long enough that a placeholder is uncomfortable to write.
MIN_RATIONALE_CHARS = 160
MIN_PREDICTION_CHARS = 40
MIN_REFUTATION_CHARS = 40


class HypothesisStatus(str, Enum):
    """Where a claim stands. ``REFUTED`` is a result, not a failure."""

    PROPOSED = "proposed"
    TESTING = "testing"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    WITHDRAWN = "withdrawn"

    @property
    def is_terminal(self) -> bool:
        return self in (HypothesisStatus.REFUTED, HypothesisStatus.WITHDRAWN)


class Evidence(BaseModel):
    """One piece of evidence, pointed FOR or AGAINST the claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: Literal["for", "against"]
    claim: str = Field(min_length=20)
    source: str = ""
    """A citation, a URL, or an experiment id. Empty means "asserted, unsourced",
    which is legitimate but visible."""
    strength: Literal["strong", "moderate", "weak"] = "moderate"

    def describe(self) -> str:
        tail = f" [{self.source}]" if self.source else " [unsourced]"
        return f"{self.direction.upper()} ({self.strength}): {self.claim}{tail}"


class Hypothesis(BaseModel):
    """A falsifiable claim about a market, with its domain and its evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: str
    title: str = Field(min_length=8, max_length=200)
    economic_rationale: str = Field(min_length=MIN_RATIONALE_CHARS)
    prediction: str = Field(min_length=MIN_PREDICTION_CHARS)
    refutation: str = Field(min_length=MIN_REFUTATION_CHARS)

    instruments: tuple[str, ...] = ()
    timeframes: tuple[Timeframe, ...] = ()
    regimes: tuple[str, ...] = ()
    """Market states in which the claim is supposed to hold, in plain words
    ("trending", "high realised volatility", "London session"). Free text on
    purpose: a regime taxonomy that pretends to be exhaustive is a bigger lie
    than a sentence."""

    evidence: tuple[Evidence, ...] = ()
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    status_reason: str = ""
    seed_strategy_ids: tuple[str, ...] = ()
    author: str = ""
    tags: tuple[str, ...] = ()
    supersedes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    # --------------------------------------------------------- validation

    @field_validator("hypothesis_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(
                f"hypothesis_id {v!r} must be a lowercase slug: ^[a-z][a-z0-9_]{{2,63}}$"
            )
        return v

    @field_validator("instruments")
    @classmethod
    def _known_instruments(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [s for s in v if not instrument_registry.exists(s)]
        if unknown:
            raise ValueError(
                f"hypothesis names unregistered instruments {unknown}; register them "
                "in core/instruments.py rather than claiming an effect on a symbol "
                "whose contract specification nobody has written down"
            )
        return tuple(dict.fromkeys(s.upper() for s in v))

    @field_validator("timeframes")
    @classmethod
    def _dedupe_timeframes(cls, v: tuple[Timeframe, ...]) -> tuple[Timeframe, ...]:
        return tuple(dict.fromkeys(v))

    @model_validator(mode="after")
    def _refutable(self) -> Hypothesis:
        if _normalise(self.refutation) == _normalise(self.prediction):
            raise ValueError(
                f"{self.hypothesis_id}: the refutation restates the prediction. A "
                "hypothesis is refutable only when some OBSERVATION would count "
                "against it; say which one, in numbers if you can."
            )
        if not any(e.direction == "against" for e in self.evidence):
            raise ValueError(
                f"{self.hypothesis_id}: no evidence AGAINST. Every seed document in "
                "research/strategies/ names the published work that says its effect "
                "does not survive correction; a hypothesis filed having read only "
                "the agreeable literature is a sales pitch."
            )
        return self

    # ------------------------------------------------------------ derived

    @property
    def evidence_for(self) -> tuple[Evidence, ...]:
        return tuple(e for e in self.evidence if e.direction == "for")

    @property
    def evidence_against(self) -> tuple[Evidence, ...]:
        return tuple(e for e in self.evidence if e.direction == "against")

    def covers(self, instrument: str, timeframe: Timeframe | str) -> bool:
        """True when the claim is made for this cell.

        An EMPTY ``instruments`` or ``timeframes`` means "unrestricted", which is
        a weaker hypothesis and is reported as such; it is not the same as
        naming the cell.
        """
        tf = Timeframe(timeframe) if not isinstance(timeframe, Timeframe) else timeframe
        ok_symbol = not self.instruments or instrument.upper() in self.instruments
        ok_tf = not self.timeframes or tf in self.timeframes
        return ok_symbol and ok_tf

    def content_hash(self) -> str:
        """Hash of the CLAIM, not of its bookkeeping.

        Status, timestamps and the supersede pointer are excluded, so a
        hypothesis that moves from ``proposed`` to ``refuted`` is recognisably
        the same claim in the ledger.
        """
        payload = self.model_dump(mode="json")
        for field in ("status", "status_reason", "created_at", "supersedes", "tags"):
            payload.pop(field, None)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def short_hash(self) -> str:
        return self.content_hash()[:12]

    # ------------------------------------------------------------ mutation

    def with_status(
        self, status: HypothesisStatus, *, reason: str, evidence: Evidence | None = None
    ) -> Hypothesis:
        """A NEW hypothesis at a new status. Nothing is edited in place.

        ``reason`` is mandatory: a status that changed for no recorded reason is
        the thing this whole module exists to prevent.
        """
        if not reason.strip():
            raise ValueError(
                "a status change needs a reason -- which observation moved it, and "
                "in which experiment"
            )
        payload = self.model_dump()
        payload["status"] = status
        payload["status_reason"] = reason
        if evidence is not None:
            payload["evidence"] = (*self.evidence, evidence)
        return type(self).model_validate(payload)

    def refuted_by(self, observation: str, *, source: str = "") -> Hypothesis:
        """Mark the claim refuted, filing the observation as evidence against."""
        return self.with_status(
            HypothesisStatus.REFUTED,
            reason=observation,
            evidence=Evidence(
                direction="against",
                claim=observation,
                source=source,
                strength="strong",
            ),
        )

    # ------------------------------------------------------- interop / io

    def as_artefact(self, *, created_by: str = "", role: str = "") -> HypothesisArtefact:
        """The agent-store view of this claim.

        Lossy by design -- the artefact has no evidence ledger and no regime
        claim -- so the evidence is folded into the rationale text rather than
        dropped silently.
        """
        against = "\n".join(f"AGAINST: {e.claim}" for e in self.evidence_against)
        for_ = "\n".join(f"FOR: {e.claim}" for e in self.evidence_for)
        return HypothesisArtefact(
            hypothesis_id=self.hypothesis_id,
            title=self.title,
            statement=self.prediction
            + ("\nRegimes claimed: " + ", ".join(self.regimes) if self.regimes else ""),
            rationale="\n\n".join(x for x in (self.economic_rationale, for_, against) if x),
            testable_prediction=self.prediction,
            falsifier=self.refutation,
            instruments=self.instruments,
            timeframes=tuple(t.value for t in self.timeframes),
            tags=self.tags,
            supersedes=self.supersedes or None,
            created_by=created_by or self.author,
            role=role,
        )

    def describe(self) -> str:
        lines = [
            f"{self.hypothesis_id} [{self.short_hash}] {self.status.value.upper()}: {self.title}",
            f"  predicts: {self.prediction}",
            f"  refuted by: {self.refutation}",
        ]
        if self.instruments or self.timeframes:
            lines.append(
                "  domain: "
                + (", ".join(self.instruments) or "any instrument")
                + " / "
                + (", ".join(t.value for t in self.timeframes) or "any timeframe")
                + (f" / {', '.join(self.regimes)}" if self.regimes else "")
            )
        lines.extend(f"  {e.describe()}" for e in self.evidence)
        if self.status_reason:
            lines.append(f"  status because: {self.status_reason}")
        return "\n".join(lines)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=indent, default=str)

    @classmethod
    def from_json(cls, blob: str) -> Hypothesis:
        return cls.model_validate(json.loads(blob))


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def load_hypotheses(path: str | Path, pattern: str = "*.json") -> tuple[Hypothesis, ...]:
    """Load every hypothesis document in a directory, ordered by id."""
    root = Path(path)
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory of hypothesis documents")
    found = [Hypothesis.from_json(p.read_text(encoding="utf-8")) for p in sorted(root.glob(pattern))]
    ids = [h.hypothesis_id for h in found]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate hypothesis ids in {root}: {duplicates}")
    return tuple(sorted(found, key=lambda h: h.hypothesis_id))


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


class HypothesisLedger:
    """Hypotheses in the append-only experiment ledger.

    There is no update. :meth:`set_status` appends a row pointing at the
    previous one, so the history of a claim is the chain of its rows and cannot
    be tidied into a single flattering state.
    """

    def __init__(self, ledger: ExperimentLedger) -> None:
        self.ledger = ledger

    # ---------------------------------------------------------------- write

    def record(
        self,
        hypothesis: Hypothesis,
        *,
        actor_name: str,
        actor_kind: ActorKind = ActorKind.AGENT,
        reason: str = "",
        parent_experiment_id: str = "",
        campaign_id: str = "",
    ) -> Experiment:
        """Append this hypothesis to the ledger and return the row."""
        tags = [HYPOTHESIS_TAG, f"status:{hypothesis.status.value}"]
        if campaign_id:
            tags.append(f"campaign:{campaign_id}")
        draft = ExperimentDraft(
            actor_kind=actor_kind,
            actor_name=actor_name,
            reason=reason or f"registering hypothesis: {hypothesis.prediction}",
            hypothesis_id=hypothesis.hypothesis_id,
            parent_experiment_id=parent_experiment_id,
            outputs={
                "hypothesis": hypothesis.model_dump(mode="json"),
                "hypothesis_content_hash": hypothesis.content_hash(),
            },
            outcome=_outcome_for(hypothesis.status),
            conclusion=hypothesis.status_reason,
            tags=tuple(tags),
        )
        return self.ledger.create(draft)

    def set_status(
        self,
        hypothesis: Hypothesis,
        status: HypothesisStatus,
        *,
        reason: str,
        actor_name: str,
        actor_kind: ActorKind = ActorKind.AGENT,
        evidence: Evidence | None = None,
        campaign_id: str = "",
    ) -> tuple[Hypothesis, Experiment]:
        """Append a status change, chained to this hypothesis's latest row."""
        updated = hypothesis.with_status(status, reason=reason, evidence=evidence)
        history = self.history(hypothesis.hypothesis_id)
        parent = history[-1].id if history else ""
        row = self.record(
            updated,
            actor_name=actor_name,
            actor_kind=actor_kind,
            reason=reason,
            parent_experiment_id=parent,
            campaign_id=campaign_id,
        )
        return updated, row

    # ----------------------------------------------------------------- read

    def history(self, hypothesis_id: str) -> builtins.list[Experiment]:
        """Every row for this hypothesis, oldest first."""
        return [
            e
            for e in self.ledger.list(hypothesis_id=str(hypothesis_id))
            if HYPOTHESIS_TAG in e.tags
        ]

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        """The LATEST filed state of this claim, or ``None`` if never filed."""
        rows = self.history(hypothesis_id)
        if not rows:
            return None
        return Hypothesis.model_validate(rows[-1].outputs["hypothesis"])

    def list(self) -> builtins.list[Hypothesis]:
        """Latest state of every hypothesis in the ledger, ordered by id."""
        latest: dict[str, Hypothesis] = {}
        for exp in self.ledger.list():
            if HYPOTHESIS_TAG not in exp.tags:
                continue
            payload = exp.outputs.get("hypothesis")
            if payload:
                latest[exp.hypothesis_id] = Hypothesis.model_validate(payload)
        return [latest[k] for k in sorted(latest)]

    def experiments_for(self, hypothesis_id: str) -> builtins.list[Experiment]:
        """Every experiment attributed to this hypothesis, including runs."""
        return self.ledger.list(hypothesis_id=str(hypothesis_id))


def _outcome_for(status: HypothesisStatus) -> Outcome:
    return {
        HypothesisStatus.PROPOSED: Outcome.PENDING,
        HypothesisStatus.TESTING: Outcome.PENDING,
        HypothesisStatus.SUPPORTED: Outcome.INCONCLUSIVE,
        HypothesisStatus.REFUTED: Outcome.REJECTED,
        HypothesisStatus.WITHDRAWN: Outcome.ABANDONED,
    }[status]


def hypotheses_covering(
    hypotheses: Iterable[Hypothesis], instrument: str, timeframe: Timeframe | str
) -> tuple[Hypothesis, ...]:
    """Those whose declared domain includes this cell, ordered by id."""
    found: Sequence[Hypothesis] = [h for h in hypotheses if h.covers(instrument, timeframe)]
    return tuple(sorted(found, key=lambda h: h.hypothesis_id))
