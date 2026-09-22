"""Trials the ledger cannot see, and the rule that they can only make it harder.

A :class:`~fiboki.data.versioning.DatasetVersion` id hashes the content checksum
together with its lineage, so re-ingesting identical bytes through a different
path mints a NEW id for the SAME BARS. ``_prior_trial_count`` keys on the
version id, so after a re-ingest it honestly reports zero prior trials for a
series that has in fact been searched hundreds of times. Campaign K2 hit exactly
this: its fresh ingest of the XAUUSD H4 parquet produced a version id that K1's
326 trials were not recorded against.

``CampaignSpec.external_prior_trials`` is the declared correction. These tests
pin the three properties that stop it becoming a tuning knob:

* it is refused without a reason;
* it cannot be negative, so it can only ever make the deflation HARDER; and
* it reaches the ladder as ``external_trial_count``, which is the only place
  where a trial count changes a decision.
"""
from __future__ import annotations

import pytest

from fiboki.core.enums import Timeframe
from fiboki.discovery.campaign import CampaignCheckpoint, CampaignRunner, CampaignSpec
from fiboki.discovery.report import deflation_threshold
from fiboki.research.experiment import ExperimentLedger
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import HoldoutRegistry
from tests.discovery_fixtures import (
    RecordingValidator,
    a_hypothesis,
    bar_source,
    one_barset,
    seed,
)

XAUUSD_H4 = one_barset()
SOURCE = bar_source({("XAUUSD", "H4"): XAUUSD_H4})

A_REASON = (
    "campaign k1 spent 326 trials on these same bars under a version id this "
    "ledger does not hold, because the bars were re-ingested through a different "
    "path and a fresh ingest is a fresh lineage node"
)


def a_spec(**overrides) -> CampaignSpec:
    payload = {
        "campaign_id": "k_declared",
        "universe": ("XAUUSD",),
        "timeframes": (Timeframe.H4,),
        "hypotheses": (a_hypothesis(),),
        "actor": "tests:declared-prior",
        "gate_set": GATE_SET_V2,
        "max_evaluations": 1000,
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


@pytest.fixture
def ledger():
    with ExperimentLedger.in_memory() as led:
        yield led


def a_runner(ledger, spec, *, validator=None):
    return CampaignRunner(
        spec,
        bars=SOURCE,
        ledger=ledger,
        registry=HoldoutRegistry.in_memory(),
        checkpoint=CampaignCheckpoint.in_memory(),
        validator=validator or RecordingValidator(),
    )


class TestADeclaredPriorCountMustBeExplainable:
    def test_a_count_without_a_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="without a reason"):
            a_spec(external_prior_trials=350)

    def test_a_blank_reason_is_not_a_reason(self) -> None:
        with pytest.raises(ValueError, match="without a reason"):
            a_spec(external_prior_trials=350, external_prior_trials_reason="   ")

    def test_a_negative_count_is_refused(self) -> None:
        """The field exists to remember forgotten trials, never to discount them."""
        with pytest.raises(ValueError, match="cannot be negative"):
            a_spec(external_prior_trials=-1, external_prior_trials_reason=A_REASON)

    def test_zero_needs_no_reason(self) -> None:
        spec = a_spec()
        assert spec.external_prior_trials == 0
        assert spec.external_prior_trials_reason == ""


class TestItRaisesTheTrueTrialCountAndNothingElse:
    def test_the_plan_carries_the_three_components_separately(self, ledger) -> None:
        spec = a_spec(external_prior_trials=350, external_prior_trials_reason=A_REASON)
        plan = a_runner(ledger, spec).plan([seed("donchian_breakout_atr")])

        assert plan.external_prior_trial_count == 350
        assert plan.prior_trial_count == 0, "the ledger genuinely holds nothing"
        assert plan.total_prior_trial_count == 350
        assert plan.true_trial_count == plan.planned_trial_count + 350
        # A reader has to be able to reconstruct the number from the report.
        assert "declared prior 350" in plan.describe()
        assert A_REASON in plan.describe()

    def test_it_can_only_make_the_search_look_bigger(self, ledger) -> None:
        without = a_runner(ledger, a_spec()).plan([seed("donchian_breakout_atr")])
        with_declared = a_runner(
            ledger,
            a_spec(external_prior_trials=350, external_prior_trials_reason=A_REASON),
        ).plan([seed("donchian_breakout_atr")])

        assert with_declared.planned_trial_count == without.planned_trial_count
        assert with_declared.true_trial_count > without.true_trial_count

    def test_a_bigger_search_raises_the_deflation_bar(self) -> None:
        """The whole point: more trials means a survivor has to be better."""
        variance = 5.583367467763483e-05
        small = deflation_threshold(326, variance)
        large = deflation_threshold(3700, variance)
        assert small is not None and large is not None
        assert large > small

    def test_the_declared_trials_reach_the_ladder(self, ledger) -> None:
        recorder = RecordingValidator()
        spec = a_spec(external_prior_trials=350, external_prior_trials_reason=A_REASON)
        runner = a_runner(ledger, spec, validator=recorder)
        plan = runner.plan([seed("donchian_breakout_atr")])
        runner.run(plan)

        assert recorder.calls
        for call in recorder.calls:
            external = call["ladder_config"].external_trial_count
            assert external == plan.true_trial_count - call["cell"].n_trials
            assert external >= 350


class TestTheReportSaysWhereTheNumberCameFrom:
    def test_the_breakdown_survives_the_json_round_trip(self, ledger, tmp_path) -> None:
        from fiboki.discovery.report import CampaignReport

        spec = a_spec(external_prior_trials=350, external_prior_trials_reason=A_REASON)
        runner = CampaignRunner(
            spec,
            bars=SOURCE,
            ledger=ledger,
            registry=HoldoutRegistry.in_memory(),
            checkpoint=CampaignCheckpoint.in_memory(),
            validator=RecordingValidator(),
            report_dir=tmp_path,
        )
        plan = runner.plan([seed("donchian_breakout_atr")])
        report = runner.run(plan)

        accounting = report.lineage["trial_accounting"]
        assert accounting["prior_declared_externally"] == 350
        assert accounting["prior_from_ledger"] == 0
        assert accounting["external_reason"] == A_REASON
        assert (
            accounting["planned_in_this_campaign"] + 350 == accounting["true_trial_count"]
        )

        reloaded = CampaignReport.from_json(report.to_json())
        assert reloaded.lineage["trial_accounting"] == accounting
        # prior_trial_count on the report is the TOTAL prior, so the arithmetic
        # a reader does on the report's own three numbers still closes.
        assert (
            reloaded.planned_trial_count + reloaded.prior_trial_count
            == reloaded.true_trial_count
        )
        assert reloaded.prior_trial_count == 350
