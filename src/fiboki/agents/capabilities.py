"""Fine-grained capabilities, role bundles and a deny-by-default resolver.

THE CARDINAL RULE
-----------------
An LLM must never be the final authority on an executable order.  This module
is where that rule stops being a paragraph in a design document and becomes a
property of the type system.

The guarantee is an *absence*: :class:`Capability` contains no member that
authorises order placement, position sizing, risk-limit changes, kill-switch
operation, execution-mode changes, broker routing or writes to market data.
An agent cannot be granted a capability that does not exist, and a tool cannot
require one either -- :mod:`fiboki.agents.tools` refuses to register a tool
whose declared capability is not a member of this enum.

Because an absence is easy to erode by accident, it is also *enforced*.
:func:`assert_no_execution_capability` runs at import time and mechanically
rejects any member whose name reads as a mutating verb applied to an execution
noun (``PLACE_ORDER``, ``SET_RISK_LIMIT``, ``WRITE_MARKET_DATA``,
``DISABLE_KILL_SWITCH``, ...).  Adding one is therefore not a silent policy
drift: it is an ImportError for the whole package.

Everything else is ordinary least-privilege plumbing: capabilities are
fine-grained, roles are bundles, and the resolver denies by default -- a
principal with no explicit grant resolves to the empty set and can do nothing.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    """Every permission an agent can hold.

    Read the list as an exhaustive statement of what an autonomous researcher
    is allowed to touch.  Note what is *not* here.
    """

    # -- reading the world ------------------------------------------------
    READ_MARKET_DATA = "read:market_data"
    READ_DATA_QUALITY = "read:data_quality"
    READ_REGIME = "read:regime"
    READ_EXPERIMENTS = "read:experiments"
    READ_RESEARCH_MEMORY = "read:research_memory"
    READ_STRATEGY = "read:strategy"
    READ_VALIDATION_REPORT = "read:validation_report"
    READ_TRADE_LEDGER = "read:trade_ledger"
    READ_PORTFOLIO = "read:portfolio"
    READ_EXECUTION_TELEMETRY = "read:execution_telemetry"
    READ_AUDIT_LEDGER = "read:audit_ledger"
    READ_EXTERNAL_WEB = "read:external_web"
    #: Forecast scorecards. Deliberately NOT folded into READ_EXPERIMENTS: the
    #: roles that forecast hold READ_EXPERIMENTS, and a forecaster that can read
    #: its own scorecard can learn to game it (hedge towards 0.5, forecast only
    #: what it already knows). Only roles that do not forecast hold this.
    READ_FORECAST_SCORES = "read:forecast_scores"
    #: Recorded headlines, point-in-time (``observed_at <= as_of``), returned as
    #: quoted data objects. Deliberately NOT held by the event classifier: the
    #: role that reads untrusted text must not also be able to go and fetch more.
    READ_NEWS_SNAPSHOT = "read:news_snapshot"

    # -- writing to the RESEARCH domain, and nowhere else -----------------
    WRITE_HYPOTHESIS = "write:hypothesis"
    WRITE_STRATEGY_PROPOSAL = "write:strategy_proposal"
    WRITE_STRATEGY_MUTATION = "write:strategy_mutation"
    WRITE_EXPERIMENT_DESIGN = "write:experiment_design"
    WRITE_CRITIQUE = "write:critique"
    WRITE_RESEARCH_NOTE = "write:research_note"
    #: A pre-registered, scoreable claim about price relative to price. Lands in
    #: the append-only research store; nothing downstream reads it as a signal.
    WRITE_FORECAST = "write:forecast"
    #: A structured event classification (type, buckets, severity, scheduled,
    #: confidence) into the QUARANTINED annotation store. Nothing reads it but a
    #: deterministic veto policy that is off by default and can only block a new
    #: entry; it cannot size, stop, exit or touch a limit.
    WRITE_EVENT_ANNOTATION = "write:event_annotation"

    # -- asking a deterministic worker to do something --------------------
    SUBMIT_JOB = "submit:job"

    @property
    def is_read(self) -> bool:
        return self.value.startswith("read:")

    @property
    def is_write(self) -> bool:
        return self.value.startswith("write:")


# ---------------------------------------------------------------------------
# The structural guarantee
# ---------------------------------------------------------------------------


class ExecutionCapabilityError(RuntimeError):
    """A capability that would give an agent authority over execution exists.

    Raised at import time.  If you are reading this in a traceback, someone
    added a capability that breaks the cardinal rule; the fix is to delete it,
    not to relax the check.
    """


#: Verbs that change the world rather than observe it.
_MUTATING_VERBS: frozenset[str] = frozenset(
    {
        "WRITE", "PLACE", "SUBMIT", "SEND", "SIZE", "SET", "ENABLE", "DISABLE",
        "CANCEL", "AMEND", "ROUTE", "MODIFY", "DELETE", "OVERRIDE", "APPROVE",
        "EXECUTE", "TRADE", "LIQUIDATE", "CLOSE", "ARM", "DISARM", "PROMOTE",
        "ACTIVATE", "DEACTIVATE", "BYPASS", "ALLOW", "GRANT",
    }
)

#: Nouns that name the execution path, the risk perimeter, or source data.
_EXECUTION_NOUNS: frozenset[str] = frozenset(
    {
        "ORDER", "ORDERS", "POSITION", "POSITIONS", "POSITION_SIZE", "SIZING",
        "BROKER", "VENUE", "FILL", "FILLS", "KILL_SWITCH", "KILLSWITCH",
        "RISK_LIMIT", "RISK_LIMITS", "RISK", "EXECUTION", "EXECUTION_MODE",
        "LIVE", "LIVE_CAPITAL", "CAPITAL", "MARKET_DATA", "ACCOUNT", "MARGIN",
        "LEVERAGE", "STOP_LOSS", "TRADE", "TRADES", "MONEY", "FUNDS",
        "LIFECYCLE", "PROMOTION",
    }
)

#: Substrings that are forbidden wherever they appear, verb analysis or not.
_ALWAYS_FORBIDDEN: tuple[str, ...] = (
    "EXECUTE",
    "KILL_SWITCH",
    "PLACE_ORDER",
    "SUBMIT_ORDER",
    "GO_LIVE",
    "LIVE_TRADING",
    "BROKER_ROUTE",
)


def _tokens_contain(haystack: str, needle: str) -> bool:
    """Token-boundary containment: ``MARKET_DATA`` is in ``WRITE_MARKET_DATA``."""
    return f"_{needle}_" in f"_{haystack}_"


def _reads_as_execution_authority(identifier: str) -> str | None:
    """Return a reason string if ``identifier`` names execution authority."""
    name = identifier.upper().replace(":", "_").replace("-", "_").replace(".", "_")
    for banned in _ALWAYS_FORBIDDEN:
        if _tokens_contain(name, banned) or banned in name:
            return f"contains the forbidden token {banned!r}"
    parts = name.split("_")
    if not parts:
        return None
    verb = parts[0]
    if verb not in _MUTATING_VERBS:
        return None
    rest = "_".join(parts[1:])
    if not rest:
        return f"bare mutating verb {verb!r}"
    for noun in _EXECUTION_NOUNS:
        if _tokens_contain(rest, noun):
            return f"mutating verb {verb!r} applied to execution noun {noun!r}"
    return None


def assert_no_execution_capability(
    members: Iterable[Capability] | None = None,
) -> None:
    """Prove that no capability grants authority over execution.

    Checks both the member NAME and its VALUE, so neither spelling can smuggle
    one past.  Called at import; also called directly by the test suite.
    """
    offenders: list[str] = []
    for member in members if members is not None else tuple(Capability):
        for identifier in (member.name, member.value):
            reason = _reads_as_execution_authority(identifier)
            if reason is not None:
                offenders.append(f"{member.name} ({identifier!r}): {reason}")
    if offenders:
        raise ExecutionCapabilityError(
            "Capability enum contains execution authority, which breaks the "
            "cardinal rule that an LLM is never the final authority on an "
            "executable order:\n  " + "\n  ".join(offenders)
        )


assert_no_execution_capability()


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

#: Everything an agent may read.  Convenience only; roles narrow it further.
ALL_READ: frozenset[Capability] = frozenset(c for c in Capability if c.is_read)
#: Every research-domain write.  There is no non-research write.
ALL_WRITE: frozenset[Capability] = frozenset(c for c in Capability if c.is_write)
#: The complete set.  Even this grants nothing over execution.
ALL_CAPABILITIES: frozenset[Capability] = frozenset(Capability)


def bundle(*capabilities: Capability) -> frozenset[Capability]:
    return frozenset(capabilities)


# ---------------------------------------------------------------------------
# Deny-by-default resolver
# ---------------------------------------------------------------------------


class PermissionDenied(PermissionError):
    """An agent asked for something it was never granted."""

    def __init__(
        self,
        principal: str,
        capability: Capability | str,
        detail: str = "",
    ) -> None:
        cap = capability.value if isinstance(capability, Capability) else capability
        self.principal = principal
        self.capability = cap
        self.detail = detail
        message = f"{principal!r} is not granted {cap!r}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Grant:
    """What one principal (an agent instance) may do."""

    principal: str
    role: str
    capabilities: frozenset[Capability]

    def __post_init__(self) -> None:
        if not self.principal:
            raise ValueError("a grant needs a principal")
        bad = [c for c in self.capabilities if not isinstance(c, Capability)]
        if bad:
            raise ValueError(f"grant contains non-Capability members: {bad!r}")


class CapabilityResolver:
    """Deny-by-default permission resolution.

    An unknown principal resolves to the empty set.  There is no wildcard, no
    "admin" short-circuit and no inheritance: a principal has exactly the
    capabilities that were written down for it.
    """

    def __init__(self, grants: Mapping[str, Grant] | None = None) -> None:
        self._grants: dict[str, Grant] = dict(grants or {})

    # -- mutation ---------------------------------------------------------

    def grant(
        self,
        principal: str,
        role: str,
        capabilities: Iterable[Capability],
    ) -> Grant:
        """Record a grant.  Re-granting the same principal replaces it."""
        caps = frozenset(capabilities)
        unknown = [c for c in caps if not isinstance(c, Capability)]
        if unknown:
            raise ValueError(f"cannot grant unknown capabilities {unknown!r}")
        # Defensive: a grant is another place a forbidden capability could be
        # introduced, e.g. by a dynamically constructed enum.
        assert_no_execution_capability(caps)
        g = Grant(principal=principal, role=role, capabilities=caps)
        self._grants[principal] = g
        return g

    def revoke(self, principal: str) -> None:
        self._grants.pop(principal, None)

    # -- reading ----------------------------------------------------------

    def capabilities_for(self, principal: str) -> frozenset[Capability]:
        g = self._grants.get(principal)
        return g.capabilities if g is not None else frozenset()

    def role_of(self, principal: str) -> str | None:
        g = self._grants.get(principal)
        return g.role if g is not None else None

    def allows(self, principal: str, capability: Capability) -> bool:
        return capability in self.capabilities_for(principal)

    def require(self, principal: str, capability: Capability, detail: str = "") -> None:
        """Raise :class:`PermissionDenied` unless the grant exists."""
        if not self.allows(principal, capability):
            held = sorted(c.value for c in self.capabilities_for(principal))
            note = detail or f"holds {held or 'nothing'}"
            raise PermissionDenied(principal, capability, note)

    def principals(self) -> tuple[str, ...]:
        return tuple(sorted(self._grants))

    def __contains__(self, principal: object) -> bool:
        return principal in self._grants


__all__ = [
    "ALL_CAPABILITIES",
    "ALL_READ",
    "ALL_WRITE",
    "Capability",
    "CapabilityResolver",
    "ExecutionCapabilityError",
    "Grant",
    "PermissionDenied",
    "assert_no_execution_capability",
    "bundle",
]
