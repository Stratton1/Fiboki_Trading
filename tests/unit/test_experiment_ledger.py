"""The experiment ledger is append-only, and the database enforces it.

An API with no ``update`` method is a convention. A trigger is a property of the
artefact: a maintainer who opens the SQLite file and tries to tidy up an
embarrassing result gets an error, not a tidier history. That matters because the
ledger's whole value is that it contains the results nobody liked.
"""
from __future__ import annotations

from typing import ClassVar

import pytest
from sqlalchemy import text

from fiboki.research.experiment import (
    ActorKind,
    Experiment,
    ExperimentDraft,
    ExperimentLedger,
    ExperimentNotFound,
    Outcome,
    is_append_only_violation,
)
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.report import RungOutcome, RungResult, ValidationReport, Verdict


@pytest.fixture
def ledger():
    with ExperimentLedger.in_memory() as led:
        yield led


def draft(**kwargs) -> ExperimentDraft:
    base = {
        "actor_kind": ActorKind.AGENT,
        "actor_name": "agent:research-loop",
        "reason": "sweep the n-wave family on H4 majors",
    }
    return ExperimentDraft(**{**base, **kwargs})


class TestAppendOnly:
    """The property that makes the record worth keeping."""

    def test_the_repository_exposes_no_way_to_change_a_record(self, ledger) -> None:
        for forbidden in ("update", "delete", "remove", "amend", "set_outcome", "save"):
            assert not hasattr(ledger, forbidden), f"ExperimentLedger.{forbidden} exists"

    def test_a_direct_sql_update_is_refused_by_the_database(self, ledger) -> None:
        experiment = ledger.create(draft(conclusion="rejected at deflation"))
        with pytest.raises(Exception) as exc, ledger.engine.begin() as conn:
            conn.execute(
                text("UPDATE experiment SET conclusion = :c WHERE id = :i"),
                {"c": "actually it was great", "i": experiment.id},
            )
        assert is_append_only_violation(exc.value)
        assert ledger.get(experiment.id).conclusion == "rejected at deflation"

    def test_a_direct_sql_delete_is_refused_by_the_database(self, ledger) -> None:
        experiment = ledger.create(draft())
        with pytest.raises(Exception) as exc, ledger.engine.begin() as conn:
            conn.execute(text("DELETE FROM experiment WHERE id = :i"), {"i": experiment.id})
        assert is_append_only_violation(exc.value)
        assert ledger.count() == 1

    def test_the_refusal_survives_reopening_the_file(self, tmp_path) -> None:
        path = tmp_path / "ledger.sqlite"
        with ExperimentLedger(path) as led:
            experiment = led.create(draft())
        with ExperimentLedger(path) as reopened:
            with pytest.raises(Exception) as exc, reopened.engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM experiment WHERE id = :i"), {"i": experiment.id}
                )
            assert is_append_only_violation(exc.value)
            assert reopened.count() == 1

    def test_a_correction_is_an_append_that_points_at_its_parent(self, ledger) -> None:
        wrong = ledger.create(draft(conclusion="promoted", outcome=Outcome.PROMOTED))
        correction = ledger.create(
            draft(
                parent_experiment_id=wrong.id,
                reason="the earlier run used a stale dataset version",
                outcome=Outcome.REJECTED,
                rejection_reason="re-run on the corrected data failed at rung 5",
            )
        )
        assert ledger.get(wrong.id).outcome is Outcome.PROMOTED  # history intact
        assert ledger.children(wrong.id) == [correction]


class TestWhatIsRecorded:
    def test_an_experiment_without_an_actor_or_a_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="actor_name is mandatory"):
            ExperimentDraft(ActorKind.HUMAN, "  ", "because")
        with pytest.raises(ValueError, match="reason is mandatory"):
            ExperimentDraft(ActorKind.HUMAN, "joe", "   ")

    def test_human_and_agent_initiators_are_distinguished(self, ledger) -> None:
        ledger.create(draft(actor_kind=ActorKind.HUMAN, actor_name="joe"))
        ledger.create(draft(actor_kind=ActorKind.AGENT, actor_name="agent:mutator"))
        kinds = {e.actor_kind for e in ledger.list()}
        assert kinds == {ActorKind.HUMAN, ActorKind.AGENT}

    def test_everything_needed_to_reproduce_a_run_is_stored(self, ledger) -> None:
        experiment = ledger.create(
            draft(
                strategy_id="n_wave",
                strategy_content_hash="f" * 64,
                strategy_version="2.0.0",
                dataset_version_id="eurusd_h4_v3",
                code_version="cafebabe",
                parameters={"fast": 10, "slow": 30},
                engine_config={"profile": "ig_realistic", "initial_balance": 10_000.0},
                outputs={"net_profit": 812.5},
                hypothesis_id="hyp_liquidity_provision",
                tags=("h4", "majors"),
            )
        )
        stored = ledger.get(experiment.id)
        assert stored.strategy_content_hash == "f" * 64
        assert stored.dataset_version_id == "eurusd_h4_v3"
        assert stored.code_version == "cafebabe"
        assert stored.parameters == {"fast": 10, "slow": 30}
        assert stored.engine_config["profile"] == "ig_realistic"
        assert stored.outputs == {"net_profit": 812.5}
        assert stored.hypothesis_id == "hyp_liquidity_provision"
        assert stored.tags == ("h4", "majors")

    def test_the_code_version_defaults_to_the_running_code(self, ledger) -> None:
        assert ledger.create(draft()).code_version

    def test_a_strategy_document_is_fingerprinted_on_the_way_in(self, ledger) -> None:
        from tests.validation_fixtures import seed_document

        doc = seed_document()
        experiment = ledger.create(draft(strategy_document=doc))
        assert experiment.strategy_content_hash == doc.content_hash()
        assert experiment.strategy_id == doc.strategy_id
        assert experiment.structure_hash
        assert experiment.structure_tokens

    def test_an_unknown_parent_is_refused(self, ledger) -> None:
        with pytest.raises(ExperimentNotFound):
            ledger.create(draft(parent_experiment_id="exp_does_not_exist"))


class TestValidationReportWiring:
    @staticmethod
    def _report(values) -> ValidationReport:
        return ValidationReport.build(
            strategy_id="n_wave",
            strategy_content_hash="f" * 64,
            dataset_version_id="eurusd_h4_v3",
            gate_set=GATE_SET_V2,
            gate_results=GATE_SET_V2.evaluate(values),
            rungs=[
                RungResult(i, f"R{i}", RungOutcome.PASS) for i in range(5)
            ]
            + [
                RungResult(5, "DEFLATION", RungOutcome.FAIL, reason="DSR 0.41")
                if values["deflated_sharpe_ratio"] < 0.95
                else RungResult(5, "DEFLATION", RungOutcome.PASS),
                RungResult(6, "HOLDOUT", RungOutcome.PASS),
            ],
        )

    PASSING: ClassVar[dict[str, float]] = {
        "n_trades": 600.0,
        "walk_forward_efficiency": 90.0,
        "oos_profitable_fraction": 0.9,
        "deflated_sharpe_ratio": 0.99,
        "pbo": 0.05,
        "spa_p_consistent": 0.001,
        "stepm_member": 1.0,
        "net_profit_at_2x_spread": 100.0,
        "point_plateau_ratio": 1.02,
    }

    def test_a_rejected_report_sets_the_outcome_and_the_rejection_reason(
        self, ledger
    ) -> None:
        report = self._report({**self.PASSING, "deflated_sharpe_ratio": 0.41})
        assert report.verdict is Verdict.REJECT
        experiment = ledger.create(draft(validation_report=report))
        assert experiment.outcome is Outcome.REJECTED
        assert "deflated_sharpe" in experiment.rejection_reason
        assert experiment.rejected_at_rung == "RUNG 5 DEFLATION"

    def test_a_promoted_report_sets_the_outcome(self, ledger) -> None:
        experiment = ledger.create(draft(validation_report=self._report(self.PASSING)))
        assert experiment.outcome is Outcome.PROMOTED
        assert experiment.rejection_reason == ""

    def test_the_stored_report_round_trips_and_is_hashed(self, ledger) -> None:
        report = self._report(self.PASSING)
        experiment = ledger.create(draft(validation_report=report))
        assert experiment.validation_report_hash == report.content_hash()
        assert experiment.validation_report.content_hash() == report.content_hash()


class TestReading:
    def test_list_returns_oldest_first(self, ledger) -> None:
        ids = [ledger.create(draft(reason=f"run {i}")).id for i in range(4)]
        assert [e.id for e in ledger.list()] == ids

    def test_filters_compose(self, ledger) -> None:
        ledger.create(draft(strategy_id="a", dataset_version_id="d1"))
        ledger.create(draft(strategy_id="b", dataset_version_id="d1"))
        ledger.create(draft(strategy_id="a", dataset_version_id="d2"))
        assert len(ledger.list(strategy_id="a")) == 2
        assert len(ledger.list(dataset_version_id="d1")) == 2
        assert len(ledger.list(strategy_id="a", dataset_version_id="d2")) == 1

    def test_getting_an_unknown_experiment_raises(self, ledger) -> None:
        with pytest.raises(ExperimentNotFound):
            ledger.get("exp_nope")

    def test_an_experiment_describes_itself_in_one_line(self, ledger) -> None:
        experiment = ledger.create(
            draft(
                strategy_id="n_wave",
                outcome=Outcome.REJECTED,
                rejection_reason="pbo 0.41",
            )
        )
        line = experiment.describe()
        assert "n_wave" in line and "rejected" in line and "pbo 0.41" in line

    def test_experiments_are_immutable_value_objects(self, ledger) -> None:
        experiment = ledger.create(draft())
        assert isinstance(experiment, Experiment)
        with pytest.raises(AttributeError):
            experiment.conclusion = "changed"  # type: ignore[misc]
