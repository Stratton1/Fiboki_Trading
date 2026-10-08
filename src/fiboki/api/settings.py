"""Deploy-time configuration. Everything dangerous is read here and nowhere else.

The execution mode is read ONCE, at process start, from the environment. There
is deliberately no setter: :mod:`fiboki.api.routers.system` exposes the mode as
data and refuses every request to change it. Moving to LIVE additionally
requires the five independent controls in :mod:`fiboki.broker.mode_guard`, the
first of which is a source constant.

Hygiene
-------
* Booleans are parsed strictly: ``1/0/true/false/yes/no/on/off`` (any case)
  and nothing else.  ``FIBOKI_COOKIE_SECURE=ture`` is a startup error, not a
  silent ``False``.
* :data:`ENV_REGISTRY` declares every ``FIBOKI_*`` variable the platform reads:
  the ones :func:`load_settings` parses here, and the ones other modules read
  directly (``read_by`` names them).
* :func:`warn_unknown_env` lists ``FIBOKI_*`` / ``FIBOKEI_*`` names nobody
  reads -- a misspelling, or a V1 leftover.  :func:`load_settings` treats any
  as a startup error in DEMO or LIVE and logs one warning otherwise.
"""
from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from fiboki.core.enums import ExecutionMode, Provenance
from fiboki.core.paths import FibokiPaths, resolve_paths
from fiboki.obs.health import HealthThresholds

__all__ = [
    "ENV_REGISTRY",
    "KNOWN_ENV_NAMES",
    "MODE_PROVENANCE",
    "EnvVar",
    "HealthThresholds",
    "Settings",
    "SettingsError",
    "health_thresholds_from_env",
    "load_settings",
    "parse_bool",
    "warn_unknown_env",
]

log = logging.getLogger("fiboki.api.settings")


class SettingsError(RuntimeError, ValueError):
    """The environment cannot be turned into a :class:`Settings`.

    Subclasses ``RuntimeError`` (what an invalid execution mode has always
    raised) and ``ValueError`` (what an unparseable number has always raised),
    so existing handlers keep catching it.
    """

#: How a figure produced *by execution in this mode* must be labelled. Used so
#: a trade row's provenance is derived from where it actually happened.
MODE_PROVENANCE: dict[ExecutionMode, Provenance] = {
    ExecutionMode.BACKTEST: Provenance.BACKTEST,
    ExecutionMode.PAPER: Provenance.PAPER,
    ExecutionMode.SHADOW: Provenance.SHADOW,
    ExecutionMode.DEMO: Provenance.BROKER_DEMO,
    ExecutionMode.LIVE: Provenance.BROKER_LIVE,
}

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

#: Modes in which configuration mistakes are refused rather than warned about:
#: the ones that talk to a broker.
_STRICT_MODES = frozenset({ExecutionMode.DEMO, ExecutionMode.LIVE})

_ENV_PREFIXES = ("FIBOKI_", "FIBOKEI_")


def parse_bool(name: str, raw: str, default: bool) -> bool:
    """Strict boolean.  Empty means ``default``; anything unrecognised raises."""
    text = raw.strip().lower()
    if not text:
        return default
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise SettingsError(
        f"{name}={raw!r} is not a boolean; use one of "
        f"{sorted(_TRUE | _FALSE)} (case-insensitive). Refusing to guess."
    )


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    return parse_bool(name, _env(name), default)


@dataclass(frozen=True, slots=True)
class EnvVar:
    """One declared environment variable.

    ``required_in_modes`` is DECLARATIVE: it records where an operator must set
    the variable, and is not enforced by :func:`load_settings` (every variable
    here still has a default, so enforcement would be a behaviour change).
    """

    name: str
    type: str
    default: str
    description: str
    required_in_modes: frozenset[ExecutionMode] = frozenset()
    read_by: str = "fiboki.api.settings"


_BROKER_MODES = frozenset({ExecutionMode.DEMO, ExecutionMode.LIVE})

#: Every ``FIBOKI_*`` variable the platform reads.  The first block is parsed by
#: :func:`load_settings`; the second is read directly by the named module.
#: ``tests/unit/test_api_settings_hygiene.py`` fails if a module starts reading
#: a ``FIBOKI_*`` name that is not declared here.
ENV_REGISTRY: tuple[EnvVar, ...] = (
    EnvVar("FIBOKI_EXECUTION_MODE", "enum", "paper",
           "Execution mode; an unknown value refuses to start."),
    EnvVar("FIBOKI_ALLOWED_ORIGINS", "csv", "",
           "Exact origins allowed to make a mutating request.", _BROKER_MODES),
    EnvVar("FIBOKI_COOKIE_NAME", "str", "fiboki_session", "Session cookie name."),
    EnvVar("FIBOKI_COOKIE_SECURE", "bool", "true",
           "Secure flag on the session cookie; off only for local http."),
    EnvVar("FIBOKI_COOKIE_SAMESITE", "str", "strict", "SameSite attribute of the cookie."),
    EnvVar("FIBOKI_COOKIE_DOMAIN", "str", "", "Cookie domain; empty means host-only."),
    EnvVar("FIBOKI_SESSION_TTL", "int", "43200", "Session lifetime in seconds."),
    EnvVar("FIBOKI_SESSION_SECRET", "secret", "",
           "Session HMAC key; empty means a per-process ephemeral key.", _BROKER_MODES),
    EnvVar("FIBOKI_STATE_DIR", "path", "var",
           "Operator state directory: kill-switch journal, intent/audit ledgers, holdout, "
           "alert outbox. Set it in every process; relative means the process's cwd.",
           read_by="fiboki.core.paths"),
    EnvVar("FIBOKI_DATA_ROOT", "path", "", "Market-data root, if provisioned.",
           read_by="fiboki.core.paths"),
    EnvVar("FIBOKI_EXPERIMENT_DB", "path", "", "Experiment ledger database, if provisioned.",
           read_by="fiboki.core.paths"),
    EnvVar("FIBOKI_BUILD_SHA", "str", "", "Build commit, injected by the deploy."),
    EnvVar("FIBOKI_BUILD_TIME", "str", "", "Build time, injected by the deploy."),
    EnvVar("FIBOKI_WORKER_HEARTBEAT", "path", "<state_dir>/worker.heartbeat",
           "Worker heartbeat file.", read_by="fiboki.core.paths"),
    EnvVar("FIBOKI_WORKER_STALE_SECONDS", "float", "120",
           "Heartbeat age after which a worker is reported stale (HealthThresholds)."),
    EnvVar("FIBOKI_WORKER_DOWN_SECONDS", "float", "300",
           "Heartbeat age after which a worker is reported down (HealthThresholds)."),
    EnvVar("FIBOKI_DATA_STALE_SECONDS", "float", "900",
           "Newest-bar age after which the live worker alerts DATA_STALE (HealthThresholds)."),
    EnvVar("FIBOKI_VENUE_URL", "str", "",
           "Broker venue URL, checked by the mode guard's parsed-hostname control."),
    EnvVar("FIBOKI_SLIPPAGE_MODEL", "str", "zero", "Slippage assumption in force."),
    EnvVar("FIBOKI_SPREAD_MODEL", "str", "static_typical", "Spread assumption in force."),
    EnvVar("FIBOKI_FINANCING_MODEL", "str", "none", "Financing assumption in force."),
    EnvVar("FIBOKI_FX_MODEL", "str", "static_rate", "FX conversion assumption in force."),
    # -- read directly by other modules ------------------------------------
    EnvVar("FIBOKI_OPERATORS", "str", "",
           "Operator directory, user:role:hash; hash is scrypt$... (legacy sha256 hex: rotate).",
           _BROKER_MODES, read_by="fiboki.api.routers.auth"),
    EnvVar("FIBOKI_PAPER_ROOT", "path", "<FIBOKI_STATE_DIR>/paper",
           "Paper journal root (read-only data source).", read_by="fiboki.core.paths"),
    EnvVar("FIBOKI_LIVE_RUNTIME_ARMED", "secret", "",
           "Mode-guard control 2: the exact live arm token.",
           read_by="fiboki.broker.mode_guard"),
    EnvVar("FIBOKI_OANDA_LIVE_RUNTIME", "secret", "", "OANDA live runtime token.",
           read_by="fiboki.broker.oanda"),
    EnvVar("FIBOKI_LIVE_EXECUTION_ENABLED", "bool", "", "CLI-level live flag, reported by doctor.",
           read_by="fiboki.cli"),
    EnvVar("FIBOKI_HOME", "path", "~/.fiboki", "Per-user home: env file, worker state db.",
           read_by="fiboki.core.paths"),
    EnvVar("FIBOKI_STATE_DB", "path", "<FIBOKI_HOME>/state.db",
           "Worker lease + heartbeat database.", read_by="fiboki.core.paths"),
    EnvVar("FIBOKI_EXPECTED_WORKERS", "csv", "", "Workers the supervisor expects to see.",
           read_by="fiboki.workers.base"),
    EnvVar("FIBOKI_ALERT_LOG", "path", "", "Alert log file.", read_by="fiboki.core.paths"),
    EnvVar("FIBOKI_ALERT_WEBHOOK_URL", "secret", "", "Alert webhook.",
           read_by="fiboki.obs.alerts"),
    EnvVar("FIBOKI_TELEGRAM_BOT_TOKEN", "secret", "", "Telegram alert bot token.",
           read_by="fiboki.obs.alerts"),
    EnvVar("FIBOKI_TELEGRAM_CHAT_ID", "str", "", "Telegram alert chat id.",
           read_by="fiboki.obs.alerts"),
    EnvVar("FIBOKI_LOG_FORMAT", "str", "json", "'plain' switches off JSON logs.",
           read_by="fiboki.obs.logging"),
    EnvVar("FIBOKI_LOG_HTTP", "str", "WARNING",
           "Level for the httpx/httpcore per-request loggers; INFO shows request lines.",
           read_by="fiboki.obs.logging"),
    EnvVar("FIBOKI_DATABASE_URL", "str", "", "Operational database URL (deploy config).",
           read_by="fiboki.obs.health"),
    EnvVar("FIBOKI_CODE_VERSION", "str", "", "Code version stamped on validation reports.",
           read_by="fiboki.validation.report"),
    EnvVar("FIBOKI_GPU_COUNT", "int", "", "GPU count override for the scheduler.",
           read_by="fiboki.workers.scheduler"),
    EnvVar("FIBOKI_AGENT_CYCLES", "bool", "false",
           "Compose the agent research runtime in the research worker (handlers, "
           "nightly cycle, incident investigations). Off: the worker is unchanged.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_AGENT_PROVIDER", "enum", "echo",
           "'echo' (offline test double) or 'local' (Ollama via LocalHTTPProvider).",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_AGENT_LOCAL_URL", "str", "http://127.0.0.1:11434",
           "Base URL of the local model server when FIBOKI_AGENT_PROVIDER=local.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_AGENT_LOCAL_MODEL", "str", "",
           "Exact local model name; mandatory when FIBOKI_AGENT_PROVIDER=local.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_AGENT_CYCLE_UTC", "str", "02:15",
           "UTC time of day (HH:MM) of the nightly agent research cycle.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_AGENT_CYCLE_TARGET", "str", "",
           "Nightly cycle target, strategy_id:INSTRUMENT:TIMEFRAME; required when "
           "FIBOKI_AGENT_CYCLES is on.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_STRATEGIES_DIR", "path", "research/strategies",
           "Strategy documents the research runtime registers.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_EVENT_SCAN_MINUTES", "int", "15",
           "Minutes between agent event scans (headline classification into the "
           "quarantined annotation store) when FIBOKI_AGENT_CYCLES is on; 0 turns it off.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_THESIS_DEBATE_INSTRUMENTS", "csv", "",
           "Instruments that get one thesis debate per FIBOKI_THESIS_DEBATE_TIMEFRAME bar "
           "close when FIBOKI_AGENT_CYCLES is on; empty schedules none.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_THESIS_DEBATE_TIMEFRAME", "enum", "H4",
           "Bar close that schedules a thesis debate: 'H4' or 'D1'.",
           read_by="fiboki.workers.research_runtime"),
    EnvVar("FIBOKI_FINNHUB_API_KEY", "secret", "",
           "Finnhub key: news (free tier, 60 calls/min enforced client-side) and the "
           "premium-only economic calendar; empty leaves both off.",
           read_by="fiboki.data.news.sources"),
    EnvVar("FIBOKI_GDELT_ENABLED", "bool", "false",
           "Opt in to the GDELT DOC 2.0 headline queries (free, open; 1 request per 5 s).",
           read_by="fiboki.data.news.sources"),
    EnvVar("FIBOKI_FF_CALENDAR_OPT_IN", "bool", "false",
           "Opt in to the ForexFactory weekly feed as a comparison calendar; its terms "
           "prohibit copying the feed and state no licence for the export.",
           read_by="fiboki.data.providers.forexfactory_feed"),
    EnvVar("FIBOKI_OANDA_BOOKS_TOKEN", "secret", "",
           "OANDA v20 token used ONLY for read-only position/order book snapshots.",
           read_by="fiboki.data.positioning.oanda_books"),
    EnvVar("FIBOKI_OANDA_BOOKS_ENVIRONMENT", "enum", "practice",
           "'practice' or 'live': which v20 REST host serves the token's books (GET only).",
           read_by="fiboki.data.positioning.oanda_books"),
    EnvVar("FIBOKI_MYFXBOOK_EMAIL", "secret", "",
           "Myfxbook account email for the Community Outlook API (personal use).",
           read_by="fiboki.data.positioning.myfxbook"),
    EnvVar("FIBOKI_MYFXBOOK_PASSWORD", "secret", "",
           "Myfxbook password; the API sends it in the query string, scrubbed from errors.",
           read_by="fiboki.data.positioning.myfxbook"),
    EnvVar("FIBOKI_MARKETAUX_API_KEY", "secret", "",
           "Marketaux news token; empty leaves the Marketaux headline source off.",
           read_by="fiboki.data.news.sources"),
    # read_by is spelt as a path for these two: the dotted module name may not
    # appear outside entrypoints/ and cli.py (only the CLI imports entrypoints).
    EnvVar("FIBOKI_OANDA_PRACTICE_TOKEN", "secret", "",
           "OANDA PRACTICE token for the paper-forward service's candle and pricing reads "
           "(never orders). Legacy name OANDA_PRACTICE_TOKEN accepted for one release, warned.",
           read_by="entrypoints/paper_forward.py"),
    EnvVar("FIBOKI_OANDA_PRACTICE_ACCOUNT_ID", "str", "",
           "OANDA PRACTICE account id for the paper-forward pricing read. Legacy name "
           "OANDA_PRACTICE_ACCOUNT_ID accepted for one release, warned.",
           read_by="entrypoints/paper_forward.py"),
    EnvVar("FIBOKI_FRED_API_KEY", "secret", "",
           "FRED/ALFRED API key for point-in-time macro vintages; empty means ALFRED refuses.",
           read_by="fiboki.data.providers.alfred"),
)

KNOWN_ENV_NAMES: frozenset[str] = frozenset(v.name for v in ENV_REGISTRY)

_warned_unknown: set[str] = set()


def warn_unknown_env(environ: Mapping[str, str]) -> list[str]:
    """Sorted ``FIBOKI_*`` / ``FIBOKEI_*`` names in ``environ`` that nothing reads.

    Pure: it returns the names and leaves the decision to the caller.
    ``FIBOKEI_*`` is the V1 spelling; V2 reads none of them, so every one is
    reported (V1 shipped a live flag under that prefix).
    """
    return sorted(
        name
        for name in environ
        if name.upper().startswith(_ENV_PREFIXES) and name not in KNOWN_ENV_NAMES
    )


def _check_unknown_env(environ: Mapping[str, str], mode: ExecutionMode) -> None:
    unknown = warn_unknown_env(environ)
    if not unknown:
        return
    if mode in _STRICT_MODES:
        raise SettingsError(
            f"unknown environment variable(s) {unknown} in {mode.value} mode. A "
            "misspelt name is a setting silently left at its default; refusing to "
            "start. Remove them or declare them in fiboki.api.settings.ENV_REGISTRY."
        )
    fresh = [n for n in unknown if n not in _warned_unknown]
    if fresh:
        _warned_unknown.update(fresh)
        log.warning(
            "unknown environment variable(s) %s are ignored; this is a startup error "
            "in demo and live mode",
            fresh,
        )


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
    #: sessions). Never inside the source tree. Resolved by
    #: :func:`fiboki.core.paths.resolve_paths`, like every other process.
    state_dir: Path = field(default_factory=lambda: Path("var"))
    #: Every operator path, resolved by the same rule the CLI and workers use.
    paths: FibokiPaths = field(default_factory=lambda: resolve_paths({}))

    #: Market-data root and catalogue, if provisioned.
    data_root: Path | None = None
    #: Where the read-only paper journal lives; ``None`` means ``<state_dir>/paper``.
    paper_root: Path | None = None
    experiment_db: Path | None = None

    #: Build identity, injected by the deploy. Empty means "unknown", which the
    #: health endpoint reports as a degradation rather than hiding.
    build_sha: str = ""
    build_time: str = ""

    #: A worker writes this file periodically. Its age is the heartbeat.
    worker_heartbeat_path: Path | None = None
    #: THE heartbeat and data staleness thresholds for every surface.
    health: HealthThresholds = field(default_factory=HealthThresholds)

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

    def __post_init__(self) -> None:
        # A Settings built by hand (not via load_settings) with only state_dir
        # must not leave the kill-switch path pointing somewhere else.
        if self.paths.state_dir != self.state_dir:
            object.__setattr__(
                self, "paths", resolve_paths({"FIBOKI_STATE_DIR": str(self.state_dir)})
            )

    @property
    def worker_heartbeat_stale_seconds(self) -> float:
        """Kept for existing readers; the value is ``health.worker_stale_after_seconds``."""
        return self.health.worker_stale_after_seconds

    @property
    def sessions_path(self) -> Path:
        return self.state_dir / "sessions.json"

    @property
    def killswitch_path(self) -> Path:
        """The ONE kill-switch journal (``core.paths``): the CLI writes it too."""
        return self.paths.killswitch_journal

    @property
    def audit_path(self) -> Path:
        return self.paths.api_audit

    @property
    def live_authorisation_path(self) -> Path:
        return self.state_dir / "live_authorisation.json"

    def provenance_for_execution(self) -> Provenance:
        return MODE_PROVENANCE[self.execution_mode]


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from the environment.

    An unrecognised ``FIBOKI_EXECUTION_MODE`` is a startup error, not a silent
    fallback to paper: a typo must not decide where orders go.  So is a
    malformed boolean or number, and, in DEMO or LIVE, any unknown
    ``FIBOKI_*`` / ``FIBOKEI_*`` name (a warning in other modes).
    """
    source = dict(os.environ if env is None else env)

    def get(name: str, default: str = "") -> str:
        return str(source.get(name, default)).strip()

    raw_mode = get("FIBOKI_EXECUTION_MODE", ExecutionMode.PAPER.value).lower()
    try:
        mode = ExecutionMode(raw_mode)
    except ValueError as exc:  # pragma: no cover - configuration error path
        valid = ", ".join(m.value for m in ExecutionMode)
        raise SettingsError(
            f"FIBOKI_EXECUTION_MODE={raw_mode!r} is not a valid execution mode "
            f"(one of: {valid}). Refusing to start rather than guess."
        ) from exc

    _check_unknown_env(source, mode)

    def _number(name: str, default: str, kind: type[int] | type[float]) -> float:
        raw = get(name, default)
        try:
            return kind(raw)
        except ValueError as exc:
            raise SettingsError(
                f"{name}={raw!r} is not a valid {kind.__name__}"
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

    # One resolution rule for every path (core.paths): the CLI and the workers
    # compute the same answer from the same environment.
    paths = resolve_paths(source)
    health = health_thresholds_from_env(source)

    def _flag_from(name: str, default: bool) -> bool:
        return parse_bool(name, get(name), default)

    return Settings(
        execution_mode=mode,
        allowed_origins=origins,
        cookie_name=get("FIBOKI_COOKIE_NAME", "fiboki_session"),
        cookie_secure=_flag_from("FIBOKI_COOKIE_SECURE", True),
        cookie_samesite=get("FIBOKI_COOKIE_SAMESITE", "strict").lower(),
        cookie_domain=get("FIBOKI_COOKIE_DOMAIN") or None,
        session_ttl_seconds=int(_number("FIBOKI_SESSION_TTL", "43200", int)),
        session_secret=secret,
        session_secret_is_ephemeral=ephemeral,
        state_dir=paths.state_dir,
        paths=paths,
        data_root=paths.data_root,
        paper_root=paths.paper_root if paths.paper_root_from_env else None,
        experiment_db=paths.experiment_db if paths.experiment_db_from_env else None,
        build_sha=get("FIBOKI_BUILD_SHA"),
        build_time=get("FIBOKI_BUILD_TIME"),
        worker_heartbeat_path=paths.worker_heartbeat,
        health=health,
        venue_url=get("FIBOKI_VENUE_URL"),
        slippage_model=get("FIBOKI_SLIPPAGE_MODEL", "zero"),
        spread_model=get("FIBOKI_SPREAD_MODEL", "static_typical"),
        financing_model=get("FIBOKI_FINANCING_MODEL", "none"),
        fx_conversion_model=get("FIBOKI_FX_MODEL", "static_rate"),
    )


def health_thresholds_from_env(env: Mapping[str, str] | None = None) -> HealthThresholds:
    """THE observability thresholds (``Settings.health``) from an environment.

    Split out of :func:`load_settings` so a surface that must not fail on an
    unrelated setting (``fiboki doctor`` reading a demo-mode environment with
    an unknown name, the live worker's config) still reads the SAME three
    numbers from the SAME variables. Unparseable or non-positive values raise
    :class:`SettingsError`.
    """
    source = os.environ if env is None else env

    def _number(name: str, default: str) -> float:
        raw = str(source.get(name, default)).strip()
        try:
            return float(raw)
        except ValueError as exc:
            raise SettingsError(f"{name}={raw!r} is not a valid float") from exc

    try:
        return HealthThresholds(
            worker_stale_after_seconds=_number("FIBOKI_WORKER_STALE_SECONDS", "120"),
            worker_down_after_seconds=_number("FIBOKI_WORKER_DOWN_SECONDS", "300"),
            data_stale_after_seconds=_number("FIBOKI_DATA_STALE_SECONDS", "900"),
        )
    except ValueError as exc:
        if isinstance(exc, SettingsError):
            raise
        raise SettingsError(f"health thresholds: {exc}") from exc
