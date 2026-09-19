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
    """``research.structure`` has no token for the session or event restriction.

    Two documents that differ only in WHEN they may deal therefore share a
    structure hash, and research memory calls the second a reparameterisation of
    the first. It is not: "these rules, London only" is a different piece of
    research from "these rules, unrestricted". The discovery layer detects the
    blind spot and demotes the relation rather than skipping real work.
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

        # The pre-existing fingerprint genuinely cannot tell them apart...
        assert structure_hash(mutant) == structure_hash(base)

        file_experiment(ledger, base, reason="the unrestricted donchian baseline")
        verdict = NoveltyIndex(ledger).assess(mutant, dataset_version_id=DATASET)

        # ...so the discovery layer does, and the mutant is queued.
        assert verdict.decision is NoveltyDecision.PROCEED
        assert verdict.is_novel
        assert verdict.diagnostics["n_demoted_by_blind_spot"] == 1
        assert verdict.prior_attempts[0].relation == "structural_variant"

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
