"""A health endpoint that can actually fail.

V1's returned ``{"status": "ok", "version": "1.0.0"}`` from a literal and was
green with the database stopped. Every field below is measured at request time:

* **database** — a real connection and a real ``SELECT 1``.
* **migration_revision** — the ledger's stamped ``schema_revision`` (V1's
  ``alembic_version`` as fallback) compared with the revision this code would
  create. ``null`` (unknown) and a mismatch are both degradations.
* **build_sha** — injected at deploy. Empty is reported as unknown.
* **worker_heartbeat_age_seconds** — ``null`` when a worker has never beaten,
  which is a different state from ``0`` and renders differently.

The overall status is the worst component status, never an average and never an
assertion. ``ok`` requires every critical check to pass.
"""
from __future__ import annotations

import platform as py_platform
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.platform import HeartbeatReading, Platform
from fiboki.api.settings import Settings


def expected_schema_revision() -> str:
    """What this build's ledger code would stamp (lazy import: api must not load
    the research package at import time)."""
    from fiboki.research.experiment import schema_revision

    return schema_revision()

__all__ = [
    "HealthCheck",
    "HealthReport",
    "build_health",
    "heartbeat_check",
    "paper_journal_check",
]

_ORDER = {"ok": 0, "degraded": 1, "down": 2}


class HealthCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: str = Field(pattern="^(ok|degraded|down)$")
    detail: str
    critical: bool = True
    latency_ms: float | None = None


class HealthReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = Field(pattern="^(ok|degraded|down)$")
    checked_at: datetime
    build_sha: str | None
    build_time: str | None
    migration_revision: str | None
    execution_mode: str
    worker_heartbeat_age_seconds: float | None
    uptime_seconds: float
    python_version: str
    checks: tuple[HealthCheck, ...]
    #: Set when the report itself should not be trusted as a green light.
    advisory: str = ""


#: Heartbeat reasons that mean "nothing has ever been written where we look".
_NEVER_WRITTEN = {"no_path", "file_missing", "no_heartbeat_table", "no_rows"}


def heartbeat_check(beat: HeartbeatReading, stale_after: float) -> tuple[str, str]:
    """``(status, detail)`` for the worker heartbeat, from one reading.

    The staleness rule is the platform's (``age >= stale_after`` is stale,
    :func:`fiboki.api.platform.read_worker_heartbeat`); this function only
    words it. A beat exactly at the threshold is therefore stale here too.
    """
    if beat.state == "absent":
        if beat.reason == "unreadable":
            return (
                "down",
                "The worker heartbeat store is unreadable, so worker liveness is "
                f"UNKNOWN (not 'never started'). {beat.detail}",
            )
        if beat.reason in _NEVER_WRITTEN:
            return (
                "down",
                "No worker heartbeat has ever been written. Nothing is evaluating "
                f"signals in this deployment. [{beat.reason}] {beat.detail}",
            )
        return ("down", f"Worker liveness is unknown [{beat.reason}]: {beat.detail}")
    age = beat.age_seconds if beat.age_seconds is not None else float("nan")
    suffix = " (mtime fallback, not a heartbeat row)" if beat.reason == "mtime_fallback" else ""
    if beat.state == "stale":
        return (
            "down",
            f"Worker heartbeat is {age:.0f}s old (stale at {stale_after:.0f}s){suffix}.",
        )
    return ("ok", f"Worker beat {age:.0f}s ago{suffix}.")


def paper_journal_check(platform: Platform) -> HealthCheck | None:
    """A paper journal that exists but does not fully parse degrades health.

    ``None`` when no journal exists at all: the seed fixture is then served and
    the ``data_provenance`` check already says so.
    """
    try:
        journal = platform.journal
    except Exception as exc:  # pragma: no cover - reader never raises by contract
        return HealthCheck(
            name="paper_journal",
            status="degraded",
            detail=f"The paper journal could not be read ({type(exc).__name__}).",
            critical=False,
        )
    if journal is None:
        return None
    if not journal.sessions:
        return HealthCheck(
            name="paper_journal",
            status="degraded",
            detail=(
                f"A paper journal exists at {journal.root} but none of its "
                f"{len(journal.errors)} session(s) could be read; trades, positions "
                "and the account are UNAVAILABLE, not empty."
            ),
            critical=False,
        )
    if journal.errors:
        names = ", ".join(name for name, _ in journal.errors)
        return HealthCheck(
            name="paper_journal",
            status="degraded",
            detail=(
                f"{len(journal.errors)} paper session(s) could not be read and are "
                f"excluded from every figure: {names}."
            ),
            critical=False,
        )
    return HealthCheck(
        name="paper_journal",
        status="ok",
        detail=f"{len(journal.sessions)} paper session(s) read without error.",
        critical=False,
    )


def build_health(platform: Platform, settings: Settings) -> HealthReport:
    checks: list[HealthCheck] = []

    db = platform.check_database()
    checks.append(
        HealthCheck(
            name="database",
            status="ok" if db.healthy else "down",
            detail=db.detail,
            critical=True,
            latency_ms=db.latency_ms,
        )
    )

    revision = platform.migration_revision()
    expected = expected_schema_revision()
    if revision is None:
        revision_status, revision_detail = (
            "degraded",
            "No schema_revision (or alembic_version) row is readable. The schema "
            "version of this deployment is unknown; the worker stamps it when it "
            "opens the ledger.",
        )
    elif revision == expected:
        revision_status, revision_detail = "ok", f"at revision {revision}"
    elif revision.startswith("ledger_"):
        revision_status, revision_detail = (
            "degraded",
            f"database at {revision}, this code expects {expected}: the ledger was "
            "last opened by a different build (restart the worker after upgrading).",
        )
    else:
        # A V1 alembic revision: known, but not this code's schema.
        revision_status, revision_detail = "degraded", (
            f"at V1 migration {revision}; the V2 ledger has not stamped this file yet."
        )
    checks.append(
        HealthCheck(
            name="migration_revision",
            status=revision_status,
            detail=revision_detail,
            critical=False,
        )
    )

    checks.append(
        HealthCheck(
            name="build_identity",
            status="ok" if settings.build_sha else "degraded",
            detail=(
                f"build {settings.build_sha}"
                if settings.build_sha
                else "FIBOKI_BUILD_SHA is not set; this process cannot say which "
                "commit it is running."
            ),
            critical=False,
        )
    )

    # One reading, judged by the platform's own rule (age >= threshold is
    # stale), so /api/health, /api/system/workers and the stream heartbeat can
    # never disagree about the same beat. The detail is keyed on WHY the age is
    # unknown: an unreadable store is not a store nobody ever wrote to.
    beat = platform.worker_heartbeat()
    age = beat.age_seconds
    worker_status, worker_detail = heartbeat_check(beat, settings.worker_heartbeat_stale_seconds)
    checks.append(
        HealthCheck(
            name="worker_heartbeat",
            status=worker_status,
            detail=worker_detail,
            critical=True,
        )
    )

    journal_check = paper_journal_check(platform)
    if journal_check is not None:
        checks.append(journal_check)

    intact, broken_at = platform.audit.verify_chain()
    checks.append(
        HealthCheck(
            name="audit_chain",
            status="ok" if intact else "down",
            detail=(
                f"{len(platform.audit)} entries, hash chain verified"
                if intact
                else f"Hash chain broken at sequence {broken_at}. The operator "
                "audit record cannot be trusted."
            ),
            critical=True,
        )
    )

    checks.append(
        HealthCheck(
            name="session_secret",
            status="ok" if not settings.session_secret_is_ephemeral else "degraded",
            detail=(
                "Session secret supplied by the environment."
                if not settings.session_secret_is_ephemeral
                else "FIBOKI_SESSION_SECRET is unset, so the signing key is "
                "per-process. Every restart signs every operator out."
            ),
            critical=False,
        )
    )

    checks.append(
        HealthCheck(
            name="origin_allow_list",
            status="ok" if settings.allowed_origins else "degraded",
            detail=(
                f"{len(settings.allowed_origins)} allowed origin(s)"
                if settings.allowed_origins
                else "FIBOKI_ALLOWED_ORIGINS is empty, so every mutating request "
                "will be refused. Set it to the workstation's origin."
            ),
            critical=False,
        )
    )

    ks = platform.kill_switch.state
    checks.append(
        HealthCheck(
            name="kill_switch",
            status="ok" if not ks.active else "degraded",
            detail=(
                "Disarmed."
                if not ks.active
                else f"ARMED in {ks.mode.value if ks.mode else '?'} by "
                f"{ks.operator}: {ks.reason}"
            ),
            critical=False,
        )
    )

    seed_sources = [s for s in platform.data_sources() if s.kind == "seed"]
    if seed_sources:
        checks.append(
            HealthCheck(
                name="data_provenance",
                status="degraded",
                detail=(
                    "Some surfaces are served from a deterministic fixture, not a "
                    "measurement: " + ", ".join(s.name for s in seed_sources)
                ),
                critical=False,
            )
        )

    worst = "ok"
    for check in checks:
        rank = _ORDER[check.status]
        if not check.critical and check.status == "down":
            rank = _ORDER["degraded"]
        if rank > _ORDER[worst]:
            worst = next(k for k, v in _ORDER.items() if v == rank)

    advisory = ""
    if worst != "ok":
        advisory = (
            "This deployment is not fully healthy. Do not read a flat or empty "
            "screen as a quiet market until every check below is ok."
        )

    return HealthReport(
        status=worst,
        checked_at=datetime.now(tz=UTC),
        build_sha=settings.build_sha or None,
        build_time=settings.build_time or None,
        migration_revision=revision,
        execution_mode=settings.execution_mode.value,
        worker_heartbeat_age_seconds=age,
        uptime_seconds=round(platform.uptime_seconds, 1),
        python_version=py_platform.python_version(),
        checks=tuple(checks),
        advisory=advisory,
    )


