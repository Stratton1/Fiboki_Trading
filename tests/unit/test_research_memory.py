"""Research memory must catch a rediscovery BEFORE the work is repeated.

The expensive failure of an automated research loop is not a bad strategy. It is
a good process rediscovering the same dead end every few weeks, arriving at it
with a period of 21 instead of 14 so that nothing keyed on a content hash
notices.
"""
from __future__ import annotations

import pytest

from fiboki.research.experiment import (
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    Outcome,
)
from fiboki.research.memory import Relation, ResearchMemory
from fiboki.research.structure import (
    fingerprint,
    is_reparameterisation,
    structural_similarity,
)
from tests.validation_fixtures import reparameterise, seed_document


@pytest.fixture
def ledger():
    with ExperimentLedger.in_memory() as led:
        yield led


@pytest.fixture
def memory(ledger):
    return ResearchMemory(ledger)


def record(ledger, document, *, reason, outcome, rejection="", rung="", **kwargs):
    return ledger.create(
        ExperimentDraft(
            actor_kind=ActorKind.AGENT,
            actor_name="agent:research-loop",
            reason=reason,
            strategy_document=document,
            outcome=outcome,
            rejection_reason=rejection,
            dataset_version_id="eurusd_h1_v3",
            **kwargs,
        )
    )


class TestStructuralFingerprints:
    def test_changing_only_numbers_keeps_the_structure_hash(self) -> None:
        doc = seed_document()
        tweaked = reparameterise(doc, rsi_period=21, rsi_ceiling=35.0)
        a, b = fingerprint(doc), fingerprint(tweaked)
        assert a.content_hash != b.content_hash
        assert a.structure_hash == b.structure_hash
        assert is_reparameterisation(doc, tweaked)

    def test_a_different_strategy_family_is_structurally_distant(self) -> None:
        a = fingerprint(seed_document("rsi_band_mean_reversion"))
        b = fingerprint(seed_document("ichimoku_kumo_trend"))
        assert a.structure_hash != b.structure_hash
        assert structural_similarity(a, b) < 0.5

    def test_similarity_with_itself_is_one(self) -> None:
        a = fingerprint(seed_document())
        assert structural_similarity(a, a) == pytest.approx(1.0)


class TestTheReparameterisedDuplicateIsCaught:
    """The headline case: a content hash cannot see this, so the structure must."""

    def test_recall_flags_a_reparameterisation_and_refuses_to_call_it_novel(
        self, ledger, memory
    ) -> None:
        original = seed_document()
        record(
            ledger,
            original,
            reason="test RSI band mean reversion on H1 majors",
            outcome=Outcome.REJECTED,
            rejection="deflated_sharpe: observed 0.41, required > 0.95",
            rung="RUNG 5 DEFLATION",
        )
        proposal = reparameterise(original, rsi_period=21, bb_num_std=2.5)

        result = memory.recall(proposal)
        assert not result.is_novel
        assert result.reparameterisations
        assert result.reparameterisations[0].relation is Relation.REPARAMETERISATION
        assert result.exact == ()  # the content hash genuinely differs

    def test_the_recommendation_says_do_not_repeat_and_explains_why(
        self, ledger, memory
    ) -> None:
        original = seed_document()
        for i in range(3):
            record(
                ledger,
                reparameterise(original, rsi_period=7 + i * 3),
                reason=f"re-test RSI band mean reversion, sweep {i}",
                outcome=Outcome.REJECTED,
                rejection="deflated_sharpe 0.41 against a required > 0.95",
            )
        proposal = reparameterise(original, rsi_period=19)

        recommendation = memory.recall(proposal).recommendation()
        assert "DO NOT REPEAT" in recommendation
        assert "REPARAMETERISATION" in recommendation
        assert "3 prior experiment(s)" in recommendation
        assert "materially different reasoning" in recommendation

    def test_an_exact_duplicate_outranks_a_reparameterisation(self, ledger, memory) -> None:
        original = seed_document()
        record(ledger, reparameterise(original, rsi_period=9), reason="variant",
               outcome=Outcome.REJECTED)
        record(ledger, original, reason="the original run", outcome=Outcome.REJECTED)

        result = memory.recall(original)
        assert result.matches[0].relation is Relation.EXACT
        assert "EXACT DUPLICATE" in result.recommendation()

    def test_a_promoted_ancestor_is_called_out_differently(self, ledger, memory) -> None:
        original = seed_document()
        record(ledger, original, reason="the run that worked", outcome=Outcome.PROMOTED)
        assert "ALREADY PROMOTED" in memory.recall(original).recommendation()

    def test_has_been_tried_is_the_one_line_form(self, ledger, memory) -> None:
        original = seed_document()
        assert not memory.has_been_tried(original)
        record(ledger, original, reason="first run", outcome=Outcome.REJECTED)
        assert memory.has_been_tried(original)
        assert memory.has_been_tried(reparameterise(original, rsi_period=21))

    def test_outcomes_for_a_structure_are_countable(self, ledger, memory) -> None:
        original = seed_document()
        record(ledger, original, reason="a", outcome=Outcome.REJECTED)
        record(ledger, reparameterise(original, rsi_period=21), reason="b",
               outcome=Outcome.REJECTED)
        assert memory.outcomes_for_structure(original) == {"rejected": 2}


class TestNovelty:
    def test_an_unrelated_strategy_is_novel(self, ledger, memory) -> None:
        record(ledger, seed_document("rsi_band_mean_reversion"), reason="mean reversion run",
               outcome=Outcome.REJECTED)
        result = memory.recall(seed_document("ichimoku_kumo_trend"))
        assert result.is_novel

    def test_an_empty_ledger_recommends_proceeding(self, memory) -> None:
        result = memory.recall(seed_document())
        assert result.is_novel
        assert result.matches == ()
        assert "NOVEL" in result.recommendation()

    def test_recall_needs_something_to_go_on(self, memory) -> None:
        with pytest.raises(ValueError, match="needs a strategy document"):
            memory.recall()


class TestTheWorkedExample:
    """"Add RSI confirmation to an N-wave strategy" -- asked in words, before work."""

    @pytest.fixture
    def populated(self, ledger):
        doc = seed_document()
        record(
            ledger,
            doc,
            reason=(
                "Agent proposal: add an RSI confirmation leg to the n-wave "
                "breakout strategy, on the theory that momentum confirms the wave count"
            ),
            outcome=Outcome.REJECTED,
            rejection="pbo: observed 0.44, required < 0.2",
            hypothesis_id="hyp_rsi_confirms_waves",
        )
        record(
            ledger,
            reparameterise(doc, rsi_period=21),
            reason=(
                "Second attempt at RSI confirmation on the n-wave family, with a "
                "slower RSI period"
            ),
            outcome=Outcome.REJECTED,
            rejection="deflated_sharpe: observed 0.62, required > 0.95",
            hypothesis_id="hyp_rsi_confirms_waves",
        )
        record(
            ledger,
            seed_document("ichimoku_kumo_trend"),
            reason="Baseline ichimoku kumo trend sweep on H4 majors",
            outcome=Outcome.PROMOTED,
        )
        return ledger

    def test_the_question_surfaces_the_prior_attempts(self, populated) -> None:
        memory = ResearchMemory(populated)
        result = memory.recall(text="add RSI confirmation to an N-wave strategy")
        assert len(result.matches) == 2
        assert all(m.relation is Relation.KEYWORD for m in result.matches)
        assert all("rsi" in m.why for m in result.matches)

    def test_it_reports_what_happened_to_each_of_them(self, populated) -> None:
        memory = ResearchMemory(populated)
        result = memory.recall(text="add RSI confirmation to an N-wave strategy")
        assert result.outcomes == {"rejected": 2}
        described = result.describe()
        assert "rejected" in described

    def test_the_unrelated_promoted_experiment_is_not_dragged_in(self, populated) -> None:
        memory = ResearchMemory(populated)
        result = memory.recall(text="add RSI confirmation to an N-wave strategy")
        assert all("ichimoku" not in m.experiment.reason.lower() for m in result.matches)

    def test_a_document_and_a_description_combine(self, populated) -> None:
        memory = ResearchMemory(populated)
        proposal = reparameterise(seed_document(), rsi_ceiling=45.0)
        result = memory.recall(proposal, text="add RSI confirmation to an N-wave strategy")
        assert not result.is_novel
        assert "DO NOT REPEAT" in result.recommendation()

    def test_the_result_serialises_for_an_agent_to_read(self, populated) -> None:
        memory = ResearchMemory(populated)
        payload = memory.recall(text="add RSI confirmation to an N-wave strategy").to_dict()
        assert payload["is_novel"] is True  # no structural match from a text-only query
        assert len(payload["matches"]) == 2
        assert payload["recommendation"]
