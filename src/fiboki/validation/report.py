"""The ValidationReport: one auditable object per candidate, pass or fail.

A number without its provenance is a rumour. This object exists so that every
figure Fiboki shows an operator can be traced to the exact strategy content, the
exact dataset version, the exact code, the exact engine and broker assumptions,
and the exact thresholds that were in force when the verdict was reached.

What makes it evidence rather than a summary
--------------------------------------------
* **It is produced for every candidate, including rejected ones.** A rejection
  is a research result. V1 kept only winners, so its population of results was
  conditioned on success and its aggregate statistics were meaningless.
* **It names the binding constraint.** "Rejected" is not actionable. "Rejected
  at RUNG 5 DEFLATION: deflated_sharpe 0.71 against a required > 0.95, short by
  0.24, with N = 12 effective trials" is.
* **It carries the trial count and the cross-trial Sharpe variance.** Those two
  numbers are what make a Sharpe interpretable. Without them a reader cannot
  reconstruct the deflation, and a report nobody can check is decoration.
* **It serialises deterministically.** The same report object always produces
  byte-identical JSON, and reloading that JSON reproduces the object, so a stored
  report can be re-hashed and compared years later.

Non-finite floats are encoded as ``{"$float": "nan" | "inf" | "-inf"}`` rather
than emitted as bare ``NaN`` literals: the output is then strict JSON that a
frontend, a database or another language can read, and it still round-trips
exactly.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from fiboki.core.enums import Provenance, StrategyLifecycle
from fiboki.validation.gates import GateResult, GateSet, GateStatus

__all__ = [
    "REPORT_VERSION",
    "BindingConstraint",
    "RungOutcome",
    "RungResult",
    "ValidationReport",
    "Verdict",
    "code_version",
]

REPORT_VERSION = "2.0.0"


# --------------------------------------------------------------------------
# Code version
# --------------------------------------------------------------------------


@lru_cache(maxsize=4)
def code_version(repo_root: str | None = None) -> str:
    """The git sha this report was produced by.

    Resolution order, most explicit first:

    1. ``FIBOKI_CODE_VERSION`` -- what a container image sets at build time,
       where there is no ``.git`` directory to ask.
    2. ``git rev-parse HEAD``, suffixed ``+dirty`` when the working tree differs
       from HEAD. A dirty tree is recorded, never hidden: a result produced from
       uncommitted code is not reproducible and the report must say so.
    3. ``"unknown"``. Never a lie, and never an exception -- a report that cannot
       be written because git is missing helps nobody.
    """
    env = os.environ.get("FIBOKI_CODE_VERSION")
    if env:
        return env.strip()
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if sha.returncode != 0 or not sha.stdout.strip():
            return "unknown"
        digest = sha.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            return f"{digest}+dirty"
        return digest
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env dependent
        return "unknown"


# --------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------

_FLOAT_TAG = "$float"


def canonicalise(node: Any) -> Any:
    """JSON-safe, order-stable, non-finite-safe view of arbitrary report data."""
    if isinstance(node, Mapping):
        return {str(k): canonicalise(node[k]) for k in sorted(node, key=str)}
    if isinstance(node, str | bool) or node is None:
        return node
    if isinstance(node, Enum):
        return node.value
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        if math.isnan(node):
            return {_FLOAT_TAG: "nan"}
        if math.isinf(node):
            return {_FLOAT_TAG: "inf" if node > 0 else "-inf"}
        return float(node)
    if isinstance(node, Sequence):
        return [canonicalise(v) for v in node]
    if isinstance(node, set | frozenset):
        return [canonicalise(v) for v in sorted(node, key=str)]
    if hasattr(node, "to_dict"):
        return canonicalise(node.to_dict())
    if hasattr(node, "isoformat"):
        return node.isoformat()
    if hasattr(node, "tolist"):  # numpy scalars and arrays
        return canonicalise(node.tolist())
    return str(node)


def decanonicalise(node: Any) -> Any:
    """Inverse of :func:`canonicalise` for the parts that are not lossy."""
    if isinstance(node, Mapping):
        if set(node) == {_FLOAT_TAG}:
            return {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}[node[_FLOAT_TAG]]
        return {k: decanonicalise(v) for k, v in node.items()}
    if isinstance(node, list):
        return [decanonicalise(v) for v in node]
    return node


def dumps(payload: Any, *, indent: int | None = 2) -> str:
    return json.dumps(
        canonicalise(payload),
        sort_keys=True,
        indent=indent,
        separators=(",", ": ") if indent else (",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------
# Rungs
# --------------------------------------------------------------------------


class RungOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_REACHED = "not_reached"
    """The ladder stopped earlier. Recorded, never treated as a pass."""
    ERROR = "error"
    """The rung could not be computed. Blocks promotion, like a failure."""

    @property
    def blocks_promotion(self) -> bool:
        return self is not RungOutcome.PASS


@dataclass(frozen=True, slots=True)
class RungResult:
    """What one rung of the ladder concluded, and why."""

    index: int
    name: str
    outcome: RungOutcome
    reason: str = ""
    """Why it rejected. Empty on a pass. A rejection with no reason is a bug."""
    metrics: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance = Provenance.BACKTEST
    is_evidence: bool = True
    """False for the in-sample screen, which is a filter and not evidence."""

    def __post_init__(self) -> None:
        if self.outcome in (RungOutcome.FAIL, RungOutcome.ERROR) and not self.reason:
            raise ValueError(
                f"rung {self.index} ({self.name}) rejected without a reason; "
                "a rejection nobody can read is not a research result"
            )

    @property
    def passed(self) -> bool:
        return self.outcome is RungOutcome.PASS

    @property
    def label(self) -> str:
        return f"RUNG {self.index} {self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "name": self.name,
            "label": self.label,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "metrics": canonicalise(self.metrics),
            "provenance": self.provenance.value,
            "is_evidence": bool(self.is_evidence),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RungResult:
        return cls(
            index=int(raw["index"]),
            name=str(raw["name"]),
            outcome=RungOutcome(raw["outcome"]),
            reason=str(raw.get("reason", "")),
            metrics=decanonicalise(dict(raw.get("metrics", {}))),
            provenance=Provenance(raw.get("provenance", Provenance.BACKTEST.value)),
            is_evidence=bool(raw.get("is_evidence", True)),
        )


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------


class Verdict(str, Enum):
    PROMOTE = "promote"
    REJECT = "reject"
    INCOMPLETE = "incomplete"
    """The ladder did not finish. Never promotable; distinct from a rejection."""

    @property
    def promotable(self) -> bool:
        return self is Verdict.PROMOTE


@dataclass(frozen=True, slots=True)
class BindingConstraint:
    """The ONE thing that stopped this candidate, and by how much."""

    kind: str
    """``"gate"``, ``"rung"`` or ``"none"``."""
    name: str
    rung_index: int
    reason: str
    observed: float | None = None
    required: float | None = None
    comparison: str = ""
    shortfall: float | None = None

    @classmethod
    def none(cls) -> BindingConstraint:
        return cls(kind="none", name="", rung_index=-1, reason="no gate or rung blocked")

    @classmethod
    def from_gate(cls, result: GateResult, *, reason: str = "") -> BindingConstraint:
        return cls(
            kind="gate",
            name=result.gate.name,
            rung_index=result.gate.rung,
            reason=reason or result.describe(),
            observed=result.value,
            required=float(result.gate.threshold),
            comparison=result.gate.comparison.value,
            shortfall=result.shortfall,
        )

    @classmethod
    def from_rung(cls, rung: RungResult) -> BindingConstraint:
        return cls(
            kind="rung",
            name=rung.label,
            rung_index=rung.index,
            reason=rung.reason,
        )

    def describe(self) -> str:
        if self.kind == "none":
            return "none"
        if self.observed is None or self.required is None:
            return f"{self.name}: {self.reason}"
        short = "" if self.shortfall is None else f", short by {self.shortfall:.4g}"
        return (
            f"{self.name}: observed {self.observed:.4g}, required "
            f"{self.comparison} {self.required:g}{short}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "rung_index": int(self.rung_index),
            "reason": self.reason,
            "observed": self.observed,
            "required": self.required,
            "comparison": self.comparison,
            "shortfall": self.shortfall,
            "description": self.describe(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BindingConstraint:
        return cls(
            kind=str(raw["kind"]),
            name=str(raw["name"]),
            rung_index=int(raw["rung_index"]),
            reason=str(raw.get("reason", "")),
            observed=_opt_float(raw.get("observed")),
            required=_opt_float(raw.get("required")),
            comparison=str(raw.get("comparison", "")),
            shortfall=_opt_float(raw.get("shortfall")),
        )


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return float(decanonicalise(value))
    return float(value)


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Everything needed to believe -- or refuse to believe -- one candidate."""

    # -- identity ---------------------------------------------------------
    strategy_id: str
    strategy_content_hash: str
    dataset_version_id: str
    code_version: str

    # -- assumptions ------------------------------------------------------
    engine_config: dict[str, Any] = field(default_factory=dict)
    broker_profile: dict[str, Any] = field(default_factory=dict)
    ladder_config: dict[str, Any] = field(default_factory=dict)
    windows: dict[str, Any] = field(default_factory=dict)

    # -- thresholds in force ----------------------------------------------
    gate_set_version: str = ""
    gate_set_fingerprint: str = ""
    gate_results: tuple[GateResult, ...] = ()

    # -- the ladder -------------------------------------------------------
    rungs: tuple[RungResult, ...] = ()

    # -- deflation inputs, recorded so a reader can redo the arithmetic ---
    trial_count_n: int = 0
    """EFFECTIVE number of independent trials used to deflate."""
    raw_trial_count: int = 0
    cross_trial_sharpe_variance: float = 0.0
    dsr_variance_source: str = ""
    """Which distribution supplied the DSR variance term."""

    # -- labelling --------------------------------------------------------
    provenance_labels: dict[str, str] = field(default_factory=dict)
    """metric name -> :class:`~fiboki.core.enums.Provenance` value."""

    # -- verdict ----------------------------------------------------------
    verdict: Verdict = Verdict.INCOMPLETE
    binding_constraint: BindingConstraint = field(default_factory=BindingConstraint.none)
    lifecycle_recommendation: StrategyLifecycle = StrategyLifecycle.RESEARCH
    notes: str = ""

    # -- bookkeeping ------------------------------------------------------
    report_version: str = REPORT_VERSION
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    experiment_id: str = ""
    actor: str = ""

    # ------------------------------------------------------------- derived

    @property
    def passed(self) -> bool:
        return self.verdict.promotable

    def rung(self, index: int) -> RungResult | None:
        for r in self.rungs:
            if r.index == index:
                return r
        return None

    def gate(self, name: str) -> GateResult | None:
        for g in self.gate_results:
            if g.gate.name == name:
                return g
        return None

    def failed_gates(self) -> tuple[GateResult, ...]:
        return tuple(g for g in self.gate_results if g.status.blocks_promotion)

    def first_failing_rung(self) -> RungResult | None:
        for r in sorted(self.rungs, key=lambda x: x.index):
            if r.outcome.blocks_promotion and r.outcome is not RungOutcome.NOT_REACHED:
                return r
        return None

    def gate_values(self) -> dict[str, float | None]:
        return {g.gate.name: g.value for g in self.gate_results}

    def summary(self) -> str:
        """One operator-readable line. Rejections name the binding constraint."""
        head = (
            f"{self.strategy_id} [{self.strategy_content_hash[:12]}] "
            f"on {self.dataset_version_id}: {self.verdict.value.upper()}"
        )
        if self.verdict.promotable:
            return f"{head} (gates {self.gate_set_version})"
        return f"{head} -- binding constraint: {self.binding_constraint.describe()}"

    # --------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "created_at": self.created_at,
            "experiment_id": self.experiment_id,
            "actor": self.actor,
            "strategy_id": self.strategy_id,
            "strategy_content_hash": self.strategy_content_hash,
            "dataset_version_id": self.dataset_version_id,
            "code_version": self.code_version,
            "engine_config": canonicalise(self.engine_config),
            "broker_profile": canonicalise(self.broker_profile),
            "ladder_config": canonicalise(self.ladder_config),
            "windows": canonicalise(self.windows),
            "gate_set_version": self.gate_set_version,
            "gate_set_fingerprint": self.gate_set_fingerprint,
            "gate_results": [g.to_dict() for g in self.gate_results],
            "rungs": [r.to_dict() for r in self.rungs],
            "trial_count_n": int(self.trial_count_n),
            "raw_trial_count": int(self.raw_trial_count),
            "cross_trial_sharpe_variance": canonicalise(self.cross_trial_sharpe_variance),
            "dsr_variance_source": self.dsr_variance_source,
            "provenance_labels": dict(sorted(self.provenance_labels.items())),
            "verdict": self.verdict.value,
            "binding_constraint": self.binding_constraint.to_dict(),
            "lifecycle_recommendation": self.lifecycle_recommendation.value,
            "notes": self.notes,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return dumps(self.to_dict(), indent=indent)

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON. Two identical reports hash identically."""
        return hashlib.sha256(self.to_json(indent=None).encode("utf-8")).hexdigest()

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> ValidationReport:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_json(cls, blob: str) -> ValidationReport:
        return cls.from_dict(json.loads(blob))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ValidationReport:
        return cls(
            strategy_id=str(raw["strategy_id"]),
            strategy_content_hash=str(raw["strategy_content_hash"]),
            dataset_version_id=str(raw["dataset_version_id"]),
            code_version=str(raw["code_version"]),
            engine_config=decanonicalise(dict(raw.get("engine_config", {}))),
            broker_profile=decanonicalise(dict(raw.get("broker_profile", {}))),
            ladder_config=decanonicalise(dict(raw.get("ladder_config", {}))),
            windows=decanonicalise(dict(raw.get("windows", {}))),
            gate_set_version=str(raw.get("gate_set_version", "")),
            gate_set_fingerprint=str(raw.get("gate_set_fingerprint", "")),
            gate_results=tuple(
                _gate_result_from_dict(g) for g in raw.get("gate_results", [])
            ),
            rungs=tuple(RungResult.from_dict(r) for r in raw.get("rungs", [])),
            trial_count_n=int(raw.get("trial_count_n", 0)),
            raw_trial_count=int(raw.get("raw_trial_count", 0)),
            cross_trial_sharpe_variance=float(
                decanonicalise(raw.get("cross_trial_sharpe_variance", 0.0))
            ),
            dsr_variance_source=str(raw.get("dsr_variance_source", "")),
            provenance_labels={
                str(k): str(v) for k, v in dict(raw.get("provenance_labels", {})).items()
            },
            verdict=Verdict(raw.get("verdict", Verdict.INCOMPLETE.value)),
            binding_constraint=BindingConstraint.from_dict(
                raw.get("binding_constraint", BindingConstraint.none().to_dict())
            ),
            lifecycle_recommendation=StrategyLifecycle(
                raw.get("lifecycle_recommendation", StrategyLifecycle.RESEARCH.value)
            ),
            notes=str(raw.get("notes", "")),
            report_version=str(raw.get("report_version", REPORT_VERSION)),
            created_at=str(raw.get("created_at", "")),
            experiment_id=str(raw.get("experiment_id", "")),
            actor=str(raw.get("actor", "")),
        )

    # ------------------------------------------------------------- building

    @classmethod
    def build(
        cls,
        *,
        strategy_id: str,
        strategy_content_hash: str,
        dataset_version_id: str,
        gate_set: GateSet,
        gate_results: Sequence[GateResult],
        rungs: Sequence[RungResult],
        engine_config: Mapping[str, Any] | None = None,
        broker_profile: Mapping[str, Any] | None = None,
        ladder_config: Mapping[str, Any] | None = None,
        windows: Mapping[str, Any] | None = None,
        trial_count_n: int = 0,
        raw_trial_count: int = 0,
        cross_trial_sharpe_variance: float = 0.0,
        dsr_variance_source: str = "",
        provenance_labels: Mapping[str, str] | None = None,
        experiment_id: str = "",
        actor: str = "",
        notes: str = "",
        code_version_override: str | None = None,
    ) -> ValidationReport:
        """Assemble a report and DERIVE the verdict, rather than being told it.

        The verdict is a function of the rungs and the gates. It is derived here
        so no caller can publish a report whose verdict disagrees with its own
        evidence.
        """
        rungs_t = tuple(rungs)
        gates_t = tuple(gate_results)
        failing_rung = next(
            (
                r
                for r in sorted(rungs_t, key=lambda x: x.index)
                if r.outcome in (RungOutcome.FAIL, RungOutcome.ERROR)
            ),
            None,
        )
        blocking_gate = GateSet.binding_constraint(gates_t)
        not_reached = [r for r in rungs_t if r.outcome is RungOutcome.NOT_REACHED]

        if failing_rung is not None:
            verdict = Verdict.REJECT
            # Prefer the gate the failing rung produced: it carries the numbers.
            gate_for_rung = next(
                (
                    g
                    for g in gates_t
                    if g.gate.rung == failing_rung.index and g.status.blocks_promotion
                ),
                None,
            )
            binding = (
                BindingConstraint.from_gate(gate_for_rung, reason=failing_rung.reason)
                if gate_for_rung is not None
                else BindingConstraint.from_rung(failing_rung)
            )
        elif blocking_gate is not None:
            verdict = (
                Verdict.INCOMPLETE
                if blocking_gate.status is GateStatus.NOT_EVALUATED
                else Verdict.REJECT
            )
            binding = BindingConstraint.from_gate(blocking_gate)
        elif not_reached:
            verdict = Verdict.INCOMPLETE
            binding = BindingConstraint(
                kind="rung",
                name=min(not_reached, key=lambda r: r.index).label,
                rung_index=min(r.index for r in not_reached),
                reason="the ladder did not run every rung",
            )
        else:
            verdict = Verdict.PROMOTE
            binding = BindingConstraint.none()

        lifecycle = (
            StrategyLifecycle.CANDIDATE if verdict.promotable else StrategyLifecycle.RESEARCH
        )
        return cls(
            strategy_id=strategy_id,
            strategy_content_hash=strategy_content_hash,
            dataset_version_id=dataset_version_id,
            code_version=code_version_override or code_version(),
            engine_config=dict(engine_config or {}),
            broker_profile=dict(broker_profile or {}),
            ladder_config=dict(ladder_config or {}),
            windows=dict(windows or {}),
            gate_set_version=gate_set.version,
            gate_set_fingerprint=gate_set.fingerprint(),
            gate_results=gates_t,
            rungs=rungs_t,
            trial_count_n=int(trial_count_n),
            raw_trial_count=int(raw_trial_count),
            cross_trial_sharpe_variance=float(cross_trial_sharpe_variance),
            dsr_variance_source=dsr_variance_source,
            provenance_labels=dict(provenance_labels or {}),
            verdict=verdict,
            binding_constraint=binding,
            lifecycle_recommendation=lifecycle,
            notes=notes,
            experiment_id=experiment_id,
            actor=actor,
        )


def _gate_result_from_dict(raw: Mapping[str, Any]) -> GateResult:
    from fiboki.validation.gates import Comparison, Gate

    g = raw["gate"]
    gate = Gate(
        name=str(g["name"]),
        metric=str(g["metric"]),
        comparison=Comparison(g["comparison"]),
        threshold=float(g["threshold"]),
        rung=int(g["rung"]),
        rationale=str(g.get("rationale", "")),
        boolean=bool(g.get("boolean", False)),
        units=str(g.get("units", "")),
    )
    return GateResult(
        gate=gate,
        value=_opt_float(raw.get("value")),
        status=GateStatus(raw["status"]),
    )
