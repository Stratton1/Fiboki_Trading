"""A hypothesis that cannot be refuted is not filed."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from fiboki.core.enums import Timeframe
from fiboki.discovery.hypothesis import (
    Evidence,
    Hypothesis,
    HypothesisLedger,
    HypothesisStatus,
    load_hypotheses,
)
from fiboki.research.experiment import ActorKind, ExperimentLedger, Outcome
from tests.discovery_fixtures import a_hypothesis


class TestItRefusesAClaimThatCannotFail:
    def test_a_refutation_that_restates_the_prediction_is_refused(self) -> None:
        """The commonest way a "falsifier" field becomes decoration."""
        with pytest.raises(ValidationError, match="restates the prediction"):
            a_hypothesis(refutation=a_hypothesis().prediction)

    def test_a_hypothesis_with_no_evidence_against_is_refused(self) -> None:
        """Every seed document names the literature that disagrees with it."""
        only_for = tuple(e for e in a_hypothesis().evidence if e.direction == "for")
        with pytest.raises(ValidationError, match="no evidence AGAINST"):
            a_hypothesis(evidence=only_for)

    def test_a_short_rationale_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            a_hypothesis(economic_rationale="gold goes up")

    def test_an_unregistered_instrument_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unregistered instruments"):
            a_hypothesis(instruments=("DOGEUSD",))


class TestItStatesItsDomain:
    def test_it_covers_only_what_it_claims(self) -> None:
        h = a_hypothesis()
        assert h.covers("XAUUSD", Timeframe.H4)
        assert not h.covers("EURUSD", Timeframe.H4)
        assert not h.covers("XAUUSD", Timeframe.H1)

    def test_an_empty_domain_is_unrestricted_and_therefore_weaker(self) -> None:
        h = a_hypothesis(instruments=(), timeframes=())
        assert h.covers("EURUSD", Timeframe.M15)

    def test_evidence_is_split_by_direction(self) -> None:
        h = a_hypothesis()
        assert len(h.evidence_for) == 1
        assert len(h.evidence_against) == 1


class TestStatusIsAppendedNeverEdited:
    def test_with_status_returns_a_new_object_and_keeps_the_claim_hash(self) -> None:
        """Status is bookkeeping; the CLAIM is what the ledger identifies."""
        original = a_hypothesis()
        moved = original.with_status(
            HypothesisStatus.TESTING, reason="queued in campaign k1"
        )
        assert original.status is HypothesisStatus.PROPOSED
        assert moved.status is HypothesisStatus.TESTING
        assert moved.content_hash() == original.content_hash()

    def test_a_status_change_needs_a_reason(self) -> None:
        with pytest.raises(ValueError, match="needs a reason"):
            a_hypothesis().with_status(HypothesisStatus.SUPPORTED, reason="  ")

    def test_refuted_by_files_the_observation_as_evidence_against(self) -> None:
        refuted = a_hypothesis().refuted_by(
            "walk-forward efficiency was 38.9 per cent against a 50 per cent bar",
            source="campaign k1",
        )
        assert refuted.status is HypothesisStatus.REFUTED
        assert len(refuted.evidence_against) == 2
        assert refuted.evidence_against[-1].strength == "strong"
        # A refutation CHANGES the claim's evidence, so it is a new claim hash.
        assert refuted.content_hash() != a_hypothesis().content_hash()


class TestPersistence:
    def test_it_round_trips_through_json(self) -> None:
        h = a_hypothesis()
        assert Hypothesis.from_json(h.to_json()) == h

    def test_a_directory_of_documents_loads_in_id_order(self, tmp_path) -> None:
        (tmp_path / "b.json").write_text(a_hypothesis(hypothesis_id="zeta_claim").to_json())
        (tmp_path / "a.json").write_text(a_hypothesis(hypothesis_id="alpha_claim").to_json())
        loaded = load_hypotheses(tmp_path)
        assert [h.hypothesis_id for h in loaded] == ["alpha_claim", "zeta_claim"]

    def test_the_ledger_keeps_the_whole_status_history(self) -> None:
        with ExperimentLedger.in_memory() as ledger:
            store = HypothesisLedger(ledger)
            h = a_hypothesis()
            store.record(h, actor_name="agent:test")
            updated, row = store.set_status(
                h,
                HypothesisStatus.REFUTED,
                reason="nothing cleared rung 2 on XAUUSD H4",
                actor_name="agent:test",
                evidence=Evidence(
                    direction="against",
                    claim="every candidate died at the walk-forward rung",
                    source="campaign k1",
                ),
            )
            history = store.history(h.hypothesis_id)
            assert len(history) == 2
            assert history[1].parent_experiment_id == history[0].id
            assert history[1].outcome is Outcome.REJECTED
            assert store.get(h.hypothesis_id) == updated
            assert store.get(h.hypothesis_id).status is HypothesisStatus.REFUTED
            assert row.id == history[1].id

    def test_the_agent_artefact_view_keeps_the_evidence_against(self) -> None:
        artefact = a_hypothesis().as_artefact(created_by="agent:test")
        assert "AGAINST:" in artefact.rationale
        assert artefact.falsifier == a_hypothesis().refutation
        assert artefact.instruments == ("XAUUSD",)

    def test_an_unfiled_hypothesis_reads_back_as_none(self) -> None:
        with ExperimentLedger.in_memory() as ledger:
            assert HypothesisLedger(ledger).get("never_filed_claim") is None

    def test_recording_uses_the_named_actor_kind(self) -> None:
        with ExperimentLedger.in_memory() as ledger:
            store = HypothesisLedger(ledger)
            row = store.record(
                a_hypothesis(), actor_name="joe", actor_kind=ActorKind.HUMAN
            )
            assert row.actor_kind is ActorKind.HUMAN
            assert [h.hypothesis_id for h in store.list()] == ["gold_trend_persistence"]
