"""The read-model container: one object that owns every source the API reads.

Everything here is either a REAL domain object from another package (the
strategy registry, the instrument universe, the kill switch, the mode guard,
the limit sets, the experiment ledger, the audit ledger) or a clearly-labelled
deterministic fixture from :mod:`fiboki.api.seed`.

``data_source`` reports which, per source, and the system router publishes it.
The rule this package follows: never make an unprovisioned source look
provisioned. V1's dashboard drew a flat £0.00 line with an "Online" badge when
the backend was down; the whole design here is that absence is visible.

Trades, positions and the account come from the persisted PAPER journal under
``FIBOKI_PAPER_ROOT`` (default ``<FIBOKI_STATE_DIR>/paper``) whenever one
exists, read by :class:`fiboki.api.paper_journal.PaperJournalReader`. Only when
no journal exists does the platform fall back to the seed fixture, and then
``data_source`` is ``"seed"`` and no fixture row carries an executed
provenance.

The worker heartbeat is read from the ``worker_heartbeat`` table of the
worker's SQLite store (``FIBOKI_WORKER_HEARTBEAT``), opened read-only. The
file's mtime is NOT the heartbeat: in WAL mode a write lands in ``-wal`` and
the main file's mtime can stay hours old while the worker beats every second.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fiboki.api.audit_trail import ApiAuditTrail
from fiboki.api.paper_journal import PaperAccount, PaperJournal, PaperJournalReader
from fiboki.api.seed import PositionRow, SeedClock, TradeRow, generate
from fiboki.api.settings import Settings
from fiboki.broker.mode_guard import (
    LIVE_EXECUTION_COMPILED_IN,
    LiveAuthorisationStore,
    ModeGuard,
)
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import ExecutionMode, Provenance
from fiboki.risk.killswitch import FileKillSwitchJournal, KillSwitch
from fiboki.risk.limits import LIMIT_SETS, LimitSet, get_limit_set

log = logging.getLogger("fiboki.api.platform")

__all__ = [
    "HeartbeatReading",
    "Platform",
    "SourceStatus",
    "WorkerBeat",
    "build_platform",
    "read_worker_heartbeat",
]

#: Environment variable naming the paper journal root. Read here rather than in
#: ``api/settings.py`` because it selects a read-only data source, not a
#: behaviour; it defaults to ``<FIBOKI_STATE_DIR>/paper``.
PAPER_ROOT_ENV = "FIBOKI_PAPER_ROOT"  # parsed by fiboki.api.settings into Settings.paper_root

_SQLITE_MAGIC = b"SQLite format 3\x00"


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """What one upstream source actually is right now."""

    name: str
    kind: str
    """``live`` (a real provisioned store) | ``seed`` | ``absent``."""
    detail: str
    healthy: bool
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "detail": self.detail,
            "healthy": self.healthy,
            "latency_ms": self.latency_ms,
        }


# ------------------------------------------------------------ heartbeat


@dataclass(frozen=True, slots=True)
class WorkerBeat:
    """One row of the worker's ``worker_heartbeat`` table, aged by the API."""

    worker_id: str
    kind: str
    status: str
    beat_at: datetime
    age_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "kind": self.kind,
            "status": self.status,
            "beat_at": self.beat_at.isoformat(),
            "age_seconds": self.age_seconds,
        }


@dataclass(frozen=True, slots=True)
class HeartbeatReading:
    """What the API can honestly say about worker liveness right now.

    ``state`` is one of:

    * ``absent`` -- nothing has ever beaten that the API can see: no path, no
      file, no ``worker_heartbeat`` table, no rows, or an unreadable store.
      ``age_seconds`` is ``None``, never ``0``. ``reason`` says which.
    * ``stale`` -- the newest beat is at least the stale threshold old.
    * ``ok`` -- the newest beat is younger than the threshold.

    ``reason`` is ``sqlite`` when the age came from the table, and
    ``mtime_fallback`` when the path is not a SQLite database and the file's
    modification time was used as a last resort (a legacy touch-file).
    """

    state: str
    age_seconds: float | None
    reason: str
    detail: str
    path: str | None
    workers: tuple[WorkerBeat, ...] = ()
    newest_beat_at: datetime | None = None


def _parse_beat(value: Any) -> datetime | None:
    """``beat_at`` as SQLAlchemy stores it on SQLite: naive UTC text."""
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def read_worker_heartbeat(
    path: Path | None,
    *,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> HeartbeatReading:
    """Read the worker heartbeat from the worker's own store, read-only.

    The age is computed from THIS process's clock against ``beat_at``, so a
    healthy worker whose database file has an old mtime (WAL mode) is reported
    as alive, and a dead worker whose file was recently touched is not.
    """
    stamp = now or datetime.now(tz=UTC)
    shown = str(path) if path is not None else None
    if path is None:
        return HeartbeatReading(
            "absent", None, "no_path", "No worker heartbeat location is configured.", shown
        )
    if not path.exists():
        return HeartbeatReading(
            "absent",
            None,
            "file_missing",
            f"No worker store exists at {path}. No worker has ever beaten here.",
            shown,
        )

    def judge(age: float) -> str:
        return "stale" if age >= stale_after_seconds else "ok"

    if not _is_sqlite(path):
        try:
            age = max(0.0, stamp.timestamp() - path.stat().st_mtime)
        except OSError:  # pragma: no cover - race on a vanishing file
            return HeartbeatReading(
                "absent", None, "file_missing", f"{path} vanished while being read.", shown
            )
        return HeartbeatReading(
            judge(age),
            age,
            "mtime_fallback",
            f"{path} is not a SQLite worker store; its modification time was used as "
            "a last resort. This is not a worker_heartbeat row.",
            shown,
        )

    from fiboki.workers.base import WORKER_HEARTBEAT

    table = WORKER_HEARTBEAT.name
    try:
        with sqlite3.connect(
            f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0
        ) as conn:
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if present is None:
                return HeartbeatReading(
                    "absent",
                    None,
                    "no_heartbeat_table",
                    f"{path} has no {table} table. No worker has ever beaten into it.",
                    shown,
                )
            rows = conn.execute(
                f"SELECT worker_id, kind, status, beat_at FROM {table}"
            ).fetchall()
    except sqlite3.Error as exc:
        return HeartbeatReading(
            "absent",
            None,
            "unreadable",
            f"The worker store at {path} could not be read ({type(exc).__name__}: "
            f"{exc}). Liveness is unknown, which is not the same as alive.",
            shown,
        )

    beats: list[WorkerBeat] = []
    for worker, kind, status, beat_at in rows:
        beat = _parse_beat(beat_at)
        if beat is None:
            continue
        beats.append(
            WorkerBeat(
                worker_id=str(worker),
                kind=str(kind or ""),
                status=str(status or ""),
                beat_at=beat,
                age_seconds=round(max(0.0, (stamp - beat).total_seconds()), 3),
            )
        )
    if not beats:
        return HeartbeatReading(
            "absent",
            None,
            "no_rows",
            f"The {table} table at {path} is empty. No worker has ever beaten.",
            shown,
        )
    beats.sort(key=lambda b: b.beat_at, reverse=True)
    newest = beats[0]
    state = judge(newest.age_seconds)
    return HeartbeatReading(
        state,
        newest.age_seconds,
        "sqlite",
        f"{len(beats)} worker(s) in {table}; newest {newest.worker_id} beat "
        f"{newest.age_seconds:.0f}s ago (stale at {stale_after_seconds:.0f}s).",
        shown,
        workers=tuple(beats),
        newest_beat_at=newest.beat_at,
    )


class Platform:
    """Assembled once per process and shared. Read-mostly, lock-guarded writes."""

    def __init__(self, settings: Settings, *, paper_root: Path | None = None) -> None:
        self.settings = settings
        self.started_at = time.time()
        self._lock = threading.Lock()

        settings.state_dir.mkdir(parents=True, exist_ok=True)

        # ---- real domain objects -------------------------------------
        self.audit = ApiAuditTrail(settings.audit_path)
        self.kill_switch = KillSwitch(FileKillSwitchJournal(settings.killswitch_path))
        self.mode_guard = ModeGuard(
            authorisation_store=LiveAuthorisationStore(settings.live_authorisation_path)
        )
        self.limits: LimitSet = get_limit_set(
            "limits_v1_paper" if "limits_v1_paper" in LIMIT_SETS else "limits_v1_default"
        )
        self.instruments = instrument_registry

        self._strategy_registry: Any | None = None
        self._strategy_error: str = ""
        self._load_strategies()

        self._experiment_db: Path | None = settings.experiment_db
        self._sources: dict[str, SourceStatus] = {}
        self._lifecycle: Any | None = None

        # ---- the paper journal, read-only ----------------------------
        self.paper_root: Path = (
            paper_root
            if paper_root is not None
            else settings.paper_root
            if settings.paper_root is not None
            else settings.state_dir / "paper"
        )
        self._journal_reader = PaperJournalReader(self.paper_root)
        self._journal_signature: tuple[tuple[str, int, int], ...] | None = None
        self._journal: PaperJournal | None = None
        self._refresh_journal()

        # ---- fixtures, labelled; served ONLY when no journal exists ----
        self.clock = SeedClock.fixed()
        symbols = self.instruments.all_symbols()
        self._seed_trades, self._seed_positions = generate(
            self.strategy_ids or ["unregistered"],
            symbols[:24] or ["EURUSD"],
            clock=self.clock,
        )

    # ------------------------------------------------------------ setup

    def _load_strategies(self) -> None:
        try:
            from fiboki.strategy import load_seed_registry

            self._strategy_registry = load_seed_registry("research/strategies")
        except Exception as exc:
            self._strategy_registry = None
            self._strategy_error = f"{type(exc).__name__}: {exc}"
            log.warning("strategy_registry_unavailable", extra={"error": self._strategy_error})

    # -------------------------------------------------------- lifecycle

    @property
    def lifecycle(self) -> Any:
        """The promotion/degradation service, backed by files under the state dir.

        Read-only from the API's point of view: the routes serve
        :meth:`LifecycleService.status` and the last
        :class:`LifecycleEvaluation`, and nothing in the HTTP surface evaluates,
        promotes or demotes. The evaluation happens on the live worker's timer,
        in a worker process, which is the only place it can happen honestly --
        V1 computed heartbeat freshness when a human loaded a page.

        The stores are the same file-backed ones the worker writes, so an API
        process and a worker process on one box read one history. On separate
        boxes they do NOT, and that is a deployment fact rather than a code
        defect: ``lifecycle_source`` reports the path it read.
        """
        if self._lifecycle is None:
            from fiboki.lifecycle.service import LifecycleService
            from fiboki.lifecycle.state import FileTransitionLog, LifecycleStateMachine
            from fiboki.lifecycle.stopping_rules import (
                FileHaltJournal,
                FilePreRegistrationStore,
                HaltRegistry,
            )

            root = self.settings.state_dir / "lifecycle"
            self._lifecycle = LifecycleService(
                machine=LifecycleStateMachine(log=FileTransitionLog(root / "transitions.jsonl")),
                registrations=FilePreRegistrationStore(root / "registrations.jsonl"),
                halts=HaltRegistry(FileHaltJournal(root / "halts.jsonl")),
            )
        return self._lifecycle

    def lifecycle_source(self) -> SourceStatus:
        """What the lifecycle routes are actually reading.

        An empty store is reported as ``live`` with a count of zero, not as
        ``absent``: the difference between "no strategy has been registered" and
        "we cannot see the lifecycle store" is exactly the difference an
        operator needs, and collapsing them is how V1's dashboard drew a healthy
        idle fleet during a total outage.
        """
        root = self.settings.state_dir / "lifecycle"
        try:
            statuses = self.lifecycle.statuses()
        except Exception as exc:
            return SourceStatus(
                "lifecycle",
                "absent",
                f"lifecycle store unreadable at {root}: {type(exc).__name__}: {exc}",
                healthy=False,
            )
        return SourceStatus(
            "lifecycle",
            "live",
            f"{len(statuses)} registered strateg(ies) at {root}",
            healthy=True,
        )

    # ------------------------------------------------------- strategies

    @property
    def strategy_ids(self) -> list[str]:
        if self._strategy_registry is None:
            return []
        return list(self._strategy_registry.ids())

    def strategy(self, strategy_id: str) -> Any | None:
        if self._strategy_registry is None:
            return None
        try:
            return self._strategy_registry.get(strategy_id)
        except Exception:
            return None

    def strategy_documents(self) -> list[Any]:
        if self._strategy_registry is None:
            return []
        return list(self._strategy_registry.documents())

    def strategy_health(self) -> Any | None:
        if self._strategy_registry is None:
            return None
        return self._strategy_registry.health_check()

    # ----------------------------------------------------- paper journal

    def _refresh_journal(self) -> PaperJournal | None:
        """Reload the journal when any session file changed. Cheap stats only."""
        with self._lock:
            try:
                signature = self._journal_reader.signature()
            except OSError as exc:  # pragma: no cover - unreadable root
                log.warning("paper_journal_unreadable", extra={"error": str(exc)})
                signature = ()
            if signature != self._journal_signature:
                self._journal = self._journal_reader.load()
                self._journal_signature = signature
                if self._journal is not None:
                    for name, error in self._journal.errors:
                        log.warning(
                            "paper_session_unreadable", extra={"session": name, "error": error}
                        )
            return self._journal

    @property
    def journal(self) -> PaperJournal | None:
        """The loaded journal, or ``None`` when no journal exists at all."""
        return self._refresh_journal()

    @property
    def data_source(self) -> str:
        """``live`` (a paper journal), ``absent`` (one exists but none of its
        sessions parse) or ``seed`` (no journal; the labelled fixture)."""
        journal = self.journal
        if journal is None:
            return "seed"
        return "live" if journal.sessions else "absent"

    def account(self) -> PaperAccount | None:
        """Account figures from the journal. ``None`` when there is no journal:
        the seed fixture has no account, and inventing a starting balance for
        it would put a number on a screen that nothing measured."""
        journal = self.journal
        if journal is None or not journal.sessions:
            return None
        return journal.account()

    def journal_as_of(self) -> datetime | None:
        journal = self.journal
        return journal.as_of if journal is not None else None

    def journal_warnings(self) -> list[str]:
        """Human sentences the trading routes attach as caveats."""
        journal = self.journal
        if journal is None:
            return []
        out: list[str] = []
        if journal.sessions:
            as_of = journal.as_of.isoformat() if journal.as_of else "an unknown time"
            out.append(
                f"Served from {len(journal.sessions)} persisted PAPER session(s) under "
                f"{journal.root}, as of {as_of}. A replay session's open position is "
                "open at the end of the replayed data, not now."
            )
            account = journal.account()
            out.extend(account.warnings)
        out.extend(
            f"Paper session {name!r} could not be read and is excluded: {error}"
            for name, error in journal.errors
        )
        return out

    def trade_record_detail(self) -> str:
        journal = self.journal
        if journal is None:
            return (
                "Deterministic demonstration fixture (fiboki.api.seed). No paper "
                f"journal exists at {self.paper_root}, so these rows are generated, "
                "not measured. No fixture row carries an executed provenance."
            )
        if not journal.sessions:
            return (
                f"A paper journal exists at {journal.root} but none of its "
                f"{len(journal.errors)} session(s) could be read; nothing is served."
            )
        return (
            f"{len(journal.sessions)} PAPER session(s) at {journal.root}: "
            f"{len(journal.trades)} closed trade(s), {len(journal.positions)} open "
            "position(s)"
            + (f"; {len(journal.errors)} unreadable session(s)" if journal.errors else "")
            + "."
        )

    # ------------------------------------------------------------ trades

    def _trade_rows(self) -> list[Any]:
        journal = self.journal
        if journal is None:
            return list(self._seed_trades)
        return list(journal.trades)

    def trades(
        self,
        *,
        provenance: set[Provenance] | None = None,
        strategy_id: str | None = None,
        instrument: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[TradeRow], int]:
        """Journal trades when a journal exists, the seed fixture otherwise.

        The two are never mixed: a fixture row next to a measured one is the
        V1 "Paper / Backtest" page again.
        """
        rows = self._trade_rows()
        if provenance:
            rows = [t for t in rows if t.provenance in provenance]
        if strategy_id:
            rows = [t for t in rows if t.strategy_id == strategy_id]
        if instrument:
            rows = [t for t in rows if t.instrument == instrument]
        rows = sorted(rows, key=lambda t: t.exit_time, reverse=True)
        return rows[offset : offset + limit], len(rows)

    def positions(self) -> list[PositionRow]:
        journal = self.journal
        if journal is None:
            return list(self._seed_positions)
        return list(journal.positions)

    # ------------------------------------------------------------ health

    def check_database(self) -> SourceStatus:
        """Actually open the database and run a query.

        V1's health endpoint returned a literal. This one connects, reads the
        schema, and reports the failure text when it cannot.
        """
        path = self._experiment_db
        if path is None:
            return SourceStatus(
                "database",
                "absent",
                "No FIBOKI_EXPERIMENT_DB configured; the experiment ledger is "
                "not provisioned in this deployment.",
                healthy=False,
            )
        started = time.perf_counter()
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0) as conn:
                conn.execute("SELECT 1").fetchone()
                tables = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
        except Exception as exc:
            return SourceStatus(
                "database",
                "absent",
                f"Unreachable: {type(exc).__name__}",
                healthy=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        return SourceStatus(
            "database",
            "live",
            f"Reachable, {len(tables)} tables",
            healthy=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def migration_revision(self) -> str | None:
        """Read ``alembic_version`` from the database. ``None`` means unknown."""
        path = self._experiment_db
        if path is None:
            return None
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0) as conn:
                row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except Exception:
            return None
        return str(row[0]) if row else None

    def worker_heartbeat(self) -> HeartbeatReading:
        """Liveness from the worker's ``worker_heartbeat`` table, read-only."""
        return read_worker_heartbeat(
            self.settings.worker_heartbeat_path,
            stale_after_seconds=self.settings.worker_heartbeat_stale_seconds,
        )

    def worker_heartbeat_age_seconds(self) -> float | None:
        """Age of the newest worker beat, or ``None`` if none has been seen.

        ``None`` is NOT zero. A worker that has never started and a worker that
        beat a moment ago must not render the same, which is precisely the
        confusion V1's dashboard created.
        """
        return self.worker_heartbeat().age_seconds

    def data_sources(self) -> list[SourceStatus]:
        sources = [self.check_database()]
        root = self.settings.data_root
        if root is None:
            sources.append(
                SourceStatus(
                    "market_data_store",
                    "absent",
                    "No FIBOKI_DATA_ROOT configured; no bar datasets are mounted.",
                    healthy=False,
                )
            )
        elif not root.exists():
            sources.append(
                SourceStatus(
                    "market_data_store",
                    "absent",
                    "FIBOKI_DATA_ROOT is set but the path does not exist.",
                    healthy=False,
                )
            )
        else:
            sources.append(
                SourceStatus("market_data_store", "live", str(root), healthy=True)
            )

        sources.append(self.lifecycle_source())
        sources.append(
            SourceStatus(
                "strategy_registry",
                "live" if self._strategy_registry is not None else "absent",
                (
                    f"{len(self.strategy_ids)} strategy documents loaded"
                    if self._strategy_registry is not None
                    else self._strategy_error or "not loaded"
                ),
                healthy=self._strategy_registry is not None,
            )
        )
        sources.append(
            SourceStatus(
                "instrument_universe",
                "live",
                f"{len(self.instruments.all_symbols())} registered instruments",
                healthy=True,
            )
        )
        sources.append(
            SourceStatus(
                "kill_switch_journal",
                "live",
                str(self.settings.killswitch_path),
                healthy=True,
            )
        )
        intact, broken_at = self.audit.verify_chain()
        sources.append(
            SourceStatus(
                "audit_trail",
                "live",
                f"{len(self.audit)} entries, chain intact"
                if intact
                else f"CHAIN BROKEN at sequence {broken_at}",
                healthy=intact,
            )
        )
        beat = self.worker_heartbeat()
        heartbeat = beat.age_seconds
        sources.append(
            SourceStatus(
                "paper_worker",
                "absent" if beat.state == "absent" else "live",
                f"[{beat.state}/{beat.reason}] {beat.detail}",
                healthy=beat.state == "ok",
                latency_ms=None if heartbeat is None else round(heartbeat * 1000, 0),
            )
        )
        record_kind = self.data_source
        sources.append(
            SourceStatus(
                "trade_and_position_records",
                record_kind,
                self.trade_record_detail(),
                healthy=record_kind == "live",
            )
        )
        return sources

    # ------------------------------------------------------------- mode

    def mode_state(self) -> dict[str, Any]:
        mode = self.settings.execution_mode
        decision = self.mode_guard.check(
            mode,
            strategy_id=self.strategy_ids[0] if self.strategy_ids else "none",
            venue_url=self.settings.venue_url,
        )
        return {
            "mode": mode.value,
            "touches_broker": mode.touches_broker,
            "touches_real_money": mode.touches_real_money,
            "provenance": self.settings.provenance_for_execution().value,
            "live_compiled_in": LIVE_EXECUTION_COMPILED_IN,
            "guard_allowed": decision.allowed,
            "guard_controls": dict(decision.controls),
            "guard_reasons": list(decision.reasons),
        }

    def live_controls(self) -> list[dict[str, Any]]:
        """The five controls, and why each blocks. Rendered on refusal."""
        decision = self.mode_guard.check(
            ExecutionMode.LIVE,
            strategy_id=self.strategy_ids[0] if self.strategy_ids else "none",
            venue_url=self.settings.venue_url,
        )
        labels = {
            "build_time_constant": (
                "Build-time constant LIVE_EXECUTION_COMPILED_IN in "
                "fiboki/broker/mode_guard.py. Changing it is a source edit, a "
                "review and a deploy. No API call can reach it."
            ),
            "runtime_env_flag": "Runtime environment token FIBOKI_LIVE_RUNTIME_ARMED.",
            "operator_authorisation": "A persisted, expiring, human-granted authorisation.",
            "strategy_allow_list": "The specific strategy named on that authorisation.",
            "venue_hostname": "The parsed venue hostname matching the live allow-list.",
        }
        return [
            {
                "control": name,
                "satisfied": bool(decision.controls.get(name, False)),
                "explanation": labels[name],
            }
            for name in ModeGuard.LIVE_CONTROLS
        ]

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


def build_platform(settings: Settings) -> Platform:
    return Platform(settings)
