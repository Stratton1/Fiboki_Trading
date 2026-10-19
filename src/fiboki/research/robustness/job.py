"""The ``robustness`` research job: one re-run diagnostic of one stored run.

Queued by ``POST /api/research/runs/{run_id}/robustness/{kind}`` (admin-gated,
audited) into the research worker's durable job ledger; executed by the worker,
never by the API. Its payload is complete: the diagnostic ``kind``, the run's
:class:`~fiboki.research.robustness.engine.RunBinding` (read back from the job
ledger by the API), the options and the cache key. The idempotency key is the
cache key, so asking twice for the same diagnostic of the same run under the
same engine runs it once.

The handler re-derives the cache key from the payload and refuses a mismatch,
re-checks the stored run's ledger hash, writes progress as it goes and files
the result in the :class:`~fiboki.research.robustness.store.RobustnessStore`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fiboki.research.robustness.engine import BindingExecutor, RunBinding
from fiboki.research.robustness.rerun import RUNNERS, options_of
from fiboki.research.robustness.store import ROBUSTNESS_VERSION, cache_key, engine_fingerprint

__all__ = ["RERUN_KINDS", "idempotency_key", "robustness_handler", "run_diagnostic"]

RERUN_KINDS = tuple(RUNNERS)


def idempotency_key(key: str) -> str:
    return f"robustness:{key}"


def run_diagnostic(kind: str, binding: RunBinding, options: Mapping[str, Any], *, template: Any, bars: Any,
                   progress: Any = None) -> dict[str, Any]:
    """Run one diagnostic and wrap it with its provenance. Pure given its inputs."""
    if kind not in RUNNERS:
        raise ValueError(f"unknown robustness diagnostic {kind!r}; known {sorted(RUNNERS)}")
    executor = BindingExecutor(binding, template, bars)
    opts = options_of(kind, options)
    out = RUNNERS[kind](executor, **opts, **({"progress": progress} if progress else {}))
    base_hash = str(((out.get("baseline") or {}).get("ledger_sha256")) or "")
    reproduces: bool | None = None
    if base_hash and binding.covers_stored_run and binding.ledger_sha256:
        reproduces = base_hash == binding.ledger_sha256
    return {
        "kind": kind, "version": ROBUSTNESS_VERSION, "run_id": binding.run_id,
        "options": opts, "output": out,
        "window": {"start": executor.window.start.isoformat(), "end": executor.window.end.isoformat(),
                   "bars": int(len(executor.window_frame(0))),
                   "prologue_available": int(executor.prologue_available)},
        "boundary": dict(binding.boundary),
        "dataset_version_id": executor.version_id,
        "engine": engine_fingerprint(binding.to_payload()),
        "n_engine_runs": int(executor.n_runs),
        "covers_stored_run": bool(binding.covers_stored_run),
        "reproduces_stored": reproduces,
    }


def robustness_handler(ctx: Any) -> Mapping[str, Any]:
    store = ctx.service("robustness")
    if store is None:
        raise ValueError("no robustness store is wired into this worker (register_research_handlers "
                         "robustness=...); refusing to compute a result that could not be filed")
    research = ctx.service("research")
    strategies = ctx.service("strategies")
    bars = ctx.service("bars")
    if bars is None:
        raise ValueError("no bar source is wired into this worker; a re-run diagnostic needs bars")
    kind, raw_binding, options, key = ctx.require("kind", "binding", "options", "cache_key")
    binding = RunBinding.from_payload(raw_binding)
    opts = options_of(str(kind), dict(options or {}))
    expected = cache_key(str(kind), binding.to_payload(), opts)
    if expected != key:
        raise ValueError("the payload's cache key does not match its own contents; refusing to file a "
                         "result under a key that does not describe it")
    if store.get(key) is not None:
        return {"cache_key": key, "kind": kind, "run_id": binding.run_id, "cached": True}
    record = research.get_backtest(binding.run_id)
    if binding.ledger_sha256 and record.ledger_sha256 != binding.ledger_sha256:
        raise ValueError(f"run {binding.run_id}'s stored ledger hash changed since the job was queued")
    template = strategies.get(binding.strategy_id)

    def progress(done: int, total: int, stage: str) -> None:
        store.progress(key, run_id=binding.run_id, kind=str(kind), job_id=ctx.job_id, done=done,
                       total=total, stage=stage)

    result = run_diagnostic(str(kind), binding, opts, template=template, bars=bars, progress=progress)
    store.put(key, run_id=binding.run_id, kind=str(kind), job_id=ctx.job_id,
              fingerprint=result["engine"], result=result)
    # ``backtest_id`` makes the job queue link the output to the run's page.
    return {"cache_key": key, "kind": kind, "run_id": binding.run_id, "cached": False,
            "backtest_id": binding.run_id, "n_engine_runs": result["n_engine_runs"],
            "strategy_id": binding.strategy_id}
