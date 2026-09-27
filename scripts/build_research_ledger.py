#!/usr/bin/env python
"""Load the real K1/K2 research history into the experiment ledger the API reads.

Why this script exists
----------------------
The Phase K campaigns wrote their rows into ``<out>/experiments.sqlite`` next to
their reports. Those SQLite files were produced on a container scratch path and
are NOT present on this host -- ``research/reports/campaign_k1_xauusd_h4/`` and
``research/reports/campaign_k2_multi_instrument/`` hold the JSON reports, the
markdown and the run logs, but no ledger. So the ledger the API serves
(``FIBOKI_EXPERIMENT_DB``) starts empty, and the Research section renders empty
tables while a real, large body of prior work sits on disk in JSON.

This script fills the ledger from what actually survives. It is explicit about
which rows are ORIGINAL artefacts and which are RECONSTRUCTED, because the two
are not the same thing and a reader must be able to tell them apart:

ORIGINAL (the artefact itself is on disk and is filed verbatim)
    * The five hypothesis documents in ``research/hypotheses/``.
    * The sixteen ``ValidationReport`` files in ``research/reports/xauusd_h4/``.
      Each is a complete report with its rungs, gate results, windows and
      fingerprints, and is attached to its ledger row in full.

RECONSTRUCTED (the row is rebuilt from the campaign report's summary of it)
    * Every K1/K2 campaign cell. The campaign JSON records, per cell, the
      strategy id and content hash, dataset version, hypothesis, origin
      operator, parents, generation, rationale, trial counts, verdict, the rung
      it died at, the binding constraint and the gate values -- and the id of
      the ledger row the campaign originally wrote. All of that is filed.
      What the campaign JSON does NOT contain, and what therefore cannot be
      reconstructed:
        - the mutant strategy DOCUMENTS, so a mutant cell's ``structure_hash``
          and structural tokens are empty rather than guessed. The mutant's real
          ``strategy_content_hash`` IS recorded, from the report.
        - the per-cell ``ValidationReport`` objects, so ``rejected_at_rung`` --
          which the ledger derives from an attached report -- stays empty. The
          rung is recorded verbatim in ``outputs['died_at_rung']`` and restated
          in the conclusion, which is the honest place for it.
      Every reconstructed row is tagged ``reconstructed`` and carries
      ``original_experiment_id``, ``reconstructed_from`` and
      ``reconstruction_note`` in its outputs.

Nothing here invents a number. A field that cannot be recovered is left empty
and named in ``reconstruction_note``.

Idempotent: a row already filed for the same campaign cell key, report hash or
hypothesis is not filed again, so re-running this script against a populated
ledger adds nothing. The ledger is append-only, so that matters.

Usage::

    python scripts/build_research_ledger.py --db var/experiments.sqlite
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:  # pragma: no cover - script bootstrap
    sys.path.insert(0, str(REPO / "src"))

from fiboki.discovery.hypothesis import (  # noqa: E402
    Evidence,
    HypothesisLedger,
    HypothesisStatus,
    load_hypotheses,
)
from fiboki.research.experiment import (  # noqa: E402
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    Outcome,
)
from fiboki.strategy.dsl import StrategyDocument  # noqa: E402

ACTOR = "script:build_research_ledger"
SEED_DIR = REPO / "research" / "strategies"

DEFAULT_CAMPAIGNS = (
    REPO / "research/reports/campaign_k1_xauusd_h4/campaign_k1_xauusd_h4.json",
    REPO / "research/reports/campaign_k2_multi_instrument/campaign_k2_multi_instrument_h4.json",
)

#: Recorded on every reconstructed row so a reader never has to guess.
RECONSTRUCTION_NOTE = (
    "RECONSTRUCTED from the campaign report JSON, not read from the campaign's "
    "own experiments.sqlite -- that file was written to a container scratch path "
    "and does not exist on this host. The cell's identifiers, trial counts, "
    "verdict, rung, binding constraint and gate values are verbatim from the "
    "report. The mutant strategy document and the per-cell ValidationReport were "
    "never written to the report, so structure_hash, structural tokens and "
    "rejected_at_rung are EMPTY rather than inferred; died_at_rung below carries "
    "the rung the report recorded."
)

_VERDICT_TO_OUTCOME = {
    "promote": Outcome.PROMOTED,
    "promoted": Outcome.PROMOTED,
    "survive": Outcome.PROMOTED,
    "survived": Outcome.PROMOTED,
    "reject": Outcome.REJECTED,
    "rejected": Outcome.REJECTED,
    "incomplete": Outcome.INCONCLUSIVE,
    "inconclusive": Outcome.INCONCLUSIVE,
    "error": Outcome.ERROR,
}


def _load_campaign_backfill() -> Any:
    """Reuse ``research/run_discovery_campaign.backfill`` rather than copy it.

    The prior-ladder backfill is real, reviewed logic that already exists; this
    script imports the module by path (``research/`` is a script directory, not
    an importable package) so there is one implementation, not two.
    """
    path = REPO / "research" / "run_discovery_campaign.py"
    spec = importlib.util.spec_from_file_location("_fiboki_run_discovery_campaign", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.backfill


def register_hypotheses(ledger: ExperimentLedger, directory: Path) -> int:
    """Pre-register the five ORIGINAL hypothesis documents. Filed verbatim."""
    store = HypothesisLedger(ledger)
    filed = 0
    for hypothesis in load_hypotheses(directory):
        if store.get(hypothesis.hypothesis_id) is not None:
            continue
        store.record(
            hypothesis,
            actor_name=ACTOR,
            actor_kind=ActorKind.HUMAN,
            reason=(
                "re-filing the pre-registered claim from its ORIGINAL document at "
                f"{directory.name}/{hypothesis.hypothesis_id}.json. The claim was "
                "pre-registered before the K1/K2 campaigns ran; the campaigns' own "
                "ledgers are not present on this host, so the original document is "
                "filed here and the campaign refutations below chain onto it."
            ),
        )
        filed += 1
    return filed


def _seed_document(strategy_id: str) -> StrategyDocument | None:
    """The seed document, and ONLY for an exact match.

    A mutant cell (``donchian_breakout_atr_m3ecf77ae``) is not its seed, so
    attaching the seed's document to it would put a wrong structural fingerprint
    in the ledger. Mutants get no document; their real content hash is recorded.
    """
    path = SEED_DIR / f"{strategy_id}.json"
    if not path.exists():
        return None
    return StrategyDocument.from_json(path.read_text(encoding="utf-8"))


def _campaign_summary_row(
    ledger: ExperimentLedger, report: dict[str, Any], source: Path
) -> str:
    """One row standing for the campaign as a whole. Returns its id."""
    campaign_id = str(report["campaign_id"])
    draft = ExperimentDraft(
        actor_kind=ActorKind.AGENT,
        actor_name=str(report.get("actor") or ACTOR),
        reason=(
            f"campaign {campaign_id}: {report.get('notes') or 'no notes recorded'}"
        ),
        dataset_version_id=", ".join(sorted(set((report.get("dataset_versions") or {}).values())))[:128],
        code_version=str(report.get("report_version") or ""),
        outcome=Outcome.REJECTED if not report.get("survivors") else Outcome.PROMOTED,
        conclusion=str(report.get("honest_statement") or report.get("summary") or ""),
        outputs={
            "kind": "campaign_summary",
            "campaign_id": campaign_id,
            "reconstructed_from": str(source.relative_to(REPO)),
            "reconstruction_note": RECONSTRUCTION_NOTE,
            "original_created_at": report.get("created_at"),
            "summary": report.get("summary"),
            "gate_set_version": report.get("gate_set_version"),
            "gate_set_fingerprint": report.get("gate_set_fingerprint"),
            "dataset_versions": report.get("dataset_versions"),
            "planned_trial_count": report.get("planned_trial_count"),
            "prior_trial_count": report.get("prior_trial_count"),
            "true_trial_count": report.get("true_trial_count"),
            "n_engine_evaluations": report.get("n_engine_evaluations"),
            "n_ladder_evaluations": report.get("n_ladder_evaluations"),
            "campaign_deflation_threshold": report.get("campaign_deflation_threshold"),
            "deflation_note": report.get("deflation_note"),
            "rejections_by_rung": report.get("rejections_by_rung"),
            "skips_by_kind": report.get("skips_by_kind"),
            "cells_attempted": len(report.get("attempted") or []),
            "cells_skipped": len(report.get("skipped") or []),
            "mutations_refused": len(report.get("rejected_mutations") or []),
            "survivors": report.get("survivors"),
            "holdout": report.get("holdout"),
            "lineage": report.get("lineage"),
            "spec": report.get("spec"),
            # The skipped cells are NOT filed as experiments: a cell that was
            # never evaluated is not an experiment, and filing 273 of them would
            # inflate the ledger's own count of work done. They are kept here in
            # full so the novelty and mutation refusals stay auditable.
            "skipped": report.get("skipped"),
            "rejected_mutations": report.get("rejected_mutations"),
        },
        tags=("campaign_summary", "reconstructed", f"campaign:{campaign_id}"),
    )
    return ledger.create(draft).id


def load_campaign(ledger: ExperimentLedger, source: Path) -> dict[str, int]:
    """File one campaign: a summary row, its cells, and its hypothesis verdicts."""
    report = json.loads(source.read_text(encoding="utf-8"))
    campaign_id = str(report["campaign_id"])

    already = {
        str(e.outputs.get("cell_key"))
        for e in ledger.list()
        if e.outputs.get("campaign_id") == campaign_id and e.outputs.get("cell_key")
    }
    summary_rows = [
        e
        for e in ledger.list()
        if e.outputs.get("campaign_id") == campaign_id
        and e.outputs.get("kind") == "campaign_summary"
    ]
    if summary_rows:
        parent_id = summary_rows[0].id
        counts = {"summary": 0}
    else:
        parent_id = _campaign_summary_row(ledger, report, source)
        counts = {"summary": 1}

    filed = 0
    for cell in report.get("attempted") or []:
        key = str(cell.get("key") or "")
        if key and key in already:
            continue
        verdict = str(cell.get("verdict") or "").lower()
        outcome = _VERDICT_TO_OUTCOME.get(verdict, Outcome.INCONCLUSIVE)
        if cell.get("error"):
            outcome = Outcome.ERROR
        rung = str(cell.get("died_at_rung") or "")
        strategy_id = str(cell.get("strategy_id") or "")
        conclusion = str(cell.get("binding_constraint") or cell.get("reason") or "")
        if rung:
            conclusion = f"died at {rung}: {conclusion}" if conclusion else f"died at {rung}"
        ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.AGENT,
                actor_name=str(report.get("actor") or ACTOR),
                reason=(
                    f"campaign {campaign_id} cell {key}: {cell.get('origin') or 'seed'} "
                    f"on {cell.get('instrument')} {cell.get('timeframe')} expressing "
                    f"{cell.get('hypothesis_id') or 'no hypothesis'} -- "
                    f"{cell.get('rationale') or 'no rationale recorded'}"
                ),
                hypothesis_id=str(cell.get("hypothesis_id") or ""),
                parent_experiment_id=parent_id,
                strategy_id=strategy_id,
                strategy_content_hash=str(cell.get("strategy_content_hash") or ""),
                strategy_document=_seed_document(strategy_id),
                dataset_version_id=str(cell.get("dataset_version_id") or ""),
                code_version=str(report.get("report_version") or ""),
                parameters={
                    "origin": cell.get("origin"),
                    "generation": cell.get("generation"),
                    "parents": cell.get("parents"),
                },
                engine_config={
                    "instrument": cell.get("instrument"),
                    "timeframe": cell.get("timeframe"),
                    "gate_set_version": report.get("gate_set_version"),
                    "gate_set_fingerprint": report.get("gate_set_fingerprint"),
                    "profile_name": (report.get("spec") or {}).get("profile_name"),
                    "account_ccy": (report.get("spec") or {}).get("account_ccy"),
                },
                outputs={
                    "kind": "campaign_cell",
                    "campaign_id": campaign_id,
                    "cell_key": key,
                    "original_experiment_id": cell.get("experiment_id"),
                    "reconstructed_from": str(source.relative_to(REPO)),
                    "reconstruction_note": RECONSTRUCTION_NOTE,
                    "verdict": cell.get("verdict"),
                    "died_at_rung": rung,
                    "reason": cell.get("reason"),
                    "binding_constraint": cell.get("binding_constraint"),
                    "gate_values": cell.get("gate_values"),
                    "n_trials": cell.get("n_trials"),
                    "external_trial_count": cell.get("external_trial_count"),
                    "n_trials_used_for_deflation": cell.get("n_trials_used_for_deflation"),
                    "n_evaluations": cell.get("n_evaluations"),
                    "engine_runs": cell.get("engine_runs"),
                    "n_trades": cell.get("n_trades"),
                    "deflated_sharpe_ratio": cell.get("deflated_sharpe_ratio"),
                    "selected_sharpe": cell.get("selected_sharpe"),
                    "deflation_threshold": cell.get("deflation_threshold"),
                    "cross_trial_sharpe_variance": cell.get("cross_trial_sharpe_variance"),
                    "error": cell.get("error"),
                },
                outcome=outcome,
                conclusion=conclusion,
                rejection_reason=str(cell.get("reason") or "") if outcome is Outcome.REJECTED else "",
                tags=(
                    "reconstructed",
                    "campaign_cell",
                    f"campaign:{campaign_id}",
                    f"instrument:{cell.get('instrument')}",
                    f"timeframe:{cell.get('timeframe')}",
                    f"origin:{cell.get('origin')}",
                    f"gates:{report.get('gate_set_version')}",
                ),
            )
        )
        filed += 1
    counts["cells"] = filed
    counts["hypotheses"] = _file_hypothesis_verdicts(ledger, report, source)
    return counts


def _file_hypothesis_verdicts(
    ledger: ExperimentLedger, report: dict[str, Any], source: Path
) -> int:
    """Append what the campaign did to each claim.

    This mirrors ``run_discovery_campaign._record_hypothesis_outcomes`` exactly:
    a claim whose every candidate was rejected is REFUTED **on this cell**, and
    a claim with any survivor goes to TESTING. Both are appended as new rows
    chained onto the claim's latest one -- the ledger has no update.
    """
    store = HypothesisLedger(ledger)
    campaign_id = str(report["campaign_id"])
    datasets = report.get("dataset_versions") or {}

    by_hypothesis: dict[str, list[dict[str, Any]]] = {}
    for cell in report.get("attempted") or []:
        by_hypothesis.setdefault(str(cell.get("hypothesis_id") or ""), []).append(cell)

    filed = 0
    for hypothesis_id, cells in sorted(by_hypothesis.items()):
        if not hypothesis_id or not cells:
            continue
        current = store.get(hypothesis_id)
        if current is None:
            continue
        history = store.history(hypothesis_id)
        if any(f"campaign:{campaign_id}" in row.tags for row in history):
            continue
        survivors = [c for c in cells if str(c.get("verdict", "")).lower() in
                     ("promote", "promoted", "survive", "survived")]
        if survivors:
            store.set_status(
                current,
                HypothesisStatus.TESTING,
                reason=(
                    f"{len(survivors)} of {len(cells)} candidate(s) cleared every gate "
                    f"on {datasets}. One cell is not evidence of an edge; the claim "
                    "stays under test."
                ),
                actor_name=ACTOR,
                campaign_id=campaign_id,
            )
            filed += 1
            continue
        rungs = sorted({str(c.get("died_at_rung") or "") for c in cells} - {""})
        store.set_status(
            current,
            HypothesisStatus.REFUTED,
            reason=(
                f"all {len(cells)} candidate(s) expressing this claim on {datasets} "
                f"were rejected (died at: {', '.join(rungs) or 'unrecorded'}). This "
                "refutes the claim ON THIS CELL, under this cost model and this gate "
                "set. It does not refute the mechanism in general."
            ),
            actor_name=ACTOR,
            evidence=Evidence(
                direction="against",
                claim=(
                    f"campaign {campaign_id}: every candidate was rejected; "
                    f"rungs reached: {', '.join(rungs) or 'unrecorded'}"
                ),
                source=str(source.relative_to(REPO)),
                strength="moderate",
            ),
            campaign_id=campaign_id,
        )
        filed += 1
    return filed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO / "var" / "experiments.sqlite",
        help="The ledger the API reads (FIBOKI_EXPERIMENT_DB).",
    )
    parser.add_argument(
        "--hypotheses", type=Path, default=REPO / "research" / "hypotheses"
    )
    parser.add_argument(
        "--backfill",
        type=Path,
        default=REPO / "research" / "reports" / "xauusd_h4",
        help="Directory of ORIGINAL ValidationReport JSON files to file verbatim.",
    )
    parser.add_argument(
        "--campaign",
        type=Path,
        action="append",
        dest="campaigns",
        help="Campaign report JSON. Repeatable. Defaults to K1 and K2.",
    )
    args = parser.parse_args(argv)
    campaigns = args.campaigns or list(DEFAULT_CAMPAIGNS)

    ledger = ExperimentLedger(args.db)
    try:
        before = len(ledger.list())
        filed_hypotheses = register_hypotheses(ledger, args.hypotheses)
        print(f"hypotheses pre-registered (ORIGINAL documents): {filed_hypotheses}")

        backfill = _load_campaign_backfill()
        filed_reports = backfill(ledger, args.backfill, actor=ACTOR)
        print(
            f"prior ladder runs filed (ORIGINAL ValidationReports from "
            f"{args.backfill.relative_to(REPO)}): {filed_reports}"
        )

        for path in campaigns:
            if not path.exists():
                print(f"SKIP  {path}: not present on this host")
                continue
            counts = load_campaign(ledger, path)
            print(
                f"campaign {path.parent.name}: {counts['summary']} summary row, "
                f"{counts['cells']} RECONSTRUCTED cell(s), "
                f"{counts['hypotheses']} hypothesis verdict(s)"
            )

        after = len(ledger.list())
        print()
        print(f"ledger {args.db}: {before} -> {after} rows")
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
