"""The single most important correctness property of Phase K, end to end.

A campaign's true trial count must reach the ladder's deflation rung and change
the answer. This test wires a real :class:`~fiboki.validation.ladder.ValidationLadder`
behind the campaign's validator seam, against a synthetic evaluator whose edge is
known exactly, and runs the SAME candidate twice:

* once as the only thing in the campaign, so the deflation sees the parameter
  sweep alone -- which is what V1 did, and it promotes;
* once as one cell of a campaign the size of V1's search, so the deflation sees
  the whole search -- and the identical strategy is rejected.

If those two ever agree, the deflation is not being told how big the search was
and every ranking the platform produces is a sampling distribution of the
maximum.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from fiboki.core.enums import Timeframe
from fiboki.discovery.campaign import (
    CampaignCheckpoint,
    CampaignRunner,
    CampaignSpec,
    CellOutcome,
)
from fiboki.research.experiment import ExperimentLedger
from fiboki.validation.evaluation import Candidate
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder
from fiboki.validation.report import Verdict
from tests.discovery_fixtures import a_hypothesis, bar_source, one_barset, seed
from tests.validation_fixtures import (
    DATA_END,
    DATA_START,
    DATASET_VERSION,
    SyntheticEvaluator,
    candidate,
    genuine_edge_evaluator,
    plateau_mu,
)

BARS = one_barset(version=DATASET_VERSION)
SOURCE = bar_source({("XAUUSD", "H4"): BARS})


def modest_edge_evaluator() -> SyntheticEvaluator:
    """A real, parameter-stable, MODEST plateau edge.

    Deliberately modest. A large edge survives any plausible deflation and a
    tiny one fails every rung, either of which would make this test pass for the
    wrong reason. This one clears every other gate at both trial counts, so the
    only thing that can move its verdict is the deflation -- which is precisely
    what is being measured.
    """
    return SyntheticEvaluator(mu=plateau_mu(peak=0.20, decay=0.02))

#: The gate set the ladder is run under here. ``min_trades`` is lowered because
#: the SYNTHETIC evaluator produces one trade per period rather than per signal,
#: and a NEW version string is minted so nothing produced under it can be
#: mistaken for a promotion decision under the audit's own bar.
SYNTHETIC_GATES = GATE_SET_V2.with_overrides("test-synthetic-min40", min_trades=40.0)


class LadderValidator:
    """Runs the REAL ladder, with the campaign's ladder config, on a known edge."""

    def __init__(self, evaluator_factory=genuine_edge_evaluator) -> None:
        self.configs: list[LadderConfig] = []
        self.evaluator_factory = evaluator_factory

    def __call__(self, **kwargs) -> CellOutcome:
        config: LadderConfig = kwargs["ladder_config"]
        self.configs.append(config)
        registry = HoldoutRegistry.in_memory()
        registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
        ladder = ValidationLadder(
            config=replace(
                config,
                min_trades=int(SYNTHETIC_GATES.by_name("min_trades").threshold),
                selection_metric="net_profit",
                stress_samples=20,
                spa_bootstraps=200,
            ),
            gate_set=SYNTHETIC_GATES,
        )
        cell = kwargs["cell"]
        report = ladder.run(
            _candidate_for(cell),
            self.evaluator_factory(),
            registry=registry,
            dataset_version_id=DATASET_VERSION,
            actor="tests:discovery",
        )
        return CellOutcome(
            report=report,
            n_evaluations=int(report.windows.get("n_evaluations", 0) or 0),
            engine_runs=0,
        )


def _candidate_for(cell) -> Candidate:
    """The synthetic fixture's candidate, wearing this cell's identity."""
    base = candidate("synthetic", cell.content_hash)
    return Candidate(
        strategy_id=cell.strategy_id,
        content_hash=cell.content_hash,
        default_params=base.default_params,
        grid=base.grid,
    )


def a_spec(campaign_id: str, **overrides) -> CampaignSpec:
    payload = {
        "campaign_id": campaign_id,
        "universe": ("XAUUSD",),
        "timeframes": (Timeframe.H4,),
        "hypotheses": (a_hypothesis(),),
        "actor": "tests:discovery",
        "gate_set": SYNTHETIC_GATES,
        "max_grid_points": 8,
        "max_values_per_axis": 2,
        "sweep_parameters": {
            "donchian_breakout_atr": (
                "channel_period",
                "stop_atr_multiple",
                "trail_atr_multiple",
            )
        },
        "max_generations": 0,
        "include_prior_trials": False,
    }
    payload.update(overrides)
    return CampaignSpec(**payload)


def run_campaign(
    spec: CampaignSpec, *, prior_trials: int = 0, evaluator=genuine_edge_evaluator
):
    ledger = ExperimentLedger.in_memory()
    validator = LadderValidator(evaluator)
    runner = CampaignRunner(
        spec,
        bars=SOURCE,
        ledger=ledger,
        registry=HoldoutRegistry.in_memory(),
        checkpoint=CampaignCheckpoint.in_memory(),
        validator=validator,
    )
    plan = runner.plan([seed("donchian_breakout_atr")])
    if prior_trials:
        plan = replace(plan, prior_trial_count=prior_trials)
    report = runner.run(plan)
    ledger.close()
    return report, plan, validator


@pytest.mark.slow
class TestTheCampaignsTrueTrialCountReachesTheDeflation:
    def test_a_lone_candidate_and_a_large_campaign_disagree_about_the_same_strategy(
        self,
    ) -> None:
        small, small_plan, small_validator = run_campaign(
            a_spec("k_small"), evaluator=modest_edge_evaluator
        )
        large, large_plan, large_validator = run_campaign(
            a_spec("k_large"), prior_trials=23_000, evaluator=modest_edge_evaluator
        )

        # The campaign handed the ladder a bigger external count in the second run.
        assert small_validator.configs[0].external_trial_count < (
            large_validator.configs[0].external_trial_count
        )
        assert large_plan.true_trial_count >= 23_000
        assert large.true_trial_count == large_plan.true_trial_count

        small_cell = small.attempted[0]
        large_cell = large.attempted[0]

        # The SAME return-generating process, the same grid, the same gates.
        assert small_cell.strategy_content_hash == large_cell.strategy_content_hash
        assert small_cell.n_trials == large_cell.n_trials

        # And a different answer, in the direction that matters.
        assert small_cell.deflated_sharpe_ratio is not None
        assert large_cell.deflated_sharpe_ratio is not None
        assert large_cell.deflated_sharpe_ratio < small_cell.deflated_sharpe_ratio
        assert large_cell.n_trials_used_for_deflation > (
            small_cell.n_trials_used_for_deflation
        )
        assert large_cell.n_trials_used_for_deflation >= 23_000

        assert small_cell.verdict == Verdict.PROMOTE.value
        assert large_cell.verdict == Verdict.REJECT.value
        assert large_cell.binding_constraint.startswith("deflated_sharpe")

    def test_the_report_states_the_threshold_the_survivor_had_to_beat(self) -> None:
        report, plan, _ = run_campaign(a_spec("k_threshold"))
        cell = report.attempted[0]
        assert cell.deflation_threshold is not None
        assert cell.sr_variance is not None and cell.sr_variance > 0.0
        assert report.campaign_deflation_threshold is not None
        assert "E[max SR]" in report.deflation_note
        assert str(plan.true_trial_count) in report.deflation_note

    def test_a_survivor_is_reported_as_not_being_a_finding(self) -> None:
        report, _plan, _ = run_campaign(a_spec("k_survivor"))
        assert report.survivors, "the synthetic plateau edge is designed to survive"
        statement = report.honest_statement()
        assert "It is NOT evidence of an edge" in statement
        assert "single instrument on a single timeframe" in statement
        assert "had no part in producing it" in statement

    def test_the_holdout_is_reported_when_a_look_was_spent(self) -> None:
        report, _plan, _ = run_campaign(a_spec("k_holdout"))
        # A promoted candidate reached rung 6, which spends the look. The ladder
        # owns its own registry here, so the campaign's registry is untouched --
        # what matters is that the report says which, rather than implying.
        assert "holdout" in report.to_dict()
        assert isinstance(report.holdout["unconsumed"], bool)
