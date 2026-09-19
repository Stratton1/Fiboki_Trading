"""The campaign report: what was tried, what was not, and what it proves.

A leaderboard is not a research result. V1 produced one -- 23,040 cells ranked
by in-sample Sharpe -- and it was worthless not because the numbers were wrong
but because the document did not say how many things had been tried, what had
been skipped, what had been rejected and why, or what the surviving numbers
would have had to beat to mean anything.

This report says all of that, in one artefact, and it round-trips: a campaign
report can be reloaded, re-read and compared with a later one rather than being
a printout.

Four things it will not let a reader miss
-----------------------------------------
**The true trial count.** Not the size of one strategy's parameter sweep -- the
size of the WHOLE search, including the candidates skipped as rediscoveries and
the trials the ledger already held for this dataset. The deflated Sharpe is a
function of this number and of nothing else that a campaign controls.

**The deflation threshold it implied.** ``E[max SR]`` over a search of that
effective size: the Sharpe a false discovery would be EXPECTED to reach by luck
alone. Printed next to every candidate's own Sharpe, so "1.1 looks good" and
"the bar was 1.4" appear in the same table.

**What was skipped, and why.** A campaign that consults its memory and declines
to re-run a dead end has done work; if the skip is not in the report, the report
overstates the search AND understates the discipline.

**What a survivor would and would not demonstrate.** Written out, every time,
whether or not anything survives -- because the sentence is exactly as true when
the answer is "nothing did".
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fiboki.stats.sharpe import expected_max_sharpe

__all__ = [
    "CampaignReport",
    "CellResult",
    "SkippedCell",
    "deflation_threshold",
]


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class CellResult:
    """One candidate, on one instrument and timeframe, taken through the ladder."""

    key: str
    strategy_id: str
    strategy_content_hash: str
    instrument: str
    timeframe: str
    origin: str
    """``seed`` or the mutation operator that produced this document."""
    hypothesis_id: str = ""
    parents: tuple[str, ...] = ()
    generation: int = 0
    rationale: str = ""

    n_trials: int = 0
    """Parameterisations of THIS candidate that the ladder swept."""
    external_trial_count: int = 0
    """Trials the campaign ran outside this candidate, handed to the ladder."""

    verdict: str = ""
    died_at_rung: str = ""
    reason: str = ""
    binding_constraint: str = ""
    gate_values: dict[str, float | None] = field(default_factory=dict)

    n_evaluations: int = 0
    engine_runs: int = 0
    n_trades: float | None = None
    deflated_sharpe_ratio: float | None = None
    selected_sharpe: float | None = None
    sr_variance: float | None = None
    """Dispersion of trial Sharpes measured at RUNG 5, or ``None`` when the
    candidate never got there."""
    cross_trial_sharpe_variance: float | None = None
    """The same dispersion measured at RUNG 1, which runs far more often. A
    candidate rejected at rung 0 or 2 still tells the campaign how much its
    trial Sharpes moved, and that is what the deflation THRESHOLD is computed
    from -- so the bar a survivor would have had to clear can be reported even
    in a campaign where nothing reached the rung that applies it."""
    n_trials_used_for_deflation: int = 0
    deflation_threshold: float | None = None
    """``E[max SR]`` for this candidate's true N and measured Sharpe dispersion."""

    experiment_id: str = ""
    report_path: str = ""
    dataset_version_id: str = ""
    error: str = ""

    @property
    def survived(self) -> bool:
        return self.verdict == "promote"

    def describe(self) -> str:
        head = f"{self.strategy_id} on {self.instrument} {self.timeframe}"
        if self.error:
            return f"{head}: ERROR {self.error}"
        where = f" at {self.died_at_rung}" if self.died_at_rung else ""
        return f"{head}: {self.verdict.upper()}{where} -- {self.binding_constraint or self.reason}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "strategy_id": self.strategy_id,
            "strategy_content_hash": self.strategy_content_hash,
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "origin": self.origin,
            "hypothesis_id": self.hypothesis_id,
            "parents": list(self.parents),
            "generation": int(self.generation),
            "rationale": self.rationale,
            "n_trials": int(self.n_trials),
            "external_trial_count": int(self.external_trial_count),
            "verdict": self.verdict,
            "died_at_rung": self.died_at_rung,
            "reason": self.reason,
            "binding_constraint": self.binding_constraint,
            "gate_values": dict(self.gate_values),
            "n_evaluations": int(self.n_evaluations),
            "engine_runs": int(self.engine_runs),
            "n_trades": _opt_float(self.n_trades),
            "deflated_sharpe_ratio": _opt_float(self.deflated_sharpe_ratio),
            "selected_sharpe": _opt_float(self.selected_sharpe),
            "sr_variance": _opt_float(self.sr_variance),
            "cross_trial_sharpe_variance": _opt_float(self.cross_trial_sharpe_variance),
            "n_trials_used_for_deflation": int(self.n_trials_used_for_deflation),
            "deflation_threshold": _opt_float(self.deflation_threshold),
            "experiment_id": self.experiment_id,
            "report_path": self.report_path,
            "dataset_version_id": self.dataset_version_id,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CellResult:
        return cls(
            key=str(raw["key"]),
            strategy_id=str(raw.get("strategy_id", "")),
            strategy_content_hash=str(raw.get("strategy_content_hash", "")),
            instrument=str(raw.get("instrument", "")),
            timeframe=str(raw.get("timeframe", "")),
            origin=str(raw.get("origin", "")),
            hypothesis_id=str(raw.get("hypothesis_id", "")),
            parents=tuple(str(p) for p in raw.get("parents", ())),
            generation=int(raw.get("generation", 0)),
            rationale=str(raw.get("rationale", "")),
            n_trials=int(raw.get("n_trials", 0)),
            external_trial_count=int(raw.get("external_trial_count", 0)),
            verdict=str(raw.get("verdict", "")),
            died_at_rung=str(raw.get("died_at_rung", "")),
            reason=str(raw.get("reason", "")),
            binding_constraint=str(raw.get("binding_constraint", "")),
            gate_values={
                str(k): _opt_float(v) for k, v in (raw.get("gate_values") or {}).items()
            },
            n_evaluations=int(raw.get("n_evaluations", 0)),
            engine_runs=int(raw.get("engine_runs", 0)),
            n_trades=_opt_float(raw.get("n_trades")),
            deflated_sharpe_ratio=_opt_float(raw.get("deflated_sharpe_ratio")),
            selected_sharpe=_opt_float(raw.get("selected_sharpe")),
            sr_variance=_opt_float(raw.get("sr_variance")),
            cross_trial_sharpe_variance=_opt_float(raw.get("cross_trial_sharpe_variance")),
            n_trials_used_for_deflation=int(raw.get("n_trials_used_for_deflation", 0)),
            deflation_threshold=_opt_float(raw.get("deflation_threshold")),
            experiment_id=str(raw.get("experiment_id", "")),
            report_path=str(raw.get("report_path", "")),
            dataset_version_id=str(raw.get("dataset_version_id", "")),
            error=str(raw.get("error", "")),
        )


@dataclass(frozen=True, slots=True)
class SkippedCell:
    """Something the campaign proposed and did not run, with the reason."""

    key: str
    strategy_id: str
    kind: str
    """``non_novel`` | ``mutation_rejected`` | ``over_budget`` | ``no_data`` |
    ``out_of_universe``."""
    reason: str = ""
    instrument: str = ""
    timeframe: str = ""
    origin: str = ""
    narrative: str = ""
    prior_experiment_ids: tuple[str, ...] = ()
    experiment_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        where = f" on {self.instrument} {self.timeframe}" if self.instrument else ""
        return f"{self.strategy_id}{where}: {self.kind} -- {self.narrative or self.reason}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "strategy_id": self.strategy_id,
            "kind": self.kind,
            "reason": self.reason,
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "origin": self.origin,
            "narrative": self.narrative,
            "prior_experiment_ids": list(self.prior_experiment_ids),
            "experiment_id": self.experiment_id,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SkippedCell:
        return cls(
            key=str(raw["key"]),
            strategy_id=str(raw.get("strategy_id", "")),
            kind=str(raw.get("kind", "")),
            reason=str(raw.get("reason", "")),
            instrument=str(raw.get("instrument", "")),
            timeframe=str(raw.get("timeframe", "")),
            origin=str(raw.get("origin", "")),
            narrative=str(raw.get("narrative", "")),
            prior_experiment_ids=tuple(str(x) for x in raw.get("prior_experiment_ids", ())),
            experiment_id=str(raw.get("experiment_id", "")),
            detail=dict(raw.get("detail", {})),
        )


def deflation_threshold(n_trials: int, sr_variance: float | None) -> float | None:
    """``E[max SR]`` for a search of ``n_trials`` independent trials.

    The number a Sharpe has to BEAT before it is evidence of anything. Returns
    ``None`` rather than a flattering zero when the dispersion of the trial
    Sharpes is unknown or degenerate: a threshold computed from a variance
    nobody measured is a decoration.
    """
    if sr_variance is None or not (sr_variance > 0.0) or n_trials < 2:
        return None
    return float(expected_max_sharpe(int(n_trials), float(sr_variance)))


@dataclass(frozen=True, slots=True)
class CampaignReport:
    """Everything one campaign did, and what may honestly be said about it."""

    campaign_id: str
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    actor: str = ""
    notes: str = ""

    spec: dict[str, Any] = field(default_factory=dict)
    gate_set_version: str = ""
    gate_set_fingerprint: str = ""
    dataset_versions: dict[str, str] = field(default_factory=dict)
    hypotheses: tuple[dict[str, Any], ...] = ()

    attempted: tuple[CellResult, ...] = ()
    skipped: tuple[SkippedCell, ...] = ()
    rejected_mutations: tuple[dict[str, Any], ...] = ()

    planned_trial_count: int = 0
    prior_trial_count: int = 0
    true_trial_count: int = 0
    n_engine_evaluations: int = 0
    """Backtests the engine actually RAN. Zero on a fully cached re-run, which
    is correct and is why the ladder-evaluation count is reported beside it."""
    n_ladder_evaluations: int = 0
    """Evaluations the ladder ASKED for, cached or not. This is the size of the
    computation the campaign describes, independent of what a cache saved."""

    campaign_deflation_threshold: float | None = None
    deflation_variance_used: float | None = None
    deflation_note: str = ""

    holdout: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)

    report_version: str = "1.0.0"

    # ------------------------------------------------------------- derived

    @property
    def survivors(self) -> tuple[CellResult, ...]:
        return tuple(c for c in self.attempted if c.survived)

    @property
    def errors(self) -> tuple[CellResult, ...]:
        return tuple(c for c in self.attempted if c.error)

    def rejections_by_rung(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cell in self.attempted:
            if cell.survived or cell.error:
                continue
            key = cell.died_at_rung or "unrecorded"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def skips_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for skip in self.skipped:
            counts[skip.kind] = counts.get(skip.kind, 0) + 1
        return dict(sorted(counts.items()))

    def honest_statement(self) -> str:
        """What the result does and does not demonstrate. Always printed."""
        cells = len(self.attempted)
        instruments = sorted({c.instrument for c in self.attempted if c.instrument})
        timeframes = sorted({c.timeframe for c in self.attempted if c.timeframe})
        scope = (
            f"{len(instruments)} instrument(s) ({', '.join(instruments) or 'none'}) on "
            f"{len(timeframes)} timeframe(s) ({', '.join(timeframes) or 'none'})"
        )
        base = (
            f"This campaign carried {cells} candidate cell(s) through the validation "
            f"ladder over {scope}, against gate set {self.gate_set_version or 'unversioned'}. "
            f"The true trial count for the whole search is {self.true_trial_count} "
            f"({self.planned_trial_count} planned in this campaign plus "
            f"{self.prior_trial_count} already in the ledger for the same dataset "
            "version(s)), and that is the number the deflation used -- not the size "
            "of any one strategy's own parameter sweep."
        )
        if not self.survivors:
            return (
                base
                + " NOTHING SURVIVED. That is the expected outcome and it is a real "
                "result: it says these rule families, on this data, under these "
                "costs, do not clear a bar set for a search of this size. It does "
                "NOT say the underlying effects do not exist, that another "
                "instrument would behave the same way, or that a different cost "
                "model would give the same answer."
            )
        names = ", ".join(f"{c.strategy_id} ({c.instrument} {c.timeframe})" for c in self.survivors)
        return (
            base
            + f" {len(self.survivors)} candidate(s) cleared every gate: {names}. "
            "What that DEMONSTRATES is narrow and should be stated narrowly: each "
            "one cleared a fixed, versioned set of thresholds on one historical "
            "window of one instrument on one timeframe, under one cost model, "
            "with one holdout look already spent. It is NOT evidence of an edge. "
            "A single instrument on a single timeframe over one historical window "
            "cannot distinguish an edge from the best of a search this size, "
            "however the deflation came out; the deflation corrects for the "
            "trials that were COUNTED, and no correction exists for the choices "
            "made in designing the seeds, the costs and the window. Before any of "
            "this is treated as a finding it needs to hold on instruments and "
            "periods that had no part in producing it."
        )

    def summary(self) -> str:
        return (
            f"campaign {self.campaign_id}: {len(self.attempted)} evaluated, "
            f"{len(self.skipped)} skipped, {len(self.survivors)} survived, "
            f"true trial count {self.true_trial_count}"
        )

    # ------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "campaign_id": self.campaign_id,
            "created_at": self.created_at,
            "actor": self.actor,
            "notes": self.notes,
            "spec": dict(self.spec),
            "gate_set_version": self.gate_set_version,
            "gate_set_fingerprint": self.gate_set_fingerprint,
            "dataset_versions": dict(self.dataset_versions),
            "hypotheses": [dict(h) for h in self.hypotheses],
            "attempted": [c.to_dict() for c in self.attempted],
            "skipped": [s.to_dict() for s in self.skipped],
            "rejected_mutations": [dict(m) for m in self.rejected_mutations],
            "planned_trial_count": int(self.planned_trial_count),
            "prior_trial_count": int(self.prior_trial_count),
            "true_trial_count": int(self.true_trial_count),
            "n_engine_evaluations": int(self.n_engine_evaluations),
            "n_ladder_evaluations": int(self.n_ladder_evaluations),
            "campaign_deflation_threshold": _opt_float(self.campaign_deflation_threshold),
            "deflation_variance_used": _opt_float(self.deflation_variance_used),
            "deflation_note": self.deflation_note,
            "holdout": dict(self.holdout),
            "lineage": dict(self.lineage),
            # Derived, written out so a reader of the JSON does not have to
            # recompute what the campaign already concluded.
            "survivors": [c.key for c in self.survivors],
            "rejections_by_rung": self.rejections_by_rung(),
            "skips_by_kind": self.skips_by_kind(),
            "honest_statement": self.honest_statement(),
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CampaignReport:
        return cls(
            campaign_id=str(raw["campaign_id"]),
            created_at=str(raw.get("created_at", "")),
            actor=str(raw.get("actor", "")),
            notes=str(raw.get("notes", "")),
            spec=dict(raw.get("spec", {})),
            gate_set_version=str(raw.get("gate_set_version", "")),
            gate_set_fingerprint=str(raw.get("gate_set_fingerprint", "")),
            dataset_versions={
                str(k): str(v) for k, v in (raw.get("dataset_versions") or {}).items()
            },
            hypotheses=tuple(dict(h) for h in raw.get("hypotheses", ())),
            attempted=tuple(CellResult.from_dict(c) for c in raw.get("attempted", ())),
            skipped=tuple(SkippedCell.from_dict(s) for s in raw.get("skipped", ())),
            rejected_mutations=tuple(dict(m) for m in raw.get("rejected_mutations", ())),
            planned_trial_count=int(raw.get("planned_trial_count", 0)),
            prior_trial_count=int(raw.get("prior_trial_count", 0)),
            true_trial_count=int(raw.get("true_trial_count", 0)),
            n_engine_evaluations=int(raw.get("n_engine_evaluations", 0)),
            n_ladder_evaluations=int(raw.get("n_ladder_evaluations", 0)),
            campaign_deflation_threshold=_opt_float(raw.get("campaign_deflation_threshold")),
            deflation_variance_used=_opt_float(raw.get("deflation_variance_used")),
            deflation_note=str(raw.get("deflation_note", "")),
            holdout=dict(raw.get("holdout", {})),
            lineage=dict(raw.get("lineage", {})),
            report_version=str(raw.get("report_version", "1.0.0")),
        )

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False, default=str)

    @classmethod
    def from_json(cls, blob: str) -> CampaignReport:
        return cls.from_dict(json.loads(blob))

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> CampaignReport:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    # ------------------------------------------------------------ markdown

    def markdown(self) -> str:
        """An operator-readable rendering. The same facts, in a readable order."""
        lines: list[str] = [
            f"# Campaign {self.campaign_id}",
            "",
            f"- actor: `{self.actor}`",
            f"- created: {self.created_at}",
            f"- gate set: `{self.gate_set_version}` (`{self.gate_set_fingerprint[:16]}`)",
            "- datasets: "
            + (", ".join(f"{k} -> `{v}`" for k, v in sorted(self.dataset_versions.items())) or "none"),
            "",
            "## Trial accounting",
            "",
            f"- planned in this campaign: **{self.planned_trial_count}**",
            f"- already in the ledger for the same dataset version(s): **{self.prior_trial_count}**",
            f"- **true trial count: {self.true_trial_count}**",
            f"- ladder evaluations requested: {self.n_ladder_evaluations}",
            f"- engine backtests actually run: {self.n_engine_evaluations} "
            "(lower when the deterministic evaluation cache was warm)",
            "",
            self.deflation_note or "",
            "",
            "## What was tried",
            "",
            "| strategy | cell | origin | n trials | external N | verdict | died at | deflated SR | threshold |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for cell in self.attempted:
            lines.append(
                "| `{sid}` | {inst} {tf} | {origin} | {n} | {ext} | {verdict} | {died} | {dsr} | {thr} |".format(
                    sid=cell.strategy_id,
                    inst=cell.instrument,
                    tf=cell.timeframe,
                    origin=cell.origin,
                    n=cell.n_trials,
                    ext=cell.external_trial_count,
                    verdict=cell.error or cell.verdict,
                    died=cell.died_at_rung or "-",
                    dsr=_fmt(cell.deflated_sharpe_ratio),
                    thr=_fmt(cell.deflation_threshold),
                )
            )
        lines += ["", "### Why each one stopped", ""]
        lines += [f"- {c.describe()}" for c in self.attempted] or ["- nothing was evaluated"]

        lines += ["", "## What was skipped", ""]
        if self.skipped:
            lines += [f"- {s.describe()}" for s in self.skipped]
        else:
            lines += ["- nothing was skipped"]

        lines += ["", "## Mutations refused before any compute", ""]
        if self.rejected_mutations:
            lines += [
                f"- `{m.get('operator')}` on {', '.join(m.get('parent_ids', []))}: "
                f"{m.get('rejection')}"
                for m in self.rejected_mutations
            ]
        else:
            lines += ["- none"]

        lines += ["", "## Rejections by rung", ""]
        for rung, count in self.rejections_by_rung().items():
            lines.append(f"- {rung}: {count}")
        if not self.rejections_by_rung():
            lines.append("- none")

        lines += ["", "## Holdout", "", f"```\n{json.dumps(self.holdout, indent=2)}\n```"]
        lines += ["", "## What this does and does not demonstrate", "", self.honest_statement(), ""]
        return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.4g}"
