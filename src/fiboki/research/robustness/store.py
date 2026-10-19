"""The robustness result cache and progress log: ``<state_dir>/research/robustness.sqlite``.

Written ONLY by the research worker's ``robustness`` job handler; read by the
API with a read-only connection (``mode=ro``). Two tables:

``result``
    One row per cache key: the diagnostic's full output as JSON, the engine
    fingerprint it was computed under, and when. Append-only in spirit: a key
    is a content hash of everything that can change the answer (see
    :func:`cache_key`), so the same key is the same answer forever and a second
    write of it is ignored.
``progress``
    The latest ``(done, total, stage)`` a running job reported, keyed by the
    same cache key, so the run page can show how far a job has got.

The cache key is keyed on the ENGINE FINGERPRINT: the engine generation
(``ENGINE_VERSION``), the cost profile's own fingerprint, sizing, construction,
account and capital, plus the run's bound content hash, dataset version, ledger
hash, research window, holdout boundary, the diagnostic's options and this
package's :data:`ROBUSTNESS_VERSION`. Bumping any of them makes every earlier
answer unreachable rather than wrong.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["ROBUSTNESS_VERSION", "RobustnessStore", "cache_key", "engine_fingerprint", "read_entry",
           "store_path"]

#: Bump when a diagnostic's computation or output shape changes.
ROBUSTNESS_VERSION = "robustness_v1"


def store_path(state_dir: Path | str) -> Path:
    """THE store path for a state directory, from the one resolver (``core/paths``)."""
    from fiboki.core.paths import resolve_paths

    return resolve_paths({"FIBOKI_STATE_DIR": str(state_dir)}).robustness_db


def engine_fingerprint(binding: Mapping[str, Any]) -> dict[str, Any]:
    """What about the EXECUTION MODEL can change a re-run's answer."""
    from fiboki.backtest.version import ENGINE_VERSION
    from fiboki.sim.profiles import get_profile

    return {
        "engine_version": ENGINE_VERSION,
        "profile": get_profile(str(binding["profile_name"])).fingerprint(),
        "sizing_policy": binding.get("sizing_policy"),
        "construction": binding.get("construction"),
        "account_ccy": binding.get("account_ccy"),
        "initial_balance": binding.get("initial_balance"),
        "risk_fraction": binding.get("risk_fraction"),
    }


def cache_key(kind: str, binding: Mapping[str, Any], options: Mapping[str, Any]) -> str:
    blob = json.dumps({
        "version": ROBUSTNESS_VERSION, "kind": kind,
        "engine": engine_fingerprint(binding),
        "binding": {k: binding.get(k) for k in sorted(binding)},
        "options": {k: options[k] for k in sorted(options)},
    }, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


_DDL = (
    "CREATE TABLE IF NOT EXISTS result (cache_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, "
    "kind TEXT NOT NULL, created_at TEXT NOT NULL, job_id TEXT NOT NULL DEFAULT '', "
    "fingerprint_json TEXT NOT NULL, result_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS progress (cache_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, "
    "kind TEXT NOT NULL, job_id TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL, total INTEGER NOT NULL, "
    "stage TEXT NOT NULL, updated_at TEXT NOT NULL)",
)


class RobustnessStore:
    """The worker-side writer."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            for ddl in _DDL:
                conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def progress(self, key: str, *, run_id: str, kind: str, job_id: str, done: int, total: int,
                 stage: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO progress (cache_key, run_id, kind, job_id, done, total, stage, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET done=excluded.done, "
                "total=excluded.total, stage=excluded.stage, job_id=excluded.job_id, "
                "updated_at=excluded.updated_at",
                (key, run_id, kind, job_id, int(done), int(total), stage[:200],
                 datetime.now(tz=UTC).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def put(self, key: str, *, run_id: str, kind: str, job_id: str, fingerprint: Mapping[str, Any],
            result: Mapping[str, Any]) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO result (cache_key, run_id, kind, created_at, job_id, "
                "fingerprint_json, result_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, run_id, kind, datetime.now(tz=UTC).isoformat(), job_id,
                 json.dumps(dict(fingerprint), sort_keys=True, default=str),
                 json.dumps(dict(result), sort_keys=True, default=_json_default)),
            )
            conn.commit()
        finally:
            conn.close()

    def get(self, key: str) -> dict[str, Any] | None:
        return read_entry(self.path, key).get("result")


def _json_default(value: Any) -> Any:
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, set | tuple):
        return list(value)
    return str(value)


def read_entry(path: Path | str, key: str) -> dict[str, Any]:
    """``{"result": ..., "created_at": ..., "job_id": ..., "progress": ...}``, read-only."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return {}
    out: dict[str, Any] = {}
    try:
        row = conn.execute("SELECT result_json, created_at, job_id, fingerprint_json FROM result "
                           "WHERE cache_key = ?", (key,)).fetchone()
        if row:
            out["result"] = json.loads(row[0])
            out["created_at"] = row[1]
            out["job_id"] = row[2]
            out["fingerprint"] = json.loads(row[3])
        prog = conn.execute("SELECT done, total, stage, updated_at, job_id FROM progress WHERE cache_key = ?",
                            (key,)).fetchone()
        if prog:
            out["progress"] = {"done": int(prog[0]), "total": int(prog[1]), "stage": str(prog[2]),
                               "updated_at": prog[3], "job_id": prog[4]}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return out
