"""Controlled mutation of strategy documents.

The cheap version of automated strategy discovery bolts indicators together at
random and keeps whatever scores. That is a search over a space with no
structure, and the thing it finds is the maximum of a sampling distribution.
This module does the opposite: a small, closed set of operators, each of which
makes ONE named, explicable change to a document that a human could have argued
for, and each of which records what it did and why.

The operators
-------------
========================= ==================================================
``add_filter``            add a regime-agnostic condition to ``filters``
``remove_filter``         drop one
``alter_stop_model``      change how risk is defined
``alter_target_model``    change how profit is taken
``change_regime_gate``    tighten, loosen, replace or remove the regime claim
``change_session_restriction``  change WHEN the strategy may deal
``change_confirmation_rule``    add, remove or replace a confirmation
``simplify``              remove a rule -- the operator most likely to help
``combine``               cross two compatible parents
========================= ==================================================

Every operator returns a :class:`MutationProposal`, never a bare document, and a
proposal carries its operator, its parents, its rationale and -- when it was
refused -- the reason. Refusals are DATA: "we tried adding a session restriction
to this and it stopped compiling" is worth as much to the next search as a
result, and V1 threw exactly that information away.

Two refusals in particular
--------------------------
**It must compile.** A mutant that cannot be turned into a runnable strategy is
rejected before anything is queued, with the compiler's own message attached.

**Its declared parameter domains must be jointly feasible.** A document whose
``macd_fast`` domain overlaps its ``macd_slow`` domain declares a grid some of
whose cells are not strategies at all. A real instance of this had 243 of 2,187
cells impossible; a sweep hitting one either dies at 3am or -- worse -- silently
skips it and reports a trial count that never happened.
:func:`domain_feasibility` enumerates the declared grid, binds every cell, and
counts the ones that cannot exist.

What is NOT here
----------------
No operator invents a new indicator vocabulary, and none of them tunes a number:
changing 14 to 21 is a REPARAMETERISATION, which
:mod:`fiboki.discovery.novelty` exists to catch and skip, not a mutation.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fiboki.research.lineage import LineageGraph, LineageNode
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import (
    BindingError,
    MutationRecord,
    ParameterSpec,
    RuleSet,
    SessionRestriction,
    StopModel,
    StrategyDocument,
    TakeProfitLeg,
    TradeDirection,
)
from fiboki.strategy.primitives import (
    IndicatorOperand,
    IndicatorSpec,
    IndicatorVsPriceRule,
    PriceOperand,
    RegimeGateRule,
    ThresholdRule,
    is_ref,
)
from fiboki.validation.evaluation import ParameterGrid

__all__ = [
    "MUTATION_OPERATORS",
    "FeasibilityReport",
    "MutationEngine",
    "MutationProposal",
    "domain_feasibility",
    "mutation_lineage",
]

MUTATION_OPERATORS: tuple[str, ...] = (
    "add_filter",
    "remove_filter",
    "alter_stop_model",
    "alter_target_model",
    "change_regime_gate",
    "change_session_restriction",
    "change_confirmation_rule",
    "simplify",
    "combine",
)

_ID_TAIL = re.compile(r"_m[0-9a-f]{6,}$")


# --------------------------------------------------------------------------
# Feasibility of the declared parameter domains
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeasibilityReport:
    """Whether every cell of a document's DECLARED grid is a strategy."""

    n_cells: int
    n_infeasible: int
    exhaustive: bool
    """True when every cell of the full declared cartesian product was checked.
    False means the product was too large and a thinned grid was checked
    instead, so a clean report is evidence but not proof."""
    declared_product: int
    axes: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()

    @property
    def feasible(self) -> bool:
        return self.n_infeasible == 0

    @property
    def infeasible_fraction(self) -> float:
        return self.n_infeasible / self.n_cells if self.n_cells else 0.0

    def describe(self) -> str:
        if self.feasible:
            scope = "the full declared grid" if self.exhaustive else "a thinned grid"
            return f"{self.n_cells} cells of {scope} all bind"
        return (
            f"{self.n_infeasible} of {self.n_cells} declared grid cells cannot exist "
            f"({self.infeasible_fraction:.1%}). The document's parameter domains are "
            f"jointly incoherent. First example: {self.examples[0] if self.examples else '?'}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_cells": self.n_cells,
            "n_infeasible": self.n_infeasible,
            "infeasible_fraction": round(self.infeasible_fraction, 6),
            "exhaustive": self.exhaustive,
            "declared_product": self.declared_product,
            "axes": list(self.axes),
            "examples": list(self.examples),
            "feasible": self.feasible,
            "description": self.describe(),
        }


def _declared_product(document: StrategyDocument) -> int:
    total = 1
    for spec in document.parameters.values():
        try:
            total *= len(spec.domain())
        except ValueError:
            # A continuous float domain has no cell count; treat it as one axis
            # of unknown size rather than claiming a product that is a fiction.
            return 0
    return total


def domain_feasibility(
    document: StrategyDocument,
    *,
    max_cells: int = 512,
    max_values_per_axis: int = 3,
    max_examples: int = 5,
) -> FeasibilityReport:
    """Bind every cell of the declared grid and count the ones that cannot exist.

    The grid is the document's OWN declared domains, thinned to
    ``max_values_per_axis`` values per axis and capped at ``max_cells`` points so
    that a seven-parameter document does not cost ten million binds. Thinning
    keeps both endpoints of every axis, which is where an incoherent pair of
    domains shows up first (``fast`` at its maximum against ``slow`` at its
    minimum).
    """
    if not document.parameters:
        try:
            document.bind_defaults()
        except BindingError as exc:
            return FeasibilityReport(1, 1, True, 1, (), (str(exc),))
        return FeasibilityReport(1, 0, True, 1, (), ())

    grid = ParameterGrid.from_document(
        document, max_values_per_axis=max_values_per_axis, max_points=max_cells
    )
    defaults = document.default_values()
    infeasible: list[str] = []
    points = grid.points()
    for point in points:
        try:
            document.bind({**defaults, **point})
        except BindingError as exc:
            if len(infeasible) < max_examples:
                infeasible.append(str(exc))
            else:
                infeasible.append("")
    declared = _declared_product(document)
    return FeasibilityReport(
        n_cells=len(points),
        n_infeasible=len(infeasible),
        exhaustive=bool(declared) and grid.full_size() == declared,
        declared_product=declared,
        axes=grid.names,
        examples=tuple(x for x in infeasible[:max_examples] if x),
    )


def compiles(document: StrategyDocument) -> tuple[bool, str]:
    """Does this document turn into a runnable strategy at its own defaults?"""
    try:
        compile_strategy(document.bind_defaults())
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


# --------------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MutationProposal:
    """One attempted mutation, accepted or refused, with its reasoning."""

    operator: str
    rationale: str
    parents: tuple[str, ...] = ()
    """Parent CONTENT hashes, in the order they contributed."""
    parent_ids: tuple[str, ...] = ()
    document: StrategyDocument | None = None
    accepted: bool = False
    rejection: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def strategy_id(self) -> str:
        return self.document.strategy_id if self.document is not None else ""

    @property
    def content_hash(self) -> str:
        return self.document.content_hash() if self.document is not None else ""

    @property
    def generation(self) -> int:
        if self.document is None or self.document.mutation is None:
            return 0
        return int(self.document.mutation.generation)

    def describe(self) -> str:
        head = f"{self.operator}({', '.join(self.parent_ids) or '-'})"
        if self.accepted:
            return f"{head} -> {self.strategy_id} [{self.content_hash[:12]}]: {self.rationale}"
        return f"{head} REJECTED: {self.rejection}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "rationale": self.rationale,
            "parents": list(self.parents),
            "parent_ids": list(self.parent_ids),
            "accepted": self.accepted,
            "rejection": self.rejection,
            "strategy_id": self.strategy_id,
            "content_hash": self.content_hash,
            "generation": self.generation,
            "diagnostics": dict(self.diagnostics),
        }


# --------------------------------------------------------------------------
# Rule catalogues
# --------------------------------------------------------------------------


def _ind(name: str, **params: Any) -> IndicatorOperand:
    return IndicatorOperand(spec=IndicatorSpec(indicator=name, params=params))


def _ind_out(name: str, output: str, **params: Any) -> IndicatorOperand:
    return IndicatorOperand(spec=IndicatorSpec(indicator=name, params=params), output=output)


#: Filters a mutation may add. Symmetric (they say nothing about direction),
#: literal-valued (they do not enlarge the parent's sweep), and each one is a
#: condition a discretionary trader would recognise.
FILTER_CATALOGUE: dict[str, Any] = {
    "adx_trend_floor": ThresholdRule(
        operand=_ind("adx", period=14), comparator=">=", value=20.0
    ),
    "adx_chop_ceiling": ThresholdRule(
        operand=_ind("adx", period=14), comparator="<=", value=45.0
    ),
    "volatility_floor": ThresholdRule(
        operand=_ind("realised_volatility", period=20), comparator=">=", value=0.0015
    ),
    "volatility_ceiling": ThresholdRule(
        operand=_ind("realised_volatility", period=20), comparator="<=", value=0.045
    ),
}

FILTER_RATIONALE: dict[str, str] = {
    "adx_trend_floor": (
        "a directional system should not deal in a range; ADX below 20 is the "
        "conventional marker for one"
    ),
    "adx_chop_ceiling": (
        "very high ADX marks an extended move, where entering late is buying the "
        "end of the trend"
    ),
    "volatility_floor": (
        "below a realised-volatility floor the spread is a larger share of the "
        "expected move than the edge is"
    ),
    "volatility_ceiling": (
        "in the top tail of realised volatility the stop distance the document "
        "declares stops describing the risk"
    ),
}


#: Confirmations come in LONG/SHORT pairs, because a confirmation that only
#: exists on one side turns a symmetric strategy into two different ones.
CONFIRMATION_CATALOGUE: dict[str, tuple[Any, Any, str]] = {
    "rsi_midline": (
        ThresholdRule(operand=_ind("rsi", period=14), comparator=">", value=50.0),
        ThresholdRule(operand=_ind("rsi", period=14), comparator="<", value=50.0),
        "require momentum to agree with the direction of the entry",
    ),
    "macd_histogram": (
        ThresholdRule(
            operand=_ind_out("macd", "hist", fast=12, slow=26, signal=9),
            comparator=">",
            value=0.0,
        ),
        ThresholdRule(
            operand=_ind_out("macd", "hist", fast=12, slow=26, signal=9),
            comparator="<",
            value=0.0,
        ),
        "require the MACD histogram to have turned before committing",
    ),
    "price_above_ema50": (
        IndicatorVsPriceRule(
            indicator=_ind("ema", period=50),
            price=PriceOperand(field="close"),
            comparator="<",
        ),
        IndicatorVsPriceRule(
            indicator=_ind("ema", period=50),
            price=PriceOperand(field="close"),
            comparator=">",
        ),
        "require price on the correct side of the medium EMA",
    ),
}


#: Session windows a mutation may impose, in UTC hours.
SESSION_CATALOGUE: dict[str, SessionRestriction | None] = {
    "london": SessionRestriction(windows=((7, 15),), weekdays=(0, 1, 2, 3, 4)),
    "new_york": SessionRestriction(windows=((13, 20),), weekdays=(0, 1, 2, 3, 4)),
    "overlap": SessionRestriction(windows=((13, 15),), weekdays=(0, 1, 2, 3, 4)),
    "no_friday_close": SessionRestriction(
        windows=(), weekdays=(0, 1, 2, 3, 4), block_bars_before_weekend=2
    ),
    "unrestricted": None,
}

SESSION_RATIONALE: dict[str, str] = {
    "london": "liquidity and the bulk of the day's range arrive with London",
    "new_york": "the US session carries the macro releases this family reacts to",
    "overlap": "the London/New York overlap is the tightest-spread window of the day",
    "no_friday_close": (
        "refuse entries that can only be managed across a weekend gap the model "
        "does not simulate"
    ),
    "unrestricted": (
        "test whether the session restriction was carrying the result or merely "
        "shrinking the sample"
    ),
}


#: Take-profit shapes. Allocations always sum to <= 1.0.
TARGET_CATALOGUE: dict[str, tuple[TakeProfitLeg, ...]] = {
    "single_2r": (TakeProfitLeg(kind="r_multiple", value=2.0, allocation=1.0, label="2R"),),
    "scale_1r_3r": (
        TakeProfitLeg(kind="r_multiple", value=1.0, allocation=0.5, label="1R"),
        TakeProfitLeg(kind="r_multiple", value=3.0, allocation=0.5, label="3R"),
    ),
    "runner_1_5r": (
        TakeProfitLeg(kind="r_multiple", value=1.5, allocation=0.5, label="1.5R"),
    ),
    "none": (),
}

TARGET_RATIONALE: dict[str, str] = {
    "single_2r": "a single fixed 2R target: the simplest thing that can be wrong",
    "scale_1r_3r": "bank half at 1R to raise the hit rate, let half run to 3R",
    "runner_1_5r": "take half off at 1.5R and leave the remainder to the trail",
    "none": (
        "remove the targets entirely and let the stop and trail decide, to see "
        "whether the targets were adding anything"
    ),
}


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------


class MutationEngine:
    """Applies the operators, validates the results, and records the refusals."""

    def __init__(
        self,
        *,
        seed: int = 20260919,
        max_feasibility_cells: int = 512,
        max_values_per_axis: int = 3,
    ) -> None:
        self.seed = int(seed)
        self.max_feasibility_cells = int(max_feasibility_cells)
        self.max_values_per_axis = int(max_values_per_axis)
        self.proposals: list[MutationProposal] = []

    # ----------------------------------------------------------- public API

    def apply(
        self,
        operator: str,
        document: StrategyDocument,
        *,
        partner: StrategyDocument | None = None,
        variant: str | None = None,
        rng: random.Random | None = None,
    ) -> MutationProposal:
        """Apply one operator and validate the result.

        Never raises for a mutation that cannot be made: an impossible mutation
        is a refused :class:`MutationProposal`, because the refusal is a finding
        the campaign records rather than an exception it has to survive.
        """
        if operator not in MUTATION_OPERATORS:
            raise KeyError(
                f"unknown mutation operator {operator!r}; the set is closed: "
                f"{list(MUTATION_OPERATORS)}"
            )
        rng = rng or random.Random(f"{self.seed}:{operator}:{document.content_hash()}")
        parents: tuple[str, ...] = (document.content_hash(),)
        parent_ids: tuple[str, ...] = (document.strategy_id,)
        if partner is not None:
            parents = (*parents, partner.content_hash())
            parent_ids = (*parent_ids, partner.strategy_id)

        try:
            built = getattr(self, f"_op_{operator}")(
                document, partner=partner, variant=variant, rng=rng
            )
        except _MutationRefused as refusal:
            return self._record(
                MutationProposal(
                    operator=operator,
                    rationale=str(refusal),
                    parents=parents,
                    parent_ids=parent_ids,
                    accepted=False,
                    rejection=str(refusal),
                )
            )

        changes, description, extra_parent = built
        partners = (partner,) if extra_parent and partner is not None else ()
        try:
            mutant = _derive(
                document,
                changes=changes,
                operator=operator,
                description=description,
                partners=partners,
            )
        except Exception as exc:
            return self._record(
                MutationProposal(
                    operator=operator,
                    rationale=description,
                    parents=parents,
                    parent_ids=parent_ids,
                    accepted=False,
                    rejection=f"the mutated document is not valid: {type(exc).__name__}: {exc}",
                )
            )

        if mutant.content_hash() == document.content_hash():
            return self._record(
                MutationProposal(
                    operator=operator,
                    rationale=description,
                    parents=parents,
                    parent_ids=parent_ids,
                    accepted=False,
                    rejection=(
                        "no-op: the mutation produced a document identical in content "
                        "to its parent, which is not a new thing to test"
                    ),
                )
            )
        return self._record(
            self._validate(mutant, document, operator, description, parents, parent_ids)
        )

    def propose(
        self,
        document: StrategyDocument,
        *,
        operators: Sequence[str] | None = None,
        partners: Sequence[StrategyDocument] = (),
        variants: Mapping[str, Sequence[str]] | None = None,
    ) -> list[MutationProposal]:
        """Every proposal this engine makes for one parent, in a fixed order.

        Deterministic: the same parent always yields the same proposals in the
        same order, so a campaign is reproducible and a resumed campaign plans
        the same population it planned before.
        """
        wanted = tuple(operators) if operators is not None else MUTATION_OPERATORS
        out: list[MutationProposal] = []
        for operator in wanted:
            if operator == "combine":
                for partner in partners:
                    if partner.content_hash() == document.content_hash():
                        continue
                    out.append(self.apply(operator, document, partner=partner))
                continue
            choices = (variants or {}).get(operator)
            if choices is None:
                out.append(self.apply(operator, document))
                continue
            out.extend(self.apply(operator, document, variant=v) for v in choices)
        return out

    @property
    def accepted(self) -> list[MutationProposal]:
        return [p for p in self.proposals if p.accepted]

    @property
    def rejected(self) -> list[MutationProposal]:
        return [p for p in self.proposals if not p.accepted]

    # ------------------------------------------------------------ internals

    def _record(self, proposal: MutationProposal) -> MutationProposal:
        self.proposals.append(proposal)
        return proposal

    def _validate(
        self,
        mutant: StrategyDocument,
        parent: StrategyDocument,
        operator: str,
        description: str,
        parents: tuple[str, ...],
        parent_ids: tuple[str, ...],
    ) -> MutationProposal:
        feasibility = domain_feasibility(
            mutant,
            max_cells=self.max_feasibility_cells,
            max_values_per_axis=self.max_values_per_axis,
        )
        diagnostics: dict[str, Any] = {"feasibility": feasibility.to_dict()}
        if not feasibility.feasible:
            return MutationProposal(
                operator=operator,
                rationale=description,
                parents=parents,
                parent_ids=parent_ids,
                accepted=False,
                rejection=(
                    "infeasible parameter domain -- " + feasibility.describe()
                ),
                diagnostics=diagnostics,
            )
        ok, message = compiles(mutant)
        diagnostics["compiles"] = ok
        if not ok:
            return MutationProposal(
                operator=operator,
                rationale=description,
                parents=parents,
                parent_ids=parent_ids,
                accepted=False,
                rejection=f"does not compile: {message}",
                diagnostics=diagnostics,
            )
        diagnostics["complexity_score"] = mutant.complexity_score
        diagnostics["parent_complexity_score"] = parent.complexity_score
        diagnostics["complexity_delta"] = round(
            mutant.complexity_score - parent.complexity_score, 3
        )
        return MutationProposal(
            operator=operator,
            rationale=description,
            parents=parents,
            parent_ids=parent_ids,
            document=mutant,
            accepted=True,
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------ operators
    #
    # Each returns ``(changes, description, uses_partner)``. ``changes`` is a
    # mapping of DUMPED document fields, which is what ``_derive`` merges.

    def _op_add_filter(
        self, doc: StrategyDocument, *, variant: str | None, rng: random.Random, **_: Any
    ) -> tuple[dict[str, Any], str, bool]:
        existing = {_canonical(r) for r in doc.all_rules()}
        available = [
            name
            for name, rule in FILTER_CATALOGUE.items()
            if _canonical(rule) not in existing
        ]
        if variant is not None:
            if variant not in FILTER_CATALOGUE:
                raise _MutationRefused(f"unknown filter variant {variant!r}")
            if variant not in available:
                raise _MutationRefused(
                    f"the parent already contains the {variant!r} condition; adding "
                    "it again would change nothing"
                )
            chosen = variant
        else:
            if not available:
                raise _MutationRefused(
                    "every filter in the catalogue is already present in the parent"
                )
            chosen = rng.choice(sorted(available))
        rule = FILTER_CATALOGUE[chosen]
        filters = [*_dump_all(doc.filters), rule.model_dump(mode="json")]
        return (
            {"filters": filters},
            f"add filter {chosen}: {FILTER_RATIONALE[chosen]}",
            False,
        )

    def _op_remove_filter(
        self, doc: StrategyDocument, *, variant: str | None, rng: random.Random, **_: Any
    ) -> tuple[dict[str, Any], str, bool]:
        if not doc.filters:
            raise _MutationRefused("the parent declares no filters, so none can be removed")
        index = rng.randrange(len(doc.filters)) if variant is None else int(variant)
        if not 0 <= index < len(doc.filters):
            raise _MutationRefused(f"filter index {index} is out of range")
        remaining = [
            r.model_dump(mode="json") for i, r in enumerate(doc.filters) if i != index
        ]
        return (
            {"filters": remaining},
            (
                f"remove filter #{index} -- a filter that is not carrying the result "
                "is only shrinking the sample"
            ),
            False,
        )

    def _op_alter_stop_model(
        self, doc: StrategyDocument, *, variant: str | None, rng: random.Random, **_: Any
    ) -> tuple[dict[str, Any], str, bool]:
        atr = doc.stop.atr or _borrowed_atr(doc) or _ind("atr", period=14)
        options: dict[str, tuple[StopModel, str]] = {
            "atr_tight": (
                StopModel(kind="atr_multiple", value=1.5, atr=atr, min_distance_atr=0.5),
                "a tighter ATR stop: smaller risk per trade, more stop-outs",
            ),
            "atr_wide": (
                StopModel(kind="atr_multiple", value=3.5, atr=atr, min_distance_atr=0.5),
                "a wider ATR stop: fewer stop-outs, larger loss when one happens",
            ),
            "percent": (
                StopModel(kind="percent", value=1.0, atr=atr, min_distance_atr=0.5),
                (
                    "a percent-of-price stop: risk no longer scales with realised "
                    "volatility, which is a different claim about what risk is"
                ),
            ),
        }
        if doc.stop.level is not None:
            options["structure"] = (
                StopModel(
                    kind=doc.stop.kind,
                    value=doc.stop.value if not is_ref(doc.stop.value) else 1.0,
                    atr=atr,
                    level=doc.stop.level,
                    buffer_atr=1.0,
                    min_distance_atr=0.5,
                ),
                "keep the structural stop but widen its buffer to a full ATR",
            )
        chosen = variant or rng.choice(sorted(options))
        if chosen not in options:
            raise _MutationRefused(
                f"stop variant {chosen!r} is not available for this parent "
                f"(available: {sorted(options)})"
            )
        stop, why = options[chosen]
        return ({"stop": stop.model_dump(mode="json")}, f"alter stop to {chosen}: {why}", False)

    def _op_alter_target_model(
        self, doc: StrategyDocument, *, variant: str | None, rng: random.Random, **_: Any
    ) -> tuple[dict[str, Any], str, bool]:
        current = _dump_all(doc.take_profits)
        options = {
            name: legs
            for name, legs in TARGET_CATALOGUE.items()
            if _dump_all(legs) != current
        }
        if not options:
            raise _MutationRefused("every target shape in the catalogue matches the parent")
        chosen = variant or rng.choice(sorted(options))
        if chosen not in options:
            raise _MutationRefused(
                f"target variant {chosen!r} is unavailable (it matches the parent or "
                f"is unknown); available: {sorted(options)}"
            )
        if chosen == "none" and (doc.trailing is None or doc.trailing.kind == "none"):
            raise _MutationRefused(
                "removing every target from a strategy with no trailing model leaves "
                "the hard stop and the time stop as the only exits, which is a "
                "different family, not a mutation of this one"
            )
        return (
            {"take_profits": _dump_all(options[chosen])},
            f"alter targets to {chosen}: {TARGET_RATIONALE[chosen]}",
            False,
        )

    def _op_change_regime_gate(
        self, doc: StrategyDocument, *, variant: str | None, rng: random.Random, **_: Any
    ) -> tuple[dict[str, Any], str, bool]:
        gates = [r for r in doc.regime if isinstance(r, RegimeGateRule)]
        options = ["adx_gate", "volatility_gate"]
        if gates:
            options = ["remove", "tighten", "loosen", *options]
        chosen = variant or rng.choice(sorted(options))
        if chosen not in options:
            raise _MutationRefused(
                f"regime variant {chosen!r} is unavailable for this parent "
                f"(available: {sorted(options)})"
            )
        if chosen == "remove":
            return (
                {"regime": []},
                (
                    "remove the regime gate -- test whether the regime claim was "
                    "doing work or only removing trades"
                ),
                False,
            )
        if chosen in ("tighten", "loosen"):
            factor = 1.5 if chosen == "tighten" else 1.0 / 1.5
            changed = [_scale_gate(r, factor) for r in doc.regime]
            if changed == _dump_all(doc.regime):
                raise _MutationRefused(
                    "the regime gate's bounds are parameter REFERENCES, not literals, "
                    "so there is no width to scale here -- the width is already part "
                    "of the declared sweep and moving it is a reparameterisation"
                )
            return (
                {"regime": changed},
                (
                    f"{chosen} the regime gate by {factor:.2f}x -- a regime claim "
                    "that only holds at one width is a fitted number, not a regime"
                ),
                False,
            )
        rule = (
            RegimeGateRule(metric=_ind("adx", period=14), min_value=20.0)
            if chosen == "adx_gate"
            else RegimeGateRule(
                metric=_ind("realised_volatility", period=20),
                min_value=0.0015,
                max_value=0.05,
            )
        )
        if _canonical(rule) in {_canonical(r) for r in doc.regime}:
            raise _MutationRefused(f"the parent already declares the {chosen!r} regime gate")
        return (
            {"regime": [rule.model_dump(mode="json")]},
            (
                f"replace the regime gate with {chosen} -- the same strategy under a "
                "different statement of where it is supposed to work"
            ),
            False,
        )

    def _op_change_session_restriction(
        self, doc: StrategyDocument, *, variant: str | None, rng: random.Random, **_: Any
    ) -> tuple[dict[str, Any], str, bool]:
        current = doc.sessions.model_dump(mode="json") if doc.sessions else None
        options = {
            name: value
            for name, value in SESSION_CATALOGUE.items()
            if (value.model_dump(mode="json") if value else None) != current
        }
        if not options:
            raise _MutationRefused("every session restriction in the catalogue matches the parent")
        chosen = variant or rng.choice(sorted(options))
        if chosen not in options:
            raise _MutationRefused(
                f"session variant {chosen!r} is unavailable (it matches the parent or "
                f"is unknown); available: {sorted(options)}"
            )
        value = options[chosen]
        return (
            {"sessions": value.model_dump(mode="json") if value else None},
            f"restrict dealing to {chosen}: {SESSION_RATIONALE[chosen]}",
            False,
        )

    def _op_change_confirmation_rule(
        self, doc: StrategyDocument, *, variant: str | None, rng: random.Random, **_: Any
    ) -> tuple[dict[str, Any], str, bool]:
        existing = {_canonical(r) for r in doc.all_rules()}
        wants_long = doc.direction in (TradeDirection.LONG, TradeDirection.BOTH)
        wants_short = doc.direction in (TradeDirection.SHORT, TradeDirection.BOTH)
        available = [
            name
            for name, (long_rule, short_rule, _why) in CONFIRMATION_CATALOGUE.items()
            if not (
                (wants_long and _canonical(long_rule) in existing)
                and (wants_short and _canonical(short_rule) in existing)
            )
        ]
        options = [*available]
        if doc.confirmation.long or doc.confirmation.short:
            options.append("remove")
        if not options:
            raise _MutationRefused(
                "every confirmation in the catalogue is already present and there is "
                "none to remove"
            )
        chosen = variant or rng.choice(sorted(options))
        if chosen not in options:
            raise _MutationRefused(
                f"confirmation variant {chosen!r} is unavailable for this parent "
                f"(available: {sorted(options)})"
            )
        if chosen == "remove":
            return (
                {"confirmation": RuleSet().model_dump(mode="json")},
                (
                    "remove the confirmation rules -- a confirmation delays entry, and "
                    "the delay costs more than the filtering earns more often than not"
                ),
                False,
            )
        long_rule, short_rule, why = CONFIRMATION_CATALOGUE[chosen]
        confirmation = {
            "long": [*_dump_all(doc.confirmation.long)] if wants_long else [],
            "short": [*_dump_all(doc.confirmation.short)] if wants_short else [],
        }
        if wants_long:
            confirmation["long"].append(long_rule.model_dump(mode="json"))
        if wants_short:
            confirmation["short"].append(short_rule.model_dump(mode="json"))
        return (
            {"confirmation": confirmation},
            f"add confirmation {chosen}: {why}",
            False,
        )

    def _op_simplify(
        self, doc: StrategyDocument, *, variant: str | None, rng: random.Random, **_: Any
    ) -> tuple[dict[str, Any], str, bool]:
        """Remove ONE rule. Never from ``entry``: that is a different strategy."""
        sections: list[tuple[str, int]] = []
        if doc.confirmation.long or doc.confirmation.short:
            sections.append(("confirmation", max(len(doc.confirmation.long), len(doc.confirmation.short))))
        if doc.filters:
            sections.append(("filters", len(doc.filters)))
        if doc.regime:
            sections.append(("regime", len(doc.regime)))
        if doc.setup.long or doc.setup.short:
            sections.append(("setup", max(len(doc.setup.long), len(doc.setup.short))))
        if doc.invalidation.long or doc.invalidation.short:
            sections.append(
                ("invalidation", max(len(doc.invalidation.long), len(doc.invalidation.short)))
            )
        if not sections:
            raise _MutationRefused(
                "there is nothing to simplify: the parent declares only entry rules, "
                "and removing one of those makes a different strategy rather than a "
                "simpler version of this one"
            )
        section = variant or max(sections, key=lambda pair: (pair[1], pair[0]))[0]
        names = {s for s, _ in sections}
        if section not in names:
            raise _MutationRefused(
                f"section {section!r} holds no removable rules (removable: {sorted(names)})"
            )
        changes: dict[str, Any]
        if section in ("filters", "regime"):
            rules = list(getattr(doc, section))
            index = rng.randrange(len(rules))
            remaining = [r.model_dump(mode="json") for i, r in enumerate(rules) if i != index]
            changes = {section: remaining}
            detail = f"{section} rule #{index}"
        else:
            ruleset: RuleSet = getattr(doc, section)
            longest = max(len(ruleset.long), len(ruleset.short))
            index = rng.randrange(longest)
            changes = {
                section: {
                    "long": [
                        r.model_dump(mode="json")
                        for i, r in enumerate(ruleset.long)
                        if i != index
                    ],
                    "short": [
                        r.model_dump(mode="json")
                        for i, r in enumerate(ruleset.short)
                        if i != index
                    ],
                }
            }
            detail = f"{section} rule #{index} on both sides"
        return (
            changes,
            (
                f"simplify: remove {detail}. Every rule is a degree of freedom, and "
                "the simplest version of an idea is the one least able to fit the "
                "sample it was found in"
            ),
            False,
        )

    def _op_combine(
        self,
        doc: StrategyDocument,
        *,
        partner: StrategyDocument | None,
        variant: str | None,
        rng: random.Random,
        **_: Any,
    ) -> tuple[dict[str, Any], str, bool]:
        """A's entry logic with B's exit model. Compatibility is CHECKED."""
        if partner is None:
            raise _MutationRefused("combine needs a second parent")
        if partner.direction is not doc.direction:
            raise _MutationRefused(
                f"incompatible parents: {doc.strategy_id} trades "
                f"{doc.direction.value} and {partner.strategy_id} trades "
                f"{partner.direction.value}; the child would have rules for a side "
                "it is not allowed to take"
            )
        shared_universe = [s for s in doc.universe if s in partner.universe]
        shared_tfs = [t for t in doc.timeframes if t in partner.timeframes]
        if not shared_universe:
            raise _MutationRefused(
                f"incompatible parents: {doc.strategy_id} and {partner.strategy_id} "
                "claim no instrument in common, so the child has no universe"
            )
        if not shared_tfs:
            raise _MutationRefused(
                f"incompatible parents: {doc.strategy_id} and {partner.strategy_id} "
                "claim no timeframe in common"
            )

        # Parameters: the union. A name declared by BOTH with different domains
        # would silently mean two different things in one document, so it is
        # refused rather than resolved by a rule nobody would remember.
        merged: dict[str, ParameterSpec] = dict(doc.parameters)
        for name, spec in partner.parameters.items():
            if name in merged and merged[name] != spec:
                raise _MutationRefused(
                    f"incompatible parents: both declare a parameter {name!r} with "
                    "different domains, so one document cannot hold both meanings"
                )
            merged[name] = spec

        # The UNION is declared here; ``_derive`` then drops whichever of them
        # the child no longer references, so the child's declared sweep is
        # exactly the sweep the child can actually perform.
        parameters = {name: spec.model_dump(mode="json") for name, spec in merged.items()}

        changes = {
            "universe": shared_universe,
            "timeframes": [t.value for t in shared_tfs],
            "stop": partner.stop.model_dump(mode="json"),
            "take_profits": _dump_all(partner.take_profits),
            "trailing": partner.trailing.model_dump(mode="json") if partner.trailing else None,
            "parameters": parameters,
            "family": "hybrid",
        }
        return (
            changes,
            (
                f"combine: the entry logic of {doc.strategy_id} with the exit model of "
                f"{partner.strategy_id}. Entries and exits are separable claims, and a "
                "cross tests whether the parent's result came from the one or the other"
            ),
            True,
        )


class _MutationRefused(Exception):
    """A mutation that cannot be made. Recorded on the proposal, never raised out."""


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------


def _derive(
    parent: StrategyDocument,
    *,
    changes: Mapping[str, Any],
    operator: str,
    description: str,
    partners: Sequence[StrategyDocument] = (),
) -> StrategyDocument:
    """Build a mutant from a parent plus a mapping of dumped field changes.

    Two things happen here that are easy to leave out and expensive to leave out.

    **Unreferenced parameters are dropped.** Removing a rule can orphan the
    parameter that rule referenced. A document that still DECLARES that
    parameter declares a sweep axis along which nothing changes, and a report of
    that sweep would claim to have explored something it did not -- the axis
    would move the content hash and the trial count while moving no behaviour at
    all. So the mutant declares exactly the parameters it still uses.

    **The id is derived from the CONTENT hash**, computed after the pruning, so
    two independently proposed but identical mutants collide on one id and one
    hash rather than becoming two entries in the ledger for one idea.
    """
    payload = parent.model_dump(mode="json")
    payload.pop("complexity_score", None)
    payload.update(dict(changes))
    payload["binding"] = None
    generation = (parent.mutation.generation + 1) if parent.mutation else 1
    payload["parent_strategy_ids"] = [
        parent.strategy_id,
        *[p.strategy_id for p in partners],
    ]
    payload["mutation"] = MutationRecord(
        parent_hash=parent.content_hash(),
        operator=operator,
        description=description,
        generation=generation,
    ).model_dump(mode="json")
    payload["hypothesis"] = (
        parent.hypothesis
        + f"\n\nMUTATION (generation {generation}, operator {operator}). "
        + description
        + " The economic story above is INHERITED and has not been re-argued for "
        "this variant; what is new here is the change just named, and the "
        "prediction that it should not make the result worse for the reason given."
    )
    payload["notes"] = (
        (parent.notes + "\n" if parent.notes else "")
        + f"derived from {parent.strategy_id} by {operator}"
    )
    payload["strategy_id"] = "mutant_placeholder"

    provisional = StrategyDocument.model_validate(payload)
    referenced = set(provisional.unbound_parameters())
    declared = dict(payload.get("parameters") or {})
    pruned = {name: spec for name, spec in declared.items() if name in referenced}
    if pruned != declared:
        payload["parameters"] = pruned
        provisional = StrategyDocument.model_validate(payload)
    payload["strategy_id"] = _mutant_id(parent.strategy_id, provisional.content_hash())
    return StrategyDocument.model_validate(payload)


def _mutant_id(parent_id: str, content_hash: str) -> str:
    base = _ID_TAIL.sub("", parent_id)[:44].rstrip("_")
    return f"{base}_m{content_hash[:8]}"


def _dump_all(items: Sequence[Any]) -> list[dict[str, Any]]:
    return [i.model_dump(mode="json") for i in items]


def _canonical(node: Any) -> str:
    payload = node.model_dump(mode="json") if hasattr(node, "model_dump") else node
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _borrowed_atr(doc: StrategyDocument) -> IndicatorOperand | None:
    """An ATR operand already present in the document, if any."""
    for operand in doc.exit_operands():
        if operand.spec.indicator == "atr":
            return operand
    for spec in doc.rule_indicators():
        if spec.indicator == "atr":
            return IndicatorOperand(spec=spec)
    return None


def _scale_gate(rule: Any, factor: float) -> dict[str, Any]:
    """Scale a regime gate's literal bounds. References are left alone."""
    payload = rule.model_dump(mode="json")
    if payload.get("op") != "regime_gate":
        return payload
    lo, hi = payload.get("min_value"), payload.get("max_value")
    if isinstance(lo, int | float) and not isinstance(lo, bool):
        payload["min_value"] = round(float(lo) * factor, 10)
    if isinstance(hi, int | float) and not isinstance(hi, bool):
        payload["max_value"] = round(float(hi) / factor, 10)
    if (
        isinstance(payload.get("min_value"), int | float)
        and isinstance(payload.get("max_value"), int | float)
        and payload["min_value"] > payload["max_value"]
    ):
        raise _MutationRefused(
            "scaling the regime gate would invert its bounds, which is not a "
            "tighter regime claim but an empty one"
        )
    return payload


# --------------------------------------------------------------------------
# Lineage
# --------------------------------------------------------------------------


def mutation_lineage(proposals: Sequence[MutationProposal]) -> LineageGraph:
    """A lineage graph of a mutation population, refusals included.

    Uses :mod:`fiboki.research.lineage`'s node and edge types so a campaign's
    proposal graph and the ledger's provenance graph are the same shape and can
    be rendered, walked or merged by the same code. An edge's ``relation`` is
    the operator, so reading the graph answers "how did this document come to
    exist?" without opening the document.
    """
    graph = LineageGraph()
    for proposal in proposals:
        child_id = proposal.content_hash or f"rejected::{_proposal_key(proposal)}"
        child = LineageNode(
            kind="strategy" if proposal.accepted else "rejected_mutation",
            id=child_id,
            label=proposal.describe(),
            attrs={
                "operator": proposal.operator,
                "accepted": proposal.accepted,
                "rationale": proposal.rationale,
                "rejection": proposal.rejection,
                "generation": proposal.generation,
                "strategy_id": proposal.strategy_id,
            },
        )
        graph.add_node(child)
        for parent_hash, parent_id in zip(
            proposal.parents, proposal.parent_ids, strict=False
        ):
            graph.add_edge(
                LineageNode(kind="strategy", id=parent_hash, label=parent_id),
                child,
                proposal.operator,
            )
        if not proposal.parents:  # pragma: no cover - every proposal has a parent
            graph.add_node(child)
    return graph


def _proposal_key(proposal: MutationProposal) -> str:
    blob = "|".join(
        (proposal.operator, *proposal.parents, proposal.rejection, proposal.rationale)
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
