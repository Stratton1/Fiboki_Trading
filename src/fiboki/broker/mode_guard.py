"""Execution-mode isolation: reaching LIVE requires five independent controls.

What V1 did
-----------
A single environment variable could reach a live broker API. The gate was a
string-equality check on the base URL, which a **trailing slash** defeated, and
``FIBOKEI_LIVE_EXECUTION_ENABLED: "true"`` was committed as a literal in deploy
config. So the "gate" was: one committed string, compared wrongly.

What V2 requires
----------------
ALL FIVE of these must hold, independently, before
:class:`ModeGuard` will authorise :attr:`ExecutionMode.LIVE`:

1. **Build-time constant** -- :data:`LIVE_EXECUTION_COMPILED_IN`, a module
   constant in source. No environment variable, config file, database row or
   API call can change it. Enabling live trading requires a code change, a
   review and a deploy.
2. **Runtime environment flag** -- ``FIBOKI_LIVE_RUNTIME_ARMED`` must equal
   :data:`LIVE_ARM_TOKEN` exactly. It is deliberately not ``"true"``: a value
   that cannot be guessed cannot be set by accident, and the token is not the
   kind of thing anyone types into a deploy file "just to see".
3. **Persisted operator authorisation** -- a stored record naming a human, with
   a granted timestamp and an expiry. It is checked against the clock, so an
   authorisation left behind after an incident goes stale by itself.
4. **Per-strategy allow-list** -- the specific ``strategy_id`` must be named in
   that authorisation. Approving the platform for live trading does not approve
   every strategy on it.
5. **Parsed-hostname assertion** -- the venue URL is parsed with
   :func:`urllib.parse.urlsplit` and its ``hostname`` compared against an
   allow-list. This is the control that V1's trailing slash defeated:
   ``https://api.example.com`` and ``https://api.example.com/`` have the same
   parsed hostname and different strings, and ``https://api.example.com.evil``
   has a different hostname and a matching prefix.

The guard is also used in the safe direction: a DEMO or PAPER run whose venue
URL resolves to a known live host is refused, because pointing demo credentials
at a live host is how "we were only testing" becomes an incident.

``tests/unit/test_mode_guard.py`` sets each control on its own and asserts live
is STILL blocked, and asserts a config file alone cannot enable it.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from fiboki.core.enums import ExecutionMode

__all__ = [
    "LIVE_ARM_TOKEN",
    "LIVE_EXECUTION_COMPILED_IN",
    "LIVE_HOSTS",
    "LiveAuthorisation",
    "LiveAuthorisationStore",
    "ModeGuard",
    "ModeGuardDecision",
    "ModeGuardError",
    "parse_host",
]


#: CONTROL 1 -- build-time. Flipping this is a source change, a code review and
#: a deploy. Nothing at runtime can alter it. It is False in the repository and
#: must stay False until live trading is a deliberate, reviewed decision.
LIVE_EXECUTION_COMPILED_IN: bool = False

#: CONTROL 2 -- the exact value ``FIBOKI_LIVE_RUNTIME_ARMED`` must carry.
#: Not "true", not "1", not "yes". Nobody sets this by accident.
LIVE_ARM_TOKEN = "ARMED-LIVE-EXECUTION-I-ACCEPT-REAL-MONEY-RISK"

_LIVE_ARM_ENV = "FIBOKI_LIVE_RUNTIME_ARMED"

#: CONTROL 5 -- hostnames that are real-money venues. Compared against a PARSED
#: hostname, never a URL prefix.
LIVE_HOSTS: frozenset[str] = frozenset(
    {
        "api-fxtrade.oanda.com",
        "stream-fxtrade.oanda.com",
        "api.ig.com",
        "deal.ig.com",
    }
)

#: Hostnames that are demo/practice venues. Everything else is unknown, and
#: unknown is refused for broker-touching modes.
DEMO_HOSTS: frozenset[str] = frozenset(
    {
        "api-fxpractice.oanda.com",
        "stream-fxpractice.oanda.com",
        "demo-api.ig.com",
    }
)


class ModeGuardError(RuntimeError):
    """Raised by :meth:`ModeGuard.require` when a mode is not authorised."""


def parse_host(url: str) -> str:
    """Return the lower-cased hostname of ``url``, or "" if it has none.

    The whole point: this is a parse, not a comparison. ``urlsplit`` normalises
    away the trailing slash, the port, the credentials and the path that V1's
    string equality tripped over.
    """
    if not url:
        return ""
    parts = urlsplit(url if "//" in url else f"//{url}")
    return (parts.hostname or "").lower()


@dataclass(frozen=True, slots=True)
class LiveAuthorisation:
    """CONTROLS 3 and 4 -- a persisted, timestamped, human-granted approval."""

    operator: str
    granted_at: pd.Timestamp
    expires_at: pd.Timestamp
    strategies: tuple[str, ...]
    venue_host: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.operator:
            raise ValueError("A live authorisation must name the operator who granted it")
        for name, ts in (("granted_at", self.granted_at), ("expires_at", self.expires_at)):
            if ts.tzinfo is None:
                raise ValueError(f"LiveAuthorisation.{name} must be timezone-aware UTC")
        if self.expires_at <= self.granted_at:
            raise ValueError("A live authorisation must expire after it was granted")
        if not self.strategies:
            raise ValueError(
                "A live authorisation with no strategies authorises nothing. "
                "Name the strategies explicitly; there is no wildcard."
            )
        if "*" in self.strategies:
            raise ValueError(
                "'*' is not a strategy id. Per-strategy approval means per "
                "strategy; a wildcard would delete control 4."
            )

    def valid_at(self, now: pd.Timestamp) -> bool:
        return self.granted_at <= now < self.expires_at

    def covers(self, strategy_id: str) -> bool:
        return strategy_id in self.strategies

    def to_json(self) -> str:
        d = asdict(self)
        d["granted_at"] = self.granted_at.isoformat()
        d["expires_at"] = self.expires_at.isoformat()
        d["strategies"] = list(self.strategies)
        return json.dumps(d, sort_keys=True, indent=2)

    @staticmethod
    def from_json(text: str) -> LiveAuthorisation:
        d = json.loads(text)
        return LiveAuthorisation(
            operator=d["operator"],
            granted_at=pd.Timestamp(d["granted_at"]),
            expires_at=pd.Timestamp(d["expires_at"]),
            strategies=tuple(d.get("strategies") or ()),
            venue_host=d.get("venue_host", ""),
            reason=d.get("reason", ""),
        )


class LiveAuthorisationStore:
    """Reads the persisted operator authorisation from disk.

    A missing or unreadable file means NO authorisation. It never raises into
    the caller's face and never falls back to a permissive default: the whole
    file being absent is the normal, safe state.
    """

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None

    def load(self) -> LiveAuthorisation | None:
        if self.path is None or not self.path.exists():
            return None
        try:
            return LiveAuthorisation.from_json(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save(self, auth: LiveAuthorisation) -> None:
        if self.path is None:
            raise ValueError("LiveAuthorisationStore has no path to save to")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(auth.to_json(), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class ModeGuardDecision:
    allowed: bool
    mode: ExecutionMode
    #: Every control and whether it passed. Rendered to the operator so a
    #: refusal says WHICH control is missing, not just "denied".
    controls: dict[str, bool] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    @property
    def failed_controls(self) -> tuple[str, ...]:
        return tuple(k for k, v in sorted(self.controls.items()) if not v)


class ModeGuard:
    """Decides whether an execution mode may be used, for this strategy, here."""

    #: Named in the decision so a report can enumerate what was required.
    LIVE_CONTROLS = (
        "build_time_constant",
        "runtime_env_flag",
        "operator_authorisation",
        "strategy_allow_list",
        "venue_hostname",
    )

    def __init__(
        self,
        *,
        authorisation_store: LiveAuthorisationStore | None = None,
        env: dict[str, str] | None = None,
        live_hosts: frozenset[str] = LIVE_HOSTS,
        demo_hosts: frozenset[str] = DEMO_HOSTS,
        compiled_in: bool | None = None,
    ) -> None:
        self.authorisation_store = authorisation_store or LiveAuthorisationStore(None)
        self._env = env if env is not None else dict(os.environ)
        self.live_hosts = live_hosts
        self.demo_hosts = demo_hosts
        # ``compiled_in`` exists ONLY so a test can prove that setting the
        # build-time control alone is still not enough. Production code passes
        # nothing and gets the module constant.
        self.compiled_in = LIVE_EXECUTION_COMPILED_IN if compiled_in is None else compiled_in

    # ------------------------------------------------------------- checks

    def check(
        self,
        mode: ExecutionMode,
        *,
        strategy_id: str,
        venue_url: str,
        now: pd.Timestamp | None = None,
    ) -> ModeGuardDecision:
        now = now or pd.Timestamp.now(tz="UTC")
        host = parse_host(venue_url)

        if mode is ExecutionMode.LIVE:
            return self._check_live(strategy_id, host, now)
        return self._check_non_live(mode, host)

    def _check_non_live(self, mode: ExecutionMode, host: str) -> ModeGuardDecision:
        reasons: list[str] = []
        controls = {"not_pointed_at_live_host": True}
        if host and host in self.live_hosts:
            controls["not_pointed_at_live_host"] = False
            reasons.append(
                f"mode={mode.value} but venue host {host!r} is a LIVE venue. "
                "Demo credentials against a live host is not a test."
            )
        if mode.touches_broker:
            known = host in self.demo_hosts or host in self.live_hosts
            controls["known_venue_host"] = bool(host) and known
            if not controls["known_venue_host"]:
                reasons.append(
                    f"mode={mode.value} requires a known venue host; {host!r} is "
                    "not in the demo or live host lists"
                )
        return ModeGuardDecision(not reasons, mode, controls, tuple(reasons))

    def _check_live(
        self, strategy_id: str, host: str, now: pd.Timestamp
    ) -> ModeGuardDecision:
        controls: dict[str, bool] = dict.fromkeys(self.LIVE_CONTROLS, False)
        reasons: list[str] = []

        # 1. build-time constant
        controls["build_time_constant"] = bool(self.compiled_in)
        if not controls["build_time_constant"]:
            reasons.append(
                "live execution is not compiled in "
                "(fiboki.broker.mode_guard.LIVE_EXECUTION_COMPILED_IN is False)"
            )

        # 2. runtime environment flag
        controls["runtime_env_flag"] = self._env.get(_LIVE_ARM_ENV) == LIVE_ARM_TOKEN
        if not controls["runtime_env_flag"]:
            reasons.append(f"{_LIVE_ARM_ENV} is not set to the live arming token")

        # 3 + 4. persisted operator authorisation, and the strategy allow-list
        auth = self.authorisation_store.load()
        if auth is None:
            reasons.append("no persisted operator live authorisation was found")
        elif not auth.valid_at(now):
            reasons.append(
                f"operator live authorisation from {auth.operator} is not valid at "
                f"{now.isoformat()} (granted {auth.granted_at.isoformat()}, "
                f"expires {auth.expires_at.isoformat()})"
            )
        else:
            controls["operator_authorisation"] = True
            if auth.covers(strategy_id):
                controls["strategy_allow_list"] = True
            else:
                reasons.append(
                    f"strategy {strategy_id!r} is not on the live allow-list "
                    f"{sorted(auth.strategies)}"
                )
            if auth.venue_host and host and auth.venue_host.lower() != host:
                reasons.append(
                    f"authorisation names venue host {auth.venue_host!r} but the "
                    f"request targets {host!r}"
                )

        # 5. parsed hostname
        controls["venue_hostname"] = bool(host) and host in self.live_hosts
        if not controls["venue_hostname"]:
            reasons.append(
                f"venue hostname {host!r} (parsed, not string-compared) is not an "
                "approved live host"
            )

        allowed = all(controls[c] for c in self.LIVE_CONTROLS) and not reasons
        return ModeGuardDecision(allowed, ExecutionMode.LIVE, controls, tuple(reasons))

    # ------------------------------------------------------------ require

    def require(
        self,
        mode: ExecutionMode,
        *,
        strategy_id: str,
        venue_url: str,
        now: pd.Timestamp | None = None,
    ) -> ModeGuardDecision:
        """Like :meth:`check` but raises :class:`ModeGuardError` on refusal."""
        decision = self.check(mode, strategy_id=strategy_id, venue_url=venue_url, now=now)
        if not decision.allowed:
            raise ModeGuardError(
                f"ModeGuard refused mode={mode.value} for strategy {strategy_id!r} "
                f"at {venue_url!r}. Failed controls: "
                f"{list(decision.failed_controls)}. Reasons: {list(decision.reasons)}"
            )
        return decision
