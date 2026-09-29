"""Verdicts, findings and reports for the offline eval harness."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fiboki.agents.audit import AuditRecord
from fiboki.research.artefacts import ResearchStore

#: Version of the harness and its report layout.  Bump when a case's meaning
#: changes, so two reports are only compared like for like.
HARNESS_VERSION = "agent-evals:1"


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    INVALID_ARTIFACT = "INVALID_ARTIFACT"


#: Worst first.  A PASS never outranks anything.
_SEVERITY: dict[Verdict, int] = {
    Verdict.FAIL: 3,
    Verdict.INVALID_ARTIFACT: 2,
    Verdict.NOT_EVALUABLE: 1,
    Verdict.PASS: 0,
}


def worst(verdicts: Iterable[Verdict]) -> Verdict:
    """The most severe verdict; NOT_EVALUABLE when there is nothing at all."""
    found = list(verdicts)
    if not found:
        return Verdict.NOT_EVALUABLE
    return max(found, key=lambda v: _SEVERITY[v])


@dataclass(frozen=True, slots=True)
class Finding:
    """One evaluated unit: a record, an artefact or a workflow run."""

    subject: str
    verdict: Verdict
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "verdict": self.verdict.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class EvalInputs:
    """What a case may look at.  Read-only by convention and by API."""

    records: tuple[AuditRecord, ...]
    store: ResearchStore | None


@dataclass(frozen=True, slots=True)
class EvalCase:
    """A case as data: identity, intent, and a pure check."""

    case_id: str
    title: str
    description: str
    check: Callable[[EvalInputs], Sequence[Finding]]


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    title: str
    verdict: Verdict
    counts: dict[str, int] = field(default_factory=dict)
    #: Every non-PASS finding, in ledger order.  PASS units are counted only.
    findings: tuple[Finding, ...] = ()

    @classmethod
    def from_findings(cls, case: EvalCase, findings: Sequence[Finding]) -> CaseResult:
        counts = {v.value: 0 for v in Verdict}
        for f in findings:
            counts[f.verdict.value] += 1
        return cls(
            case_id=case.case_id,
            title=case.title,
            verdict=worst(f.verdict for f in findings),
            counts=counts,
            findings=tuple(f for f in findings if f.verdict is not Verdict.PASS),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "verdict": self.verdict.value,
            "counts": dict(self.counts),
            "findings": [f.as_dict() for f in self.findings],
        }


@dataclass(frozen=True, slots=True)
class EvalReport:
    """The whole evaluation.  No timestamps, so it is reproducible byte for byte."""

    source: str
    ledger_sha256: str
    n_records: int
    chain_ok: bool
    verdict: Verdict
    cases: tuple[CaseResult, ...]
    store_present: bool
    harness_version: str = HARNESS_VERSION

    def case(self, case_id: str) -> CaseResult:
        for result in self.cases:
            if result.case_id == case_id:
                return result
        raise KeyError(case_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "harness_version": self.harness_version,
            "source": self.source,
            "ledger_sha256": self.ledger_sha256,
            "n_records": self.n_records,
            "chain_ok": self.chain_ok,
            "store_present": self.store_present,
            "verdict": self.verdict.value,
            "cases": [c.as_dict() for c in self.cases],
        }


__all__ = [
    "HARNESS_VERSION",
    "CaseResult",
    "EvalCase",
    "EvalInputs",
    "EvalReport",
    "Finding",
    "Verdict",
    "worst",
]
