"""Fiboki V2 research record: what was tried, by whom, why, and what happened.

    structure   a strategy reduced to its shape, with the numbers removed
    experiment  the append-only ledger of every experiment ever run
    memory      "have we tried this already?", answered BEFORE the work
    lineage     from a live candidate back to the raw dataset bytes

The ledger is append-only at the database level, not merely by convention, so
the record includes the results nobody liked. That is the only version of a
research record worth having: a history you can edit is a history that will be
edited, and an automated search that only remembers its successes will keep
rediscovering its failures.
"""
from __future__ import annotations

from fiboki.research.experiment import (
    APPEND_ONLY_MESSAGE,
    ActorKind,
    Experiment,
    ExperimentDraft,
    ExperimentLedger,
    ExperimentNotFound,
    LedgerError,
    Outcome,
    is_append_only_violation,
)
from fiboki.research.lineage import (
    LineageEdge,
    LineageGraph,
    LineageNode,
    LineageService,
    ProvenanceChain,
    ProvenanceStep,
)
from fiboki.research.memory import (
    RecallResult,
    RelatedExperiment,
    Relation,
    ResearchMemory,
)
from fiboki.research.structure import (
    StructuralFingerprint,
    fingerprint,
    is_reparameterisation,
    keywords,
    structural_similarity,
    structural_tokens,
    structure_hash,
)

__all__ = [
    "APPEND_ONLY_MESSAGE",
    "ActorKind",
    "Experiment",
    "ExperimentDraft",
    "ExperimentLedger",
    "ExperimentNotFound",
    "LedgerError",
    "LineageEdge",
    "LineageGraph",
    "LineageNode",
    "LineageService",
    "Outcome",
    "ProvenanceChain",
    "ProvenanceStep",
    "RecallResult",
    "RelatedExperiment",
    "Relation",
    "ResearchMemory",
    "StructuralFingerprint",
    "fingerprint",
    "is_append_only_violation",
    "is_reparameterisation",
    "keywords",
    "structural_similarity",
    "structural_tokens",
    "structure_hash",
]
