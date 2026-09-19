"""Promotion gates as DATA, versioned, and recorded on every report.

Two separate failures are being prevented here.

**Thresholds as code.** V1's promotion rules were ``if`` statements scattered
across the ranking script, so "what did this strategy actually have to beat?"
could only be answered by reading the version of the script that happened to be
checked out at the time -- and that script had already changed. Here a gate is a
row: a name, a comparison, a number, and a reason the number is what it is. A
:class:`GateSet` has a version and a fingerprint, both stamped onto the
:class:`~fiboki.validation.report.ValidationReport`, so a reader two years later
can tell whether a result cleared today's bar or a softer one.

**Silent skipping.** A gate whose input could not be computed is NOT a pass. It
is :class:`GateStatus.NOT_APPLICABLE` only when the gate genuinely does not
apply (a strategy with no numeric parameters has no plateau to sit on), and
:class:`GateStatus.NOT_EVALUATED` -- which blocks promotion -- whenever the
input was merely missing. Failing closed is the whole point.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "GATE_SET_V2",
    "Comparison",
    "Gate",
    "GateResult",
    "GateSet",
    "GateStatus",
]


class Comparison(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"

    def holds(self, value: float, threshold: float) -> bool:
        if not math.isfinite(value):
            return False
        return {
            Comparison.GT: value > threshold,
            Comparison.GTE: value >= threshold,
            Comparison.LT: value < threshold,
            Comparison.LTE: value <= threshold,
        }[self]

    @property
    def symbol(self) -> str:
        return {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[self.value]

    @property
    def wants_larger(self) -> bool:
        return self in (Comparison.GT, Comparison.GTE)


class GateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    NOT_EVALUATED = "not_evaluated"

    @property
    def blocks_promotion(self) -> bool:
        return self in (GateStatus.FAIL, GateStatus.NOT_EVALUATED)


@dataclass(frozen=True, slots=True)
class Gate:
    """One promotion threshold."""

    name: str
    metric: str
    """Key looked up in the metric mapping the ladder produces."""
    comparison: Comparison
    threshold: float
    rung: int
    """The ladder rung that produces this gate's input."""
    rationale: str = ""
    boolean: bool = False
    """True when the metric is an indicator in {0, 1} rather than a measurement."""
    units: str = ""

    def evaluate(self, value: float | None, *, applicable: bool = True) -> GateResult:
        if not applicable:
            return GateResult(self, None, GateStatus.NOT_APPLICABLE)
        if value is None:
            return GateResult(self, None, GateStatus.NOT_EVALUATED)
        v = float(value)
        if not math.isfinite(v):
            return GateResult(self, v, GateStatus.NOT_EVALUATED)
        ok = self.comparison.holds(v, self.threshold)
        return GateResult(self, v, GateStatus.PASS if ok else GateStatus.FAIL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "comparison": self.comparison.value,
            "threshold": float(self.threshold),
            "rung": int(self.rung),
            "boolean": bool(self.boolean),
            "units": self.units,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: Gate
    value: float | None
    status: GateStatus

    @property
    def passed(self) -> bool:
        return self.status is GateStatus.PASS

    @property
    def shortfall(self) -> float | None:
        """How far short the value fell, in the metric's own units.

        Always non-negative for a failure, and ``0.0`` for a value that sits
        exactly on a strict boundary (which fails). ``None`` when there is no
        value to compare.
        """
        if self.value is None or not math.isfinite(self.value):
            return None
        if self.gate.comparison.wants_larger:
            gap = self.gate.threshold - self.value
        else:
            gap = self.value - self.gate.threshold
        return float(max(0.0, gap))

    def describe(self) -> str:
        g = self.gate
        if self.value is None:
            return f"{g.name}: {self.status.value} (required {g.comparison.symbol} {g.threshold:g})"
        if g.boolean:
            got = "yes" if self.value >= 0.5 else "no"
            return f"{g.name}: {got} (required yes) -> {self.status.value}"
        return (
            f"{g.name}: {self.value:.4g} vs required {g.comparison.symbol} "
            f"{g.threshold:g} -> {self.status.value}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate.to_dict(),
            "value": None if self.value is None else float(self.value),
            "status": self.status.value,
            "passed": self.passed,
            "shortfall": self.shortfall,
            "description": self.describe(),
        }


@dataclass(frozen=True, slots=True)
class GateSet:
    """A versioned collection of gates, evaluated as one."""

    version: str
    gates: tuple[Gate, ...]
    description: str = ""

    def __post_init__(self) -> None:
        names = [g.name for g in self.gates]
        if len(set(names)) != len(names):
            raise ValueError("gate names must be unique within a GateSet")

    def by_name(self, name: str) -> Gate:
        for gate in self.gates:
            if gate.name == name:
                return gate
        raise KeyError(name)

    @property
    def metrics(self) -> tuple[str, ...]:
        return tuple(g.metric for g in self.gates)

    def evaluate(
        self,
        values: Mapping[str, float | None],
        *,
        not_applicable: frozenset[str] | set[str] = frozenset(),
    ) -> tuple[GateResult, ...]:
        """Evaluate every gate, in declaration order.

        A metric absent from ``values`` is ``NOT_EVALUATED``, which blocks
        promotion. Name a gate in ``not_applicable`` only when it genuinely
        cannot apply to this candidate, never to make a missing number go away.
        """
        return tuple(
            gate.evaluate(
                values.get(gate.metric),
                applicable=gate.name not in not_applicable,
            )
            for gate in self.gates
        )

    @staticmethod
    def binding_constraint(results: tuple[GateResult, ...]) -> GateResult | None:
        """The first blocking gate, in ladder order then declaration order.

        "First" is deliberately not "worst": the ladder is fail-fast, so the gate
        that stopped the candidate is the one a researcher must address, and
        ranking by shortfall would point them at a gate that was never the
        obstacle.
        """
        blocking = [r for r in results if r.status.blocks_promotion]
        if not blocking:
            return None
        return min(blocking, key=lambda r: (r.gate.rung, results.index(r)))

    def fingerprint(self) -> str:
        blob = json.dumps(
            {"version": self.version, "gates": [g.to_dict() for g in self.gates]},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
            "fingerprint": self.fingerprint(),
            "gates": [g.to_dict() for g in self.gates],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GateSet:
        return cls(
            version=str(raw["version"]),
            gates=tuple(
                Gate(
                    name=str(g["name"]),
                    metric=str(g["metric"]),
                    comparison=Comparison(g["comparison"]),
                    threshold=float(g["threshold"]),
                    rung=int(g["rung"]),
                    rationale=str(g.get("rationale", "")),
                    boolean=bool(g.get("boolean", False)),
                    units=str(g.get("units", "")),
                )
                for g in raw["gates"]
            ),
            description=str(raw.get("description", "")),
        )

    def with_overrides(self, version: str, **thresholds: float) -> GateSet:
        """A NEW gate set with some thresholds changed.

        Loosening a threshold is legitimate research (a 15-minute strategy will
        not produce 400 trades in a year of data); doing it without minting a new
        version is not, because the report would then claim to have cleared a bar
        it never faced. The version is therefore mandatory and positional.
        """
        unknown = set(thresholds) - {g.name for g in self.gates}
        if unknown:
            raise KeyError(f"unknown gate(s): {sorted(unknown)}")
        gates = tuple(
            Gate(
                name=g.name,
                metric=g.metric,
                comparison=g.comparison,
                threshold=float(thresholds.get(g.name, g.threshold)),
                rung=g.rung,
                rationale=g.rationale,
                boolean=g.boolean,
                units=g.units,
            )
            for g in self.gates
        )
        return GateSet(version=version, gates=gates, description=self.description)


# --------------------------------------------------------------------------
# The audit's gate set
# --------------------------------------------------------------------------

#: Thresholds as specified by the V2 audit. Changing ANY number here requires a
#: new ``version`` string, because reports carry the version and readers rely on
#: it meaning one fixed thing.
GATE_SET_V2 = GateSet(
    version="v2.0.0-audit",
    description=(
        "Fiboki V2 promotion gates. A candidate must clear every one of these "
        "before it may leave RESEARCH for a paper allocation."
    ),
    gates=(
        Gate(
            name="min_trades",
            metric="n_trades",
            comparison=Comparison.GTE,
            threshold=400.0,
            rung=0,
            units="trades",
            rationale=(
                "V1 ranked on a 80-trade minimum. At 23,040 searched cells the "
                "standard error of an 80-trade mean is large enough that the "
                "leaderboard is a sampling distribution of the maximum, not a "
                "ranking of edges. 400 is the point at which a 0.1 per-trade "
                "effect is separable from noise at this search scale."
            ),
        ),
        Gate(
            name="walk_forward_efficiency",
            metric="walk_forward_efficiency",
            comparison=Comparison.GTE,
            threshold=50.0,
            rung=2,
            units="%",
            rationale=(
                "Out-of-sample profit rate as a percentage of the in-sample "
                "profit rate of the SAME selected parameters. Below 50% the "
                "selection step is destroying more than it adds."
            ),
        ),
        Gate(
            name="oos_window_hit_rate",
            metric="oos_profitable_fraction",
            comparison=Comparison.GTE,
            threshold=0.60,
            rung=2,
            units="fraction",
            rationale=(
                "A strategy profitable in aggregate but in only half its windows "
                "is a bet on one regime that happened to be in the sample."
            ),
        ),
        Gate(
            name="deflated_sharpe",
            metric="deflated_sharpe_ratio",
            comparison=Comparison.GT,
            threshold=0.95,
            rung=5,
            units="probability",
            rationale=(
                "Probability the Sharpe exceeds the expected MAXIMUM of a search "
                "of this effective size. The single number V1 never computed."
            ),
        ),
        Gate(
            name="pbo",
            metric="pbo",
            comparison=Comparison.LT,
            threshold=0.20,
            rung=5,
            units="probability",
            rationale=(
                "CSCV probability that the in-sample best ranks below median "
                "out-of-sample. At 0.5 selection carries no information at all."
            ),
        ),
        Gate(
            name="spa_consistent_p",
            metric="spa_p_consistent",
            comparison=Comparison.LT,
            threshold=0.05,
            rung=5,
            units="p-value",
            rationale=(
                "Hansen's consistent p-value for the best of the family against "
                "the benchmark, which corrects for the whole family being searched."
            ),
        ),
        Gate(
            name="stepm_survivor",
            metric="stepm_member",
            comparison=Comparison.GTE,
            threshold=1.0,
            rung=5,
            boolean=True,
            rationale=(
                "Romano-Wolf StepM identifies WHICH strategies beat the "
                "benchmark under FWER control. A candidate outside the survivor "
                "set has not been individually shown to have an edge."
            ),
        ),
        Gate(
            name="survives_2x_spread",
            metric="net_profit_at_2x_spread",
            comparison=Comparison.GT,
            threshold=0.0,
            rung=4,
            units="account ccy",
            rationale=(
                "Spreads widen. An edge that is exactly the cost assumption is "
                "a cost assumption, not an edge."
            ),
        ),
        Gate(
            name="parameter_plateau",
            metric="point_plateau_ratio",
            comparison=Comparison.LTE,
            threshold=1.25,
            rung=4,
            units="ratio",
            rationale=(
                "Selected point's score divided by the mean of its neighbourhood. "
                "Above 1.25 the result is a spike in the parameter surface, which "
                "does not survive contact with a different sample."
            ),
        ),
    ),
)
