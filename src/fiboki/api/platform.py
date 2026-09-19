"""The read-model container: one object that owns every source the API reads.

Everything here is either a REAL domain object from another package (the
strategy registry, the instrument universe, the kill switch, the mode guard,
the limit sets, the experiment ledger, the audit ledger) or a clearly-labelled
deterministic fixture from :mod:`fiboki.api.seed`.

``data_source`` reports which, per source, and the system router publishes it.
The rule this package follows: never make an unprovisioned source look
provisioned. V1's dashboard drew a flat £0.00 line with an "Online" badge when
the backend was down; the whole design here is that absence is visible.
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

__all__ = ["Platform", "SourceStatus", "build_platform"]


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


class Platform:
    """Assembled once per process and shared. Read-mostly, lock-guarded writes."""

    def __init__(self, settings: Settings) -> None:
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

        # ---- fixtures, labelled --------------------------------------
        self.clock = SeedClock.fixed()
        symbols = self.instruments.all_symbols()
        self._trades, self._positions = generate(
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

    # ------------------------------------------------------------ trades

    def trades(
        self,
        *,
        provenance: set[Provenance] | None = None,
        strategy_id: str | None = None,
        instrument: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[TradeRow], int]:
        rows = self._trades
        if provenance:
            rows = [t for t in rows if t.provenance in provenance]
        if strategy_id:
            rows = [t for t in rows if t.strategy_id == strategy_id]
        if instrument:
            rows = [t for t in rows if t.instrument == instrument]
        rows = sorted(rows, key=lambda t: t.exit_time, reverse=True)
        return rows[offset : offset + limit], len(rows)

    def positions(self) -> list[PositionRow]:
        return list(self._positions)

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

    def worker_heartbeat_age_seconds(self) -> float | None:
        """Age of the worker heartbeat file, or ``None`` if it has never beaten.

        ``None`` is NOT zero. A worker that has never started and a worker that
        beat a moment ago must not render the same, which is precisely the
        confusion V1's dashboard created.
        """
        path = self.settings.worker_heartbeat_path
        if path is None or not path.exists():
            return None
        try:
            return max(0.0, time.time() - path.stat().st_mtime)
        except OSError:  # pragma: no cover - race on a vanishing file
            return None

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
        heartbeat = self.worker_heartbeat_age_seconds()
        sources.append(
            SourceStatus(
                "paper_worker",
                "absent" if heartbeat is None else "live",
                "No heartbeat has ever been written by a worker in this deployment."
                if heartbeat is None
                else f"Last beat {heartbeat:.0f}s ago",
                healthy=(
                    heartbeat is not None
                    and heartbeat <= self.settings.worker_heartbeat_stale_seconds
                ),
                latency_ms=None if heartbeat is None else round(heartbeat * 1000, 0),
            )
        )
        sources.append(
            SourceStatus(
                "trade_and_position_records",
                "seed",
                "Deterministic demonstration fixture (fiboki.api.seed). No paper "
                "engine journal or broker session is provisioned in this "
                "deployment, so these rows are generated, not measured.",
                healthy=False,
            )
        )
        return sources

    @property
    def data_source(self) -> str:
        return "seed"

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
