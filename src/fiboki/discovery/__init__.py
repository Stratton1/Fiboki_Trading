"""Phase K: automated strategy discovery, and the machinery that makes it honest.

    hypothesis  a falsifiable claim, its domain, and the evidence AGAINST it
    mutation    a closed set of controlled operators over the strategy DSL
    novelty     "have we tried this?", asked BEFORE compute is spent
    campaign    the runner: propose, filter, queue, validate, record, report
    report      what was tried, what was skipped, what survived, and what that
                does and does not demonstrate

Everything below this package already exists to make the results here
trustworthy: the DSL makes a strategy data that can be mutated and hashed, the
ladder makes a rejection cheap and a promotion expensive, the holdout registry
makes the last look unrepeatable, and the experiment ledger makes the whole
history append-only. This package is the part that actually searches -- and the
single thing it must get right is the one V1 got wrong: the deflation has to
know how big the search was, not how big one strategy's parameter sweep was.
"""
from __future__ import annotations

from fiboki.discovery.campaign import (
    BarSet,
    BarSource,
    CampaignCheckpoint,
    CampaignPlan,
    CampaignRunner,
    CampaignSpec,
    CandidateCell,
    CellOutcome,
    CellStatus,
    run_cell,
    seed_documents,
    store_bar_source,
)
from fiboki.discovery.hypothesis import (
    Evidence,
    Hypothesis,
    HypothesisLedger,
    HypothesisStatus,
    load_hypotheses,
)
from fiboki.discovery.mutation import (
    MUTATION_OPERATORS,
    FeasibilityReport,
    MutationEngine,
    MutationProposal,
    domain_feasibility,
    mutation_lineage,
)
from fiboki.discovery.novelty import (
    NoveltyDecision,
    NoveltyIndex,
    NoveltyVerdict,
    PriorAttempt,
)
from fiboki.discovery.report import (
    CampaignReport,
    CellResult,
    SkippedCell,
    deflation_threshold,
)

__all__ = [
    "MUTATION_OPERATORS",
    "BarSet",
    "BarSource",
    "CampaignCheckpoint",
    "CampaignPlan",
    "CampaignReport",
    "CampaignRunner",
    "CampaignSpec",
    "CandidateCell",
    "CellOutcome",
    "CellResult",
    "CellStatus",
    "Evidence",
    "FeasibilityReport",
    "Hypothesis",
    "HypothesisLedger",
    "HypothesisStatus",
    "MutationEngine",
    "MutationProposal",
    "NoveltyDecision",
    "NoveltyIndex",
    "NoveltyVerdict",
    "PriorAttempt",
    "SkippedCell",
    "deflation_threshold",
    "domain_feasibility",
    "load_hypotheses",
    "mutation_lineage",
    "run_cell",
    "seed_documents",
    "store_bar_source",
]
