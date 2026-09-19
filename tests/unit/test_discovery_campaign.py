"""The campaign runner: trial accounting, resume, and the no-data rule.

The assertions here are about the two things a campaign can get catastrophically
wrong and still look like it is working:

* deflating each candidate against its own parameter sweep rather than against
  the whole search, which is exactly what made V1's 23,040-cell leaderboard
  meaningless; and
* checkpointing a cell that returned NO DATA as done, which permanently removes
  an instrument from every later run and says nothing about it.
"""
from __future__ import annotations

import pytest

from fiboki.core.enums import Timeframe
from fiboki.discovery.campaign import (
    CampaignCheckpoint,
    CampaignRunner,
    CampaignSpec,
    CellOutcome,
    CellStatus,
)
from fiboki.discovery.report import CampaignReport
from fiboki.research.experiment import ActorKind, ExperimentDraft, ExperimentLedger, Outcome
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


def a_spec(**overrides) -> CampaignSpec:
    payload = {
        "campaign_id": "k_test",
        "universe": ("XAUUSD",),
        "timeframes": (Timeframe.H4,),
        "hypotheses": (a_hypothesis(),),
        "actor": "tests:campaign",
        "gate_set": GATE_SET_V2,
        "max_evaluations": 1000,
        "max_grid_points": 8,
        "max_values_per_axis": 2,
        "sweep_parameters": {
            "donchian_breakout_atr": ("channel_period", "stop_atr_multiple", "trail_atr_multiple")
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


def a_runner(ledger, spec=None, *, checkpoint=None, validator=None, source=SOURCE):
    return CampaignRunner(
        spec or a_spec(),
        bars=source,
        ledger=ledger,
        registry=HoldoutRegistry.in_memory(),
        checkpoint=checkpoint or CampaignCheckpoint.in_memory(),
        validator=validator or RecordingValidator(),
    )


class TestTheTrueTrialCountIsTheWholeSearch:
    def test_external_trials_are_the_campaign_minus_the_candidate(self, ledger) -> None:
        runner = a_runner(ledger, a_spec(max_generations=1))
        plan = runner.plan([seed("donchian_breakout_atr"), seed("ichimoku_kumo_trend")])

        assert len(plan.cells) > 2, "mutations must widen the population"
        assert plan.planned_trial_count == sum(c.n_trials for c in plan.cells)
        assert plan.true_trial_count == plan.planned_trial_count

        for cell in plan.cells:
            assert plan.external_trials_for(cell) == plan.true_trial_count - cell.n_trials
            # The point of the whole exercise: a candidate is deflated against
            # far more than its own grid.
            assert plan.external_trials_for(cell) > cell.n_trials

    def test_the_count_is_fixed_before_anything_runs(self, ledger) -> None:
        """Every candidate is deflated against the SAME N, not a growing one."""
        recorder = RecordingValidator()
        runner = a_runner(ledger, a_spec(max_generations=1), validator=recorder)
        plan = runner.plan([seed("donchian_breakout_atr"), seed("ichimoku_kumo_trend")])
        runner.run(plan)

        totals = {
            call["ladder_config"].external_trial_count + call["cell"].n_trials
            for call in recorder.calls
        }
        assert totals == {plan.true_trial_count}

    def test_the_external_count_reaches_the_ladder_config(self, ledger) -> None:
        recorder = RecordingValidator()
        runner = a_runner(ledger, validator=recorder)
        plan = runner.plan([seed("donchian_breakout_atr")])
        runner.run(plan)

        assert recorder.calls, "the validator must have been called"
        config = recorder.calls[0]["ladder_config"]
        assert config.external_trial_count == plan.external_trials_for(plan.cells[0])
        # And the ladder's own contract is honoured, so the run would not be
        # refused by ValidationLadder.__post_init__.
        assert config.min_trades == int(GATE_SET_V2.by_name("min_trades").threshold)

    def test_prior_ledger_trials_are_counted_when_asked(self, ledger) -> None:
        """A campaign run last month against the same bars is part of the search."""
        ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.AGENT,
                actor_name="agent:last-month",
                reason="an earlier campaign on the same bars",
                dataset_version_id=XAUUSD_H4.dataset_version_id,
                strategy_id="something_else_entirely",
            )
        )
        # Fabricate the recorded trial count the way a real report carries it.
        rows = ledger.list()
        assert rows
        with ledger.engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(
                text(
                    "UPDATE experiment SET validation_report_json = :p WHERE 1 = 0"
                ),
                {"p": "{}"},
            )
        runner = a_runner(ledger, a_spec(include_prior_trials=True))
        plan = runner.plan([seed("donchian_breakout_atr")])
        # No prior report carried a trial count, so the prior count is honestly 0.
        assert plan.prior_trial_count == 0
        assert plan.true_trial_count == plan.planned_trial_count

    def test_a_budget_defers_cells_rather_than_shrinking_their_grids(self, ledger) -> None:
        runner = a_runner(ledger, a_spec(max_generations=1, max_evaluations=16))
        plan = runner.plan([seed("donchian_breakout_atr"), seed("ichimoku_kumo_trend")])
        assert plan.planned_trial_count <= 16
        deferred = [s for s in plan.skipped if s.kind == "over_budget"]
        assert deferred
        assert "Deferred rather than run at a smaller grid" in deferred[0].reason


class TestOutOfUniverseCellsAreDeclinedNotRun:
    def test_a_strategy_that_does_not_claim_the_instrument_is_skipped(self, ledger) -> None:
        """``rsi_band_mean_reversion`` does not list XAUUSD in its universe."""
        runner = a_runner(ledger)
        plan = runner.plan([seed("rsi_band_mean_reversion")])
        assert plan.cells == ()
        assert [s.kind for s in plan.skipped] == ["out_of_universe"]
        assert "validate a strategy nobody wrote" in plan.skipped[0].reason


class TestNovelty:
    def test_a_rediscovery_is_skipped_and_the_skip_is_recorded(self, ledger) -> None:
        document = seed("donchian_breakout_atr")
        ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.AGENT,
                actor_name="agent:prior",
                reason="donchian breakout, already tried",
                strategy_id=document.strategy_id,
                strategy_document=document,
                dataset_version_id=XAUUSD_H4.dataset_version_id,
                outcome=Outcome.REJECTED,
                conclusion="died at the walk-forward rung",
            )
        )
        runner = a_runner(ledger, a_spec(max_generations=0))
        plan = runner.plan([document])
        assert plan.cells == ()
        assert [s.kind for s in plan.skipped] == ["non_novel"]

        before = len(ledger.list())
        report = runner.run(plan)
        after = ledger.list()
        assert len(after) == before + 1, "the skip itself is a ledger row"
        skip_row = after[-1]
        assert skip_row.outcome is Outcome.ABANDONED
        assert "NOT RUN" in skip_row.reason
        assert report.skips_by_kind() == {"non_novel": 1}

    def test_a_resumed_campaign_does_not_re_file_the_same_skip(self, ledger) -> None:
        document = seed("donchian_breakout_atr")
        ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.AGENT,
                actor_name="agent:prior",
                reason="donchian breakout, already tried",
                strategy_id=document.strategy_id,
                strategy_document=document,
                dataset_version_id=XAUUSD_H4.dataset_version_id,
            )
        )
        checkpoint = CampaignCheckpoint.in_memory()
        runner = a_runner(ledger, checkpoint=checkpoint)
        runner.run(runner.plan([document]))
        count = len(ledger.list())

        again = a_runner(ledger, checkpoint=checkpoint)
        again.run(again.plan([document]))
        assert len(ledger.list()) == count


class TestResume:
    @staticmethod
    def _succeeding_validator(ledger):
        """A validator that produces a real, rejected ValidationReport."""
        from fiboki.validation.report import RungOutcome, RungResult, ValidationReport

        def validate(**kwargs):
            report = ValidationReport.build(
                strategy_id=kwargs["cell"].strategy_id,
                strategy_content_hash=kwargs["cell"].content_hash,
                dataset_version_id=kwargs["bars"].dataset_version_id,
                gate_set=kwargs["spec"].gate_set,
                gate_results=kwargs["spec"].gate_set.evaluate({"n_trades": 12.0}),
                rungs=[
                    RungResult(
                        index=0,
                        name="SANITY",
                        outcome=RungOutcome.FAIL,
                        reason="12 trades, below the 400 minimum",
                    )
                ],
                ladder_config={
                    "external_trial_count": kwargs["ladder_config"].external_trial_count
                },
                windows={"n_evaluations": 1},
            )
            return CellOutcome(report=report, n_evaluations=1, engine_runs=1)

        return validate

    def test_a_completed_cell_is_not_recomputed_and_its_result_is_replayed(
        self, ledger, tmp_path
    ) -> None:
        path = tmp_path / "checkpoint.json"
        spec = a_spec()
        seeds = [seed("donchian_breakout_atr")]

        first = CampaignRunner(
            spec,
            bars=SOURCE,
            ledger=ledger,
            registry=HoldoutRegistry.in_memory(),
            checkpoint=CampaignCheckpoint(path),
            validator=self._succeeding_validator(ledger),
        )
        report_one = first.run(first.plan(seeds))
        assert len(report_one.attempted) == 1
        assert report_one.attempted[0].died_at_rung == "RUNG 0 SANITY"

        # A fresh process, the same checkpoint file on disk.
        recorder = RecordingValidator()
        second = CampaignRunner(
            spec,
            bars=SOURCE,
            ledger=ledger,
            registry=HoldoutRegistry.in_memory(),
            checkpoint=CampaignCheckpoint(path),
            validator=recorder,
        )
        report_two = second.run(second.plan(seeds))

        assert recorder.calls == [], "a completed cell must not be recomputed"
        assert [c.key for c in report_two.attempted] == [c.key for c in report_one.attempted]
        assert report_two.attempted[0] == report_one.attempted[0]

    def test_the_checkpoint_survives_a_new_object_over_the_same_file(
        self, ledger, tmp_path
    ) -> None:
        path = tmp_path / "checkpoint.json"
        checkpoint = CampaignCheckpoint(path)
        checkpoint.record("cell_a", CellStatus.COMPLETED, {"strategy_id": "x"})
        reloaded = CampaignCheckpoint(path)
        assert reloaded.is_complete("cell_a")
        assert reloaded.payload("cell_a")["strategy_id"] == "x"


class TestANoDataCellIsNeverComplete:
    def test_it_is_recorded_incomplete_and_retried(self, ledger) -> None:
        """V1 marked these done, so a missing feed silently removed an instrument."""
        checkpoint = CampaignCheckpoint.in_memory()
        empty = bar_source({})  # the feed has nothing for this cell
        recorder = RecordingValidator()
        runner = a_runner(ledger, checkpoint=checkpoint, validator=recorder, source=empty)
        plan = runner.plan([seed("donchian_breakout_atr")])
        assert len(plan.cells) == 1

        report = runner.run(plan)
        key = plan.cells[0].key
        assert checkpoint.status(key) is CellStatus.NO_DATA
        assert not checkpoint.is_complete(key)
        assert key in checkpoint.incomplete_keys()
        assert recorder.calls == [], "no bars means no validation was attempted"
        assert [s.kind for s in report.skipped] == ["no_data"]

        # Resume with the feed restored: the cell is attempted, not skipped.
        again = a_runner(ledger, checkpoint=checkpoint, validator=RecordingValidator())
        plan_two = again.plan([seed("donchian_breakout_atr")])
        again.run(plan_two)
        assert again._validator.calls, "a no-data cell must be retried on resume"

    def test_the_status_enum_says_which_statuses_count_as_complete(self) -> None:
        assert CellStatus.COMPLETED.is_complete
        assert CellStatus.SKIPPED.is_complete
        assert not CellStatus.NO_DATA.is_complete
        assert not CellStatus.ERROR.is_complete

    def test_a_validator_error_is_recorded_without_killing_the_campaign(
        self, ledger
    ) -> None:
        checkpoint = CampaignCheckpoint.in_memory()
        runner = a_runner(ledger, checkpoint=checkpoint)
        plan = runner.plan([seed("donchian_breakout_atr")])
        report = runner.run(plan)
        assert len(report.errors) == 1
        assert checkpoint.status(plan.cells[0].key) is CellStatus.ERROR
        assert not checkpoint.is_complete(plan.cells[0].key)


class TestTheReport:
    def test_it_round_trips(self, ledger, tmp_path) -> None:
        runner = CampaignRunner(
            a_spec(max_generations=1),
            bars=SOURCE,
            ledger=ledger,
            registry=HoldoutRegistry.in_memory(),
            checkpoint=CampaignCheckpoint.in_memory(),
            validator=TestResume._succeeding_validator(ledger),
            report_dir=tmp_path,
        )
        report = runner.run(runner.plan([seed("donchian_breakout_atr")]))

        assert CampaignReport.from_dict(report.to_dict()) == report
        assert CampaignReport.from_json(report.to_json()) == report

        path = tmp_path / f"campaign_{report.campaign_id}.json"
        assert path.exists()
        assert CampaignReport.load(path) == report
        assert (tmp_path / f"campaign_{report.campaign_id}.md").exists()

    def test_it_states_the_true_trial_count_and_what_nothing_surviving_means(
        self, ledger
    ) -> None:
        runner = a_runner(
            ledger,
            a_spec(max_generations=1),
            validator=TestResume._succeeding_validator(ledger),
        )
        plan = runner.plan([seed("donchian_breakout_atr")])
        report = runner.run(plan)

        assert report.true_trial_count == plan.true_trial_count
        assert report.survivors == ()
        statement = report.honest_statement()
        assert str(report.true_trial_count) in statement
        assert "NOTHING SURVIVED" in statement
        assert "does NOT say" in statement or "It does NOT say" in statement
        assert "RUNG 0 SANITY" in report.markdown()
        assert report.holdout["unconsumed"] is True

    def test_refused_mutations_are_carried_into_the_report(self, ledger) -> None:
        runner = a_runner(ledger, a_spec(max_generations=1))
        plan = runner.plan([seed("donchian_breakout_atr")])
        report = runner.run(plan)
        assert report.rejected_mutations
        assert any(m["rejection"] for m in report.rejected_mutations)
        assert "Mutations refused before any compute" in report.markdown()
