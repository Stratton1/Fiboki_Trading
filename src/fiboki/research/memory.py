"""Research memory: "have we tried this already?", asked BEFORE the work.

The expensive failure mode of an automated research loop is not a bad strategy.
It is a good process rediscovering the same dead end every few weeks, because
nothing in the system remembers that the idea was tried, why it was rejected,
and what would have to be different for it to be worth trying again.

This module answers that question against the experiment ledger, on three keys:

**Exact.** Same ``content_hash``. The identical document has been run before.

**Structural.** Same ``structure_hash``: the same rules over the same
indicators, differing only in numbers. This is the one that matters, because a
search process rediscovers ideas in reparameterised form far more often than
verbatim, and a content hash cannot see it.

**Related.** Weighted structural similarity above a threshold, plus free-text
recall over the reasons people wrote down. "Add RSI confirmation to an N-wave
strategy" finds the experiments that added RSI confirmation to an N-wave
strategy, whether or not anybody tagged them.

The answer is not "yes/no". It is the prior experiments, their outcomes, the
rungs they died at, and a recommendation -- because "we tried it and it failed at
the deflation rung with a DSR of 0.41" and "we tried it and it failed at rung 0
because the data was wrong" call for completely different decisions.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fiboki.research.experiment import Experiment, ExperimentLedger, Outcome
from fiboki.research.structure import (
    StructuralFingerprint,
    fingerprint,
    structural_similarity,
)
from fiboki.strategy.dsl import strategy_key_version

__all__ = [
    "RecallResult",
    "RelatedExperiment",
    "Relation",
    "ResearchMemory",
]

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have if in into is it its of on or
    that the than then there these this to was were will with we you your do
    add adding also try trying test testing use using run running strategy
    """.split()
)


class Relation(str, Enum):
    """How a prior experiment relates to the proposal."""

    EXACT = "exact_duplicate"
    REPARAMETERISATION = "reparameterisation"
    STRUCTURAL_VARIANT = "structural_variant"
    KEYWORD = "keyword_match"

    @property
    def is_rediscovery(self) -> bool:
        return self in (Relation.EXACT, Relation.REPARAMETERISATION)


@dataclass(frozen=True, slots=True)
class RelatedExperiment:
    experiment: Experiment
    relation: Relation
    similarity: float
    why: str

    def describe(self) -> str:
        return (
            f"[{self.relation.value} {self.similarity:.2f}] "
            f"{self.experiment.describe()}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation.value,
            "similarity": round(float(self.similarity), 6),
            "why": self.why,
            "experiment": self.experiment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RecallResult:
    """What the ledger already knows about this proposal."""

    query: str
    matches: tuple[RelatedExperiment, ...] = ()
    fingerprint: StructuralFingerprint | None = field(default=None, repr=False)
    threshold: float = 0.75
    incomparable: tuple[Experiment, ...] = field(default=(), repr=False)
    """Experiments whose stored keys were derived under a DIFFERENT version of the
    hashing algorithm, so neither the exact nor the reparameterisation test can be
    applied to them. They are not evidence of novelty and they are not evidence of
    rediscovery: they are the absence of an answer, and :attr:`is_novel` treats
    them as such."""

    @property
    def exact(self) -> tuple[RelatedExperiment, ...]:
        return tuple(m for m in self.matches if m.relation is Relation.EXACT)

    @property
    def reparameterisations(self) -> tuple[RelatedExperiment, ...]:
        return tuple(m for m in self.matches if m.relation is Relation.REPARAMETERISATION)

    @property
    def rediscoveries(self) -> tuple[RelatedExperiment, ...]:
        return tuple(m for m in self.matches if m.relation.is_rediscovery)

    @property
    def is_novel(self) -> bool:
        """No rediscovery, AND every prior experiment was actually comparable.

        Fails CLOSED on :attr:`incomparable`. "Novel" licenses spending a holdout
        look, so the claim has to be earned: an experiment whose key was derived
        under a different schema might be this very strategy, and answering
        "novel" because its hash no longer matches is how a rediscovery gets run
        as if it were new work.
        """
        return not self.rediscoveries and not self.incomparable

    @property
    def outcomes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.matches:
            key = m.experiment.outcome.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def recommendation(self) -> str:
        """Plain advice, with the evidence attached. Never a bare verdict."""
        if self.incomparable:
            return (
                f"CANNOT SAY ({len(self.incomparable)} experiment(s) not comparable): "
                "their stored strategy keys were derived under a different version of "
                "the hashing algorithm, so neither the exact-duplicate nor the "
                "reparameterisation test applies to them. This is NOT novelty. Restate "
                "those rows' key version with ExperimentLedger.restate_key_versions() "
                "if you can show which version wrote them, or read them by hand before "
                "spending a holdout look on this."
            )
        if not self.matches:
            return (
                "NOVEL: nothing in the ledger resembles this. Proceed, and record "
                "the result whichever way it goes."
            )
        rediscovered = self.rediscoveries
        if not rediscovered:
            return (
                f"RELATED PRIOR WORK: {len(self.matches)} nearby experiment(s) "
                f"({_counts(self.outcomes)}). Not a rediscovery, but read them "
                "before running -- the nearest is: "
                f"{self.matches[0].experiment.describe()}"
            )
        promoted = [m for m in rediscovered if m.experiment.outcome is Outcome.PROMOTED]
        if promoted:
            return (
                f"ALREADY PROMOTED: {promoted[0].experiment.describe()}. Running it "
                "again produces a second set of numbers for the same strategy, not "
                "more evidence. Vary the idea or validate on a NEW dataset version."
            )
        rungs = _rung_histogram(rediscovered)
        exact = self.exact
        label = "EXACT DUPLICATE" if exact else "REPARAMETERISATION"
        lead = (exact or rediscovered)[0].experiment
        return (
            f"DO NOT REPEAT ({label}): {len(rediscovered)} prior experiment(s) ran "
            f"this structure and none survived. {_counts(_outcome_counts(rediscovered))}. "
            f"Rejected at: {rungs}. Most recent: {lead.describe()}. "
            "Repeat ONLY with materially different reasoning -- a new economic "
            "story, a different instrument class, or a dataset version that did "
            "not exist when these ran. New parameter values are not materially "
            "different reasoning; that is what a reparameterisation is."
        )

    def describe(self) -> str:
        lines = [f"query: {self.query}", self.recommendation()]
        lines.extend(f"  - {m.describe()}" for m in self.matches)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "is_novel": self.is_novel,
            "threshold": self.threshold,
            "n_incomparable_key_versions": len(self.incomparable),
            "incomparable_experiment_ids": [e.id for e in self.incomparable],
            "outcomes": self.outcomes,
            "recommendation": self.recommendation(),
            "fingerprint": self.fingerprint.to_dict() if self.fingerprint else None,
            "matches": [m.to_dict() for m in self.matches],
        }


def _outcome_counts(matches: Sequence[RelatedExperiment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in matches:
        counts[m.experiment.outcome.value] = counts.get(m.experiment.outcome.value, 0) + 1
    return dict(sorted(counts.items()))


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{v} {k}" for k, v in counts.items()) or "no recorded outcomes"


def _rung_histogram(matches: Sequence[RelatedExperiment]) -> str:
    counts: dict[str, int] = {}
    for m in matches:
        rung = m.experiment.rejected_at_rung or "unrecorded"
        counts[rung] = counts.get(rung, 0) + 1
    return ", ".join(f"{k} x{v}" for k, v in sorted(counts.items()))


def _keys_comparable(exp: Experiment, expected: str) -> bool:
    """Can this experiment's strategy keys be compared with keys derived now?

    Both hashes are checked because both are used: the content hash decides EXACT
    and the structure hash decides REPARAMETERISATION, and one of them being
    comparable does not make the other so.
    """
    return exp.key_is_comparable(
        "strategy_content_hash", expected
    ) and exp.key_is_comparable("structure_hash", expected)


def _tokenise(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 1}


class ResearchMemory:
    """Queries the experiment ledger for prior work related to a proposal."""

    def __init__(
        self,
        ledger: ExperimentLedger,
        *,
        similarity_threshold: float = 0.75,
        keyword_threshold: float = 0.5,
        max_matches: int = 10,
    ) -> None:
        if not 0.0 < similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in (0, 1]")
        self.ledger = ledger
        self.similarity_threshold = float(similarity_threshold)
        self.keyword_threshold = float(keyword_threshold)
        self.max_matches = int(max_matches)

    # ------------------------------------------------------------- queries

    def recall(
        self,
        document: Any = None,
        *,
        text: str = "",
        limit: int | None = None,
    ) -> RecallResult:
        """Prior experiments related to a proposed strategy and/or a description.

        Call this BEFORE running anything. Both arguments are optional and
        combine: a mutation proposal usually has both a candidate document and
        the sentence that motivated it, and the sentence often matches prior work
        whose structure has since drifted.
        """
        if document is None and not text.strip():
            raise ValueError("recall needs a strategy document, a text query, or both")

        fp = fingerprint(document) if document is not None else None
        query_terms = _tokenise(text) if text else set()
        if fp is not None:
            query_terms |= set(fp.keywords)

        expected = strategy_key_version()
        matches: dict[str, RelatedExperiment] = {}
        incomparable: list[Experiment] = []
        for exp in self.ledger.list(allow_stale_keys=True):
            # A hash comparison across key versions is meaningless, and its FALSE
            # answer is the dangerous one: it reads as "never tried". Set those rows
            # aside where the caller can see them rather than letting them vanish
            # into the novel bucket.
            if fp is not None and not _keys_comparable(exp, expected):
                incomparable.append(exp)
                continue
            match = self._classify(exp, fp, query_terms, bool(text.strip()))
            if match is not None:
                matches[exp.id] = match

        ranked = sorted(
            matches.values(),
            key=lambda m: (
                -_relation_rank(m.relation),
                -m.similarity,
                -m.experiment.created_at.timestamp(),
            ),
        )
        cap = self.max_matches if limit is None else int(limit)
        description = text.strip() or (
            f"strategy {getattr(document, 'strategy_id', '?')} "
            f"[{(fp.content_hash if fp else '')[:12]}]"
        )
        return RecallResult(
            query=description,
            matches=tuple(ranked[:cap]),
            fingerprint=fp,
            threshold=self.similarity_threshold,
            incomparable=tuple(incomparable),
        )

    def has_been_tried(self, document: Any) -> bool:
        """True when this exact strategy, or a reparameterisation, has been run."""
        return not self.recall(document).is_novel

    def outcomes_for_structure(self, document: Any) -> dict[str, int]:
        """Outcome histogram for this structure.

        Raises :class:`~fiboki.research.experiment.LedgerKeyVersionMismatch` when
        the ledger holds structure hashes it cannot compare -- an empty histogram
        would read as "never tried this structure".
        """
        fp = fingerprint(document)
        counts: dict[str, int] = {}
        for exp in self.ledger.list(structure_hash=fp.structure_hash):
            counts[exp.outcome.value] = counts.get(exp.outcome.value, 0) + 1
        return dict(sorted(counts.items()))

    # ------------------------------------------------------------ internals

    def _classify(
        self,
        exp: Experiment,
        fp: StructuralFingerprint | None,
        query_terms: set[str],
        has_text: bool,
    ) -> RelatedExperiment | None:
        if fp is not None and fp.content_hash and exp.strategy_content_hash == fp.content_hash:
            return RelatedExperiment(
                experiment=exp,
                relation=Relation.EXACT,
                similarity=1.0,
                why="identical strategy content hash",
            )
        if fp is not None and fp.structure_hash and exp.structure_hash == fp.structure_hash:
            return RelatedExperiment(
                experiment=exp,
                relation=Relation.REPARAMETERISATION,
                similarity=1.0,
                why=(
                    "identical structure hash: same rules over the same indicators, "
                    "same exits, same context -- only the numbers differ"
                ),
            )
        if fp is not None and exp.structure_tokens:
            score = structural_similarity(fp.tokens, exp.structure_tokens)
            if score >= self.similarity_threshold:
                return RelatedExperiment(
                    experiment=exp,
                    relation=Relation.STRUCTURAL_VARIANT,
                    similarity=score,
                    why=f"weighted structural similarity {score:.2f}",
                )
        if has_text and query_terms:
            haystack = _tokenise(
                " ".join(
                    (
                        exp.reason,
                        exp.conclusion,
                        exp.rejection_reason,
                        exp.strategy_id,
                        exp.hypothesis_id,
                        " ".join(exp.tags),
                        " ".join(t.split(":")[-1] for t in exp.structure_tokens),
                    )
                )
            )
            hit = len(query_terms & haystack)
            score = hit / len(query_terms)
            if hit >= 2 and score >= self.keyword_threshold:
                shared = ", ".join(sorted(query_terms & haystack)[:6])
                return RelatedExperiment(
                    experiment=exp,
                    relation=Relation.KEYWORD,
                    similarity=score,
                    why=f"description overlaps on: {shared}",
                )
        return None


def _relation_rank(relation: Relation) -> int:
    return {
        Relation.EXACT: 3,
        Relation.REPARAMETERISATION: 2,
        Relation.STRUCTURAL_VARIANT: 1,
        Relation.KEYWORD: 0,
    }[relation]
