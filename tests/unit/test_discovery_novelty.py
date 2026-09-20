"""Novelty detection: the exact duplicate, the reparameterised rediscovery, and
the worked example from the Phase K brief.

The worked example is the whole point of the module, so it is asserted
literally: "adding RSI to N-wave was tested in E-1241, E-1282 and E-1309; it
improved in-sample and reduced OOS; do not repeat without materially different
reasoning."
"""
from __future__ import annotations

import json

import pytest

from fiboki.discovery.mutation import MutationEngine
from fiboki.discovery.novelty import NoveltyDecision, NoveltyIndex
from fiboki.research.experiment import ActorKind, ExperimentDraft, ExperimentLedger, Outcome
from fiboki.strategy.dsl import StrategyDocument
from tests.discovery_fixtures import seed

DATASET = "ds_xauusd_h4_0001"


@pytest.fixture
def ledger():
    with ExperimentLedger.in_memory() as led:
        yield led


def file_experiment(
    ledger: ExperimentLedger,
    document: StrategyDocument,
    *,
    reason: str,
    conclusion: str = "",
    outcome: Outcome = Outcome.REJECTED,
    rung: str = "",
    dataset_version_id: str = DATASET,
):
    return ledger.create(
        ExperimentDraft(
            actor_kind=ActorKind.AGENT,
            actor_name="agent:prior-campaign",
            reason=reason,
            strategy_id=document.strategy_id,
            strategy_document=document,
            dataset_version_id=dataset_version_id,
            outcome=outcome,
            conclusion=conclusion,
            rejection_reason=rung and f"died at {rung}",
        )
    )


def reparameterise(document: StrategyDocument, **defaults: float) -> StrategyDocument:
    """Same rules and indicators, different numbers: a structural twin."""
    raw = json.loads(document.to_json())
    for name, value in defaults.items():
        raw["parameters"][name]["default"] = value
    return StrategyDocument.model_validate(raw)


class TestExactDuplicates:
    def test_the_identical_document_is_not_novel(self, ledger) -> None:
        document = seed("donchian_breakout_atr")
        file_experiment(ledger, document, reason="first run of the donchian seed")
        verdict = NoveltyIndex(ledger).assess(document, dataset_version_id=DATASET)
        assert not verdict.is_novel
        assert verdict.decision is NoveltyDecision.SKIP_REDISCOVERY
        assert not verdict.should_run
        assert verdict.prior_attempts[0].relation == "exact_duplicate"
        assert verdict.prior_attempts[0].similarity == 1.0

    def test_an_unseen_document_is_novel(self, ledger) -> None:
        verdict = NoveltyIndex(ledger).assess(
            seed("donchian_breakout_atr"), dataset_version_id=DATASET
        )
        assert verdict.is_novel
        assert verdict.decision is NoveltyDecision.PROCEED
        assert verdict.prior_attempts == ()
        assert "nothing in the ledger resembles this" in verdict.narrative()


class TestReparameterisedRediscoveries:
    def test_the_same_structure_with_different_numbers_is_caught(self, ledger) -> None:
        """A content hash cannot see this. It is how a search actually re-finds."""
        original = seed("donchian_breakout_atr")
        file_experiment(
            ledger,
            original,
            reason="donchian breakout on XAUUSD H4",
            conclusion="walk-forward efficiency 38.9 against a 50 bar",
            rung="RUNG 2 WALK_FORWARD",
        )
        twin = reparameterise(original, channel_period=35.0, trend_ema=150.0)
        assert twin.content_hash() != original.content_hash()

        verdict = NoveltyIndex(ledger).assess(twin, dataset_version_id=DATASET)
        assert not verdict.is_novel
        assert verdict.decision is NoveltyDecision.SKIP_REDISCOVERY
        assert verdict.prior_attempts[0].relation == "reparameterisation"
        # The STRUCTURE matched even though the content hash did not.
        from fiboki.research.structure import structure_hash

        assert verdict.structure_hash == structure_hash(original)
        assert verdict.content_hash == twin.content_hash()

    def test_new_parameter_values_alone_do_not_override_the_skip(self, ledger) -> None:
        original = seed("ichimoku_kumo_trend")
        file_experiment(ledger, original, reason="ichimoku kumo trend baseline")
        twin = reparameterise(original, kijun_period=30.0)
        verdict = NoveltyIndex(ledger).assess(twin, dataset_version_id=DATASET)
        assert verdict.decision is NoveltyDecision.SKIP_REDISCOVERY

    def test_a_new_dataset_version_IS_materially_different(self, ledger) -> None:
        original = seed("ichimoku_kumo_trend")
        file_experiment(ledger, original, reason="ichimoku on the 2024 vintage")
        verdict = NoveltyIndex(ledger).assess(
            original, dataset_version_id="ds_xauusd_h4_2026_vintage"
        )
        assert verdict.decision is NoveltyDecision.PROCEED_WITH_CAUTION
        assert verdict.should_run
        assert "none of which is the proposed" in verdict.override_reason

    def test_an_explicit_written_reason_overrides_and_is_recorded(self, ledger) -> None:
        original = seed("ichimoku_kumo_trend")
        file_experiment(ledger, original, reason="ichimoku baseline")
        verdict = NoveltyIndex(ledger).assess(
            original,
            dataset_version_id=DATASET,
            override_reason="the engine now honours trailing stops, so the prior run measured a different strategy",
        )
        assert verdict.decision is NoveltyDecision.PROCEED_WITH_CAUTION
        assert "trailing stops" in verdict.recommendation
        assert "trailing stops" in verdict.to_dict()["override_reason"]


class TestTheWorkedExample:
    """The brief's example, asserted on its own words."""

    def test_it_names_the_prior_experiments_their_outcome_and_the_recommendation(
        self, ledger
    ) -> None:
        base = seed("donchian_breakout_atr")
        engine = MutationEngine(seed=11)
        with_rsi = engine.apply("change_confirmation_rule", base, variant="rsi_midline")
        assert with_rsi.accepted, with_rsi.rejection
        document = with_rsi.document
        assert document is not None

        priors = [
            file_experiment(
                ledger,
                document,
                reason="add RSI confirmation to the N-wave breakout",
                conclusion="it improved in-sample and reduced OOS",
                rung="RUNG 2 WALK_FORWARD",
            )
            for _ in range(3)
        ]

        verdict = NoveltyIndex(ledger).assess(
            document,
            rationale="adding RSI to N-wave",
            dataset_version_id=DATASET,
        )
        narrative = verdict.narrative()

        assert verdict.decision is NoveltyDecision.SKIP_REDISCOVERY
        assert len(verdict.prior_attempts) == 3
        for prior in priors:
            assert prior.id[:12] in narrative
        assert narrative.startswith("adding RSI to N-wave was tested in ")
        assert " and " in narrative  # "E-1, E-2 and E-3"
        assert "it improved in-sample and reduced OOS" in narrative
        assert narrative.endswith("do not repeat without materially different reasoning.")

    def test_the_verdict_round_trips(self, ledger) -> None:
        document = seed("macd_ema_trend_hybrid")
        file_experiment(ledger, document, reason="macd baseline", conclusion="died at rung 0")
        verdict = NoveltyIndex(ledger).assess(document, dataset_version_id=DATASET)
        from fiboki.discovery.novelty import NoveltyVerdict

        assert NoveltyVerdict.from_dict(verdict.to_dict()) == verdict


class TestOutcomesAreReported:
    def test_a_previously_promoted_structure_is_reported_as_such(self, ledger) -> None:
        document = seed("donchian_breakout_atr")
        file_experiment(
            ledger,
            document,
            reason="donchian promoted to paper",
            outcome=Outcome.PROMOTED,
            conclusion="cleared every gate",
        )
        verdict = NoveltyIndex(ledger).assess(document, dataset_version_id=DATASET)
        assert verdict.decision is NoveltyDecision.SKIP_REDISCOVERY
        assert "ALREADY PROMOTED" in verdict.recommendation
        assert verdict.outcomes == {"promoted": 1}

    def test_the_rung_each_prior_died_at_is_carried(self, ledger) -> None:
        document = seed("fib_golden_pocket_pullback")
        file_experiment(
            ledger, document, reason="fib pullback baseline", rung="RUNG 0 SANITY"
        )
        verdict = NoveltyIndex(ledger).assess(document, dataset_version_id=DATASET)
        assert "RUNG 0 SANITY" in verdict.prior_attempts[0].reason
        assert verdict.prior_attempts[0].dataset_version_id == DATASET
        assert verdict.prior_attempts[0].describe()


class TestTheStructuralBlindSpot:
    """When a strategy may deal is now part of its structural fingerprint.

    This used to be a genuine blind spot: ``research.structure`` had no token
    for the session or event restriction, so two documents differing only in
    WHEN they may deal shared a structure hash and research memory called the
    second a reparameterisation of the first. It is not -- "these rules, London
    only" is a different piece of research from "these rules, unrestricted" --
    and the discovery layer papered over it by demoting the relation.

    The fingerprint now carries the dealing window, so the hashes differ at
    source and the demotion never fires for a session variant. The discovery
    layer's guard is LEFT IN PLACE on purpose: it is now redundant for every
    difference the fingerprint has learned to see, it only ever demotes towards
    doing MORE work (never towards skipping), and it keeps its value as a
    backstop for any future restriction field the fingerprint has not been
    taught about.
    """

    def test_a_session_restriction_is_not_a_reparameterisation(self, ledger) -> None:
        from fiboki.research.structure import structure_hash

        base = seed("donchian_breakout_atr")
        engine = MutationEngine(seed=5)
        restricted = engine.apply(
            "change_session_restriction", base, variant="london"
        )
        assert restricted.accepted, restricted.rejection
        mutant = restricted.document
        assert mutant is not None
        assert mutant.sessions is not None and base.sessions is None

        # Fixed at source: the fingerprint can now tell them apart.
        assert structure_hash(mutant) != structure_hash(base)

        file_experiment(ledger, base, reason="the unrestricted donchian baseline")
        verdict = NoveltyIndex(ledger).assess(mutant, dataset_version_id=DATASET)

        assert verdict.decision is NoveltyDecision.PROCEED
        assert verdict.is_novel
        # Nothing to demote any more: the relation was never a
        # reparameterisation in the first place.
        assert verdict.diagnostics["n_demoted_by_blind_spot"] == 0
        assert all(
            p.relation != "reparameterisation" for p in verdict.prior_attempts
        )

    def test_a_blackout_margin_is_a_knob_and_stays_a_reparameterisation(
        self, ledger
    ) -> None:
        """Standing aside for 90 minutes instead of 15 is tuning, not a new idea.

        This is the line the fingerprint draws deliberately: WHICH events and
        WHETHER there is a blackout are structure; HOW WIDE the blackout is is a
        number. Widening it must still read as a rediscovery, or a campaign
        could re-run the same experiment by nudging a margin.
        """
        base = seed("donchian_breakout_atr")
        raw = json.loads(base.to_json())
        raw["events"]["block_minutes_before"] = 90
        raw["events"]["block_minutes_after"] = 90
        widened = StrategyDocument.model_validate(raw)

        from fiboki.research.structure import structure_hash

        assert structure_hash(widened) == structure_hash(base)
        assert widened.content_hash() != base.content_hash()

        file_experiment(ledger, base, reason="the narrow-blackout donchian baseline")
        verdict = NoveltyIndex(ledger).assess(widened, dataset_version_id=DATASET)
        assert verdict.decision is NoveltyDecision.SKIP_REDISCOVERY

    def test_dropping_an_event_tag_is_a_different_piece_of_research(
        self, ledger
    ) -> None:
        """WHICH events are avoided is structure, and the fingerprint sees it."""
        base = seed("donchian_breakout_atr")
        raw = json.loads(base.to_json())
        assert raw["events"]["blocked_event_tags"], "the seed blocks some tags"
        raw["events"]["blocked_event_tags"] = []
        unguarded = StrategyDocument.model_validate(raw)

        from fiboki.research.structure import structure_hash

        assert structure_hash(unguarded) != structure_hash(base)
        file_experiment(ledger, base, reason="the tag-avoiding donchian baseline")
        verdict = NoveltyIndex(ledger).assess(unguarded, dataset_version_id=DATASET)
        assert verdict.decision is NoveltyDecision.PROCEED

    def test_an_identical_session_restriction_is_still_a_rediscovery(
        self, ledger
    ) -> None:
        """The demotion must not become a blanket exemption."""
        base = seed("ichimoku_kumo_trend")
        twin = reparameterise(base, kijun_period=30.0)
        assert base.sessions == twin.sessions
        file_experiment(ledger, base, reason="the ichimoku baseline")
        verdict = NoveltyIndex(ledger).assess(twin, dataset_version_id=DATASET)
        assert verdict.decision is NoveltyDecision.SKIP_REDISCOVERY
        assert verdict.diagnostics["n_demoted_by_blind_spot"] == 0

    def test_a_prior_row_with_no_document_keeps_the_conservative_relation(
        self, ledger
    ) -> None:
        """A missing record makes the campaign MORE likely to skip, not less."""
        base = seed("donchian_breakout_atr")
        ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.AGENT,
                actor_name="agent:prior",
                reason="a report filed without its document",
                strategy_id=base.strategy_id,
                strategy_content_hash=base.content_hash(),
                dataset_version_id=DATASET,
            )
        )
        verdict = NoveltyIndex(ledger).assess(base, dataset_version_id=DATASET)
        assert verdict.decision is NoveltyDecision.SKIP_REDISCOVERY
        assert verdict.prior_attempts[0].relation == "exact_duplicate"
