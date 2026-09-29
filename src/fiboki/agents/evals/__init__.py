"""Offline evaluation of recorded agent runs.  Deterministic and read-only.

The harness reads an audit ledger (and, for the cases that need it, the
research store) and returns one verdict per case:

``PASS``              every unit the case applies to was checked and passed;
``FAIL``              at least one unit violates the case;
``NOT_EVALUABLE``     the instrumentation the case needs is absent, or there
                      was nothing to evaluate.  **Missing evidence is never a
                      pass**: a case that could not look has not looked;
``INVALID_ARTIFACT``  something the ledger points at cannot be read, or the
                      ledger itself does not verify, so no honest verdict is
                      possible.

When units disagree the worst wins, in the order FAIL, INVALID_ARTIFACT,
NOT_EVALUABLE, PASS.  A ledger whose hash chain does not verify is
INVALID_ARTIFACT on every case: evaluating tampered evidence would only
launder it.

Cases are data (:data:`~fiboki.agents.evals.cases.CASES`): an id, a title, a
description and a pure check function.  The report carries no wall-clock
time, so the same ledger and store always give byte-identical JSON.

Pattern after Vibe-Trading ``agent/evals`` (MIT), written fresh.

Usage::

    from fiboki.agents.evals import run_evals, write_eval_report
    from fiboki.research.artefacts import ResearchStore
    report = run_evals("data/agents/audit.jsonl", ResearchStore("data/research"))
    write_eval_report(report, "data/agents/eval_report.json")
"""
from __future__ import annotations

from fiboki.agents.evals.cases import CASES, TRADE_INSTRUCTION_PATTERNS, find_trade_instructions
from fiboki.agents.evals.core import (
    CaseResult,
    EvalCase,
    EvalInputs,
    EvalReport,
    Finding,
    Verdict,
    worst,
)
from fiboki.agents.evals.runner import (
    load_ledger,
    run_evals,
    run_evals_on_records,
    write_eval_report,
)

__all__ = [
    "CASES",
    "TRADE_INSTRUCTION_PATTERNS",
    "CaseResult",
    "EvalCase",
    "EvalInputs",
    "EvalReport",
    "Finding",
    "Verdict",
    "find_trade_instructions",
    "load_ledger",
    "run_evals",
    "run_evals_on_records",
    "worst",
    "write_eval_report",
]
