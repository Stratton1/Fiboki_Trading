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
calendar_app = typer.Typer(
    help="Economic calendar: coverage of the official fixture, blackout checks.",
    no_args_is_help=True,
)

app.add_typer(data_app, name="data")
app.add_typer(research_app, name="research")
app.add_typer(strategy_app, name="strategy")
app.add_typer(worker_app, name="worker")
app.add_typer(system_app, name="system")
app.add_typer(broker_app, name="broker")
app.add_typer(killswitch_app, name="killswitch")
app.add_typer(calendar_app, name="calendar")


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


def research_jobs_ledger_path(env: Mapping[str, str] | None = None) -> Path:
    """The research worker's durable job ledger: ``<FIBOKI_STATE_DIR>/jobs.sqlite``.

    Resolved by :func:`fiboki.core.paths.resolve_paths`, the same rule the API
    and the other workers use, so every process opens the same file.
    """
    from fiboki.core.paths import resolve_paths

    return resolve_paths(os.environ if env is None else env).state_dir / "jobs.sqlite"


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


@research_app.command("sweep-superseded")
def research_sweep_superseded(
    store_dir: Path = typer.Option(
        ..., "--store", help="Research store directory (holds research.sqlite)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be marked; write nothing."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Mark stored backtests produced by an older engine generation.

    Nothing is deleted. The research store is append-only by SQLite trigger, so
    a superseded record stays readable beside the note saying why it must not be
    quoted -- which is what you need when a decision was taken on it.
    """
    from fiboki.backtest.version import ENGINE_VERSION
    from fiboki.research.artefacts import ResearchStore

    if not store_dir.exists():
        _fail(f"no research store at {store_dir}", EXIT_MISUSE)
    with ResearchStore(store_dir) as store:
        notes = store.sweep_superseded_backtests(dry_run=dry_run)
        payload: dict[str, Any] = {
            "engine_version": ENGINE_VERSION,
            "dry_run": dry_run,
            "superseded": [
                {
                    "backtest_id": n.links["backtest_id"],
                    "strategy_id": n.links["strategy_id"],
                    "engine_version_found": n.links["engine_version_found"],
                }
                for n in notes
            ],
            "count": len(notes),
        }
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return
    verb = "would mark" if dry_run else "marked"
    typer.echo(f"engine {ENGINE_VERSION}: {verb} {len(notes)} stale backtest record(s)")
    for item in payload["superseded"]:
        typer.echo(
            f"  {item['backtest_id']}  {item['strategy_id']:32s} "
            f"from {item['engine_version_found']}"
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

    # DURABLE job ledger (<state_dir>/jobs.sqlite): queued jobs, attempts,
    # results and idempotency keys survive a restart and are visible to the
    # API. An in-memory ledger here lost every queued job on each launchd
    # restart and let a resubmitted idempotency key run twice.
    jobs_path = research_jobs_ledger_path()
    orchestrator = Orchestrator(path=jobs_path)
    if os.environ.get("FIBOKI_AGENT_CYCLES", "").strip().lower() in {"1", "true", "yes", "on"}:
        _warn(
            "FIBOKI_AGENT_CYCLES is on: the worker composes the agent research "
            "runtime and registers the deterministic job handlers in setup()."
        )
    else:
        _warn(
            "this orchestrator has no handlers registered, so the worker will idle. "
            "Set FIBOKI_AGENT_CYCLES=true to compose the research runtime; this "
            "path exists so the process, lease and heartbeat can be exercised standalone."
        )
    worker = ResearchWorker(orchestrator, store, config, dispatcher=dispatcher)
    console.print(
        Panel(
            f"worker_id: [bold]{worker.worker_id}[/bold]\n"
            f"lease:     {config.lease_name}\n"
            f"state db:  {state_db_path()}\n"
            f"jobs db:   {jobs_path}\n"
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
    stale_after: float | None = typer.Option(
        None, "--stale-after", help="Default: Settings.health (FIBOKI_WORKER_STALE_SECONDS)."
    ),
    down_after: float | None = typer.Option(
        None, "--down-after", help="Default: Settings.health (FIBOKI_WORKER_DOWN_SECONDS)."
    ),
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

    thresholds = _health_thresholds()
    if stale_after is None:
        stale_after = thresholds.worker_stale_after_seconds
    if down_after is None:
        down_after = thresholds.worker_down_after_seconds
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
        WorkerHeartbeatCheck.from_thresholds(store.heartbeats, _health_thresholds()),
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


def _health_thresholds() -> Any:
    """THE heartbeat thresholds (``Settings.health``), from the environment."""
    from fiboki.api.settings import load_settings

    return load_settings().health


def _killswitch_journal(override: Path | None) -> tuple[Path, Any]:
    """The journal to use, and the resolved paths it was checked against.

    There is no CLI default of its own any more: the journal is the one
    :func:`fiboki.core.paths.resolve_paths` gives every process for this
    environment. ``--journal`` still exists for forensic use and says loudly
    when it is not the journal gateways read.
    """
    from fiboki.core.paths import resolve_paths

    paths = resolve_paths(os.environ, cwd=Path.cwd())
    resolved = paths.killswitch_journal
    if override is None:
        return resolved, paths
    return (override.expanduser().resolve(), paths)


def _journal_notes(path: Path, paths: Any) -> None:
    console.print(f"journal: {path}")
    if path.resolve() != paths.killswitch_journal.resolve():
        _warn(
            f"--journal is NOT the journal the API and gateways resolve "
            f"({paths.killswitch_journal}). Nothing that trades reads this file."
        )
    elif not paths.state_dir_from_env:
        _warn(
            "FIBOKI_STATE_DIR is not set, so this journal was resolved from the current "
            f"directory ({Path.cwd()}). A process started in another directory resolves a "
            "different one. Export FIBOKI_STATE_DIR (the desktop services do)."
        )


def _journal(path: Path) -> Any:
    from fiboki.risk.killswitch import KillSwitch

    return KillSwitch.at_path(path)


@killswitch_app.command("pause")
def killswitch_pause(
    journal: Path | None = typer.Option(
        None, "--journal", help="Forensic override. Default: the resolved journal."
    ),
    reason: str = typer.Option(..., "--reason", help="Required. It goes in the journal."),
    operator: str = typer.Option("operator", "--operator", help="Who decided. Journalled."),
) -> None:
    """Block NEW risk. Existing positions and their exits are untouched."""
    from fiboki.risk.killswitch import KillSwitchMode

    path, paths = _killswitch_journal(journal)
    switch = _journal(path)
    switch.activate(KillSwitchMode.PAUSE, reason=reason, operator=operator)
    _ok(f"kill switch PAUSED: {reason}")
    _journal_notes(path, paths)
    _warn(
        "opens are blocked from the next decision of every gateway that reads this "
        "journal (it is re-read before each one); closes and reductions still run. "
        "Use `status` to confirm."
    )


@killswitch_app.command("flatten")
def killswitch_flatten(
    journal: Path | None = typer.Option(
        None, "--journal", help="Forensic override. Default: the resolved journal."
    ),
    reason: str = typer.Option(..., "--reason"),
    operator: str = typer.Option("operator", "--operator", help="Who decided. Journalled."),
) -> None:
    """Block new risk AND require every position to be closed.

    This command records the decision.  The flatten orders themselves are
    produced by the execution service, which is the only thing permitted to
    construct an order -- so running this without a worker records the intent
    and closes nothing, and says so.
    """
    from fiboki.risk.killswitch import KillSwitchMode

    path, paths = _killswitch_journal(journal)
    switch = _journal(path)
    switch.activate(KillSwitchMode.FLATTEN, reason=reason, operator=operator)
    _ok(f"kill switch FLATTEN recorded: {reason}")
    _journal_notes(path, paths)
    _warn(
        "this records the decision. Every gateway reading this journal refuses new risk "
        "from its next decision. Positions are closed ONLY by a running worker whose "
        "execution service reads this journal. If none is running, NOTHING HAS BEEN "
        "CLOSED: check `fiboki worker status` right now."
    )


@killswitch_app.command("status")
def killswitch_status(
    journal: Path | None = typer.Option(
        None, "--journal", help="Forensic override. Default: the resolved journal."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the current kill-switch state and its history."""
    path, paths = _killswitch_journal(journal)
    if not path.exists():
        payload_missing = {"active": False, "journal": str(path), "events": 0}
        if as_json:
            console.print_json(json.dumps(payload_missing))
            return
        _ok(f"no kill-switch journal at {path}: the switch has never been activated here")
        _journal_notes(path, paths)
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
        "journal": str(path),
        "journal_is_resolved": path.resolve() == paths.killswitch_journal.resolve(),
    }

    def _render() -> None:
        style = "red" if payload["active"] else "green"
        console.print(
            Panel(
                f"active: [bold]{payload['active']}[/bold]\n"
                f"mode: {payload['mode'] or '-'}\n"
                f"blocks new risk: {payload['blocks_new_risk']}\n"
                f"requires flatten: {payload['requires_flatten']}\n"
                f"journal events: {payload['events']}\n"
                f"journal: {payload['journal']}",
                title="kill switch",
                border_style=style,
            )
        )
        _journal_notes(path, paths)

    _emit(payload, as_json=as_json, render=_render)


# ---------------------------------------------------------------------------
# alerts and watchdog
# ---------------------------------------------------------------------------

alerts_app = typer.Typer(help="Alert channels: prove delivery on demand.", no_args_is_help=True)
watchdog_app = typer.Typer(help="The heartbeat watchdog process.", no_args_is_help=True)
app.add_typer(alerts_app, name="alerts")
app.add_typer(watchdog_app, name="watchdog")


@alerts_app.command("test")
def alerts_test(
    critical: bool = typer.Option(
        False, "--critical", help="Send at CRITICAL, exercising the durable outbox."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Send a test alert through every configured channel and report each one.

    Exits non-zero when any channel failed, or when no REMOTE channel
    (webhook, Telegram) is configured: an alert that only reaches this
    terminal and a log file does not reach the operator.
    """
    from fiboki.obs.alerts import AlertEvent, Severity, build_default_dispatcher

    dispatcher = build_default_dispatcher(source="alerts-test")
    names = dispatcher.channel_names()
    alert = dispatcher.fire(
        AlertEvent.ALERT_TEST,
        "Fiboki test alert. If you can read this on your phone, this channel works.",
        severity=Severity.CRITICAL if critical else Severity.WARNING,
        force=True,
        host=platform.node(),
    )
    failed = {name: error for name, _a, error in dispatcher.delivery_failures}
    remote = [n for n in names if n in {"webhook", "telegram"}]
    payload = {
        "sent": alert is not None,
        "severity": alert.severity.value if alert is not None else None,
        "channels": {n: ("failed: " + failed[n]) if n in failed else "ok" for n in names},
        "remote_channels": remote,
    }

    def _render() -> None:
        for name, outcome in payload["channels"].items():
            (_ok if outcome == "ok" else _warn)(f"{name}: {outcome}")
        if not remote:
            _warn(
                "no remote channel is configured (FIBOKI_TELEGRAM_BOT_TOKEN + "
                "FIBOKI_TELEGRAM_CHAT_ID, or FIBOKI_ALERT_WEBHOOK_URL): alerts do not "
                "leave this machine."
            )

    _emit(payload, as_json=as_json, render=_render)
    if failed or not remote:
        raise typer.Exit(EXIT_FAIL)


@watchdog_app.command("run")
def watchdog_run(
    interval: float = typer.Option(30.0, "--interval", help="Seconds between evaluations."),
    once: bool = typer.Option(False, "--once", help="Run a single evaluation and exit."),
    max_cycles: int = typer.Option(0, "--max-cycles", help="0 = run forever."),
    lease_ttl: float = typer.Option(120.0, "--lease-ttl"),
) -> None:
    """Run the heartbeat watchdog as a supervised worker PROCESS.

    It holds the ``watchdog`` lease (so a second copy exits 75), writes its own
    heartbeat, and every cycle evaluates every worker heartbeat against
    ``Settings.health``, fires WORKER_DOWN / HEARTBEAT_STALE, and retries the
    CRITICAL-alert outbox. Run it under launchd next to the workers it watches:
    a watchdog inside the process it watches dies with it.
    """
    from fiboki.obs.alerts import HeartbeatWatchdog, WatchdogThresholds, build_default_dispatcher
    from fiboki.workers.base import (
        CycleResult,
        Worker,
        WorkerConfig,
        expected_workers_from_env,
        watchdog_views,
    )

    store = _open_store()
    dispatcher = build_default_dispatcher(source="watchdog")
    thresholds = _health_thresholds()
    watchdog = HeartbeatWatchdog(
        dispatcher,
        lambda: watchdog_views(store),
        thresholds=WatchdogThresholds.from_health(thresholds),
        interval_seconds=interval,
        expected_workers=expected_workers_from_env(),
    )

    class _WatchdogWorker(Worker):
        def run_cycle(self) -> CycleResult:
            fired = watchdog.evaluate()
            delivered, failed = dispatcher.retry_pending()
            detail = f"alerts={len(fired)} outbox_delivered={delivered} outbox_failed={failed}"
            if fired or delivered or failed:
                return CycleResult.worked(jobs=len(fired), detail=detail)
            return CycleResult.idle(detail)

    config = WorkerConfig(
        kind="watchdog",
        idle_sleep_seconds=interval,
        busy_sleep_seconds=interval,
        lease_ttl_seconds=lease_ttl,
        max_cycles=1 if once else max_cycles,
    )
    worker = _WatchdogWorker(config, store, dispatcher=dispatcher)
    console.print(
        Panel(
            f"worker_id: [bold]{worker.worker_id}[/bold]\n"
            f"state db:  {state_db_path()}\n"
            f"stale/down: {thresholds.worker_stale_after_seconds:.0f}s / "
            f"{thresholds.worker_down_after_seconds:.0f}s\n"
            f"expected:  {', '.join(watchdog.expected_workers) or '(none: set FIBOKI_EXPECTED_WORKERS)'}\n"
            f"alerts:    {', '.join(dispatcher.channel_names())}",
            title="fiboki watchdog",
        )
    )
    code = worker.run()
    raise typer.Exit(EXIT_LEASE_HELD if code == EXIT_LEASE_HELD else code)


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------


def _utc_arg(raw: str, what: str) -> Any:
    import pandas as pd

    try:
        ts = pd.Timestamp(raw)
    except (ValueError, TypeError):
        _fail(f"{what} {raw!r} is not an ISO-8601 timestamp", EXIT_MISUSE)
    if ts.tzinfo is None:
        _fail(
            f"{what} {raw!r} has no timezone. Give it in UTC, e.g. "
            "2025-03-07T13:30:00Z; the calendar will not guess a zone.",
            EXIT_MISUSE,
        )
    return ts.tz_convert("UTC")


@calendar_app.command("status")
def calendar_status(
    start: str | None = typer.Option(None, "--start", help="ISO UTC start of a run to check coverage for."),
    end: str | None = typer.Option(None, "--end", help="ISO UTC end of a run to check coverage for."),
    fixture: Path | None = typer.Option(None, "--fixture", help="Alternative events file (default: the official fixture)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """What the official calendar actually knows. Exit 1 if it cannot cover --start..--end."""
    from fiboki.marketstate.calendar import (
        CalendarError,
        load_official_calendar,
        official_calendar_manifest,
    )

    try:
        calendar = load_official_calendar(fixture)
        manifest = official_calendar_manifest(fixture)
    except CalendarError as exc:
        _fail(str(exc))
    cov = calendar.coverage()
    per_source: dict[str, dict[str, Any]] = {}
    for e in calendar.all_events():
        row = per_source.setdefault(
            e.source, {"n": 0, "currency": e.currency, "first": e.event_time, "last": e.event_time}
        )
        row["n"] += 1
        row["last"] = e.event_time
    covered: bool | None = None
    if start is not None or end is not None:
        if start is None or end is None:
            _fail("--start and --end go together", EXIT_MISUSE)
        covered = cov.covers(_utc_arg(start, "--start"), _utc_arg(end, "--end"))
    payload = {
        "coverage": cov.to_dict(),
        "per_source": {
            k: {**v, "first": str(v["first"]), "last": str(v["last"])} for k, v in per_source.items()
        },
        "retrieved_at": manifest.get("retrieved_at"),
        "sources_skipped": manifest.get("sources_skipped", {}),
        "requested": None if covered is None else {"start": start, "end": end, "covered": covered},
    }

    def _render() -> None:
        console.print(
            f"[bold]{cov.n_events}[/bold] events  {cov.first_event} .. {cov.last_event}"
        )
        console.print(
            f"declared complete span  {cov.declared_start} .. {cov.declared_end}"
        )
        console.print(f"currencies  {', '.join(cov.currencies)}")
        console.print(f"retrieved   {manifest.get('retrieved_at')}")
        table = Table("source", "ccy", "n", "first", "last")
        for key, row in sorted(per_source.items()):
            table.add_row(key, row["currency"], str(row["n"]), str(row["first"]), str(row["last"]))
        console.print(table)
        for key, why in (manifest.get("sources_skipped") or {}).items():
            _warn(f"skipped {key}: {why}")
        if covered is True:
            _ok(f"covers {start} .. {end}")
        elif covered is False:
            err_console.print(
                f"[bold red]✗[/bold red] does NOT cover {start} .. {end}: blackout "
                "queries outside the declared span answer False, i.e. trade through events"
            )

    _emit(payload, as_json=as_json, render=_render)
    if not cov.is_populated or covered is False:
        raise typer.Exit(EXIT_FAIL)


@calendar_app.command("check")
def calendar_check(
    instant: str = typer.Argument(..., help="ISO-8601 UTC instant, e.g. 2025-03-07T13:30:00Z."),
    ccy: str = typer.Argument(..., help="Currency (USD) or a registered instrument (EURUSD)."),
    minutes_before: int = typer.Option(30, "--before", help="Blackout minutes before an event."),
    minutes_after: int = typer.Option(30, "--after", help="Blackout minutes after an event."),
    min_impact: str = typer.Option("high", "--min-impact"),
    fixture: Path | None = typer.Option(None, "--fixture"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Is INSTANT in an event blackout for CCY, and why.

    Exit 0 with an answer (in blackout or not); exit 1 when the calendar cannot
    answer honestly (instant outside the declared span, or no events for the
    currency), because "not in blackout" would then be a guess.
    """
    from fiboki.marketstate.calendar import (
        CalendarError,
        instrument_currencies,
        load_official_calendar,
    )

    ts = _utc_arg(instant, "INSTANT")
    code = ccy.upper()
    try:
        currencies = (code,) if len(code) == 3 else instrument_currencies(code)
    except Exception as exc:  # unregistered instrument
        _fail(f"{ccy!r} is neither a currency code nor a registered instrument: {exc}", EXIT_MISUSE)
    try:
        calendar = load_official_calendar(fixture)
        events = calendar.events_near_currencies(
            currencies, ts, minutes_before=minutes_before, minutes_after=minutes_after,
            min_impact=min_impact,
        )
    except (CalendarError, ValueError) as exc:
        _fail(str(exc), EXIT_MISUSE)
    cov = calendar.coverage()
    unknown: list[str] = []
    if not cov.covers(ts, ts):
        unknown.append(
            f"{ts} is outside the declared span {cov.declared_start} .. {cov.declared_end}"
        )
    missing = sorted(set(currencies) - set(cov.currencies))
    if missing:
        unknown.append(f"no events at all for {missing}")
    in_blackout = bool(events)
    payload = {
        "instant": ts.isoformat(),
        "currencies": list(currencies),
        "in_blackout": in_blackout,
        "answer_trustworthy": not unknown,
        "why_untrustworthy": unknown,
        "window": {"minutes_before": minutes_before, "minutes_after": minutes_after,
                   "min_impact": min_impact},
        "events": [
            {"event_time": e.event_time.isoformat(),
             "window_end": None if e.window_end is None else e.window_end.isoformat(),
             "currency": e.currency, "name": e.name, "source": e.source,
             "source_url": e.source_url}
            for e in events
        ],
    }

    def _render() -> None:
        if in_blackout:
            console.print(f"[bold red]IN BLACKOUT[/bold red]  {ts} for {', '.join(currencies)}")
            for e in events:
                span = "" if e.window_end is None else f" .. {e.window_end} (time not fixed)"
                console.print(f"  {e.currency} {e.name} @ {e.event_time}{span}  (source: {e.source})")
        elif unknown:
            console.print(f"[bold yellow]UNKNOWN[/bold yellow]  {ts} for {', '.join(currencies)}")
        else:
            _ok(f"not in blackout  {ts} for {', '.join(currencies)} "
                f"(±{minutes_before}/{minutes_after} min, impact >= {min_impact})")
        for why in unknown:
            _warn(why)

    _emit(payload, as_json=as_json, render=_render)
    if unknown and not in_blackout:
        raise typer.Exit(EXIT_FAIL)


# ===========================================================================
# BEGIN news + macro (Wave 3: point-in-time text and macro data)
# Self-contained block: its own sub-apps, helpers and commands. Nothing above
# this line depends on it.
# ===========================================================================

news_app = typer.Typer(
    help="Headline recorder: append-only central-bank and vendor headlines.",
    no_args_is_help=True,
)
macro_app = typer.Typer(
    help="Point-in-time macro providers: describe, fetch, as-of views.",
    no_args_is_help=True,
)
app.add_typer(news_app, name="news")
app.add_typer(macro_app, name="macro")

_NEWS_STATE_ENV = "FIBOKI_STATE_DIR"


def _news_store_path(state_dir: Path | None) -> Path:
    from fiboki.data.news import default_store_path

    base = state_dir if state_dir is not None else Path(os.environ.get(_NEWS_STATE_ENV) or "var")
    return default_store_path(base)


def _http_client() -> Any:
    """The one real HTTP client for news and macro fetches (proxy from the environment)."""
    import httpx

    return httpx.Client(timeout=30.0, follow_redirects=True)


@news_app.command("record")
def news_record(
    once: bool = typer.Option(False, "--once", help="Poll every source once and exit."),
    loop: bool = typer.Option(False, "--loop", help="Poll on a fixed cadence until SIGTERM/SIGINT."),
    interval: float = typer.Option(300.0, "--interval", help="Seconds between polls (--loop)."),
    state_dir: Path | None = typer.Option(None, "--state-dir", help="Default: $FIBOKI_STATE_DIR or ./var."),
    no_vendors: bool = typer.Option(False, "--no-vendors", help="Official feeds only, even if vendor keys are set."),
    marketaux_min_interval: float = typer.Option(900.0, "--marketaux-min-interval", help="Seconds between Marketaux calls (plan quota)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Record headlines. observed_at is this process's UTC clock at each poll.

    Exit 0 when the poll(s) ran, even if some feeds failed (failures are in the
    poll log and printed); exit 1 when EVERY enabled source failed in the last
    poll, because that is a recorder recording nothing.
    """
    import signal
    from datetime import UTC, datetime

    from fiboki.data.news import HeadlineStore, NewsRecorder, run_loop
    from fiboki.data.news.sources import vendor_clients_from_env

    if once == loop:
        _fail("choose exactly one of --once or --loop", EXIT_MISUSE)
    if interval < 30:
        _fail("--interval below 30 s is impolite to the publishers; refusing", EXIT_MISUSE)
    path = _news_store_path(state_dir)
    client = _http_client()
    extra: list[Any] = []
    disabled: dict[str, str] = {}
    if no_vendors:
        disabled = {"finnhub": "--no-vendors", "marketaux": "--no-vendors"}
    else:
        extra, disabled = vendor_clients_from_env(
            client, marketaux_min_interval_s=marketaux_min_interval
        )
    store = HeadlineStore(path)
    recorder = NewsRecorder.official(store, client, extra_readers=extra, disabled=disabled)
    last: dict[str, Any] = {}

    def _report(result: Any) -> None:
        last["result"] = result
        d = result.to_dict()
        if as_json:
            console.print_json(json.dumps(d, default=str))
            return
        console.print(
            f"{d['started_at']}  new={d['new']} fetched={d['fetched']} dup={d['duplicate']} "
            f"revised={d['revised']} rejected={d['rejected']} errors={len(d['errors'])}"
        )
        for key, why in d["errors"].items():
            _warn(f"{key}: {why}")

    def _now() -> datetime:
        return datetime.now(tz=UTC)

    try:
        if once:
            _report(recorder.poll_once(_now()))
        else:
            stop = {"flag": False}

            def _stop(*_: Any) -> None:
                stop["flag"] = True

            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)
            console.print(f"recording to {path} every {interval:.0f}s; SIGTERM to stop")
            run_loop(recorder, interval_s=interval, clock=_now,
                     should_stop=lambda: stop["flag"], on_poll=_report)
    finally:
        store.close()
        client.close()
    result = last.get("result")
    if result is not None:
        attempted = [v for v in result.per_feed.values() if "fetched" in v or "error" in v]
        if attempted and all("error" in v for v in attempted):
            raise typer.Exit(EXIT_FAIL)


@news_app.command("status")
def news_status(
    state_dir: Path | None = typer.Option(None, "--state-dir", help="Default: $FIBOKI_STATE_DIR or ./var."),
    gap_threshold: float = typer.Option(900.0, "--gap-threshold", help="Seconds between polls that count as a gap."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Rows, per-source counts, last observed_at, feed health and capture gaps.

    Exit 1 when there is no store, no poll has ever run, or the last poll is
    older than --gap-threshold: a recorder that stopped is not healthy.
    """
    from datetime import UTC, datetime, timedelta

    from fiboki.data.news import HeadlineStore, gaps_in_polls

    path = _news_store_path(state_dir)
    if not path.exists():
        _fail(f"no headline store at {path}; run `fiboki news record --once` first")
    with HeadlineStore(path) as store:
        st = store.status()
    polls = st["polls"]
    now = datetime.now(tz=UTC)
    threshold = timedelta(seconds=gap_threshold)
    starts = [p[0] for p in polls]
    gaps = gaps_in_polls(starts, threshold=threshold, until=now)
    feed_health: dict[str, dict[str, Any]] = {}
    for started, _finished, outcome in polls:
        for key, res in (outcome.get("per_feed") or {}).items():
            row = feed_health.setdefault(key, {"last_ok": None, "last_error": None, "error": None})
            if "error" in res:
                row["last_error"], row["error"] = started.isoformat(), res["error"]
            elif "fetched" in res:
                row["last_ok"], row["error"] = started.isoformat(), None
            elif "disabled" in res:
                row["disabled"] = res["disabled"]
    last_poll = starts[-1] if starts else None
    stale = last_poll is None or (now - last_poll) > threshold
    payload = {
        "store": str(path),
        "rows": st["rows"],
        "revisions": st["revisions"],
        "per_source": st["per_source"],
        "polls": len(polls),
        "first_poll": None if not starts else starts[0].isoformat(),
        "last_poll": None if last_poll is None else last_poll.isoformat(),
        "stale": stale,
        "gap_threshold_s": gap_threshold,
        "gaps": gaps,
        "feeds": feed_health,
        "feed_sources": st["feeds"],
    }

    def _render() -> None:
        console.print(f"[bold]{st['rows']}[/bold] headlines ({st['revisions']} revisions) in {path}")
        console.print(f"polls {len(polls)}  first {payload['first_poll']}  last {payload['last_poll']}")
        table = Table("source", "rows", "first observed_at", "last observed_at")
        for src, row in st["per_source"].items():
            table.add_row(src, str(row["rows"]), row["first_observed_at"], row["last_observed_at"])
        console.print(table)
        ft = Table("feed", "last ok", "last error", "state")
        for key, row in sorted(feed_health.items()):
            state = row.get("disabled") or ("FAILING: " + row["error"] if row["error"] else "ok")
            ft.add_row(key, str(row["last_ok"]), str(row["last_error"]), state)
        console.print(ft)
        for g in gaps:
            _warn(f"gap {g['from']} .. {g['to']} ({g['seconds']:.0f}s)")
        if stale:
            err_console.print("[bold red]✗[/bold red] recorder is not running (last poll older than threshold)")
        else:
            _ok("recorder polled within the threshold")

    _emit(payload, as_json=as_json, render=_render)
    if stale:
        raise typer.Exit(EXIT_FAIL)


@macro_app.command("describe")
def macro_describe(
    name: str | None = typer.Argument(None, help="Provider name; omit for all."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Source, licence, attribution, point-in-time semantics and limitations."""
    from fiboki.data.providers import MACRO_PROVIDERS

    names = sorted(MACRO_PROVIDERS) if name is None else [name]
    for n in names:
        if n not in MACRO_PROVIDERS:
            _fail(f"unknown macro provider {n!r}; known: {sorted(MACRO_PROVIDERS)}", EXIT_MISUSE)
    descriptors = {n: MACRO_PROVIDERS[n]().descriptor for n in names}
    _emit(
        {n: d.to_dict() for n, d in descriptors.items()},
        as_json=as_json,
        render=lambda: [console.print(d.render() + "\n") for d in descriptors.values()],
    )


def _macro_date(raw: str | None, what: str) -> Any:
    from datetime import date

    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        _fail(f"{what} {raw!r} is not YYYY-MM-DD", EXIT_MISUSE)


@macro_app.command("fetch")
def macro_fetch(
    provider: str = typer.Argument(..., help="alfred | cftc_cot | ecb_sdmx | boe_iadb | ons | nyfed"),
    series: list[str] = typer.Option(..., "--series", help=(
        "alfred: FRED id; cftc_cot: contract code; ecb_sdmx: FLOW/KEY; boe_iadb: IADB code; "
        "ons: CDID/DATASET; nyfed: sofr|bgcr|tgcr|effr|obfr|repo. Repeatable where the source allows.")),
    start: str | None = typer.Option(None, "--start", help="YYYY-MM-DD (required: boe_iadb, nyfed)."),
    end: str | None = typer.Option(None, "--end", help="YYYY-MM-DD (required: nyfed)."),
    include_previous: int = typer.Option(0, "--include-previous", help="ons: archived versions to fetch."),
    as_of: str | None = typer.Option(None, "--as-of", help="ISO UTC instant: print the point-in-time view."),
    data_root: Path | None = typer.Option(None, "--data-root", help="Default: $FIBOKI_DATA_ROOT. Omit both to fetch without storing."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch one dataset, write it content-addressed, optionally show an as-of view."""
    from fiboki.data.providers import (
        AlfredProvider,
        BoeIadbProvider,
        CftcCotProvider,
        EcbSdmxProvider,
        MacroDatasetStore,
        NyFedMarketsProvider,
        OnsTimeseriesProvider,
    )
    from fiboki.data.providers.base import ProviderError

    s0, e0 = _macro_date(start, "--start"), _macro_date(end, "--end")
    client = _http_client()
    try:
        if provider == "alfred":
            if len(series) != 1:
                _fail("alfred takes exactly one --series", EXIT_MISUSE)
            ds = AlfredProvider.from_env(client).fetch(series[0])
        elif provider == "cftc_cot":
            ds = CftcCotProvider(http_client=client).fetch(tuple(series), since=s0)
        elif provider == "ecb_sdmx":
            if len(series) != 1 or "/" not in series[0]:
                _fail("ecb_sdmx takes one --series FLOW/KEY, e.g. EXR/D.USD.EUR.SP00.A", EXIT_MISUSE)
            flow, key = series[0].split("/", 1)
            ds = EcbSdmxProvider(http_client=client).fetch(flow, key, start_period=start, end_period=end)
        elif provider == "boe_iadb":
            if s0 is None:
                _fail("boe_iadb needs --start", EXIT_MISUSE)
            ds = BoeIadbProvider(http_client=client).fetch(tuple(series), start=s0, end=e0)
        elif provider == "ons":
            if len(series) != 1 or "/" not in series[0]:
                _fail("ons takes one --series CDID/DATASET, e.g. D7G7/MM23", EXIT_MISUSE)
            cdid, dataset = series[0].split("/", 1)
            ds = OnsTimeseriesProvider(http_client=client).fetch(
                cdid, dataset, include_previous=include_previous > 0,
                max_previous=include_previous or None,
            )
        elif provider == "nyfed":
            if s0 is None or e0 is None or len(series) != 1:
                _fail("nyfed takes one --series plus --start and --end", EXIT_MISUSE)
            ny = NyFedMarketsProvider(http_client=client)
            ds = ny.fetch_repo(start=s0, end=e0) if series[0] == "repo" else ny.fetch(series[0], start=s0, end=e0)
        else:
            _fail(f"unknown macro provider {provider!r}", EXIT_MISUSE)
    except ProviderError as exc:
        _fail(f"{type(exc).__name__}: {exc}")
    finally:
        client.close()

    root = data_root if data_root is not None else data_root_env()
    stored: dict[str, Any] = {"stored": False, "reason": "no --data-root and no FIBOKI_DATA_ROOT"}
    if root is not None:
        try:
            vid, where, created = MacroDatasetStore(root).write(ds)
        except Exception as exc:
            _fail(f"could not store: {exc}")
        stored = {"stored": True, "version_id": vid, "path": str(where), "created": created}
    view = None
    if as_of is not None:
        view = ds.as_of(_utc_arg(as_of, "--as-of"))
    payload = {
        "provider": ds.provider,
        "dataset_key": ds.dataset_key,
        "version_id": ds.version_id,
        "rows": len(ds.frame),
        "availability": ds.frame["availability_basis"].value_counts().to_dict(),
        "report": ds.report,
        "attribution": ds.descriptor.attribution,
        **stored,
        "as_of": None if view is None else {
            "instant": as_of,
            "rows": len(view),
            "latest": view.tail(10)[["series_id", "period", "value", "available_at"]]
            .astype(str).to_dict(orient="records"),
        },
    }

    def _render() -> None:
        console.print(f"[bold]{ds.provider}[/bold] {ds.dataset_key}  {ds.version_id}  rows={len(ds.frame)}")
        console.print(f"availability  {payload['availability']}")
        console.print(f"attribution   {ds.descriptor.attribution}")
        if stored["stored"]:
            _ok(f"stored at {stored['path']} ({'new' if stored['created'] else 'already present'})")
        else:
            _warn(f"not stored: {stored['reason']}")
        if view is not None:
            console.print(f"as of {as_of}: {len(view)} current values")
            console.print(view.tail(10)[["series_id", "period", "value", "available_at"]].to_string(index=False))

    _emit(payload, as_json=as_json, render=_render)


def data_root_env() -> Path | None:
    """``FIBOKI_DATA_ROOT`` if set (the same rule as ``data_root()`` above)."""
    return data_root()


# ===========================================================================
# END news + macro
# ===========================================================================


# ===========================================================================
# BEGIN doctor (desktop readiness: `fiboki doctor`)
# Self-contained block: its own sub-app, host seam, checks and commands.
# Nothing above this line depends on it. `fiboki system doctor` is unchanged;
# this is the wider desktop check (toolchain, services, ports, local model).
# ===========================================================================

doctor_app = typer.Typer(
    help="Desktop readiness: toolchain, data, services, ports and the local model.",
    invoke_without_command=True,
)
app.add_typer(doctor_app, name="doctor")

#: The launchd labels scripts/launchd-install.sh installs (deploy/launchd/).
DOCTOR_LAUNCHD_LABELS: tuple[str, ...] = tuple(
    f"uk.fiboki.{name}" for name in ("api", "worker", "web", "news", "llama", "paper")
)
#: Installed only on request (not in the installer's default list): reported,
#: but "not loaded" is not a warning. Loaded and not running still is.
DOCTOR_LAUNCHD_OPTIONAL: frozenset[str] = frozenset({"uk.fiboki.paper"})
#: Ports the desktop services bind: API, web, llama-server.
DOCTOR_PORTS: dict[int, str] = {8000: "api", 3000: "web", 8080: "llama-server"}
#: Pins the charter calls out by name: a drift in any of these is a FAIL.
DOCTOR_NAMED_PINS: tuple[str, ...] = ("numpy", "pandas", "scipy", "click")
_DOCTOR_LLAMA_URL = "http://127.0.0.1:8080"
_DOCTOR_OLLAMA_URL = "http://127.0.0.1:11434"


class DoctorStatus:
    """The three verdicts. Plain strings so ``--json`` needs no encoder."""

    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    ALL = ("OK", "WARN", "FAIL")


@dataclass
class DoctorCheck:
    """One check that was actually performed, what it saw, and the fix."""

    name: str
    status: str
    detail: str
    fix: str = ""
    data: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
            "data": self.data or {},
        }


class DoctorHost:
    """Everything the doctor touches outside Python, behind one seam.

    Tests replace this with a fake; the real one runs short, read-only
    commands with a timeout and never raises for a missing binary.
    """

    def __init__(
        self,
        repo: Path | None = None,
        env: Mapping[str, str] | None = None,
        *,
        env_file: bool = True,
    ) -> None:
        from fiboki.core.env_file import EnvFile, env_file_path, read_env_file

        self.repo = (repo or repo_root()).resolve()
        self.env: dict[str, str] = dict(os.environ if env is None else env)
        # The services read ~/.fiboki/env (scripts/fiboki-service.sh); a doctor
        # run from a bare shell must see the same values or it reports problems
        # the services do not have. The process environment wins; the file
        # fills what is unset. Recorded so the environment check can say so.
        self.env_file: EnvFile = EnvFile(path=None)
        self.env_file_applied: tuple[str, ...] = ()
        if env_file:
            self.env_file = read_env_file(env_file_path(self.env))
            applied = [k for k in self.env_file.values if not self.env.get(k)]
            for key in applied:
                self.env[key] = self.env_file.values[key]
            self.env_file_applied = tuple(sorted(applied))
        self.system = platform.system()
        self.machine = platform.machine()
        self.python_version: tuple[int, int, int] = tuple(sys.version_info[:3])  # type: ignore[assignment]
        self.executable = sys.executable
        self.prefix = sys.prefix
        self.uid = os.getuid() if hasattr(os, "getuid") else 0

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> tuple[int, str]:
        import subprocess

        try:
            proc = subprocess.run(
                list(argv), capture_output=True, text=True, timeout=timeout, check=False
            )
        except FileNotFoundError:
            return 127, ""
        except (subprocess.TimeoutExpired, OSError) as exc:
            return 124, str(exc)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def dist_version(self, name: str) -> str | None:
        from importlib import metadata

        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None

    def disk_free(self, path: Path) -> int:
        import shutil

        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return shutil.disk_usage(probe).free

    def port_open(self, port: int) -> bool:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def http_client(self) -> Any:
        from fiboki.agents.providers import ollama_http_client

        return ollama_http_client(timeout=600.0, connect_timeout=2.0)

    def now(self) -> Any:
        from datetime import UTC, datetime

        return datetime.now(tz=UTC)


def _doctor_guard(name: str, fn: Callable[[DoctorHost], list[DoctorCheck]], host: DoctorHost
                  ) -> list[DoctorCheck]:
    """A check that crashes is a FAIL with its exception, never a missing row."""
    try:
        return fn(host)
    except Exception as exc:  # the report must survive any single broken check
        return [DoctorCheck(name, DoctorStatus.FAIL, f"check crashed: {type(exc).__name__}: {exc}",
                            "Report this; the check itself is broken, not necessarily the system.")]


def _doctor_paths(host: DoctorHost) -> dict[str, tuple[Path, str]]:
    """Resolve state paths the way the desktop services do (scripts/fiboki-service.sh).

    Each value is ``(path, source)`` where source is ``env`` or ``desktop default``.
    """
    env = host.env

    def pick(name: str, default: Path) -> tuple[Path, str]:
        raw = env.get(name, "").strip()
        return (Path(raw).expanduser(), "env") if raw else (default, "desktop default")

    state_dir, state_src = pick("FIBOKI_STATE_DIR", host.repo / "var")
    home = Path(env.get("FIBOKI_HOME") or (Path.home() / ".fiboki")).expanduser()
    return {
        "state_dir": (state_dir, state_src),
        "home": (home, "env" if env.get("FIBOKI_HOME") else "desktop default"),
        "data_root": pick("FIBOKI_DATA_ROOT", state_dir / "datastore"),
        "experiment_db": pick("FIBOKI_EXPERIMENT_DB", state_dir / "experiments.sqlite"),
        "paper_root": pick("FIBOKI_PAPER_ROOT", state_dir / "paper"),
        "state_db": pick("FIBOKI_STATE_DB", home / "state.db"),
        "news_store": (state_dir / "news" / "headlines.sqlite", state_src),
    }


def _read_pins(repo: Path) -> dict[str, str]:
    import tomllib

    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pins: dict[str, str] = {}
    for spec in project.get("dependencies", []):
        name, sep, version = str(spec).partition("==")
        if sep:
            pins[name.strip().lower()] = version.strip()
    return pins


def _check_python(host: DoctorHost) -> list[DoctorCheck]:
    out: list[DoctorCheck] = []
    major, minor, micro = host.python_version
    ok = (major, minor) in ((3, 11), (3, 12))
    out.append(DoctorCheck(
        "python", DoctorStatus.OK if ok else DoctorStatus.FAIL,
        f"{major}.{minor}.{micro} at {host.executable}",
        "" if ok else "pyproject requires >=3.11,<3.13. Run scripts/desktop-install.sh, which "
        "builds .venv from Homebrew python@3.11.",
    ))
    pins = _read_pins(host.repo)
    drift, missing = [], []
    for name, want in sorted(pins.items()):
        got = host.dist_version(name)
        if got is None:
            missing.append(name)
        elif got != want:
            drift.append(f"{name} {got} != {want}")
    named = {n: host.dist_version(n) for n in DOCTOR_NAMED_PINS}
    bad = drift or missing
    detail = "; ".join(drift + [f"MISSING {m}" for m in missing]) or (
        f"{len(pins)} exact pins match ("
        + ", ".join(f"{n} {v}" for n, v in named.items()) + ")"
    )
    out.append(DoctorCheck(
        "pins", DoctorStatus.FAIL if bad else DoctorStatus.OK, detail,
        "" if not bad else ".venv/bin/pip install -e '.[dev]' -c deploy/constraints.txt "
        "(scripts/desktop-install.sh does this). A drifted numerical pin changes stored results.",
        {"pins": pins, "installed": {n: host.dist_version(n) for n in pins}},
    ))
    return out


def _check_venv(host: DoctorHost) -> list[DoctorCheck]:
    venv = host.repo / ".venv"
    cfg = venv / "pyvenv.cfg"
    if not (venv / "bin" / "python").exists() or not cfg.exists():
        return [DoctorCheck(".venv", DoctorStatus.FAIL, f"{venv} missing or incomplete",
                            "scripts/desktop-install.sh (a .venv is rebuilt, never copied).")]
    values: dict[str, str] = {}
    for line in cfg.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    home = Path(values.get("home", ""))
    version = values.get("version_info") or values.get("version") or "?"
    problems: list[str] = []
    if not home.exists():
        problems.append(f"its base interpreter {home} does not exist (copied from another machine?)")
    if not version.startswith(("3.11", "3.12")):
        problems.append(f"built with Python {version}")
    running_in = Path(host.prefix).resolve() == venv.resolve()
    detail = f"python {version}, base {home}" + ("" if running_in else "; doctor is NOT running from it")
    if problems:
        return [DoctorCheck(".venv", DoctorStatus.FAIL, "; ".join(problems),
                            f"mv {venv} {venv}.broken && scripts/desktop-install.sh")]
    if not running_in:
        return [DoctorCheck(".venv", DoctorStatus.WARN, detail,
                            f"Run the doctor as {venv}/bin/fiboki doctor so pins are read from the venv.")]
    return [DoctorCheck(".venv", DoctorStatus.OK, detail)]


def _parse_semver(text: str) -> tuple[int, int, int] | None:
    import re

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(g) for g in match.groups()) if match else None  # type: ignore[return-value]


def _check_node(host: DoctorHost) -> list[DoctorCheck]:
    rc_node, node_out = host.run(["node", "--version"])
    rc_npm, npm_out = host.run(["npm", "--version"])
    if rc_node != 0 or rc_npm != 0:
        return [DoctorCheck("node/npm", DoctorStatus.FAIL,
                            f"node rc={rc_node} npm rc={rc_npm}", "brew install node")]
    node_v = _parse_semver(node_out)
    required = None
    next_pkg = host.repo / "apps" / "web" / "node_modules" / "next" / "package.json"
    if next_pkg.exists():
        engines = json.loads(next_pkg.read_text(encoding="utf-8")).get("engines") or {}
        required = str(engines.get("node") or "") or None
    detail = f"node {node_out.strip()}, npm {npm_out.strip()}"
    if required and node_v is not None:
        floor = _parse_semver(required)
        if floor is not None and required.strip().startswith(">=") and node_v < floor:
            return [DoctorCheck("node/npm", DoctorStatus.FAIL,
                                f"{detail}; next requires node {required}", "brew upgrade node")]
        detail += f" (next requires {required})"
    return [DoctorCheck("node/npm", DoctorStatus.OK, detail)]


def _expected_swc(system: str, machine: str) -> set[str]:
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(machine, machine)
    if system == "Darwin":
        return {f"swc-darwin-{arch}"}
    if system == "Linux":
        return {f"swc-linux-{arch}-gnu", f"swc-linux-{arch}-musl"}
    if system == "Windows":
        return {f"swc-win32-{arch}-msvc"}
    return set()


def _check_node_modules(host: DoctorHost) -> list[DoctorCheck]:
    web = host.repo / "apps" / "web"
    modules = web / "node_modules"
    if not modules.is_dir():
        return [DoctorCheck("web node_modules", DoctorStatus.FAIL, f"{modules} missing",
                            "cd apps/web && npm ci")]
    next_dir = modules / "@next"
    present = sorted(p.name for p in next_dir.iterdir() if p.name.startswith("swc-")) if next_dir.is_dir() else []
    expected = _expected_swc(host.system, host.machine)
    data = {"swc_present": present, "swc_expected": sorted(expected)}
    if present and expected and not (set(present) & expected):
        return [DoctorCheck(
            "web node_modules", DoctorStatus.FAIL,
            f"built for another platform: {', '.join(present)}; this is {host.system} {host.machine}",
            "node_modules is not portable. cd apps/web && rm -rf node_modules && npm ci", data)]
    if not present:
        return [DoctorCheck("web node_modules", DoctorStatus.WARN,
                            "no @next/swc-* native binary (next falls back to a slower path)",
                            "cd apps/web && npm ci", data)]
    lock = web / "package-lock.json"
    stamp = modules / ".package-lock.json"
    if lock.exists() and stamp.exists() and lock.stat().st_mtime > stamp.stat().st_mtime:
        return [DoctorCheck("web node_modules", DoctorStatus.WARN,
                            "package-lock.json is newer than the installed tree",
                            "cd apps/web && npm ci", data)]
    return [DoctorCheck("web node_modules", DoctorStatus.OK, f"native: {', '.join(present)}", data=data)]


def _check_git(host: DoctorHost) -> list[DoctorCheck]:
    rc, branch = host.run(["git", "-C", str(host.repo), "rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        return [DoctorCheck("git", DoctorStatus.WARN, f"not a git checkout ({host.repo})",
                            "Clone the repository rather than copying a tree; the build SHA is "
                            "stamped from git.")]
    _, head = host.run(["git", "-C", str(host.repo), "rev-parse", "--short", "HEAD"])
    _, status = host.run(["git", "-C", str(host.repo), "status", "--porcelain"])
    dirty = [line for line in status.splitlines() if line.strip()]
    detail = f"{branch.strip()} @ {head.strip()}" + (f", {len(dirty)} uncommitted change(s)" if dirty else ", clean")
    return [DoctorCheck(
        "git", DoctorStatus.WARN if dirty else DoctorStatus.OK, detail,
        "Commit or discard local changes before a migration, or results carry an unrecorded code state."
        if dirty else "",
        {"branch": branch.strip(), "head": head.strip(), "dirty": len(dirty)},
    )]


def _check_env(host: DoctorHost) -> list[DoctorCheck]:
    from fiboki.api.settings import ENV_REGISTRY, warn_unknown_env
    from fiboki.core.enums import ExecutionMode

    out: list[DoctorCheck] = []
    raw_mode = host.env.get("FIBOKI_EXECUTION_MODE", "paper").strip().lower() or "paper"
    try:
        mode = ExecutionMode(raw_mode)
    except ValueError:
        return [DoctorCheck("environment", DoctorStatus.FAIL,
                            f"FIBOKI_EXECUTION_MODE={raw_mode!r} is not a mode",
                            "export FIBOKI_EXECUTION_MODE=paper")]
    unknown = warn_unknown_env(host.env)
    missing = sorted(
        v.name for v in ENV_REGISTRY if mode in v.required_in_modes and not host.env.get(v.name)
    )
    strict = mode in (ExecutionMode.DEMO, ExecutionMode.LIVE)
    if missing or (unknown and strict):
        status = DoctorStatus.FAIL
    elif unknown:
        status = DoctorStatus.WARN
    else:
        status = DoctorStatus.OK
    parts = [f"mode {mode.value}"]
    env_file = host.env_file
    if env_file.exists:
        parts.append(f"{len(host.env_file_applied)} of {len(env_file.values)} values from {env_file.path}")
        if env_file.malformed:
            status = max(status, DoctorStatus.WARN, key=DoctorStatus.ALL.index)
            parts.append(f"malformed lines ignored: {', '.join(env_file.malformed)}")
    elif env_file.path is not None:
        parts.append(f"no {env_file.path}")
    if unknown:
        parts.append(f"unknown: {', '.join(unknown)}")
    if missing:
        parts.append(f"missing in {mode.value}: {', '.join(missing)}")
    fix = ""
    if unknown or missing:
        fix = ("Unknown FIBOKI_* names are settings silently left at their default (a startup "
               "error in demo/live). Remove them or declare them in fiboki.api.settings.ENV_REGISTRY.")
    elif env_file.malformed:
        fix = f"Every line in {env_file.path} must be KEY=VALUE with KEY in [A-Z0-9_]; the services skip the rest."
    out.append(DoctorCheck(
        "environment", status, "; ".join(parts), fix,
        {"unknown": unknown, "missing_in_mode": missing, "mode": mode.value,
         "env_file": str(env_file.path) if env_file.exists else None,
         "env_file_applied": list(host.env_file_applied),
         "env_file_malformed": list(env_file.malformed)},
    ))
    live_flag = host.env.get("FIBOKI_LIVE_EXECUTION_ENABLED", "").strip().lower()
    armed = bool(host.env.get("FIBOKI_LIVE_RUNTIME_ARMED") or host.env.get("FIBOKI_OANDA_LIVE_RUNTIME"))
    live = live_flag in {"1", "true", "yes", "on"} or armed or mode is ExecutionMode.LIVE
    out.append(DoctorCheck(
        "live controls", DoctorStatus.FAIL if live else DoctorStatus.OK,
        "a live control is set in this environment" if live else "none set (desktop runs paper)",
        "Unset FIBOKI_LIVE_EXECUTION_ENABLED, FIBOKI_LIVE_RUNTIME_ARMED and "
        "FIBOKI_OANDA_LIVE_RUNTIME. The desktop deployment is paper only." if live else "",
    ))
    return out


def _check_operator_hashes(host: DoctorHost) -> list[DoctorCheck]:
    """Flag operator entries still on the legacy unsalted SHA-256 hash.

    Reads ``FIBOKI_OPERATORS`` (user:role:hash) and classifies each hash with
    :func:`fiboki.api.routers.auth.is_legacy_hash`. Never prints a hash.
    """
    from fiboki.api.routers.auth import is_legacy_hash

    raw = host.env.get("FIBOKI_OPERATORS", "").strip()
    if not raw:
        return [DoctorCheck("operator hashes", DoctorStatus.WARN,
                            "FIBOKI_OPERATORS is not set: nobody can sign in",
                            "Add user:role:scrypt$... entries to ~/.fiboki/env (see "
                            "docs/v2/SECURITY_MODEL.md).")]
    users: list[str] = []
    legacy: list[str] = []
    malformed = 0
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if not entry.strip():
            continue
        if len(parts) != 3:
            malformed += 1
            continue
        user = parts[0].strip().lower()
        users.append(user)
        if is_legacy_hash(parts[2].strip()):
            legacy.append(user)
    problems: list[str] = []
    if legacy:
        problems.append(f"legacy unsalted sha256 hash for: {', '.join(sorted(legacy))}")
    if malformed:
        problems.append(f"{malformed} malformed entr{'y' if malformed == 1 else 'ies'} (ignored at login)")
    if problems:
        return [DoctorCheck(
            "operator hashes", DoctorStatus.WARN, "; ".join(problems),
            "Rotate to scrypt: python -c \"from fiboki.api.routers.auth import hash_password as h; "
            "print(h(input()))\" and replace the entry in ~/.fiboki/env.",
            {"operators": len(users), "legacy": sorted(legacy), "malformed": malformed},
        )]
    return [DoctorCheck("operator hashes", DoctorStatus.OK, f"{len(users)} operator(s), all scrypt",
                        data={"operators": len(users), "legacy": [], "malformed": 0})]


def _check_data_root(host: DoctorHost) -> list[DoctorCheck]:
    root, source = _doctor_paths(host)["data_root"]
    from fiboki.data.store import ROOT_MARKER, DataStore

    if not root.is_dir():
        return [DoctorCheck("data root", DoctorStatus.FAIL, f"{root} ({source}) does not exist",
                            "scripts/migrate-store.sh, or restore a backup that includes the datastore")]
    if not (root / ROOT_MARKER).exists():
        return [DoctorCheck("data root", DoctorStatus.FAIL, f"{root} ({source}) has no {ROOT_MARKER}",
                            "Point FIBOKI_DATA_ROOT at the MIGRATED store; never mark a directory "
                            "by hand to silence this.")]
    if not os.access(root, os.R_OK | os.X_OK):
        return [DoctorCheck("data root", DoctorStatus.FAIL, f"{root} is not readable", f"chmod u+rx {root}")]
    inventory = DataStore(root).inventory()
    if inventory.empty:
        return [DoctorCheck("data root", DoctorStatus.WARN, f"{root} is marked but holds no dataset",
                            "scripts/migrate-store.sh")]
    per_instrument = {
        str(k): int(v) for k, v in inventory.groupby("instrument")["rows"].sum().sort_index().items()
    }
    top = ", ".join(f"{k} {v:,}" for k, v in list(per_instrument.items())[:6])
    more = "" if len(per_instrument) <= 6 else f", +{len(per_instrument) - 6} more"
    return [DoctorCheck(
        "data root", DoctorStatus.OK,
        f"{root}: {len(per_instrument)} instruments, {sum(per_instrument.values()):,} bars ({top}{more})",
        data={"root": str(root), "bars_per_instrument": per_instrument},
    )]


def _sqlite_ro(path: Path) -> Any:
    import sqlite3

    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)


def _check_ledger(host: DoctorHost) -> list[DoctorCheck]:
    path, source = _doctor_paths(host)["experiment_db"]
    if not path.exists() or path.stat().st_size == 0:
        return [DoctorCheck("experiment ledger", DoctorStatus.FAIL, f"{path} ({source}) missing or empty",
                            ".venv/bin/python scripts/build_research_ledger.py, or restore a backup")]
    with _sqlite_ro(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        count = conn.execute("SELECT COUNT(*) FROM experiment").fetchone()[0] if "experiment" in tables else None
    if count is None:
        return [DoctorCheck("experiment ledger", DoctorStatus.FAIL, f"{path} has no experiment table",
                            "This is not an experiment ledger; check FIBOKI_EXPERIMENT_DB.")]
    return [DoctorCheck("experiment ledger", DoctorStatus.OK if count else DoctorStatus.WARN,
                        f"{path}: {count} experiments", "" if count else
                        ".venv/bin/python scripts/build_research_ledger.py", {"experiments": count})]


def _check_paper(host: DoctorHost) -> list[DoctorCheck]:
    root, source = _doctor_paths(host)["paper_root"]
    if not root.is_dir():
        return [DoctorCheck("paper journal", DoctorStatus.WARN, f"{root} ({source}) does not exist",
                            "Until a paper session is persisted here the trading pages serve the "
                            "labelled seed fixture. scripts/run_paper_session.py writes one.")]
    sessions = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                and ((p / "summary.json").exists() or (p / "trades.csv").exists())]
    return [DoctorCheck("paper journal", DoctorStatus.OK if sessions else DoctorStatus.WARN,
                        f"{root}: {len(sessions)} session(s)",
                        "" if sessions else "scripts/run_paper_session.py writes one.",
                        {"sessions": len(sessions)})]


def _check_heartbeat(host: DoctorHost) -> list[DoctorCheck]:
    from fiboki.api.settings import health_thresholds_from_env

    path, source = _doctor_paths(host)["state_db"]
    # Settings.health: the SAME threshold `fiboki worker status`, the health
    # page and the watchdog use (FIBOKI_WORKER_STALE_SECONDS, validated).
    stale_after = health_thresholds_from_env(host.env).worker_stale_after_seconds
    if not path.exists():
        return [DoctorCheck("worker heartbeat", DoctorStatus.FAIL,
                            f"{path} ({source}) does not exist: no worker has ever beaten",
                            "Start the research worker (launchctl kickstart gui/$(id -u)/uk.fiboki.worker).")]
    from fiboki.workers.base import WorkerStore

    store = WorkerStore.sqlite_at(path, create=False)
    try:
        views = store.heartbeats(now=host.now())
    finally:
        store.close()
    if not views:
        return [DoctorCheck("worker heartbeat", DoctorStatus.FAIL, "no heartbeat row: age unknown, not 0",
                            "Start the research worker.")]
    freshest = min(views, key=lambda v: v.age_seconds)
    status = DoctorStatus.OK if freshest.age_seconds <= stale_after else DoctorStatus.FAIL
    return [DoctorCheck(
        "worker heartbeat", status,
        f"{freshest.worker_id} beat {freshest.age_seconds:.0f}s ago (stale after {stale_after:.0f}s)",
        "" if status == DoctorStatus.OK else "Check `launchctl print gui/$(id -u)/uk.fiboki.worker` "
        "and var/logs/worker.log; exit 75 means another holder has the lease.",
        {v.worker_id: round(v.age_seconds, 1) for v in views},
    )]


def _check_news(host: DoctorHost) -> list[DoctorCheck]:
    path, _ = _doctor_paths(host)["news_store"]
    if not path.exists():
        return [DoctorCheck("news store", DoctorStatus.WARN, f"{path} does not exist",
                            "Optional. Load uk.fiboki.news or run `fiboki news record --once`.")]
    from datetime import UTC, datetime

    with _sqlite_ro(path) as conn:
        row = conn.execute("SELECT MAX(started_at) FROM poll_log").fetchone()
    last = None
    if row and row[0]:
        # The store writes fixed-width UTC text (fiboki.data.news.store.to_utc_text).
        last = datetime.strptime(str(row[0]), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    if last is None:
        return [DoctorCheck("news store", DoctorStatus.WARN, f"{path}: no poll has ever run",
                            "fiboki news record --once")]
    age = (host.now() - last).total_seconds()
    status = DoctorStatus.OK if age <= 900 else DoctorStatus.WARN
    return [DoctorCheck("news store", status, f"last poll {last.isoformat()} ({age:.0f}s ago)",
                        "" if status == DoctorStatus.OK else "The recorder has stopped: "
                        "launchctl kickstart -k gui/$(id -u)/uk.fiboki.news",
                        {"last_poll": last.isoformat(), "age_seconds": round(age, 1)})]


def _check_calendar(host: DoctorHost) -> list[DoctorCheck]:
    from fiboki.marketstate.calendar import load_official_calendar

    cov = load_official_calendar().coverage()
    end = cov.declared_end if cov.declared_end is not None else cov.last_event
    if end is None:
        return [DoctorCheck("calendar coverage", DoctorStatus.FAIL, "the official calendar is empty",
                            "USER_ACTIONS P3: extend the economic calendar.")]
    now = host.now()
    days = (end.to_pydatetime() - now).total_seconds() / 86400.0
    if days < 0:
        status, fix = DoctorStatus.FAIL, (
            "Coverage ends in the past: blackout queries after it answer 'not in blackout', i.e. "
            "trade through events. USER_ACTIONS P3.")
    elif days < 30:
        status, fix = DoctorStatus.WARN, "Coverage ends within 30 days. USER_ACTIONS P3."
    else:
        status, fix = DoctorStatus.OK, ""
    return [DoctorCheck("calendar coverage", status,
                        f"declared to {end} ({cov.n_events} events, {', '.join(cov.currencies)})",
                        fix, {"declared_end": str(end)})]


def _runtime_drives_llama_cpp() -> bool:
    """Whether the research runtime builds a provider that can talk to llama-server."""
    import inspect

    from fiboki.workers import research_runtime

    source = inspect.getsource(research_runtime._build_provider)
    return "for_local_server" in source or "for_llama_cpp" in source


def _local_model_report(host: DoctorHost, *, hash_weights: bool) -> DoctorCheck:
    from fiboki.agents.providers import (
        LlamaCppProvider,
        LocalHTTPProvider,
        ProviderError,
        ProviderUnavailable,
    )

    env = host.env
    wanted = env.get("FIBOKI_AGENT_PROVIDER", "echo").strip().lower() == "local"
    configured = env.get("FIBOKI_AGENT_LOCAL_URL", "").strip()
    model = env.get("FIBOKI_AGENT_LOCAL_MODEL", "").strip() or None
    urls = [configured] if configured else [_DOCTOR_LLAMA_URL, _DOCTOR_OLLAMA_URL]
    optional = "" if wanted else " (optional while FIBOKI_AGENT_PROVIDER is not local)"
    cache = _doctor_paths(host)["home"][0] / "gguf-digests.json"
    client = host.http_client()
    try:
        unreachable: list[str] = []
        for url in urls:
            try:
                provider = LocalHTTPProvider.for_local_server(
                    model, client=client, base_url=url, digest_cache_path=cache
                )
            except ProviderUnavailable as exc:
                unreachable.append(f"{url}: {exc}")
                continue
            except ProviderError as exc:
                return DoctorCheck(
                    "local model", DoctorStatus.FAIL if wanted else DoctorStatus.WARN,
                    f"{url}: {exc}",
                    "Make FIBOKI_AGENT_LOCAL_MODEL name the model the server has loaded" + optional,
                )
            spec = provider.models()[0]
            backend = "llama.cpp" if isinstance(provider, LlamaCppProvider) else "ollama"
            data: dict[str, Any] = {"url": url, "backend": backend, "model": spec.model,
                                    "context": spec.context_window, "version": spec.version}
            where = f"{backend} at {url}: {spec.model} (n_ctx {spec.context_window})"
            if backend == "llama.cpp" and not hash_weights:
                return DoctorCheck("local model", DoctorStatus.WARN,
                                   f"{where}; digest not computed (--no-hash)",
                                   "Run once without --no-hash; the digest is then cached by "
                                   "path, size and mtime.", data)
            try:
                fp = provider.model_fingerprint(spec.model)
            except ProviderError as exc:
                return DoctorCheck("local model", DoctorStatus.FAIL,
                                   f"{where}: cannot be pinned: {exc}",
                                   "Start llama-server with an absolute -m path "
                                   "(scripts/llama-server.sh does).", data)
            data.update(digest=fp.digest, source=fp.source)
            status, fix = DoctorStatus.OK, ""
            if wanted and model is None:
                status, fix = DoctorStatus.FAIL, f"export FIBOKI_AGENT_LOCAL_MODEL={spec.model}"
            elif wanted and backend == "llama.cpp" and not _runtime_drives_llama_cpp():
                status, fix = DoctorStatus.FAIL, (
                    "fiboki.workers.research_runtime._build_provider still hardcodes "
                    "LocalHTTPProvider.for_ollama, which cannot talk to llama-server. Apply the "
                    "one-line change in docs/v2/OPERATIONS.md (llama.cpp runbook).")
            return DoctorCheck("local model", status, f"{where} {fp.digest}", fix, data)
        return DoctorCheck(
            "local model", DoctorStatus.FAIL if wanted else DoctorStatus.WARN,
            "no local model server answered: " + "; ".join(unreachable),
            "scripts/llama-server.sh, or launchctl kickstart gui/$(id -u)/uk.fiboki.llama" + optional,
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _check_local_model(host: DoctorHost, *, hash_weights: bool = True) -> list[DoctorCheck]:
    return [_local_model_report(host, hash_weights=hash_weights)]


def _check_disk(host: DoctorHost) -> list[DoctorCheck]:
    paths = _doctor_paths(host)
    seen: dict[str, int] = {}
    for label, path in (("repo", host.repo), ("data root", paths["data_root"][0])):
        seen[label] = host.disk_free(path)
    worst = min(seen.values())
    gib = 1024 ** 3
    status = DoctorStatus.FAIL if worst < 5 * gib else DoctorStatus.WARN if worst < 20 * gib else DoctorStatus.OK
    return [DoctorCheck(
        "disk free", status, ", ".join(f"{k} {v / gib:.1f} GiB" for k, v in seen.items()),
        "" if status == DoctorStatus.OK else "Free space: SQLite ledgers and the news store are "
        "append-only, and a full disk fails a write mid-record.",
        {k: v for k, v in seen.items()},
    )]


def _port_owner(host: DoctorHost, port: int) -> tuple[int, str] | None:
    rc, out = host.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"])
    if rc == 127:
        raise FileNotFoundError("lsof")
    pids = [int(line[1:]) for line in out.splitlines() if line.startswith("p") and line[1:].isdigit()]
    if not pids:
        return None
    _, command = host.run(["ps", "-o", "command=", "-p", str(pids[0])])
    return pids[0], command.strip()


def _is_fiboki_process(host: DoctorHost, command: str) -> bool:
    return str(host.repo) in command or any(
        marker in command for marker in ("fiboki.api.app", "llama-server", "next-server", "next start")
    )


def _check_ports(host: DoctorHost) -> list[DoctorCheck]:
    out: list[DoctorCheck] = []
    for port, role in DOCTOR_PORTS.items():
        name = f"port {port} ({role})"
        try:
            owner = _port_owner(host, port)
        except FileNotFoundError:
            busy = host.port_open(port)
            out.append(DoctorCheck(name, DoctorStatus.WARN if busy else DoctorStatus.OK,
                                   "in use; owner unknown (lsof unavailable)" if busy else "free"))
            continue
        if owner is None:
            out.append(DoctorCheck(name, DoctorStatus.OK, "free"))
        elif _is_fiboki_process(host, owner[1]):
            out.append(DoctorCheck(name, DoctorStatus.OK, f"owned by Fiboki: pid {owner[0]} {owner[1][:120]}"))
        else:
            out.append(DoctorCheck(name, DoctorStatus.FAIL, f"held by pid {owner[0]}: {owner[1][:120]}",
                                   f"Stop pid {owner[0]} or the {role} service cannot bind {port}."))
    return out


def _check_launchd(host: DoctorHost) -> list[DoctorCheck]:
    if host.system != "Darwin":
        return [DoctorCheck("launchd", DoctorStatus.WARN, f"not macOS ({host.system}); services not checked")]
    states: dict[str, str] = {}
    for label in (*DOCTOR_LAUNCHD_LABELS, "com.fiboki.research-worker"):
        rc, out = host.run(["launchctl", "print", f"gui/{host.uid}/{label}"])
        if rc != 0:
            states[label] = "not loaded"
            continue
        state = "loaded"
        for line in out.splitlines():
            text = line.strip()
            if text.startswith("state = "):
                state = text.split("=", 1)[1].strip()
            elif text.startswith("last exit code = "):
                state += f", last exit {text.split('=', 1)[1].strip()}"
        states[label] = state
    legacy = states.pop("com.fiboki.research-worker")
    missing = [lbl for lbl, st in states.items()
               if st == "not loaded" and lbl not in DOCTOR_LAUNCHD_OPTIONAL]
    not_running = [lbl for lbl, st in states.items() if st != "not loaded" and not st.startswith("running")]
    status = DoctorStatus.OK
    fix = ""
    if legacy != "not loaded" and states.get("uk.fiboki.worker") != "not loaded":
        status, fix = DoctorStatus.WARN, (
            "Both com.fiboki.research-worker and uk.fiboki.worker are loaded; the lease lets only "
            "one work. launchctl bootout gui/$(id -u)/com.fiboki.research-worker")
    elif missing or not_running:
        status, fix = DoctorStatus.WARN, "scripts/launchd-install.sh --load (see docs/v2/OPERATIONS.md)"
    detail = "; ".join(
        f"{lbl.removeprefix('uk.fiboki.')}: {st}"
        + (" (optional)" if lbl in DOCTOR_LAUNCHD_OPTIONAL and st == "not loaded" else "")
        for lbl, st in states.items()
    )
    return [DoctorCheck("launchd", status, detail, fix, {**states, "com.fiboki.research-worker": legacy})]


DOCTOR_CHECKS: tuple[tuple[str, Callable[[DoctorHost], list[DoctorCheck]]], ...] = (
    ("python", _check_python),
    (".venv", _check_venv),
    ("node/npm", _check_node),
    ("web node_modules", _check_node_modules),
    ("git", _check_git),
    ("environment", _check_env),
    ("operator hashes", _check_operator_hashes),
    ("data root", _check_data_root),
    ("experiment ledger", _check_ledger),
    ("paper journal", _check_paper),
    ("worker heartbeat", _check_heartbeat),
    ("news store", _check_news),
    ("calendar coverage", _check_calendar),
    ("local model", _check_local_model),
    ("disk free", _check_disk),
    ("ports", _check_ports),
    ("launchd", _check_launchd),
)


def run_doctor(host: DoctorHost, *, hash_weights: bool = True,
               only: Sequence[str] | None = None) -> list[DoctorCheck]:
    """Every desktop check, in a fixed order.  Pure except for ``host``."""
    results: list[DoctorCheck] = []
    for name, fn in DOCTOR_CHECKS:
        if only and name not in only:
            continue
        if fn is _check_local_model:
            results.extend(_doctor_guard(name, lambda h: _check_local_model(h, hash_weights=hash_weights), host))
        else:
            results.extend(_doctor_guard(name, fn, host))
    return results


def _doctor_payload(checks: Sequence[DoctorCheck]) -> dict[str, Any]:
    counts = {s: sum(1 for c in checks if c.status == s) for s in DoctorStatus.ALL}
    return {"ok": counts["FAIL"] == 0, "counts": counts, "checks": [c.as_dict() for c in checks]}


def _render_doctor(checks: Sequence[DoctorCheck]) -> None:
    colour = {DoctorStatus.OK: "green", DoctorStatus.WARN: "yellow", DoctorStatus.FAIL: "red"}
    table = Table(title="fiboki doctor (desktop)", show_lines=False)
    table.add_column("status", width=6)
    table.add_column("check", style="cyan")
    table.add_column("observed", overflow="fold")
    table.add_column("fix", overflow="fold")
    for check in checks:
        table.add_row(Text(check.status, style=colour[check.status]), check.name,
                      check.detail, check.fix)
    console.print(table)
    counts = _doctor_payload(checks)["counts"]
    console.print(f"{counts['OK']} OK, {counts['WARN']} WARN, {counts['FAIL']} FAIL")


@doctor_app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json"),
    no_hash: bool = typer.Option(False, "--no-hash", help="Do not hash the GGUF weights file."),
    only: list[str] = typer.Option([], "--only", help="Run only the named check(s)."),
    repo: Path | None = typer.Option(None, "--repo", help="Repository root (default: this checkout)."),
) -> None:
    """Check this machine can run Fiboki continuously. Exit 1 on any FAIL.

    Every row is a check that was performed; WARN is something that works but
    should be fixed, FAIL is something that will not work.
    """
    if ctx.invoked_subcommand is not None:
        return
    host = DoctorHost(repo=repo)
    checks = run_doctor(host, hash_weights=not no_hash, only=only or None)
    payload = _doctor_payload(checks)
    _emit(payload, as_json=as_json, render=lambda: _render_doctor(checks))
    if not payload["ok"]:
        raise typer.Exit(EXIT_FAIL)


@doctor_app.command("model")
def doctor_model(
    as_json: bool = typer.Option(False, "--json"),
    no_hash: bool = typer.Option(False, "--no-hash", help="Do not hash the GGUF weights file."),
) -> None:
    """The local model server, the model it serves and its weights digest."""
    check = _doctor_guard("local model", lambda h: _check_local_model(h, hash_weights=not no_hash),
                          DoctorHost())[0]
    payload = _doctor_payload([check])
    _emit(payload, as_json=as_json, render=lambda: _render_doctor([check]))
    if not payload["ok"]:
        raise typer.Exit(EXIT_FAIL)


# ===========================================================================
# END doctor
# ===========================================================================


# ===========================================================================
# BEGIN events (Wave 4: the agent event channel, shadow evaluation only)
# Self-contained block. Nothing here can enable the veto: the policy is a
# source constant (marketstate/events.py) and this command only READS.
# ===========================================================================

events_app = typer.Typer(
    help="Agent event channel: shadow evaluation of the (default-off) event veto.",
    no_args_is_help=True,
)
app.add_typer(events_app, name="events")

_SHADOW_TRADE_KEYS = ("trade_id", "instrument", "entry_time", "net_pnl", "max_adverse_excursion")


def _load_shadow_trades(path: Path) -> list[Any]:
    """A JSON array or JSON-lines file of trade-ledger rows."""
    from types import SimpleNamespace

    import pandas as pd

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        rows = json.loads(text) if text.startswith("[") else [
            json.loads(line) for line in text.splitlines() if line.strip()
        ]
    except json.JSONDecodeError as exc:
        _fail(f"{path} is not JSON or JSON lines: {exc}", EXIT_MISUSE)
    out: list[Any] = []
    for i, row in enumerate(rows):
        missing = [k for k in _SHADOW_TRADE_KEYS if k not in row]
        if missing:
            _fail(f"{path} row {i} lacks {missing}; need {list(_SHADOW_TRADE_KEYS)}", EXIT_MISUSE)
        entry = pd.Timestamp(row["entry_time"])
        if entry.tzinfo is None:
            _fail(f"{path} row {i}: entry_time {row['entry_time']!r} has no timezone", EXIT_MISUSE)
        out.append(
            SimpleNamespace(
                trade_id=str(row["trade_id"]),
                instrument=str(row["instrument"]).upper(),
                entry_time=entry,
                net_pnl=float(row["net_pnl"]),
                max_adverse_excursion=float(row["max_adverse_excursion"]),
            )
        )
    return out


@events_app.command("shadow-report")
def events_shadow_report(
    trades: Path = typer.Option(..., "--trades", help="JSON array or JSON lines of trade-ledger rows."),
    state_dir: Path | None = typer.Option(None, "--state-dir", help="Default: $FIBOKI_STATE_DIR or ./var."),
    bars_timeframe: str | None = typer.Option(
        None, "--bars-timeframe",
        help="Load bars at this timeframe from FIBOKI_DATA_ROOT for the vol-spike baseline.",
    ),
    no_calendar: bool = typer.Option(False, "--no-calendar", help="Skip the calendar-only baseline."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Would-have-vetoed per trade, vs the vol-spike and calendar-only baselines.

    Reads the quarantined annotation store read-only. Exit 1 when the store is
    missing. An insufficient sample (fewer vetoed trades than the pre-registered
    floor) is reported, not failed: it is the expected state for months.
    """
    from fiboki.marketstate.events import (
        AnnotationStore,
        AnnotationStoreError,
        default_annotation_store_path,
        shadow_report,
    )

    base = state_dir if state_dir is not None else Path(os.environ.get("FIBOKI_STATE_DIR") or "var")
    path = default_annotation_store_path(base)
    try:
        store = AnnotationStore(path, read_only=True)
    except AnnotationStoreError as exc:
        _fail(f"{exc}; the event scan has not written anything yet")
    try:
        annotations = store.annotations()
    finally:
        store.close()
    rows = _load_shadow_trades(trades)
    bars: dict[str, Any] = {}
    if bars_timeframe is not None:
        root = data_root()
        if root is None:
            _fail("--bars-timeframe needs FIBOKI_DATA_ROOT", EXIT_MISUSE)
        from fiboki.data.store import DataStore

        ds = DataStore(root)
        for sym in sorted({t.instrument for t in rows}):
            try:
                bars[sym], _version = ds.read_latest(sym, bars_timeframe)
            except Exception as exc:  # absence is reported, never filled in
                _warn(f"no bars for {sym} {bars_timeframe}: {exc}; vol baseline NOT_EVALUATED")
    calendar = None
    if not no_calendar:
        from fiboki.marketstate.calendar import load_official_calendar

        calendar = load_official_calendar()
    report = shadow_report(annotations, rows, bars=bars or None, calendar=calendar)

    def _stats(g: Any) -> dict[str, Any]:
        return {
            "n": g.n,
            "total_net_pnl": g.total_net_pnl,
            "net_expectancy": g.net_expectancy,
            "mean_adverse_excursion": g.mean_adverse_excursion,
        }

    payload = {
        "store": str(path),
        "policy": dict(report.policy),
        "n_trades": report.n_trades,
        "n_annotations": report.n_annotations,
        "sufficient": report.sufficient,
        "min_affected_trades": report.min_affected_trades,
        "channels": [
            {
                "name": c.name,
                "version": c.version,
                "blocked": _stats(c.blocked),
                "kept": _stats(c.kept),
                "not_evaluated": c.not_evaluated,
                "expectancy_kept_minus_blocked": c.expectancy_kept_minus_blocked,
            }
            for c in report.channels
        ],
        "caveats": list(report.caveats),
    }

    def _render() -> None:
        console.print(
            f"{report.n_trades} trades, {report.n_annotations} annotations, policy "
            f"{report.policy['event_veto_policy']} (enabled={report.policy['event_veto_enabled']})"
        )
        table = Table("channel", "blocked n", "blocked E[net]", "kept n", "kept E[net]",
                      "not evaluated")
        for c in report.channels:
            table.add_row(
                c.name, str(c.blocked.n), str(c.blocked.net_expectancy), str(c.kept.n),
                str(c.kept.net_expectancy), str(c.not_evaluated),
            )
        console.print(table)
        if not report.sufficient:
            _warn(
                f"fewer than {report.min_affected_trades} would-be-vetoed trades: below the "
                "pre-registered floor, read nothing into this comparison"
            )
        for c in report.caveats:
            console.print(f"  - {c}")

    _emit(payload, as_json=as_json, render=_render)


# ===========================================================================
# END events
# ===========================================================================


# ===========================================================================
# paper forward: the reviewed entrypoint (fiboki/entrypoints/paper_forward.py)
# ===========================================================================

paper_app = typer.Typer(
    help="PAPER trading forward on live OANDA practice prices, from a committed wiring file.",
    no_args_is_help=True,
)
app.add_typer(paper_app, name="paper")


@paper_app.command("forward")
def paper_forward(
    wiring: Path = typer.Option(
        ..., "--wiring", help="The committed, versioned wiring file (entrypoints/wiring/)."
    ),
    once: bool = typer.Option(False, "--once", help="Run a single cycle and exit."),
    max_cycles: int = typer.Option(0, "--max-cycles", help="0 = run forever."),
    check: bool = typer.Option(
        False, "--check", help="Load and validate the wiring, print its hash, start nothing."
    ),
) -> None:
    """Trade PAPER forward in time. Nothing reaches a broker.

    Everything the worker trades with comes from ``--wiring``: the strategy (by
    content hash), instruments, timeframe, limit set, cost profile, calendar and
    ledger location. The wiring's sha256 is stamped on every risk-gateway
    attempt. ``fiboki worker run live`` still refuses: a market-facing worker is
    never assembled from flags, only from a reviewed wiring file, and this one
    is PAPER only (``allowed_modes = ["paper"]``, enforced when it is loaded).
    """
    from fiboki.entrypoints.paper_forward import WiringError, compose_runtime, load_wiring
    from fiboki.workers.base import EXIT_LEASE_HELD as LEASE_CODE

    path = wiring.expanduser()
    if not path.exists():
        _fail(f"wiring file {path} does not exist", EXIT_MISUSE)
    try:
        wired = load_wiring(path)
    except WiringError as exc:
        _fail(f"wiring refused: {exc}", EXIT_MISUSE)
        return
    if check:
        _ok(f"wiring {wired.version} OK  sha256={wired.sha256}")
        return

    from fiboki.api.settings import load_settings
    from fiboki.broker.http_transport import MissingCredential
    from fiboki.marketstate.calendar import CalendarError
    from fiboki.obs.alerts import build_default_dispatcher

    settings = load_settings()
    if settings.execution_mode.value != "paper":
        _fail(
            f"FIBOKI_EXECUTION_MODE is {settings.execution_mode.value!r}; paper forward "
            "runs only with FIBOKI_EXECUTION_MODE=paper",
            EXIT_MISUSE,
        )
    store = _open_store()
    dispatcher = build_default_dispatcher(source="paper-forward")
    try:
        runtime = compose_runtime(
            settings, wired, store=store, dispatcher=dispatcher, max_cycles=1 if once else max_cycles
        )
    except (WiringError, MissingCredential, CalendarError) as exc:
        _fail(str(exc), EXIT_FAIL)
        return
    worker = runtime.worker
    console.print(
        Panel(
            f"worker_id: [bold]{worker.worker_id}[/bold]\n"
            f"wiring:    {wired.path}\n"
            f"sha256:    {wired.sha256}\n"
            f"strategy:  {', '.join(f'{s.strategy_id}@{s.content_hash[:12]}' for s in wired.strategies)}\n"
            f"markets:   {', '.join(wired.instruments)} {wired.timeframe.value}\n"
            f"limits:    {wired.limits_version}   profile: {wired.broker_profile}\n"
            f"journal:   {runtime.journal.ledger_dir}\n"
            f"session:   {runtime.journal.session_dir}\n"
            f"state db:  {state_db_path()}\n"
            f"alerts:    {', '.join(dispatcher.channel_names())}",
            title="fiboki paper forward (PAPER ONLY)",
        )
    )
    code = worker.run()
    if code == LEASE_CODE:
        raise typer.Exit(EXIT_LEASE_HELD)
    raise typer.Exit(code)


# ===========================================================================
# agents tier: the signed record of how far agent output may reach
# ===========================================================================

agents_app = typer.Typer(help="Agent layer controls that are operator acts.", no_args_is_help=True)
agents_tier_app = typer.Typer(
    help="Agent influence tier (core/tier.py): status, and set (signed, audited).",
    no_args_is_help=True,
)
agents_app.add_typer(agents_tier_app, name="tier")
app.add_typer(agents_app, name="agents")


def _tier_paths() -> tuple[Path, Path, Any]:
    """The tier record and its audit trail, under the resolved state directory."""
    from fiboki.core.paths import resolve_paths
    from fiboki.core.tier import default_tier_audit_path, default_tier_path

    paths = resolve_paths(os.environ, cwd=Path.cwd())
    return default_tier_path(paths.state_dir), default_tier_audit_path(paths.state_dir), paths


@agents_tier_app.command("status")
def agents_tier_status(as_json: bool = typer.Option(False, "--json")) -> None:
    """The tier in force, where it came from, and what it permits.

    Exit 1 when a record exists but cannot be honoured (bad signature,
    unreadable, or no FIBOKI_SESSION_SECRET to verify it): the tier then falls
    back to T1 (shadow only), and the operator should know.
    """
    from fiboki.api.settings import load_settings
    from fiboki.core.tier import read_tier

    record_path, audit_path, paths = _tier_paths()
    settings = load_settings()
    secret = None if settings.session_secret_is_ephemeral else settings.session_secret
    reading = read_tier(record_path, secret)
    tier = reading.tier
    payload = {
        **reading.stamp(),
        "detail": reading.detail,
        "operator": reading.operator,
        "recorded_at": reading.recorded_at,
        "record_path": str(record_path),
        "audit_path": str(audit_path),
        "state_dir_from_env": paths.state_dir_from_env,
        "permits": {
            "shadow_writes": tier.permits_shadow_writes,
            "event_veto": tier.permits_veto,
            "conviction_dampen": tier.permits_dampen,
            "author_candidates": tier.permits_candidates,
            "upsizing": tier.permits_upsizing,
        },
    }

    def _render() -> None:
        console.print(f"agent tier: [bold]{tier.value}[/bold]  (source: {reading.source})")
        if reading.requested is not None and reading.requested is not tier:
            _warn(f"record asks for {reading.requested.value}; effective {tier.value}: "
                  f"{reading.detail}")
        if reading.operator:
            console.print(f"recorded by {reading.operator} at {reading.recorded_at}")
        console.print(f"ceiling (reviewed source constant): {reading.ceiling.value}")
        console.print(
            f"event veto may block: {tier.permits_veto}   "
            f"conviction may dampen: {tier.permits_dampen}   upsizing: never"
        )
        console.print(f"record: {record_path}")
        if not paths.state_dir_from_env:
            _warn("FIBOKI_STATE_DIR is not set; the record was resolved from the current directory")

    _emit(payload, as_json=as_json, render=_render)
    if reading.needs_alert and reading.source != "clamped":
        raise typer.Exit(EXIT_FAIL)


@agents_tier_app.command("set")
def agents_tier_set(
    tier_value: str = typer.Option(..., "--tier", help="t0_observe .. t4_author_candidates"),
    operator: str = typer.Option(..., "--operator", help="Required. Who decided. Signed."),
    reason: str = typer.Option(..., "--reason", help="Required. Why. Signed and audited."),
) -> None:
    """Record a new tier, HMAC-signed with FIBOKI_SESSION_SECRET, and audit it.

    Raising above the reviewed ceiling (``core.tier.MAX_AUTHORISED_TIER``) is
    refused: that needs a reviewed commit first. Lowering is always allowed.
    Every set, including the previous reading, is appended to the audit trail.
    """
    from datetime import UTC, datetime

    from fiboki.api.settings import load_settings
    from fiboki.core.tier import (
        MAX_AUTHORISED_TIER,
        AgentInfluenceTier,
        TierError,
        append_tier_audit,
        read_tier,
        sign_tier_record,
        write_tier_record,
    )

    try:
        tier = AgentInfluenceTier(tier_value.strip().lower())
    except ValueError:
        _fail(
            f"{tier_value!r} is not a tier; one of "
            f"{[t.value for t in AgentInfluenceTier]}",
            EXIT_MISUSE,
        )
        return
    settings = load_settings()
    if settings.session_secret_is_ephemeral:
        _fail(
            "FIBOKI_SESSION_SECRET is unset: a record signed with a per-process key could "
            "never be verified by the worker or the API. Set it, then retry.",
            EXIT_MISUSE,
        )
    if tier.level > MAX_AUTHORISED_TIER.level:
        _fail(
            f"{tier.value} is above the reviewed ceiling {MAX_AUTHORISED_TIER.value}. Raising "
            "agent influence needs a reviewed commit that raises core.tier.MAX_AUTHORISED_TIER "
            "(after the pre-registered shadow evaluation) AND this signed record.",
            EXIT_MISUSE,
        )
    record_path, audit_path, _paths = _tier_paths()
    previous = read_tier(record_path, settings.session_secret)
    try:
        record = sign_tier_record(
            operator=operator, tier=tier, reason=reason, secret=settings.session_secret,
            at=datetime.now(tz=UTC),
        )
    except TierError as exc:
        _fail(str(exc), EXIT_MISUSE)
        return
    write_tier_record(record_path, record)
    append_tier_audit(
        audit_path,
        {
            "action": "set",
            "at": record.recorded_at,
            "operator": record.operator,
            "reason": record.reason,
            "tier": record.tier.value,
            "previous": {**previous.stamp(), "operator": previous.operator},
            "record_path": str(record_path),
            "signature_prefix": record.signature[:12],
        },
    )
    _ok(
        f"agent tier recorded: {record.tier.value} by {record.operator} "
        f"(was {previous.tier.value}, source {previous.source})"
    )
    console.print(f"record: {record_path}\naudit:  {audit_path}")


# ===========================================================================
# END agents tier
# ===========================================================================


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
