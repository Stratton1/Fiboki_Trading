"""Mutation operators: every accepted mutant is a runnable strategy, and every
refusal is recorded with the reason.

The two refusals that matter most are asserted directly: a document that will
not compile, and a document whose declared parameter domains are jointly
infeasible. The second is the one that actually happened -- a ``macd_fast``
domain overlapping ``macd_slow``, making 243 of 2,187 declared grid cells
impossible -- and it is the kind of defect that only shows up at 3am, two thirds
of the way through a sweep.
"""
from __future__ import annotations

import json

import pytest

from fiboki.discovery.mutation import (
    MUTATION_OPERATORS,
    MutationEngine,
    domain_feasibility,
    mutation_lineage,
)
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from tests.discovery_fixtures import all_seeds, seed

SEEDS = all_seeds()


@pytest.fixture
def engine() -> MutationEngine:
    return MutationEngine(seed=7)


class TestEveryAcceptedMutantIsAStrategy:
    @pytest.mark.parametrize("document", SEEDS, ids=[d.strategy_id for d in SEEDS])
    def test_every_operator_yields_valid_documents_or_a_recorded_refusal(
        self, document, engine
    ) -> None:
        proposals = engine.propose(
            document, partners=[d for d in SEEDS if d.strategy_id != document.strategy_id]
        )
        assert proposals, "every seed must produce at least one proposal"
        for proposal in proposals:
            if proposal.accepted:
                assert proposal.document is not None
                # It compiles, and it round-trips through its own JSON.
                compile_strategy(proposal.document.bind_defaults())
                assert (
                    StrategyDocument.from_json(proposal.document.to_json())
                    == proposal.document
                )
            else:
                assert proposal.rejection, f"{proposal.operator} refused without a reason"

    def test_a_mutant_records_its_operator_parents_and_rationale(self, engine) -> None:
        parent = seed("donchian_breakout_atr")
        proposal = engine.apply("add_filter", parent, variant="adx_trend_floor")
        assert proposal.accepted
        mutant = proposal.document
        assert mutant is not None
        assert mutant.mutation is not None
        assert mutant.mutation.operator == "add_filter"
        assert mutant.mutation.parent_hash == parent.content_hash()
        assert mutant.mutation.generation == 1
        assert "adx_trend_floor" in mutant.mutation.description
        assert mutant.parent_strategy_ids == ("donchian_breakout_atr",)
        assert len(mutant.filters) == len(parent.filters) + 1

    def test_a_mutant_id_is_derived_from_its_content_and_does_not_grow(
        self, engine
    ) -> None:
        """Generations are counted in the mutation record, not in the name."""
        first = engine.apply("add_filter", seed("donchian_breakout_atr"), variant="adx_trend_floor")
        assert first.document is not None
        second = engine.apply("add_filter", first.document, variant="volatility_floor")
        assert second.document is not None
        assert second.document.strategy_id.startswith("donchian_breakout_atr_m")
        assert len(second.document.strategy_id) <= 64
        assert second.document.strategy_id.count("_m") == 1
        assert second.document.mutation.generation == 2

    def test_a_no_op_mutation_is_refused_rather_than_queued(self, engine) -> None:
        parent = seed("donchian_breakout_atr")
        first = engine.apply("add_filter", parent, variant="adx_trend_floor")
        assert first.document is not None
        again = engine.apply("add_filter", first.document, variant="adx_trend_floor")
        assert not again.accepted
        assert "already contains" in again.rejection

    def test_removing_a_rule_drops_the_parameter_it_orphaned(self, engine) -> None:
        """A declared axis that changes nothing would inflate the trial count."""
        parent = seed("macd_ema_trend_hybrid")
        assert "adx_floor" in parent.parameters
        simplified = engine.apply("simplify", parent, variant="regime")
        assert simplified.accepted
        mutant = simplified.document
        assert mutant is not None
        assert "adx_floor" not in mutant.parameters
        assert set(mutant.parameters) == set(mutant.unbound_parameters())


class TestCombineChecksCompatibility:
    def test_incompatible_parents_are_refused_with_the_reason(self, engine) -> None:
        a = seed("donchian_breakout_atr")
        b = seed("macd_ema_trend_hybrid")
        proposal = engine.apply("combine", a, partner=b)
        assert not proposal.accepted
        assert "stop_atr_multiple" in proposal.rejection

    def test_a_compatible_cross_takes_As_entries_and_Bs_exits(self, engine) -> None:
        a = seed("donchian_breakout_atr")
        b = seed("ichimoku_kumo_trend")
        proposal = engine.apply("combine", a, partner=b)
        assert proposal.accepted, proposal.rejection
        child = proposal.document
        assert child is not None
        assert child.stop.kind == b.stop.kind
        assert len(child.take_profits) == len(b.take_profits)
        assert child.entry.long == a.entry.long
        assert child.parent_strategy_ids == (a.strategy_id, b.strategy_id)
        assert set(child.universe) <= set(a.universe) & set(b.universe)

    def test_combine_needs_a_partner(self, engine) -> None:
        assert not engine.apply("combine", seed("donchian_breakout_atr")).accepted


class TestInfeasibleDomainsAreRejectedAndRecorded:
    @staticmethod
    def _overlapping_macd() -> StrategyDocument:
        """``macd_fast`` widened so its domain overlaps ``macd_slow``'s.

        The real defect, reproduced: MACD requires ``0 < fast < slow``, so every
        declared cell with ``fast >= slow`` is not a strategy at all.
        """
        raw = json.loads(seed("macd_ema_trend_hybrid").to_json())
        raw["parameters"]["macd_fast"]["max_value"] = 30.0
        return StrategyDocument.model_validate(raw)

    def test_the_seed_documents_declare_feasible_domains(self) -> None:
        for document in SEEDS:
            report = domain_feasibility(document, max_cells=256)
            assert report.feasible, f"{document.strategy_id}: {report.describe()}"

    def test_an_overlapping_domain_is_detected_and_counted(self) -> None:
        report = domain_feasibility(self._overlapping_macd(), max_cells=256)
        assert not report.feasible
        assert report.n_infeasible > 0
        assert 0.0 < report.infeasible_fraction < 1.0
        assert "0 < fast < slow" in report.examples[0]
        assert "cannot exist" in report.describe()
        assert report.to_dict()["feasible"] is False

    def test_a_mutation_that_produces_one_is_refused_and_recorded(self, engine) -> None:
        broken = self._overlapping_macd()
        proposal = engine.apply("add_filter", broken, variant="adx_trend_floor")
        assert not proposal.accepted
        assert "infeasible parameter domain" in proposal.rejection
        assert proposal.diagnostics["feasibility"]["n_infeasible"] > 0
        assert proposal in engine.rejected
        assert proposal not in engine.accepted
        # Recorded, not raised: the campaign has to be able to carry on.
        assert engine.proposals[-1] is proposal

    def test_a_document_with_no_parameters_is_checked_at_its_defaults(self) -> None:
        raw = json.loads(seed("donchian_breakout_atr").to_json())
        # Replace every reference with a literal so nothing is declared.
        blob = json.dumps(raw)
        for name, spec in raw["parameters"].items():
            blob = blob.replace(json.dumps({"$param": name}), json.dumps(spec["default"]))
        stripped = json.loads(blob)
        stripped["parameters"] = {}
        report = domain_feasibility(StrategyDocument.model_validate(stripped))
        assert report.feasible
        assert report.n_cells == 1


class TestOperatorSurface:
    def test_the_operator_set_is_closed(self, engine) -> None:
        with pytest.raises(KeyError, match="the set is closed"):
            engine.apply("bolt_on_every_indicator", seed("donchian_breakout_atr"))

    def test_every_declared_operator_is_implemented(self, engine) -> None:
        for operator in MUTATION_OPERATORS:
            assert hasattr(engine, f"_op_{operator}"), operator

    def test_proposals_are_deterministic(self) -> None:
        """A resumed campaign must plan the same population it planned before."""
        parent = seed("ichimoku_kumo_trend")
        first = MutationEngine(seed=3).propose(parent, partners=list(SEEDS))
        second = MutationEngine(seed=3).propose(parent, partners=list(SEEDS))
        assert [p.content_hash for p in first] == [p.content_hash for p in second]
        assert [p.rejection for p in first] == [p.rejection for p in second]

    def test_removing_a_filter_needs_one_to_remove(self, engine) -> None:
        proposal = engine.apply("remove_filter", seed("donchian_breakout_atr"))
        assert not proposal.accepted
        assert "declares no filters" in proposal.rejection


class TestLineage:
    def test_the_graph_names_the_operator_on_every_edge(self, engine) -> None:
        parent = seed("donchian_breakout_atr")
        engine.propose(parent, partners=[d for d in SEEDS if d.strategy_id != parent.strategy_id])
        graph = mutation_lineage(engine.proposals)
        relations = {e.relation for e in graph.edges}
        assert relations <= set(MUTATION_OPERATORS)
        assert "add_filter" in relations
        parent_key = f"strategy:{parent.content_hash()}"
        assert parent_key in graph.nodes
        assert graph.children_of(parent_key), "the parent must have mutant children"

    def test_refused_mutations_appear_in_the_graph_too(self, engine) -> None:
        engine.apply("remove_filter", seed("donchian_breakout_atr"))
        graph = mutation_lineage(engine.proposals)
        kinds = {n.kind for n in graph.nodes.values()}
        assert "rejected_mutation" in kinds
        rejected = [n for n in graph.nodes.values() if n.kind == "rejected_mutation"]
        assert rejected[0].attrs["rejection"]
        assert graph.to_dict()["edges"]
