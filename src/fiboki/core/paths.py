"""Where operator state lives. ONE resolution rule, used by every process.

The defect this closes (audit F, P0-1)
--------------------------------------
The kill-switch journal was resolved three different ways. The CLI defaulted
``--journal`` to ``~/.fiboki/killswitch.jsonl``; the API used
``<FIBOKI_STATE_DIR>/killswitch.jsonl`` with ``state_dir`` defaulting to the
relative path ``var``; and the paper runtime's gateway held an in-memory
journal. ``fiboki killswitch flatten`` therefore wrote a file that neither the
API nor any worker read, and then printed that a worker would act on it.

The rule
--------
:func:`resolve_paths` is a PURE function of an environment mapping (and,
optionally, a working directory to anchor a relative path against). It reads no
global state, touches no file and creates no directory, so the CLI, the API
(through :func:`fiboki.api.settings.load_settings`), the workers and the
scripts all compute the same answer from the same environment, and a test can
prove it without a filesystem.

* ``FIBOKI_HOME`` (default ``~/.fiboki``): per-user configuration and the
  worker lease/heartbeat database (``FIBOKI_STATE_DB``, default
  ``<home>/state.db``).
* ``FIBOKI_STATE_DIR`` (default ``var``): every operator LEDGER. The
  kill-switch journal, the order-intent ledger, the audit ledgers, the holdout
  registry, the alert outbox and the news/event stores all live under it, so
  ``scripts/backup.sh`` (which archives the state directory) captures all of
  them.

``var`` is relative, so a process started in another directory resolves a
different one. That default is kept because the desktop services and
``scripts/dev-up.sh`` always export ``FIBOKI_STATE_DIR`` and existing
deployments have data there; :attr:`FibokiPaths.state_dir_from_env` says which
case applies, and the CLI prints a warning when it is ``False``.

``core`` imports nothing from Fiboki; this module is data and one function.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

__all__ = [
    "DEFAULT_HOME",
    "DEFAULT_STATE_DIR",
    "FibokiPaths",
    "resolve_paths",
]

#: ``FIBOKI_HOME`` when unset. Expanded against the mapping's ``HOME``.
DEFAULT_HOME = "~/.fiboki"
#: ``FIBOKI_STATE_DIR`` when unset. Relative: see the module docstring.
DEFAULT_STATE_DIR = "var"


@dataclass(frozen=True, slots=True)
class FibokiPaths:
    """Every operator path, resolved once. Data only."""

    home: Path
    state_dir: Path
    #: ``True`` when ``FIBOKI_STATE_DIR`` was set. ``False`` means the relative
    #: default, which depends on the working directory of the process.
    state_dir_from_env: bool
    #: The ONE kill-switch journal every gateway, the API and the CLI read.
    killswitch_journal: Path
    #: Durable order-intent ledger (``JsonlIntentStore``).
    intent_ledger: Path
    #: Agent audit ledger (hash-chained, ``JsonlAuditLedger``).
    audit_ledger: Path
    #: HTTP API audit trail.
    api_audit: Path
    holdout_db: Path
    experiment_db: Path
    #: ``True`` when ``FIBOKI_EXPERIMENT_DB`` was set. The API reports an
    #: unprovisioned ledger rather than silently opening the default one.
    experiment_db_from_env: bool
    news_store: Path
    events_store: Path
    #: Worker lease + heartbeat database (``WorkerStore``).
    heartbeat_db: Path
    #: The API's heartbeat file/database (``FIBOKI_WORKER_HEARTBEAT``).
    worker_heartbeat: Path
    paper_root: Path
    #: ``True`` when ``FIBOKI_PAPER_ROOT`` was set (the API then treats the
    #: paper journal as explicitly provisioned).
    paper_root_from_env: bool
    data_root: Path | None
    #: ``FIBOKI_ALERT_LOG``; ``None`` means alerts are not persisted to a file.
    alert_log: Path | None
    #: At-least-once outbox for CRITICAL alerts to remote channels.
    alert_outbox: Path

    def as_dict(self) -> dict[str, str | bool | None]:
        out: dict[str, str | bool | None] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            out[item.name] = str(value) if isinstance(value, Path) else value
        return out


def _expand(raw: str, env: Mapping[str, str]) -> Path:
    """``~`` against the mapping's ``HOME`` first, so the function stays pure."""
    text = raw.strip()
    if text == "~" or text.startswith("~/"):
        home = str(env.get("HOME", "")).strip()
        if home:
            return Path(home + text[1:])
        return Path(text).expanduser()
    return Path(text)


def _anchor(path: Path, cwd: Path | None) -> Path:
    if cwd is None or path.is_absolute():
        return path
    return cwd / path


def resolve_paths(env: Mapping[str, str], *, cwd: str | Path | None = None) -> FibokiPaths:
    """Resolve every operator path from ``env``. Pure: no I/O, no globals.

    ``cwd`` anchors relative paths (``var`` by default) when given; without it
    a relative path stays relative, exactly as the API has always held it.
    """
    base = Path(cwd) if cwd is not None else None

    def get(name: str) -> str:
        return str(env.get(name, "") or "").strip()

    home = _anchor(_expand(get("FIBOKI_HOME") or DEFAULT_HOME, env), base)
    raw_state = get("FIBOKI_STATE_DIR")
    state_dir = _anchor(_expand(raw_state or DEFAULT_STATE_DIR, env), base)

    def opt(name: str) -> Path | None:
        raw = get(name)
        return _anchor(_expand(raw, env), base) if raw else None

    experiment_env = opt("FIBOKI_EXPERIMENT_DB")
    return FibokiPaths(
        home=home,
        state_dir=state_dir,
        state_dir_from_env=bool(raw_state),
        killswitch_journal=state_dir / "killswitch.jsonl",
        intent_ledger=state_dir / "execution" / "intents.jsonl",
        audit_ledger=state_dir / "agents" / "audit.jsonl",
        api_audit=state_dir / "api_audit.jsonl",
        holdout_db=state_dir / "holdout.sqlite",
        experiment_db=experiment_env or state_dir / "experiments.sqlite",
        experiment_db_from_env=experiment_env is not None,
        news_store=state_dir / "news" / "headlines.sqlite",
        events_store=state_dir / "events" / "annotations.sqlite",
        heartbeat_db=opt("FIBOKI_STATE_DB") or home / "state.db",
        worker_heartbeat=opt("FIBOKI_WORKER_HEARTBEAT") or state_dir / "worker.heartbeat",
        paper_root=opt("FIBOKI_PAPER_ROOT") or state_dir / "paper",
        paper_root_from_env=bool(get("FIBOKI_PAPER_ROOT")),
        data_root=opt("FIBOKI_DATA_ROOT"),
        alert_log=opt("FIBOKI_ALERT_LOG"),
        alert_outbox=state_dir / "alerts_outbox.sqlite",
    )
