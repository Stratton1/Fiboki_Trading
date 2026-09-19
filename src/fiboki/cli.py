"""``fiboki`` -- the operator's command line.

Design rules this CLI follows
-----------------------------
**Every command that can lie, doesn't.**  A command that cannot reach the thing
it is reporting on says so and exits non-zero.  V1's status page returned
``ok`` from a handler that performed no checks; the same instinct applied to a
CLI produces ``fiboki worker status`` printing "running" because a row exists.

**Exit codes are meaningful**, because a CLI is also an API for ``make`` and
for a launchd ``KeepAlive`` block:

===  ===========================================================
0    all good
1    a check failed / the operation did not complete
2    misuse (bad arguments, missing file)
75   another holder has the lease -- do not retry in a loop
===  ===========================================================

**Nothing here places an order.**  ``fiboki killswitch flatten`` asks the
execution service for flatten intents; it does not construct one.  The single
``Order`` construction site is enforced by an AST test over ``src/``, and this
module is inside ``src/``.

**``fiboki system doctor`` is the important one.**  It is the command an
operator runs at 07:40 when something is wrong and they have no idea what.  It
checks the environment end to end and prints the fix, not the symptom.
"""
# typer declares options and arguments as CALLS in parameter defaults. That is
# the library's entire API, so B008 is disabled for this module rather than
# repeated ~20 times as a per-line noqa.
# ruff: noqa: B008
from __future__ import annotations

import json
import os
import platform
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fiboki import __version__

__all__ = ["app", "main"]

console = Console()
err_console = Console(stderr=True)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_MISUSE = 2
EXIT_LEASE_HELD = 75

app = typer.Typer(
    name="fiboki",
    help="Fiboki V2 - research laboratory and trading operating system.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

data_app = typer.Typer(help="Bar data: ingest, validate, version, migrate.", no_args_is_help=True)
research_app = typer.Typer(help="Sweeps, validation, campaigns, memory.", no_args_is_help=True)
strategy_app = typer.Typer(help="Strategy documents: list, show, bind, compile.", no_args_is_help=True)
worker_app = typer.Typer(help="Worker processes: run and inspect.", no_args_is_help=True)
system_app = typer.Typer(help="Health and diagnostics.", no_args_is_help=True)
broker_app = typer.Typer(help="Venue status and reconciliation.", no_args_is_help=True)
killswitch_app = typer.Typer(help="Pause, flatten, and status.", no_args_is_help=True)

app.add_typer(data_app, name="data")
app.add_typer(research_app, name="research")
app.add_typer(strategy_app, name="strategy")
app.add_typer(worker_app, name="worker")
app.add_typer(system_app, name="system")
app.add_typer(broker_app, name="broker")
app.add_typer(killswitch_app, name="killswitch")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ENV_DATA_ROOT = "FIBOKI_DATA_ROOT"
ENV_STATE_DB = "FIBOKI_STATE_DB"
ENV_MODE = "FIBOKI_EXECUTION_MODE"
ENV_LIVE = "FIBOKI_LIVE_EXECUTION_ENABLED"


def state_db_path() -> Path:
    """Where worker leases and heartbeats live.  One resolution rule, here."""
    raw = os.environ.get(ENV_STATE_DB)
    if raw:
        return Path(raw).expanduser()
    return Path(os.environ.get("FIBOKI_HOME", Path.home() / ".fiboki")).expanduser() / "state.db"


def data_root() -> Path | None:
    raw = os.environ.get(ENV_DATA_ROOT)
    return Path(raw).expanduser() if raw else None


def _fail(message: str, code: int = EXIT_FAIL) -> None:
    err_console.print(f"[bold red]✗[/bold red] {message}")
    raise typer.Exit(code)


def _ok(message: str) -> None:
    console.print(f"[bold green]✓[/bold green] {message}")


def _warn(message: str) -> None:
    console.print(f"[bold yellow]![/bold yellow] {message}")


def _emit(payload: Any, *, as_json: bool, render: Callable[[], None]) -> None:
    if as_json:
        console.print_json(json.dumps(payload, default=str))
    else:
        render()


def repo_root() -> Path:
    """The repository root, when the CLI is running from a source checkout."""
    return Path(__file__).resolve().parents[2]


def _load_script(name: str) -> Any:
    """Load ``scripts/<name>.py`` by path.

    ``scripts/`` deliberately is not a package: these are operator tools, not
    library code, and importing them should not be possible by accident from
    inside ``fiboki``.
    """
    import importlib.util

    path = repo_root() / "scripts" / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(path)
    module_name = f"_fiboki_script_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: dataclasses resolve annotations through
    # ``sys.modules[cls.__module__]``, so a module that is not there yet fails
    # with a confusing AttributeError inside the stdlib.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _open_store(create: bool = True) -> Any:
    from fiboki.workers.base import WorkerStore

    path = state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return WorkerStore.sqlite_at(path, create=create)


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


@app.callback()
def root(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug-level structured logs."),
    log_json: bool = typer.Option(False, "--log-json", help="Force JSON logs even on a tty."),
) -> None:
    from fiboki.obs.logging import configure_logging

    configure_logging(
        level="DEBUG" if verbose else "INFO",
        json_output=True if log_json else None,
        component="cli",
    )
    ctx.obj = {"verbose": verbose}


@app.command()
def version(as_json: bool = typer.Option(False, "--json")) -> None:
    """Print versions of fiboki, Python and the pinned numerical stack."""
    payload: dict[str, Any] = {
        "fiboki": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
    }
    for module in ("numpy", "pandas", "scipy", "pyarrow", "sqlalchemy"):
        try:
            payload[module] = __import__(module).__version__
        except Exception:
            payload[module] = "MISSING"

    def _render() -> None:
        table = Table(title="fiboki", box=None)
        table.add_column("component", style="cyan")
        table.add_column("version")
        for key, value in payload.items():
            table.add_row(key, str(value))
        console.print(table)

    _emit(payload, as_json=as_json, render=_render)


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


@data_app.command("ingest")
def data_ingest(
    instrument: list[str] = typer.Option(..., "--instrument", "-i", help="Symbol, repeatable."),
    timeframe: str = typer.Option("H1", "--timeframe", "-t"),
    provider: str = typer.Option("dukascopy", "--provider", "-p"),
    start: str = typer.Option(..., "--start", help="ISO date, inclusive."),
    end: str = typer.Option(..., "--end", help="ISO date, exclusive."),
    root: Path | None = typer.Option(None, "--root", help=f"Overrides ${ENV_DATA_ROOT}."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; write nothing."),
) -> None:
    """Fetch bars into the raw layer and promote them to canonical.

    Raw is immutable by construction (the store makes the files read-only), so
    a re-ingest of the same window produces a NEW version rather than editing
    history.  That is why this command never has a ``--force-overwrite``.
    """
    from fiboki.data.store import DataStore

    target = root or data_root()
    if target is None:
        _fail(f"no data root: pass --root or set ${ENV_DATA_ROOT}", EXIT_MISUSE)
    assert target is not None

    plan = [
        {"instrument": sym, "timeframe": timeframe, "start": start, "end": end}
        for sym in instrument
    ]
    table = Table(title=f"ingest plan ({provider})")
    for column in ("instrument", "timeframe", "start", "end"):
        table.add_column(column)
    for row in plan:
        table.add_row(*(str(row[c]) for c in ("instrument", "timeframe", "start", "end")))
    console.print(table)

    if dry_run:
        _ok(f"dry run: {len(plan)} fetch(es) planned into {target}")
        return

    try:
        DataStore.initialise(target)
    except Exception as exc:
        _fail(f"could not open the data store at {target}: {exc}")
    _warn(
        "provider credentials are read from the environment; no provider is contacted "
        "in --dry-run or in CI."
    )
    _fail(
        "ingest requires a configured provider session. Run "
        "`fiboki system doctor` to see which provider variables are missing, then re-run. "
        "This command deliberately does NOT fall back to synthetic data: V1's fallback "
        "wrote fabricated bars into the canonical layer and nothing downstream could tell."
    )


@data_app.command("validate")
def data_validate(
    path: Path = typer.Argument(..., help="Parquet/CSV file or dataset directory."),
    strict: bool = typer.Option(False, "--strict", help="Any defect fails."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run the integrity checks and print every defect found."""
    import pandas as pd

    from fiboki.data.integrity import Severity, validate

    if not path.exists():
        _fail(f"no such path: {path}", EXIT_MISUSE)
    try:
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(
            path, index_col=0, parse_dates=True
        )
    except Exception as exc:
        _fail(f"could not read {path}: {exc}", EXIT_MISUSE)

    report = validate(frame)
    defects = list(getattr(report, "defects", ()))
    payload = {
        "path": str(path),
        "rows": int(len(frame)),
        "defects": [
            {
                "code": getattr(d.code, "value", str(d.code)),
                "severity": getattr(d.severity, "value", str(d.severity)),
                "detail": getattr(d, "detail", ""),
            }
            for d in defects
        ],
    }

    def _render() -> None:
        table = Table(title=f"integrity: {path.name} ({len(frame)} rows)")
        table.add_column("severity")
        table.add_column("code")
        table.add_column("detail", overflow="fold")
        for defect in defects:
            severity = getattr(defect.severity, "value", str(defect.severity))
            colour = {"error": "red", "warning": "yellow"}.get(severity, "white")
            table.add_row(
                Text(severity, style=colour),
                str(getattr(defect.code, "value", defect.code)),
                str(getattr(defect, "detail", "")),
            )
        console.print(table if defects else "[green]no defects[/green]")

    _emit(payload, as_json=as_json, render=_render)
    errors = [d for d in defects if getattr(d.severity, "value", "") == Severity.ERROR.value]
    if errors or (strict and defects):
        _fail(f"{len(errors) or len(defects)} defect(s); this dataset is NOT clean")
    if not as_json:
        _ok("clean")


@data_app.command("version")
def data_version(
    catalogue: Path = typer.Option(..., "--catalogue", help="Path to the version catalogue DB."),
    dataset: str | None = typer.Option(None, "--dataset", help="Show one version id."),
    limit: int = typer.Option(20, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List dataset versions, or describe one, with its lineage."""
    from fiboki.data.versioning import DatasetCatalogue

    if not catalogue.exists():
        _fail(f"no catalogue at {catalogue}", EXIT_MISUSE)
    with DatasetCatalogue(catalogue) as cat:
        if dataset:
            try:
                version = cat.resolve(dataset)
            except Exception as exc:
                _fail(str(exc))
            chain = cat.lineage_chain(dataset)
            payload = {
                "version_id": version.version_id,
                "short_id": version.short_id,
                "describe": version.describe(),
                "lineage_depth": len(chain),
            }

            def _render_one() -> None:
                console.print(Panel(version.describe(), title=version.short_id))
                for step in chain:
                    console.print(f"  ← {step.short_id} {step.describe()}")

            _emit(payload, as_json=as_json, render=_render_one)
            return

        versions = cat.list_versions()[:limit]
        payload = {
            "versions": [
                {"version_id": v.version_id, "short_id": v.short_id, "describe": v.describe()}
                for v in versions
            ]
        }

        def _render() -> None:
            table = Table(title="dataset versions")
            table.add_column("version", style="cyan")
            table.add_column("description", overflow="fold")
            for item in versions:
                table.add_row(item.short_id, item.describe())
            console.print(table)

        _emit(payload, as_json=as_json, render=_render)


@data_app.command("migrate-v1")
def data_migrate_v1(
    source: Path = typer.Argument(..., help="V1 data directory."),
    dest: Path = typer.Argument(..., help="V2 data root."),
    check: bool = typer.Option(False, "--check", help="Report only; write nothing."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Migrate V1 bar files into the V2 store, reporting what was rejected.

    Rejections are the point: V1's directory contains files that will not pass
    V2's integrity checks, and the honest outcome is a list of them rather than
    a migration that quietly repairs data.
    """
    from fiboki.data.migrate_v1 import migrate

    if not source.exists():
        _fail(f"no such source: {source}", EXIT_MISUSE)
    if check:
        # `migrate` has no dry-run mode. Pretending otherwise by calling it
        # anyway would WRITE, which is the opposite of what --check promises.
        _fail(
            "fiboki.data.migrate_v1.migrate has no dry-run mode, so --check cannot be "
            "honoured without writing. Run it against a scratch destination directory "
            "instead and inspect the report.",
            EXIT_MISUSE,
        )
    try:
        report = migrate(source, dest)
    except Exception as exc:
        _fail(f"migration failed: {exc}")

    payload = {
        "source": str(source),
        "dest": str(dest),
        "entries": len(report.entries),
        "succeeded": len(report.succeeded),
        "failed": len(report.failed),
        "total_rows": report.total_rows,
        "rejected": [
            {"instrument": e.instrument, "reason": getattr(e, "error", "")}
            for e in report.failed
        ],
    }

    def _render() -> None:
        console.print(
            Panel(
                f"{payload['succeeded']} migrated, {payload['failed']} REJECTED, "
                f"{payload['total_rows']} rows",
                title="migrate-v1",
            )
        )
        if report.failed:
            # Rejections are the point: V1's directory contains files that will
            # not pass V2's integrity checks, and the honest outcome is a list.
            table = Table(title="rejected")
            table.add_column("instrument", style="red")
            table.add_column("reason", overflow="fold")
            for entry in report.failed:
                table.add_row(str(entry.instrument), str(getattr(entry, "error", "")))
            console.print(table)

    _emit(payload, as_json=as_json, render=_render)
    if report.failed:
        raise typer.Exit(EXIT_FAIL)


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


@research_app.command("sweep")
def research_sweep(
    sweep_id: str = typer.Argument(..., help="Stable id; resumption keys off this."),
    checkpoints: Path = typer.Option(
        Path("research/checkpoints.db"), "--checkpoints", help="Checkpoint database."
    ),
    status_only: bool = typer.Option(False, "--status", help="Report progress, run nothing."),
    max_no_data: float = typer.Option(0.02, "--max-no-data", help="Fail above this fraction."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Report or resume a parameter sweep.

    ``--status`` is the command to run after a crash.  It distinguishes DONE
    from NO_DATA, which is exactly the distinction V1's checkpoint file could
    not express and which produced a false ``phase1_complete.json``.
    """
    from fiboki.workers.research_worker import CheckpointStore

    if not checkpoints.exists() and status_only:
        _fail(f"no checkpoint database at {checkpoints}", EXIT_MISUSE)
    with CheckpointStore(checkpoints) as store:
        counts = store.counts(sweep_id)
        total = store.total(sweep_id)
        done = counts["done"] + counts["excluded"]
        outstanding = max(0, total - done)
        no_data_fraction = counts["no_data"] / total if total else 0.0
        payload = {
            "sweep_id": sweep_id,
            "total": total,
            "counts": counts,
            "outstanding": outstanding,
            "no_data_fraction": round(no_data_fraction, 6),
            "complete": total > 0 and outstanding == 0 and counts["no_data"] == 0,
        }

        def _render() -> None:
            table = Table(title=f"sweep {sweep_id}")
            table.add_column("state", style="cyan")
            table.add_column("cells", justify="right")
            for key, value in counts.items():
                table.add_row(key, str(value))
            table.add_row("[bold]total[/bold]", str(total))
            table.add_row("[bold]outstanding[/bold]", str(outstanding))
            console.print(table)
            if counts["no_data"]:
                _warn(
                    f"{counts['no_data']} cell(s) returned NO DATA. They are NOT complete and "
                    "will be recomputed. Do not rank on this sweep yet."
                )
            if payload["complete"]:
                _ok("every cell is done or explicitly excluded")

        _emit(payload, as_json=as_json, render=_render)

    if no_data_fraction > max_no_data and total:
        _fail(
            f"no-data fraction {no_data_fraction:.1%} exceeds the {max_no_data:.1%} threshold"
        )
    if status_only:
        return
    _fail(
        "resuming a sweep from the CLI requires the campaign definition. Queue it through "
        "the orchestrator and run `fiboki worker run research`, which is the supported path "
        "(and the one with a lease, a heartbeat and a no-data guard)."
    )


@research_app.command("validate")
def research_validate(
    ledger: Path = typer.Option(..., "--ledger", help="Experiment ledger database."),
    experiment: str = typer.Option(..., "--experiment", "-e"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print an experiment's validation report, rung by rung."""
    from fiboki.research.experiment import ExperimentLedger

    if not ledger.exists():
        _fail(f"no ledger at {ledger}", EXIT_MISUSE)
    store = ExperimentLedger(ledger)
    try:
        record = store.get(experiment)
    except Exception as exc:
        _fail(str(exc))
    report = getattr(record, "validation_report", None)
    payload = {
        "experiment": experiment,
        "outcome": getattr(record.outcome, "value", str(record.outcome)),
        "rejected_at_rung": getattr(record, "rejected_at_rung", ""),
        "conclusion": getattr(record, "conclusion", ""),
        "has_report": report is not None,
    }

    def _render() -> None:
        console.print(
            Panel(
                f"outcome: [bold]{payload['outcome']}[/bold]\n"
                f"rejected at: {payload['rejected_at_rung'] or '—'}\n"
                f"{payload['conclusion']}",
                title=experiment,
            )
        )
        if report is None:
            _warn("no validation report attached to this experiment")

    _emit(payload, as_json=as_json, render=_render)


@research_app.command("campaign")
def research_campaign(
    definition: Path = typer.Argument(..., help="Campaign JSON."),
    queue: str = typer.Option("research", "--queue"),
    submit: bool = typer.Option(False, "--submit", help="Actually enqueue (default: plan)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Plan (or enqueue) a research campaign as orchestrator jobs.

    Planning is the default because a campaign is thousands of jobs and the
    operator should see the count before it exists.
    """
    if not definition.exists():
        _fail(f"no campaign at {definition}", EXIT_MISUSE)
    try:
        spec = json.loads(definition.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"{definition} is not valid JSON: {exc}", EXIT_MISUSE)

    jobs = spec.get("jobs", [])
    payload = {"campaign": spec.get("name", definition.stem), "queue": queue, "jobs": len(jobs)}

    def _render() -> None:
        table = Table(title=f"campaign {payload['campaign']}")
        table.add_column("job type", style="cyan")
        table.add_column("count", justify="right")
        counts: dict[str, int] = {}
        for job in jobs:
            key = str(job.get("job_type", "?"))
            counts[key] = counts.get(key, 0) + 1
        for key, value in sorted(counts.items()):
            table.add_row(key, str(value))
        console.print(table)

    _emit(payload, as_json=as_json, render=_render)
    if not submit:
        if not as_json:
            _warn(f"plan only. Re-run with --submit to enqueue {len(jobs)} job(s) on {queue!r}.")
        return
    _fail(
        "enqueueing needs a live orchestrator with handlers registered. Start the API or "
        "the research worker process, which own that wiring."
    )


@research_app.command("memory")
def research_memory(
    ledger: Path = typer.Option(..., "--ledger"),
    query: str = typer.Argument(..., help="What you are about to try."),
    limit: int = typer.Option(10, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Ask 'have we tried this already?' BEFORE spending the compute."""
    from fiboki.research.experiment import ExperimentLedger
    from fiboki.research.memory import ResearchMemory

    if not ledger.exists():
        _fail(f"no ledger at {ledger}", EXIT_MISUSE)
    memory = ResearchMemory(ExperimentLedger(ledger))
    try:
        result = memory.recall(query, limit=limit)  # type: ignore[call-arg]
    except TypeError:
        result = memory.recall(query)  # type: ignore[call-arg]
    except Exception as exc:
        _fail(f"recall failed: {exc}")

    matches = list(getattr(result, "matches", ()) or ())
    payload = {"query": query, "matches": len(matches)}

    def _render() -> None:
        if not matches:
            _ok("nothing similar on record — this is new ground")
            return
        table = Table(title=f"{len(matches)} prior experiment(s)")
        table.add_column("relation", style="cyan")
        table.add_column("experiment")
        table.add_column("outcome")
        for match in matches[:limit]:
            table.add_row(
                str(getattr(match.relation, "value", match.relation)),
                str(getattr(match, "experiment_id", "")),
                str(getattr(getattr(match, "outcome", ""), "value", "")),
            )
        console.print(table)

    _emit(payload, as_json=as_json, render=_render)


# ---------------------------------------------------------------------------
# strategy
# ---------------------------------------------------------------------------


def _load_registry(directory: Path) -> Any:
    from fiboki.strategy.registry import StrategyRegistry

    registry = StrategyRegistry()
    if not directory.exists():
        _fail(f"no strategy directory at {directory}", EXIT_MISUSE)
    registry.load_directory(directory)
    return registry


@strategy_app.command("list")
def strategy_list(
    directory: Path = typer.Option(Path("research/strategies"), "--dir", "-d"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List every registered strategy document."""
    registry = _load_registry(directory)
    ids = registry.ids()
    payload = {"directory": str(directory), "count": len(ids), "ids": ids}

    def _render() -> None:
        table = Table(title=f"{len(ids)} strategies in {directory}")
        table.add_column("id", style="cyan")
        table.add_column("version")
        for strategy_id in ids:
            doc = registry.get(strategy_id)
            table.add_row(strategy_id, str(getattr(doc, "version", "")))
        console.print(table)

    _emit(payload, as_json=as_json, render=_render)


@strategy_app.command("show")
def strategy_show(
    strategy_id: str = typer.Argument(...),
    directory: Path = typer.Option(Path("research/strategies"), "--dir", "-d"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print one strategy document and its content hash."""
    registry = _load_registry(directory)
    if strategy_id not in registry:
        _fail(f"no strategy {strategy_id!r} in {directory}", EXIT_MISUSE)
    doc = registry.get(strategy_id)
    payload = {
        "id": strategy_id,
        "version": str(getattr(doc, "version", "")),
        "content_hash": str(getattr(doc, "content_hash", "")),
    }

    def _render() -> None:
        console.print(
            Panel(
                "\n".join(f"{k}: {v}" for k, v in payload.items()),
                title=strategy_id,
            )
        )

    _emit(payload, as_json=as_json, render=_render)


@strategy_app.command("bind")
def strategy_bind(
    strategy_id: str = typer.Argument(...),
    instrument: list[str] = typer.Option(..., "--instrument", "-i"),
    timeframe: str = typer.Option("H1", "--timeframe", "-t"),
    directory: Path = typer.Option(Path("research/strategies"), "--dir", "-d"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Bind a strategy to instruments/timeframe and print the resulting cells."""
    registry = _load_registry(directory)
    if strategy_id not in registry:
        _fail(f"no strategy {strategy_id!r}", EXIT_MISUSE)
    cells = [
        {"strategy": strategy_id, "instrument": symbol, "timeframe": timeframe}
        for symbol in instrument
    ]
    payload = {"strategy": strategy_id, "cells": cells, "count": len(cells)}

    def _render() -> None:
        table = Table(title=f"{strategy_id} → {len(cells)} binding(s)")
        table.add_column("instrument", style="cyan")
        table.add_column("timeframe")
        for cell in cells:
            table.add_row(cell["instrument"], cell["timeframe"])
        console.print(table)

    _emit(payload, as_json=as_json, render=_render)


@strategy_app.command("compile")
def strategy_compile(
    strategy_id: str | None = typer.Argument(None, help="Omit to compile all."),
    directory: Path = typer.Option(Path("research/strategies"), "--dir", "-d"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Compile strategy documents and report causality/health failures."""
    from fiboki.strategy.compiler import CompilationError, compile_strategy

    registry = _load_registry(directory)
    targets = [strategy_id] if strategy_id else registry.ids()
    failures: list[dict[str, str]] = []
    for target in targets:
        if target not in registry:
            failures.append({"strategy": target, "error": "not found"})
            continue
        try:
            compile_strategy(registry.get(target))
        except CompilationError as exc:
            failures.append({"strategy": target, "error": str(exc)})
        except Exception as exc:  # a non-compilation error is still a failure
            failures.append({"strategy": target, "error": f"{type(exc).__name__}: {exc}"})

    health = registry.health_check()
    payload = {
        "compiled": len(targets) - len(failures),
        "failed": len(failures),
        "failures": failures,
        "registry_ok": bool(getattr(health, "ok", True)),
    }

    def _render() -> None:
        if failures:
            table = Table(title="compilation failures")
            table.add_column("strategy", style="red")
            table.add_column("error", overflow="fold")
            for failure in failures:
                table.add_row(failure["strategy"], failure["error"])
            console.print(table)
        else:
            _ok(f"{len(targets)} strategy document(s) compiled")
        console.print(getattr(health, "summary", lambda: "")())

    _emit(payload, as_json=as_json, render=_render)
    if failures or not getattr(health, "ok", True):
        raise typer.Exit(EXIT_FAIL)


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------


@worker_app.command("run")
def worker_run(
    kind: str = typer.Argument("research", help="research | live"),
    once: bool = typer.Option(False, "--once", help="Run a single cycle and exit."),
    max_cycles: int = typer.Option(0, "--max-cycles", help="0 = run forever."),
    lease_ttl: float = typer.Option(60.0, "--lease-ttl"),
    idle_sleep: float = typer.Option(2.0, "--idle-sleep"),
    nice: int | None = typer.Option(None, "--nice", help="Yield CPU to the desktop."),
) -> None:
    """Run a worker PROCESS in the foreground.

    Foreground is the default on purpose.  A worker that daemonises itself is
    a worker whose death is invisible; run it under launchd/systemd (see
    ``deploy/``) and let the supervisor own the restart policy.
    """
    from fiboki.obs.alerts import build_default_dispatcher
    from fiboki.workers.base import EXIT_LEASE_HELD as LEASE_CODE
    from fiboki.workers.research_worker import ResearchWorker, ResearchWorkerConfig
    from fiboki.workers.scheduler import apply_nice

    if kind not in {"research", "live"}:
        _fail(f"unknown worker kind {kind!r}; expected research or live", EXIT_MISUSE)
    if kind == "live":
        _fail(
            "the live worker needs a wired execution service, feed, evaluator and risk "
            "context builder. It is started from the application entrypoint that owns that "
            "wiring, not from a bare CLI invocation -- a live worker assembled from command "
            "line flags is a live worker whose risk configuration nobody reviewed.",
            EXIT_MISUSE,
        )

    apply_nice(nice)
    store = _open_store()
    dispatcher = build_default_dispatcher(source=f"{kind}-worker")
    config = ResearchWorkerConfig(
        kind=kind,
        max_cycles=1 if once else max_cycles,
        lease_ttl_seconds=lease_ttl,
        idle_sleep_seconds=idle_sleep,
    )
    from fiboki.agents.orchestrator import Orchestrator

    orchestrator = Orchestrator()
    _warn(
        "this orchestrator has no handlers registered, so the worker will idle. "
        "Handlers are registered by the application wiring; this path exists so the "
        "process, lease and heartbeat can be exercised standalone."
    )
    worker = ResearchWorker(orchestrator, store, config, dispatcher=dispatcher)
    console.print(
        Panel(
            f"worker_id: [bold]{worker.worker_id}[/bold]\n"
            f"lease:     {config.lease_name}\n"
            f"state db:  {state_db_path()}\n"
            f"alerts:    {', '.join(dispatcher.channel_names())}",
            title=f"fiboki {kind} worker",
        )
    )
    code = worker.run()
    if code == LEASE_CODE:
        raise typer.Exit(EXIT_LEASE_HELD)
    raise typer.Exit(code)


@worker_app.command("status")
def worker_status(
    stale_after: float = typer.Option(120.0, "--stale-after"),
    down_after: float = typer.Option(300.0, "--down-after"),
    release: str | None = typer.Option(
        None, "--release", help="Force-expire this lease. OPERATOR ACTION."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show every worker's heartbeat age and every lease holder.

    This is a READ of the recorded state.  It does not decide whether a worker
    is alive; the watchdog does that on a timer and alerts.  What this command
    guarantees is that the same numbers are visible on demand.
    """
    from fiboki.workers.base import force_release, summarise_leases

    store = _open_store()
    if release:
        if force_release(store, release):
            _ok(f"lease {release!r} force-expired. Start exactly one worker now.")
        else:
            _fail(f"no lease named {release!r}", EXIT_MISUSE)
        return

    views = store.heartbeats()
    leases = list(summarise_leases(store))
    worker_rows: list[dict[str, Any]] = [
            {
                "worker_id": v.worker_id,
                "kind": v.kind,
                "age_seconds": round(v.age_seconds, 1),
                "status": v.status,
                "last_error": v.last_error,
                "verdict": (
                    "down"
                    if v.age_seconds >= down_after
                    else "stale"
                    if v.age_seconds >= stale_after
                    else "fresh"
                ),
            }
        for v in views
    ]
    payload = {
        "state_db": str(state_db_path()),
        "workers": worker_rows,
        "leases": leases,
    }

    def _render() -> None:
        table = Table(title="workers")
        for column in ("worker", "kind", "age", "status", "verdict", "last error"):
            table.add_column(column)
        for row in worker_rows:
            colour = {"down": "red", "stale": "yellow", "fresh": "green"}[row["verdict"]]
            table.add_row(
                row["worker_id"],
                row["kind"],
                f"{row['age_seconds']}s",
                row["status"],
                Text(row["verdict"], style=colour),
                (row["last_error"] or "")[:60],
            )
        console.print(table if views else "[yellow]no worker has ever written a heartbeat[/yellow]")

        lease_table = Table(title="leases")
        for column in ("lease", "holder", "fence", "expires in", "live"):
            lease_table.add_column(column)
        for lease in leases:
            lease_table.add_row(
                str(lease["lease"]),
                str(lease["holder"]),
                str(lease["fence"]),
                f"{lease['expires_in_seconds']}s",
                "yes" if lease["live"] else "no",
            )
        console.print(lease_table if leases else "[dim]no leases[/dim]")

    _emit(payload, as_json=as_json, render=_render)
    down = [w for w in worker_rows if w["verdict"] == "down"]
    if down:
        _fail(f"{len(down)} worker(s) DOWN: {', '.join(w['worker_id'] for w in down)}")


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------


def _build_health_checks() -> list[Any]:
    from fiboki.obs.health import (
        DatabaseCheck,
        QueueDepthCheck,
        WorkerHeartbeatCheck,
    )

    store = _open_store()
    return [
        DatabaseCheck(probe=store.ping),
        WorkerHeartbeatCheck(heartbeats=store.heartbeats),
        QueueDepthCheck(depths=lambda: {}),
    ]


@system_app.command("health")
def system_health(as_json: bool = typer.Option(False, "--json")) -> None:
    """Run the real health checks and exit non-zero if any FAIL."""
    from fiboki.obs.health import HealthStatus, run_checks

    report = run_checks(_build_health_checks())
    payload = report.to_dict()

    def _render() -> None:
        table = Table(title=f"health: {report.status.value.upper()}")
        for column in ("check", "status", "detail", "remedy"):
            table.add_column(column, overflow="fold")
        for result in report.results:
            colour = {"ok": "green", "degraded": "yellow", "fail": "red"}[result.status.value]
            table.add_row(
                result.name,
                Text(result.status.value, style=colour),
                result.detail,
                result.remedy,
            )
        console.print(table)

    _emit(payload, as_json=as_json, render=_render)
    if report.status is HealthStatus.FAIL:
        raise typer.Exit(EXIT_FAIL)


@dataclass
class Diagnosis:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def _diagnose(env: Mapping[str, str] | None = None) -> list[Diagnosis]:
    """Every check ``fiboki system doctor`` runs.  Pure enough to unit-test."""
    environ = dict(env if env is not None else os.environ)
    out: list[Diagnosis] = []

    # -- python -----------------------------------------------------------
    major, minor = sys.version_info[:2]
    supported = (major, minor) in ((3, 11), (3, 12))
    out.append(
        Diagnosis(
            "python",
            supported,
            f"{platform.python_version()} at {sys.executable}",
            "" if supported else "Fiboki pins 3.11-3.12. Recreate the venv with a supported "
            "interpreter: numerical results are not guaranteed identical across versions.",
        )
    )

    # -- pinned numerical stack ------------------------------------------
    expected = {
        "numpy": "2.2.6",
        "pandas": "2.2.3",
        "scipy": "1.14.1",
        "pyarrow": "18.1.0",
    }
    drift: list[str] = []
    missing: list[str] = []
    for module, want in expected.items():
        try:
            got = __import__(module).__version__
        except Exception:
            missing.append(module)
            continue
        if got != want:
            drift.append(f"{module} {got} != pinned {want}")
    notes: list[str] = []
    if missing:
        notes.append(f"MISSING: {', '.join(missing)}")
    notes.extend(drift)
    out.append(
        Diagnosis(
            "pinned dependencies",
            not drift and not missing,
            "; ".join(notes) or "all at pin",
            "" if not (drift or missing) else "Run `make setup` (or `pip install -e '.[dev]'`). "
            "A drifted numerical pin silently changes stored results; re-run the golden "
            "tests before trusting anything computed on it.",
        )
    )

    # -- state database ---------------------------------------------------
    path = state_db_path()
    writable = False
    detail = f"{path}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from fiboki.workers.base import WorkerStore

        store = WorkerStore.sqlite_at(path)
        store.ping()
        writable = True
        detail = f"{path} (reachable)"
        store.close()
    except Exception as exc:
        detail = f"{path}: {type(exc).__name__}: {exc}"
    out.append(
        Diagnosis(
            "state database",
            writable,
            detail,
            "" if writable else f"Create the directory or set ${ENV_STATE_DB} to a writable path.",
        )
    )

    # -- data root --------------------------------------------------------
    root = data_root()
    if root is None:
        out.append(
            Diagnosis(
                "data root",
                False,
                f"${ENV_DATA_ROOT} is not set",
                f"export {ENV_DATA_ROOT}=~/fiboki-data — without it every data command "
                "needs an explicit --root.",
            )
        )
    else:
        exists = root.exists()
        out.append(
            Diagnosis(
                "data root",
                exists,
                f"{root}{'' if exists else ' (does not exist)'}",
                "" if exists else f"mkdir -p {root}",
            )
        )

    # -- execution mode ---------------------------------------------------
    mode = environ.get(ENV_MODE, "paper").lower()
    live_flag = environ.get(ENV_LIVE, "").strip().lower()
    live_on = live_flag in {"1", "true", "yes", "on"}
    out.append(
        Diagnosis(
            "execution mode",
            mode == "paper" and not live_on,
            f"{ENV_MODE}={mode} {ENV_LIVE}={live_flag or '<unset>'}",
            ""
            if (mode == "paper" and not live_on)
            else "LIVE EXECUTION IS ENABLED IN THIS ENVIRONMENT. If that is not deliberate, "
            f"unset {ENV_LIVE} now. V1 shipped this flag set to \"true\" inside a committed "
            "render.yaml and it stayed there for months.",
        )
    )

    # -- alert channels ---------------------------------------------------
    channels = ["console"]
    if environ.get("FIBOKI_ALERT_LOG"):
        channels.append("file")
    if environ.get("FIBOKI_ALERT_WEBHOOK_URL"):
        channels.append("webhook")
    if environ.get("FIBOKI_TELEGRAM_BOT_TOKEN") and environ.get("FIBOKI_TELEGRAM_CHAT_ID"):
        channels.append("telegram")
    out.append(
        Diagnosis(
            "alert channels",
            len(channels) > 1,
            ", ".join(channels),
            ""
            if len(channels) > 1
            else "Only the console is configured, so an alert raised while nobody is watching "
            "the terminal goes nowhere. Set FIBOKI_ALERT_LOG at minimum.",
        )
    )

    # -- expected workers -------------------------------------------------
    expected_workers = environ.get("FIBOKI_EXPECTED_WORKERS", "")
    out.append(
        Diagnosis(
            "expected workers",
            bool(expected_workers),
            expected_workers or "<unset>",
            ""
            if expected_workers
            else "Without this the watchdog cannot alert on a worker that NEVER STARTED "
            "(e.g. after a reboot) — only on one that started and then went stale.",
        )
    )

    # -- worker liveness --------------------------------------------------
    try:
        from fiboki.workers.base import WorkerStore

        store = WorkerStore.sqlite_at(state_db_path())
        views = store.heartbeats()
        store.close()
        stale = [v.worker_id for v in views if v.age_seconds > 300]
        out.append(
            Diagnosis(
                "worker heartbeats",
                bool(views) and not stale,
                f"{len(views)} worker(s); stale: {', '.join(stale) or 'none'}",
                ""
                if views and not stale
                else "Start the worker process: `fiboki worker run research`, or load the "
                "launchd/systemd unit in deploy/.",
            )
        )
    except Exception as exc:
        out.append(Diagnosis("worker heartbeats", False, f"unreadable: {exc}", "Fix the state DB."))

    # -- lockfile ---------------------------------------------------------
    # Loaded by path, not imported: `scripts/` is a tools directory, not a
    # package, and putting it on sys.path permanently to read one file would
    # be a worse trade than an explicit loader.
    try:
        lockfile_script = _load_script("lockfile")
        result = lockfile_script.verify(lockfile_script.DEFAULT_LOCKFILE)
        out.append(
            Diagnosis(
                "dependency lockfile",
                result.ok,
                result.summary(),
                "" if result.ok else "Run `python scripts/lockfile.py record` after reviewing "
                "the drift. A drifted lockfile means the numbers this machine produces are "
                "not the numbers CI produces.",
            )
        )
    except Exception:
        out.append(
            Diagnosis(
                "dependency lockfile",
                False,
                "not verifiable from here",
                "Run `python scripts/lockfile.py verify` from the repository root.",
            )
        )

    return out


@system_app.command("doctor")
def system_doctor(as_json: bool = typer.Option(False, "--json")) -> None:
    """Diagnose a broken local setup and print exactly what to fix.

    This is the command to run first.  It never guesses: each line is a check
    that was actually performed, and each failure carries the command that
    fixes it.
    """
    findings = _diagnose()
    payload = {
        "ok": all(f.ok for f in findings),
        "findings": [
            {"name": f.name, "ok": f.ok, "detail": f.detail, "fix": f.fix} for f in findings
        ],
    }

    def _render() -> None:
        table = Table(title="fiboki doctor", show_lines=False)
        table.add_column("", width=2)
        table.add_column("check", style="cyan")
        table.add_column("observed", overflow="fold")
        for finding in findings:
            table.add_row(
                "[green]✓[/green]" if finding.ok else "[red]✗[/red]",
                finding.name,
                finding.detail,
            )
        console.print(table)

        problems = [f for f in findings if not f.ok and f.fix]
        if not problems:
            _ok("nothing to fix")
            return
        console.print()
        console.print("[bold]What to fix, in order:[/bold]")
        for index, finding in enumerate(problems, start=1):
            console.print(
                Panel(finding.fix, title=f"{index}. {finding.name}", border_style="yellow")
            )

    _emit(payload, as_json=as_json, render=_render)
    if not payload["ok"]:
        raise typer.Exit(EXIT_FAIL)


@system_app.command("metrics")
def system_metrics() -> None:
    """Print the Prometheus exposition body this process would serve."""
    from fiboki.obs.metrics import render_prometheus

    sys.stdout.write(render_prometheus())


# ---------------------------------------------------------------------------
# broker
# ---------------------------------------------------------------------------


@broker_app.command("status")
def broker_status(as_json: bool = typer.Option(False, "--json")) -> None:
    """Report the configured execution mode and whether live is enabled.

    It does NOT connect to a venue from a bare CLI invocation: opening a broker
    session needs credentials this command has no business handling.  What it
    tells you is which mode you are in, which is the question that precedes it.
    """
    mode = os.environ.get(ENV_MODE, "paper")
    live = os.environ.get(ENV_LIVE, "").strip().lower() in {"1", "true", "yes", "on"}
    payload = {"mode": mode, "live_execution_enabled": live}

    def _render() -> None:
        style = "red" if live else "green"
        console.print(
            Panel(
                f"mode: [bold]{mode}[/bold]\nlive execution enabled: "
                f"[{style}]{live}[/{style}]",
                title="broker",
                border_style=style,
            )
        )
        if live:
            _warn(
                "live execution is ENABLED in this environment. Confirm that is deliberate."
            )

    _emit(payload, as_json=as_json, render=_render)


@broker_app.command("reconcile")
def broker_reconcile(
    intents: Path = typer.Option(..., "--intents", help="JSONL intent store."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Summarise unresolved intents in the local store.

    A full reconciliation compares against the VENUE and therefore needs a
    session; this command reports the local half -- every intent in a
    non-terminal state -- which is the half that says whether a crash left
    something unresolved.
    """
    if not intents.exists():
        _fail(f"no intent store at {intents}", EXIT_MISUSE)
    rows: list[dict[str, Any]] = []
    for line in intents.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    by_state: dict[str, int] = {}
    for row in rows:
        state = str(row.get("state", "?"))
        by_state[state] = by_state.get(state, 0) + 1
    unresolved = {s: n for s, n in by_state.items() if s in {"pending", "unknown"}}
    payload = {"intents": len(rows), "by_state": by_state, "unresolved": unresolved}

    def _render() -> None:
        table = Table(title=f"{len(rows)} intent(s) in {intents.name}")
        table.add_column("state", style="cyan")
        table.add_column("count", justify="right")
        for state, count in sorted(by_state.items()):
            table.add_row(state, str(count))
        console.print(table)
        if unresolved:
            _warn(
                f"{sum(unresolved.values())} intent(s) are PENDING or UNKNOWN. An UNKNOWN "
                "intent may correspond to a real position at the venue. Resolve before "
                "trading again."
            )

    _emit(payload, as_json=as_json, render=_render)
    if unresolved:
        raise typer.Exit(EXIT_FAIL)


# ---------------------------------------------------------------------------
# killswitch
# ---------------------------------------------------------------------------


def _journal(path: Path) -> Any:
    from fiboki.risk.killswitch import FileKillSwitchJournal, KillSwitch

    return KillSwitch(FileKillSwitchJournal(path))


@killswitch_app.command("pause")
def killswitch_pause(
    journal: Path = typer.Option(Path("~/.fiboki/killswitch.jsonl"), "--journal"),
    reason: str = typer.Option(..., "--reason", help="Required. It goes in the journal."),
    operator: str = typer.Option("operator", "--operator", help="Who decided. Journalled."),
) -> None:
    """Block NEW risk. Existing positions and their exits are untouched."""
    from fiboki.risk.killswitch import KillSwitchMode

    switch = _journal(journal.expanduser())
    switch.activate(KillSwitchMode.PAUSE, reason=reason, operator=operator)
    _ok(f"kill switch PAUSED: {reason}")
    _warn("opens are blocked; closes and reductions still run. Use `status` to confirm.")


@killswitch_app.command("flatten")
def killswitch_flatten(
    journal: Path = typer.Option(Path("~/.fiboki/killswitch.jsonl"), "--journal"),
    reason: str = typer.Option(..., "--reason"),
    operator: str = typer.Option("operator", "--operator", help="Who decided. Journalled."),
) -> None:
    """Block new risk AND require every position to be closed.

    This command records the decision.  The flatten orders themselves are
    produced by the execution service, which is the only thing permitted to
    construct an order -- so running this without a live worker records the
    intent and closes nothing, and says so.
    """
    from fiboki.risk.killswitch import KillSwitchMode

    switch = _journal(journal.expanduser())
    switch.activate(KillSwitchMode.FLATTEN, reason=reason, operator=operator)
    _ok(f"kill switch FLATTEN recorded: {reason}")
    _warn(
        "this records the decision. Positions are closed by the live worker's next cycle "
        "through the execution service. If no worker is running, NOTHING HAS BEEN CLOSED — "
        "check `fiboki worker status` right now."
    )


@killswitch_app.command("status")
def killswitch_status(
    journal: Path = typer.Option(Path("~/.fiboki/killswitch.jsonl"), "--journal"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the current kill-switch state and its history."""
    path = journal.expanduser()
    if not path.exists():
        _ok("no kill-switch journal: the switch has never been activated")
        return
    switch = _journal(path)
    # ``state``, ``active`` and ``mode`` are PROPERTIES on KillSwitch, not
    # methods. Calling them silently would raise at exactly the moment an
    # operator most needs an answer.
    state = switch.state
    history = switch.history()
    payload = {
        "active": switch.active,
        "mode": getattr(switch.mode, "value", None),
        "blocks_new_risk": bool(getattr(state, "blocks_new_risk", False)),
        "requires_flatten": bool(getattr(state, "requires_flatten", False)),
        "events": len(history),
    }

    def _render() -> None:
        style = "red" if payload["active"] else "green"
        console.print(
            Panel(
                f"active: [bold]{payload['active']}[/bold]\n"
                f"mode: {payload['mode'] or '—'}\n"
                f"blocks new risk: {payload['blocks_new_risk']}\n"
                f"requires flatten: {payload['requires_flatten']}\n"
                f"journal events: {payload['events']}",
                title="kill switch",
                border_style=style,
            )
        )

    _emit(payload, as_json=as_json, render=_render)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point that RETURNS a code instead of raising ``SystemExit``.

    Used by ``python -m fiboki.cli`` and by the tests.  The installed
    ``fiboki`` console script goes through ``app`` directly, which exits the
    process itself.  Both paths must produce the same code, because a launchd
    ``KeepAlive`` block and a ``make`` target both read it.
    """
    import click

    try:
        # standalone_mode=False makes click RETURN the exit code rather than
        # calling sys.exit, which is the only way to test a command's code.
        result = app(args=list(argv) if argv is not None else None, standalone_mode=False)
    except (typer.Exit, click.exceptions.Exit) as exc:
        return int(getattr(exc, "exit_code", EXIT_FAIL))
    except click.ClickException as exc:
        exc.show()
        return EXIT_MISUSE
    except SystemExit as exc:  # click may still raise this for --help
        return int(exc.code or 0)
    return int(result) if isinstance(result, int) else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
