"""S0–S3 and S7–S9 for an agent-authored candidate. S4 and S6 are refused.

Nothing here imports the broker, the risk gateway, or an order type. A
candidate can be stored. It cannot be submitted to paper.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

__all__ = [
    "CandidateDraft",
    "CandidateRecord",
    "PipelineRefusal",
    "refuse_trial_stage",
    "review_candidate",
    "store_candidate",
]


class PipelineRefusal(Exception):
    """The candidate stops here. This is not a gate pass."""


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    brief: str
    evidence_against: str
    document: Any = None


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    candidate_id: str
    brief: str
    evidence_against: str
    status: str
    can_execute: bool
    refusal: str
    recorded_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "brief": self.brief,
            "evidence_against": self.evidence_against,
            "status": self.status,
            "can_execute": self.can_execute,
            "refusal": self.refusal,
            "recorded_at": self.recorded_at,
        }


def _require_text(value: str, label: str) -> str:
    text = value.strip()
    if len(text) < 8:
        raise PipelineRefusal(f"{label} is required")
    return text


def review_candidate(draft: CandidateDraft, memory: Any | None = None) -> CandidateRecord:
    """S0–S3 and S7–S8. Novelty uses research memory when a document is present."""
    brief = _require_text(draft.brief, "brief")
    evidence = _require_text(draft.evidence_against, "evidence against")
    refusal = "Awaiting human review. Can execute is false."
    status = "awaiting_review"
    if draft.document is not None:
        if memory is None:
            raise PipelineRefusal("novelty check needs research memory")
        result = memory.recall(draft.document)
        if not result.is_novel:
            raise PipelineRefusal(result.recommendation())
    return CandidateRecord(
        candidate_id=f"cand_{uuid4().hex[:12]}",
        brief=brief,
        evidence_against=evidence,
        status=status,
        can_execute=False,
        refusal=refusal,
        recorded_at=datetime.now(tz=UTC).isoformat(),
    )


def refuse_trial_stage(stage: str) -> None:
    """S4 and S6 spend trials. They are not implemented as a run."""
    if stage not in {"S4", "S6"}:
        raise PipelineRefusal(f"{stage} is not a trial stage")
    raise PipelineRefusal(f"{stage} would evaluate bars and spend trials")


def store_candidate(directory: Path, record: CandidateRecord) -> Path:
    """Append one review record. Does not write the experiment ledger or a tier file."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "candidates.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.as_dict(), sort_keys=True) + "\n")
    return path
