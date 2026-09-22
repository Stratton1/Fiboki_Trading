"""Deploy-time configuration. Everything dangerous is read here and nowhere else.

The execution mode is read ONCE, at process start, from the environment. There
is deliberately no setter: :mod:`fiboki.api.routers.system` exposes the mode as
data and refuses every request to change it. Moving to LIVE additionally
requires the five independent controls in :mod:`fiboki.broker.mode_guard`, the
first of which is a source constant.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from fiboki.core.enums import ExecutionMode, Provenance

__all__ = [
    "MODE_PROVENANCE",
    "Settings",
    "load_settings",
]

#: How a figure produced *by execution in this mode* must be labelled. Used so
#: a trade row's provenance is derived from where it actually happened.
MODE_PROVENANCE: dict[ExecutionMode, Provenance] = {
    ExecutionMode.BACKTEST: Provenance.BACKTEST,
    ExecutionMode.PAPER: Provenance.PAPER,
    ExecutionMode.SHADOW: Provenance.SHADOW,
    ExecutionMode.DEMO: Provenance.BROKER_DEMO,
    ExecutionMode.LIVE: Provenance.BROKER_LIVE,
}

_TRUE = {"1", "true", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in _TRUE


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable process configuration."""

    #: The mode this deployment runs in. Read from ``FIBOKI_EXECUTION_MODE``.
    execution_mode: ExecutionMode = ExecutionMode.PAPER

    #: Exact origins allowed to make a mutating request. Not a prefix match and
    #: not a regex: V1 had no Origin check at all, which left every no-body POST
    #: (arm, disarm, promote, approve) reachable from any page on the internet.
    allowed_origins: tuple[str, ...] = ()

    #: Cookie hardening. ``secure`` defaults to True and is only turned off for
    #: local http development, explicitly.
    cookie_name: str = "fiboki_session"
    cookie_secure: bool = True
    cookie_samesite: str = "strict"
    cookie_domain: str | None = None
    session_ttl_seconds: int = 12 * 3600

    #: HMAC key for the session cookie. Generated per-process if unset, which
    #: means restarts invalidate sessions — correct for a single-node dev box
    #: and reported as a health warning so nobody ships it that way.
    session_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    session_secret_is_ephemeral: bool = True

    #: Login rate limiting, per source identity.
    login_max_attempts: int = 5
    login_window_seconds: int = 300
    login_lockout_seconds: int = 900

    #: Where mutable operator state lives (kill-switch journal, audit trail,
    #: sessions). Never inside the source tree.
    state_dir: Path = field(default_factory=lambda: Path("var"))

    #: Market-data root and catalogue, if provisioned.
    data_root: Path | None = None
    experiment_db: Path | None = None

    #: Build identity, injected by the deploy. Empty means "unknown", which the
    #: health endpoint reports as a degradation rather than hiding.
    build_sha: str = ""
    build_time: str = ""

    #: A worker writes this file periodically. Its age is the heartbeat.
    worker_heartbeat_path: Path | None = None
    worker_heartbeat_stale_seconds: float = 120.0

    #: Broker venue this deployment would talk to, if any. Used by the mode
    #: guard's parsed-hostname control.
    venue_url: str = ""

    #: Realism assumptions actually in force. These feed the computed caveats
    #: attached to performance figures — the fix for V1's hardcoded prose.
    slippage_model: str = "zero"
    spread_model: str = "static_typical"
    financing_model: str = "none"
    fx_conversion_model: str = "static_rate"

    @property
    def cookie_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "httponly": True,
            "secure": self.cookie_secure,
            "samesite": self.cookie_samesite,
            "path": "/",
        }
        if self.cookie_domain:
            kwargs["domain"] = self.cookie_domain
        return kwargs

    @property
    def sessions_path(self) -> Path:
        return self.state_dir / "sessions.json"

    @property
    def killswitch_path(self) -> Path:
        return self.state_dir / "killswitch.jsonl"

    @property
    def audit_path(self) -> Path:
        return self.state_dir / "api_audit.jsonl"

    @property
    def live_authorisation_path(self) -> Path:
        return self.state_dir / "live_authorisation.json"

    def provenance_for_execution(self) -> Provenance:
        return MODE_PROVENANCE[self.execution_mode]


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from the environment.

    An unrecognised ``FIBOKI_EXECUTION_MODE`` is a startup error, not a silent
    fallback to paper: a typo must not decide where orders go.
    """
    source = dict(os.environ if env is None else env)

    def get(name: str, default: str = "") -> str:
        return str(source.get(name, default)).strip()

    raw_mode = get("FIBOKI_EXECUTION_MODE", ExecutionMode.PAPER.value).lower()
    try:
        mode = ExecutionMode(raw_mode)
    except ValueError as exc:  # pragma: no cover - configuration error path
        valid = ", ".join(m.value for m in ExecutionMode)
        raise RuntimeError(
            f"FIBOKI_EXECUTION_MODE={raw_mode!r} is not a valid execution mode "
            f"(one of: {valid}). Refusing to start rather than guess."
        ) from exc

    origins = tuple(
        o.strip().rstrip("/")
        for o in get("FIBOKI_ALLOWED_ORIGINS").split(",")
        if o.strip()
    )

    secret_text = get("FIBOKI_SESSION_SECRET")
    if secret_text:
        secret, ephemeral = secret_text.encode("utf-8"), False
    else:
        secret, ephemeral = secrets.token_bytes(32), True

    state_dir = Path(get("FIBOKI_STATE_DIR", "var"))
    data_root = get("FIBOKI_DATA_ROOT")
    experiment_db = get("FIBOKI_EXPERIMENT_DB")
    heartbeat = get("FIBOKI_WORKER_HEARTBEAT")

    def _flag_from(name: str, default: bool) -> bool:
        raw = get(name)
        return default if not raw else raw.lower() in _TRUE

    return Settings(
        execution_mode=mode,
        allowed_origins=origins,
        cookie_name=get("FIBOKI_COOKIE_NAME", "fiboki_session"),
        cookie_secure=_flag_from("FIBOKI_COOKIE_SECURE", True),
        cookie_samesite=get("FIBOKI_COOKIE_SAMESITE", "strict").lower(),
        cookie_domain=get("FIBOKI_COOKIE_DOMAIN") or None,
        session_ttl_seconds=int(get("FIBOKI_SESSION_TTL", "43200")),
        session_secret=secret,
        session_secret_is_ephemeral=ephemeral,
        state_dir=state_dir,
        data_root=Path(data_root) if data_root else None,
        experiment_db=Path(experiment_db) if experiment_db else None,
        build_sha=get("FIBOKI_BUILD_SHA"),
        build_time=get("FIBOKI_BUILD_TIME"),
        worker_heartbeat_path=Path(heartbeat) if heartbeat else state_dir / "worker.heartbeat",
        worker_heartbeat_stale_seconds=float(get("FIBOKI_WORKER_STALE_SECONDS", "120")),
        venue_url=get("FIBOKI_VENUE_URL"),
        slippage_model=get("FIBOKI_SLIPPAGE_MODEL", "zero"),
        spread_model=get("FIBOKI_SPREAD_MODEL", "static_typical"),
        financing_model=get("FIBOKI_FINANCING_MODEL", "none"),
        fx_conversion_model=get("FIBOKI_FX_MODEL", "static_rate"),
    )
