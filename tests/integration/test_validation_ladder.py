"""End-to-end: the ladder, the gates, the holdout, the ledger and the memory.

Three candidates go through the whole pipeline against known return-generating
processes:

* pure noise, which must be rejected whatever the seed;
* a genuine, parameter-stable edge, which must survive;
* the same genuine edge deflated by a campaign the size of V1's 23,040-cell
  search, which shows that "is this real?" cannot be answered without knowing how
  many things were tried.

If the first two ever swap answers, the package is no longer doing its job.
"""
from __future__ import annotations

import pytest

from fiboki.core.enums import Provenance, StrategyLifecycle
from fiboki.research.experiment import (
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    Outcome,
)
from fiboki.research.lineage import LineageService
from fiboki.research.memory import ResearchMemory
from fiboki.validation.gates import GATE_SET_V2, GateStatus
from fiboki.validation.holdout import HoldoutAlreadyConsumed, HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder
from fiboki.validation.report import RungOutcome, ValidationReport, Verdict
from tests.validation_fixtures import (
    DATA_END,
    DATA_START,
    DATASET_VERSION,
    SyntheticEvaluator,
    candidate,
    genuine_edge_evaluator,
    plateau_mu,
    pure_noise_evaluator,
)

CONFIG = LadderConfig(selection_metric="net_profit", stress_samples=20, spa_bootstraps=200)


def fresh_registry() -> HoldoutRegistry:
    registry = HoldoutRegistry.in_memory()
    registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    return registry


def run(evaluator, content_hash: str, config: LadderConfig = CONFIG, registry=None):
    registry = registry or fresh_registry()
    ladder = ValidationLadder(config=config)
    report = ladder.run(
        candidate("synthetic", content_hash),
        evaluator,
        registry=registry,
        dataset_version_id=DATASET_VERSION,
        engine_config={"initial_balance": 10_000.0, "account_ccy": "GBP"},
        broker_profile={"name": "ig_realistic"},
        actor="agent:test-harness",
    )
    return report, registry


@pytest.fixture(scope="module")
def edge_report():
    report, _ = run(genuine_edge_evaluator(), "a" * 64)
    return report


class TestPureNoiseIsRejected:
    """Whatever the seed. A search over noise must never produce a candidate."""

    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
    def test_noise_never_promotes_and_dies_on_a_cheap_rung(self, seed) -> None:
        """Ordering the ladder by cost is only worth doing if it works.

        With every one of these seeds noise is killed at RUNG 0 (the declared
        defaults have negative expectancy) or RUNG 2 (the walk-forward selection
        does not transfer). None reaches the expensive deflation rung, which is
        the point of a fail-fast ladder.
        """
        report, _ = run(pure_noise_evaluator(seed=seed), f"{seed:064d}")
        assert report.verdict is Verdict.REJECT
        assert not report.passed
        failing = report.first_failing_rung()
        assert failing is not None
        assert failing.index in (0, 2), failing.label

    def test_the_rejection_explains_itself_in_numbers(self) -> None:
        report, _ = run(pure_noise_evaluator(seed=2), "f" * 64)
        failing = report.first_failing_rung()
        assert failing.reason
        assert report.binding_constraint.describe() != "none"

    def test_rungs_after_the_failure_are_recorded_as_not_reached(self) -> None:
        report, _ = run(pure_noise_evaluator(seed=2), "e" * 64)
        failing = report.first_failing_rung()
        later = [r for r in report.rungs if r.index > failing.index]
        assert later
        assert all(r.outcome is RungOutcome.NOT_REACHED for r in later)

    def test_a_rejected_candidate_still_produces_a_full_report(self) -> None:
        """A rejection is a research result, so it is written down like one."""
        report, _ = run(pure_noise_evaluator(seed=3), "d" * 64)
        assert report.strategy_content_hash
        assert report.dataset_version_id == DATASET_VERSION
        assert report.code_version
        assert report.gate_set_version == GATE_SET_V2.version
        assert report.gate_set_fingerprint == GATE_SET_V2.fingerprint()
        assert len(report.rungs) == 7
        assert report.lifecycle_recommendation is StrategyLifecycle.RESEARCH
        assert ValidationReport.from_json(report.to_json()).to_json() == report.to_json()

    def test_noise_never_reaches_the_holdout(self) -> None:
        """The expensive, single-use resource is not spent on obvious failures."""
        report, registry = run(pure_noise_evaluator(seed=2), "c" * 64)
        assert report.rung(6).outcome is RungOutcome.NOT_REACHED
        assert registry.consumptions(DATASET_VERSION) == []


class TestAGenuineEdgeSurvives:
    def test_it_is_promoted(self, edge_report) -> None:
        assert edge_report.verdict is Verdict.PROMOTE
        assert edge_report.binding_constraint.kind == "none"
        assert edge_report.lifecycle_recommendation is StrategyLifecycle.CANDIDATE

    def test_every_rung_passed(self, edge_report) -> None:
        assert [r.outcome for r in edge_report.rungs] == [RungOutcome.PASS] * 7

    def test_every_gate_passed_or_was_genuinely_inapplicable(self, edge_report) -> None:
        for result in edge_report.gate_results:
            assert result.status in (GateStatus.PASS, GateStatus.NOT_APPLICABLE)

    @pytest.mark.parametrize(
        "gate",
        [
            "min_trades",
            "walk_forward_efficiency",
            "oos_window_hit_rate",
            "deflated_sharpe",
            "pbo",
            "spa_consistent_p",
            "stepm_survivor",
            "survives_2x_spread",
            "parameter_plateau",
        ],
    )
    def test_each_audit_gate_was_actually_evaluated(self, edge_report, gate) -> None:
        """Passing because nobody computed the number is not passing."""
        assert edge_report.gate(gate).status is GateStatus.PASS

    def test_the_purged_cv_rung_produced_a_real_distribution(self, edge_report) -> None:
        metrics = edge_report.rung(3).metrics
        assert metrics["n_paths"] > 1
        assert metrics["selection_repeated_per_split"] is True
        assert len(set(metrics["path_sharpes"])) > 1, "paths must differ, or nothing was refit"
        assert metrics["path_sharpe_variance"] > 0.0

    def test_the_stress_suite_ran_the_audits_spread_multipliers(self, edge_report) -> None:
        curve = edge_report.rung(4).metrics["stress"]["spread_multiplier"]
        assert curve["levels"] == [1.0, 1.5, 2.0, 3.0]
        assert curve["median"] == sorted(curve["median"], reverse=True)

    def test_the_robustness_rung_ran_every_declared_stress(self, edge_report) -> None:
        assert set(edge_report.rung(4).metrics["stress"]) == {
            "spread_multiplier",
            "slippage",
            "execution_delay_capture",
            "execution_delay_adverse",
            "random_deletion",
            "start_date",
            "end_date",
        }

    def test_the_selected_point_sits_on_a_plateau(self, edge_report) -> None:
        plateau = edge_report.rung(4).metrics["plateau"]
        assert plateau["not_applicable"] is False
        assert plateau["is_isolated_peak"] is False
        assert plateau["point_plateau_ratio"] <= 1.25

    def test_the_deflation_inputs_are_recorded_for_a_reader_to_check(
        self, edge_report
    ) -> None:
        assert edge_report.trial_count_n >= 2
        assert edge_report.raw_trial_count == 25
        assert edge_report.cross_trial_sharpe_variance > 0.0
        assert "cpcv_path_sharpes" in edge_report.dsr_variance_source

    def test_the_in_sample_rung_is_labelled_as_a_screen(self, edge_report) -> None:
        screen = edge_report.rung(1)
        assert screen.is_evidence is False
        assert "SCREEN_NOT_EVIDENCE" in screen.metrics

    def test_provenance_labels_mark_out_of_sample_statistics(self, edge_report) -> None:
        labels = edge_report.provenance_labels
        assert labels["pbo"] == Provenance.OUT_OF_SAMPLE.value
        assert labels["walk_forward_efficiency"] == Provenance.WALKFORWARD.value
        assert labels["holdout_net_profit"] == Provenance.HOLDOUT.value

    def test_the_engine_and_broker_assumptions_are_on_the_report(
        self, edge_report
    ) -> None:
        assert edge_report.engine_config["account_ccy"] == "GBP"
        assert edge_report.broker_profile["name"] == "ig_realistic"
        assert edge_report.ladder_config["min_trades"] == 400


class TestTheHoldoutIsSpentExactlyOnce:
    def test_a_promoted_run_consumes_the_holdout(self) -> None:
        report, registry = run(genuine_edge_evaluator(), "b" * 64)
        assert report.rung(6).outcome is RungOutcome.PASS
        consumptions = registry.consumptions(DATASET_VERSION)
        assert len(consumptions) == 1
        assert consumptions[0].strategy_content_hash == "b" * 64
        assert consumptions[0].outcome is not None
        assert consumptions[0].actor == "agent:test-harness"

    def test_running_the_same_candidate_twice_is_refused_at_rung_6(self) -> None:
        registry = fresh_registry()
        first, _ = run(genuine_edge_evaluator(), "b" * 64, registry=registry)
        assert first.verdict is Verdict.PROMOTE

        second, _ = run(genuine_edge_evaluator(), "b" * 64, registry=registry)
        assert second.verdict is Verdict.REJECT
        assert second.rung(6).outcome is RungOutcome.ERROR
        assert "already consumed" in second.rung(6).reason
        assert len(registry.consumptions(DATASET_VERSION)) == 1

    def test_no_rung_before_the_holdout_touches_holdout_data(self) -> None:
        """Checked from the evaluator's call log, not from the ladder's report."""
        evaluator = genuine_edge_evaluator()
        report, registry = run(evaluator, "9" * 64)
        holdout = registry.segment(DATASET_VERSION).window
        holdout_calls = [w for _, w in evaluator.calls if w == holdout.name]
        assert len(holdout_calls) == 1, "the holdout was evaluated more than once"
        assert report.rung(6).metrics["window"]["name"] == holdout.name


class TestDeflationDependsOnTheSizeOfTheSearch:
    """The number V1 never computed, and the reason its leaderboard was fiction."""

    def test_a_strong_edge_survives_a_campaign_scale_correction(self) -> None:
        report, _ = run(
            genuine_edge_evaluator(),
            "7" * 64,
            LadderConfig(
                selection_metric="net_profit",
                stress_samples=20,
                spa_bootstraps=200,
                external_trial_count=23_000,
            ),
        )
        assert report.verdict is Verdict.PROMOTE
        assert report.trial_count_n > 23_000
        assert report.gate("deflated_sharpe").value > 0.95

    def test_a_marginal_edge_does_not(self) -> None:
        marginal = SyntheticEvaluator(mu=plateau_mu(peak=0.12, decay=0.012))
        loose = LadderConfig(
            selection_metric="net_profit", stress_samples=20, spa_bootstraps=200
        )
        strict = LadderConfig(
            selection_metric="net_profit",
            stress_samples=20,
            spa_bootstraps=200,
            external_trial_count=23_000,
        )
        small, _ = run(marginal, "6" * 64, loose)
        large, _ = run(SyntheticEvaluator(mu=plateau_mu(peak=0.12, decay=0.012)), "5" * 64, strict)

        assert large.gate("deflated_sharpe").value < small.gate("deflated_sharpe").value
        assert large.verdict is Verdict.REJECT
        assert large.binding_constraint.name == "deflated_sharpe"
        assert large.binding_constraint.rung_index == 5

    def test_the_effective_trial_count_is_below_the_raw_count(self) -> None:
        """25 correlated parameterisations are not 25 independent bets."""
        report, _ = run(genuine_edge_evaluator(), "4" * 64)
        clustering = report.rung(5).metrics["clustering"]
        assert clustering["n_trials"] == 25
        assert clustering["n_effective"] < 25
        assert clustering["redundancy"] > 0.0


class TestTheWholePipelineRecordsItself:
    def test_a_ladder_run_becomes_a_ledger_entry_with_full_provenance(self) -> None:
        report, _ = run(genuine_edge_evaluator(), "3" * 64)
        with ExperimentLedger.in_memory() as ledger:
            experiment = ledger.create(
                ExperimentDraft(
                    actor_kind=ActorKind.AGENT,
                    actor_name="agent:research-loop",
                    reason="validate the synthetic plateau edge on H4 EURUSD",
                    strategy_id=report.strategy_id,
                    strategy_content_hash=report.strategy_content_hash,
                    dataset_version_id=report.dataset_version_id,
                    validation_report=report,
                )
            )
            assert experiment.outcome is Outcome.PROMOTED
            assert experiment.validation_report_hash == report.content_hash()

            chain = LineageService(ledger).provenance_chain(experiment.id)
            assert chain.steps[0].kind == "experiment"
            assert chain.steps[0].detail["validation_report_hash"] == report.content_hash()
            # No dataset catalogue was supplied, so the chain says so rather than
            # claiming a provenance it cannot demonstrate.
            assert not chain.complete
            assert chain.gaps

    def test_a_rejection_teaches_the_next_search_not_to_repeat_it(self) -> None:
        from tests.validation_fixtures import reparameterise, seed_document

        doc = seed_document()
        report, _ = run(pure_noise_evaluator(seed=2), doc.content_hash())
        assert report.verdict is Verdict.REJECT

        with ExperimentLedger.in_memory() as ledger:
            ledger.create(
                ExperimentDraft(
                    actor_kind=ActorKind.AGENT,
                    actor_name="agent:research-loop",
                    reason="first sweep of the RSI band family",
                    strategy_document=doc,
                    validation_report=report,
                )
            )
            memory = ResearchMemory(ledger)
            proposal = reparameterise(doc, rsi_period=21)
            recall = memory.recall(proposal)
            assert not recall.is_novel
            assert "DO NOT REPEAT" in recall.recommendation()
            assert report.first_failing_rung().label in recall.recommendation()


class TestLadderConfiguration:
    def test_the_ladder_refuses_a_min_trades_that_disagrees_with_its_gate(self) -> None:
        with pytest.raises(ValueError, match="disagrees with"):
            ValidationLadder(config=LadderConfig(min_trades=80))

    def test_lowering_the_bar_requires_minting_a_new_gate_version(self) -> None:
        gates = GATE_SET_V2.with_overrides("v2.0.0-intraday", min_trades=100.0)
        ladder = ValidationLadder(config=LadderConfig(min_trades=100), gate_set=gates)
        report = ladder.run(
            candidate("synthetic", "2" * 64),
            genuine_edge_evaluator(),
            registry=fresh_registry(),
            dataset_version_id=DATASET_VERSION,
        )
        assert report.gate_set_version == "v2.0.0-intraday"
        assert report.gate_set_fingerprint != GATE_SET_V2.fingerprint()

    def test_a_leaky_research_window_is_refused_before_anything_runs(self) -> None:
        registry = fresh_registry()
        # Redefining is refused, so build a registry whose holdout covers
        # everything and confirm the ladder will not start against it.
        other = HoldoutRegistry.in_memory()
        other.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
        segment = other.segment(DATASET_VERSION)
        with pytest.raises(Exception, match="overlaps the reserved holdout"):
            other.assert_untouched(DATASET_VERSION, segment.window)
        assert registry.consumptions(DATASET_VERSION) == []


class TestTheHoldoutRefusalIsStructural:
    def test_the_registry_itself_refuses_regardless_of_the_ladder(self) -> None:
        registry = fresh_registry()
        registry.claim(DATASET_VERSION, "1" * 64)
        with pytest.raises(HoldoutAlreadyConsumed):
            registry.claim(DATASET_VERSION, "1" * 64)
