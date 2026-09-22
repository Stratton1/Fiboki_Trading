"""Lineage: a live candidate must be traceable back to the raw dataset bytes.

A provenance chain that quietly omits its missing link is worse than no chain,
because it looks complete. Every gap here is reported as a gap.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.schema import DatasetKind, PriceBasis
from fiboki.data.versioning import (
    DatasetCatalogue,
    DatasetVersion,
    TransformationStep,
)
from fiboki.research.experiment import (
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    Outcome,
)
from fiboki.research.lineage import LineageService
from tests.validation_fixtures import seed_document


@pytest.fixture
def ledger():
    with ExperimentLedger.in_memory() as led:
        yield led


@pytest.fixture
def catalogue(tmp_path):
    with DatasetCatalogue(tmp_path / "datasets.sqlite") as cat:
        raw = DatasetVersion(
            content_checksum="a" * 64,
            lineage=(
                TransformationStep(operation="ingest", parameters={"provider": "dukascopy"}),
            ),
            instrument="EURUSD",
            timeframe=Timeframe.H1,
            price_basis=PriceBasis.MID,
            kind=DatasetKind.RAW,
            quality=DataQuality.RAW,
            row_count=100_000,
            first_timestamp=pd.Timestamp("2019-01-01", tz="UTC"),
            last_timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
            storage_path="data/raw/eurusd_h1.parquet",
            source="dukascopy",
        )
        cat.register(raw)
        cleaned = raw.with_step(
            TransformationStep(operation="validate", parameters={"drop_gaps": True}),
            content_checksum="b" * 64,
            quality=DataQuality.VALIDATED,
            storage_path="data/clean/eurusd_h1.parquet",
            parent_ids=(raw.version_id,),
        )
        cat.register(cleaned)
        yield cat, raw, cleaned


def draft(**kwargs) -> ExperimentDraft:
    base = {
        "actor_kind": ActorKind.AGENT,
        "actor_name": "agent:research-loop",
        "reason": "validate the candidate",
    }
    return ExperimentDraft(**{**base, **kwargs})


class TestExperimentAncestry:
    def test_it_walks_parents_oldest_first(self, ledger) -> None:
        first = ledger.create(draft(reason="initial idea"))
        second = ledger.create(draft(reason="follow-up", parent_experiment_id=first.id))
        third = ledger.create(draft(reason="third", parent_experiment_id=second.id))
        chain = LineageService(ledger).experiment_ancestry(third.id)
        assert [e.id for e in chain] == [first.id, second.id, third.id]

    def test_descendants_are_breadth_first(self, ledger) -> None:
        root = ledger.create(draft(reason="root"))
        a = ledger.create(draft(reason="a", parent_experiment_id=root.id))
        b = ledger.create(draft(reason="b", parent_experiment_id=root.id))
        c = ledger.create(draft(reason="c", parent_experiment_id=a.id))
        found = LineageService(ledger).descendants(root.id)
        assert {e.id for e in found} == {a.id, b.id, c.id}

    def test_an_unknown_experiment_yields_an_empty_ancestry(self, ledger) -> None:
        assert LineageService(ledger).experiment_ancestry("exp_nope") == []


class TestProvenanceChain:
    def test_a_complete_chain_reaches_the_raw_source_checksum(
        self, ledger, catalogue
    ) -> None:
        cat, raw, cleaned = catalogue
        experiment = ledger.create(
            draft(strategy_document=seed_document(), dataset_version_id=cleaned.version_id)
        )
        chain = LineageService(ledger, cat).provenance_chain(experiment.id)
        assert chain.complete
        assert chain.gaps == ()
        kinds = [s.kind for s in chain.steps]
        assert kinds[0] == "experiment"
        assert kinds[1] == "strategy"
        assert "dataset" in kinds
        assert kinds[-1] == "raw_source"
        assert chain.steps[-1].detail["content_checksum"] == raw.content_checksum

    def test_the_chain_names_the_strategy_content_and_structure_hashes(
        self, ledger, catalogue
    ) -> None:
        cat, _, cleaned = catalogue
        doc = seed_document()
        experiment = ledger.create(
            draft(strategy_document=doc, dataset_version_id=cleaned.version_id)
        )
        step = LineageService(ledger, cat).provenance_chain(experiment.id).steps[1]
        assert step.detail["content_hash"] == doc.content_hash()
        assert step.detail["structure_hash"]

    def test_every_transformation_of_the_dataset_appears(self, ledger, catalogue) -> None:
        cat, _, cleaned = catalogue
        experiment = ledger.create(
            draft(strategy_document=seed_document(), dataset_version_id=cleaned.version_id)
        )
        chain = LineageService(ledger, cat).provenance_chain(experiment.id)
        dataset_steps = [s for s in chain.steps if s.kind == "dataset"]
        assert len(dataset_steps) == 2  # cleaned, then its raw parent
        operations = [op for s in dataset_steps for op in s.detail["lineage"]]
        assert "ingest" in operations
        assert "validate" in operations

    def test_a_missing_dataset_version_is_reported_as_a_gap(self, ledger) -> None:
        experiment = ledger.create(draft(strategy_document=seed_document()))
        chain = LineageService(ledger).provenance_chain(experiment.id)
        assert not chain.complete
        assert chain.gaps
        assert "records no dataset version" in chain.gaps[0].summary
        assert "INCOMPLETE" in chain.describe()

    def test_a_missing_catalogue_is_reported_rather_than_skipped(self, ledger) -> None:
        experiment = ledger.create(
            draft(strategy_document=seed_document(), dataset_version_id="some_version")
        )
        chain = LineageService(ledger).provenance_chain(experiment.id)
        assert not chain.complete
        assert "no catalogue supplied" in chain.gaps[0].summary

    def test_an_unresolvable_dataset_version_is_a_gap_not_a_crash(
        self, ledger, catalogue
    ) -> None:
        cat, _, _ = catalogue
        experiment = ledger.create(
            draft(strategy_document=seed_document(), dataset_version_id="not_registered")
        )
        chain = LineageService(ledger, cat).provenance_chain(experiment.id)
        assert not chain.complete
        assert "could not be resolved" in chain.gaps[0].summary

    def test_an_unknown_experiment_is_a_single_gap(self, ledger) -> None:
        chain = LineageService(ledger).provenance_chain("exp_nope")
        assert not chain.complete
        assert len(chain.steps) == 1
        assert chain.steps[0].missing

    def test_the_chain_serialises(self, ledger, catalogue) -> None:
        cat, _, cleaned = catalogue
        experiment = ledger.create(
            draft(strategy_document=seed_document(), dataset_version_id=cleaned.version_id)
        )
        payload = LineageService(ledger, cat).provenance_chain(experiment.id).to_dict()
        assert payload["complete"] is True
        assert payload["n_steps"] == len(payload["steps"])


class TestGraph:
    def test_experiments_strategies_and_datasets_all_appear(self, ledger) -> None:
        doc = seed_document()
        root = ledger.create(
            draft(strategy_document=doc, dataset_version_id="eurusd_h1_v3")
        )
        ledger.create(
            draft(
                strategy_document=doc,
                dataset_version_id="eurusd_h1_v3",
                parent_experiment_id=root.id,
                outcome=Outcome.REJECTED,
            )
        )
        graph = LineageService(ledger).graph()
        kinds = {n.kind for n in graph.nodes.values()}
        assert kinds == {"experiment", "strategy", "dataset"}

    def test_a_child_experiment_has_its_parent_as_an_ancestor(self, ledger) -> None:
        root = ledger.create(draft(reason="root"))
        child = ledger.create(draft(reason="child", parent_experiment_id=root.id))
        graph = LineageService(ledger).graph()
        ancestors = {n.id for n in graph.ancestors(f"experiment:{child.id}")}
        assert root.id in ancestors

    def test_the_graph_serialises(self, ledger) -> None:
        ledger.create(draft(strategy_document=seed_document(), dataset_version_id="d1"))
        payload = LineageService(ledger).graph().to_dict()
        assert payload["nodes"] and payload["edges"]


class TestStrategyAncestry:
    def test_a_mutation_chain_is_walked_back_through_the_ledger(self, ledger) -> None:
        parent = seed_document()
        child_data = parent.model_dump(mode="json")
        child_data.pop("complexity_score", None)
        child_data["strategy_id"] = "rsi_band_mean_reversion_v2"
        child_data["parameters"]["rsi_period"] = {
            **child_data["parameters"]["rsi_period"],
            "default": 21,
        }
        child_data["mutation"] = {
            "parent_hash": parent.content_hash(),
            "operator": "tune_parameter",
            "description": "slower RSI",
            "generation": 1,
        }
        child = type(parent).model_validate(child_data)

        ledger.create(draft(strategy_document=parent, reason="parent run"))
        ledger.create(draft(strategy_document=child, reason="mutated run"))

        chain = LineageService(ledger).strategy_ancestry(child.content_hash())
        assert [c["content_hash"] for c in chain] == [
            parent.content_hash(),
            child.content_hash(),
        ]
        assert chain[-1]["operator"] == "tune_parameter"
        assert all(c["known_to_ledger"] for c in chain)

    def test_produced_by_returns_the_first_run_of_a_strategy(self, ledger) -> None:
        doc = seed_document()
        first = ledger.create(draft(strategy_document=doc, reason="first"))
        ledger.create(draft(strategy_document=doc, reason="second"))
        assert LineageService(ledger).produced_by(doc.content_hash()).id == first.id
