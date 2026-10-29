"""Agent candidates stop before a trial and cannot execute."""
from __future__ import annotations

import pytest

from fiboki.agents.candidate_pipeline import (
    CandidateDraft,
    PipelineRefusal,
    refuse_trial_stage,
    review_candidate,
    store_candidate,
)


def test_a_brief_without_evidence_against_is_refused() -> None:
    with pytest.raises(PipelineRefusal, match="evidence against"):
        review_candidate(CandidateDraft(brief="A carry idea", evidence_against=""))


def test_review_cannot_execute_and_trial_stages_are_refused(tmp_path) -> None:
    record = review_candidate(
        CandidateDraft(
            brief="Carry on the majors",
            evidence_against="Published studies find the edge dies after costs.",
        )
    )
    assert record.can_execute is False
    assert record.status == "awaiting_review"
    path = store_candidate(tmp_path, record)
    assert path.name == "candidates.jsonl"
    for stage in ("S4", "S6"):
        with pytest.raises(PipelineRefusal, match="spend trials"):
            refuse_trial_stage(stage)


def test_a_rediscovery_stops_the_candidate() -> None:
    class Memory:
        def recall(self, document: object) -> object:
            class Result:
                is_novel = False

                def recommendation(self) -> str:
                    return "DO NOT REPEAT"

            return Result()

    with pytest.raises(PipelineRefusal, match="DO NOT REPEAT"):
        review_candidate(
            CandidateDraft(
                brief="Same structure again",
                evidence_against="The prior rejection still stands.",
                document=object(),
            ),
            Memory(),
        )
