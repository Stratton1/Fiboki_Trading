"""Load a ledger without touching it, run every case, write the report."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from fiboki.agents.audit import GENESIS_HASH, AuditRecord
from fiboki.agents.evals.cases import CASES
from fiboki.agents.evals.core import (
    CaseResult,
    EvalCase,
    EvalInputs,
    EvalReport,
    Finding,
    Verdict,
    worst,
)
from fiboki.research.artefacts import ResearchStore


class LedgerUnreadable(ValueError):
    """A line of the ledger does not parse as an audit record."""


def load_ledger(path: str | Path) -> tuple[tuple[AuditRecord, ...], str, bool]:
    """``(records, sha256 of the file bytes, chain verifies)``.  Opens read-only.

    Deliberately not :class:`~fiboki.agents.audit.JsonlAuditLedger`: that
    class creates the parent directory and a ``.lock`` sidecar, and an eval
    must leave no trace on the evidence it reads.  A missing file raises
    :class:`FileNotFoundError`: an absent ledger is not an empty one.
    """
    blob = Path(path).read_bytes()
    records: list[AuditRecord] = []
    for lineno, line in enumerate(blob.split(b"\n"), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            records.append(AuditRecord.from_payload(json.loads(text)))
        except (ValueError, KeyError, TypeError) as exc:
            raise LedgerUnreadable(f"{path}:{lineno}: {exc}") from exc
    return tuple(records), hashlib.sha256(blob).hexdigest(), verify_records(records)


def verify_records(records: Sequence[AuditRecord]) -> bool:
    """The same chain rule the ledger enforces, re-derived from the records."""
    previous = GENESIS_HASH
    for index, record in enumerate(records):
        if record.sequence != index or record.previous_hash != previous:
            return False
        if record.compute_hash() != record.record_hash:
            return False
        previous = record.record_hash
    return True


def _run_cases(
    cases: Sequence[EvalCase], inputs: EvalInputs, *, chain_ok: bool, why: str = ""
) -> tuple[CaseResult, ...]:
    results: list[CaseResult] = []
    for case in cases:
        if not chain_ok:
            findings: Sequence[Finding] = [
                Finding("ledger", Verdict.INVALID_ARTIFACT, why or "hash chain does not verify")
            ]
        else:
            try:
                findings = case.check(inputs)
            except Exception as exc:  # a crashing case must not read as a pass
                findings = [
                    Finding(
                        "case",
                        Verdict.INVALID_ARTIFACT,
                        f"case raised {type(exc).__name__}: {exc}",
                    )
                ]
        results.append(CaseResult.from_findings(case, findings))
    return tuple(results)


def run_evals_on_records(
    records: Sequence[AuditRecord],
    store: ResearchStore | None,
    *,
    source: str = "<memory>",
    ledger_sha256: str = "",
    cases: Sequence[EvalCase] = CASES,
) -> EvalReport:
    """Evaluate records already in memory (for example ``AuditLedger.records()``)."""
    chain_ok = verify_records(records)
    results = _run_cases(
        cases, EvalInputs(records=tuple(records), store=store), chain_ok=chain_ok
    )
    return EvalReport(
        source=source,
        ledger_sha256=ledger_sha256,
        n_records=len(records),
        chain_ok=chain_ok,
        verdict=worst(r.verdict for r in results),
        cases=results,
        store_present=store is not None,
    )


def run_evals(
    ledger_path: str | Path,
    store: ResearchStore | None,
    *,
    cases: Sequence[EvalCase] = CASES,
) -> EvalReport:
    """Evaluate a JSONL audit ledger against the research store.

    Read-only: the ledger is read as bytes, and the store is only asked for
    records by id or by collection.
    """
    try:
        records, digest, chain_ok = load_ledger(ledger_path)
    except LedgerUnreadable as exc:
        results = _run_cases(
            cases, EvalInputs(records=(), store=store), chain_ok=False, why=str(exc)
        )
        blob = Path(ledger_path).read_bytes()
        return EvalReport(
            source=str(ledger_path),
            ledger_sha256=hashlib.sha256(blob).hexdigest(),
            n_records=0,
            chain_ok=False,
            verdict=Verdict.INVALID_ARTIFACT,
            cases=results,
            store_present=store is not None,
        )
    report = run_evals_on_records(
        records, store, source=str(ledger_path), ledger_sha256=digest, cases=cases
    )
    assert report.chain_ok == chain_ok
    return report


def write_eval_report(report: EvalReport, path: str | Path) -> Path:
    """Write the report as sorted, indented JSON.  The only write in the harness."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return target


__all__ = [
    "LedgerUnreadable",
    "load_ledger",
    "run_evals",
    "run_evals_on_records",
    "verify_records",
    "write_eval_report",
]
