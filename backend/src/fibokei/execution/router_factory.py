"""Build an :class:`ExecutionRouter` from environment variables.

Phase 1 configuration is environment-driven. Phase 2 will replace this with
a database-backed factory that reads the ``execution_accounts`` and
``bot_execution_targets`` tables. Until then, every running bot fans out to
every enabled execution account, which is why the router-mode flag is
``env_global_fanout`` rather than ``db_targets``.

Defaults are deliberately safe:

* ``FIBOKEI_EXECUTION_ROUTER_MODE`` defaults to ``legacy_single`` so an
  unconfigured deploy behaves exactly like the pre-Phase-1 system.
* ``FIBOKEI_PAPER_ACCOUNT_ENABLED`` defaults to ``true`` (paper-only).
* ``FIBOKEI_IG_ACCOUNT_ENABLED`` defaults to ``false``.
* ``FIBOKEI_TRADOVATE_ACCOUNT_ENABLED`` defaults to ``false``.
* Live execution requires ``FIBOKEI_LIVE_EXECUTION_ENABLED=true`` AND a
  per-broker live-allow flag (see ``ResolvedTarget.is_environment_allowed``).
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from fibokei.execution.router import ExecutionRouter
from fibokei.execution.targets import (
    BROKER_IG,
    BROKER_PAPER,
    BROKER_TRADOVATE,
    ENV_DEMO,
    ENV_LIVE,
    ENV_PAPER,
    ROUTER_MODE_DB_TARGETS,
    ROUTER_MODE_ENV_GLOBAL_FANOUT,
    ROUTER_MODE_LEGACY_SINGLE,
    VALID_ROUTER_MODES,
    ResolvedTarget,
)
from fibokei.paper.account import PaperAccount

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float in %s; using default %s", name, default)
        return default


def _str_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def get_router_mode() -> str:
    """Read and validate the configured router mode."""
    mode = _str_env("FIBOKEI_EXECUTION_ROUTER_MODE", ROUTER_MODE_LEGACY_SINGLE).lower()
    if mode not in VALID_ROUTER_MODES:
        logger.warning(
            "Unknown FIBOKEI_EXECUTION_ROUTER_MODE='%s'; falling back to %s",
            mode, ROUTER_MODE_LEGACY_SINGLE,
        )
        return ROUTER_MODE_LEGACY_SINGLE
    return mode


# ── Per-broker target builders ────────────────────────────────────


def _build_paper_target(account: PaperAccount | None) -> ResolvedTarget | None:
    """Build the paper execution target if enabled."""
    if not _bool_env("FIBOKEI_PAPER_ACCOUNT_ENABLED", True):
        return None
    from fibokei.execution.paper_adapter import PaperExecutionAdapter

    default_capital = account.initial_balance if account else 1000.0
    capital = _float_env("FIBOKEI_PAPER_ACCOUNT_CAPITAL", default_capital)
    risk_pct = _float_env("FIBOKEI_PAPER_ACCOUNT_RISK_PCT", 1.0)
    adapter = PaperExecutionAdapter(account=account)
    return ResolvedTarget(
        target_id="paper-default",
        name="Paper",
        broker=BROKER_PAPER,
        environment=ENV_PAPER,
        allocated_capital=capital,
        risk_per_trade_pct=risk_pct,
        is_enabled=True,
        adapter=adapter,
        live_allowed=False,
    )


def _build_ig_target() -> ResolvedTarget | None:
    """Build the IG execution target if enabled."""
    if not _bool_env("FIBOKEI_IG_ACCOUNT_ENABLED", False):
        return None
    from fibokei.execution.ig_adapter import IGExecutionAdapter

    env = _str_env("FIBOKEI_IG_ACCOUNT_ENV", "demo").lower() or "demo"
    if env not in (ENV_DEMO, ENV_LIVE):
        logger.warning("Invalid FIBOKEI_IG_ACCOUNT_ENV='%s'; defaulting to demo", env)
        env = "demo"

    capital = _float_env("FIBOKEI_IG_ACCOUNT_CAPITAL", 1000.0)
    risk_pct = _float_env("FIBOKEI_IG_ACCOUNT_RISK_PCT", 1.0)
    # IG live is hard-blocked at the IGClient layer regardless of this flag.
    # We expose live_allowed=False so that the router-side gate also refuses.
    live_allowed = (
        env == ENV_LIVE
        and _bool_env("FIBOKEI_IG_LIVE_ALLOWED", False)
        and _bool_env("FIBOKEI_LIVE_EXECUTION_ENABLED", False)
    )
    return ResolvedTarget(
        target_id="ig-demo-main" if env == ENV_DEMO else "ig-live-main",
        name="IG Demo Main" if env == ENV_DEMO else "IG Live Main",
        broker=BROKER_IG,
        environment=env,
        allocated_capital=capital,
        risk_per_trade_pct=risk_pct,
        is_enabled=True,
        adapter=IGExecutionAdapter(),
        live_allowed=live_allowed,
    )


def _build_tradovate_target() -> ResolvedTarget | None:
    """Build the Tradovate execution target if enabled."""
    if not _bool_env("FIBOKEI_TRADOVATE_ACCOUNT_ENABLED", False):
        return None
    from fibokei.execution.tradovate_adapter import TradovateExecutionAdapter

    env = _str_env("FIBOKEI_TRADOVATE_ACCOUNT_ENV", "demo").lower() or "demo"
    if env not in (ENV_DEMO, ENV_LIVE):
        logger.warning("Invalid FIBOKEI_TRADOVATE_ACCOUNT_ENV='%s'; defaulting to demo", env)
        env = "demo"
    capital = _float_env("FIBOKEI_TRADOVATE_ACCOUNT_CAPITAL", 5000.0)
    risk_pct = _float_env("FIBOKEI_TRADOVATE_ACCOUNT_RISK_PCT", 1.0)
    live_allowed = (
        env == ENV_LIVE
        and _bool_env("FIBOKEI_TRADOVATE_LIVE_ALLOWED", False)
        and _bool_env("FIBOKEI_LIVE_EXECUTION_ENABLED", False)
    )
    return ResolvedTarget(
        target_id="tradovate-demo-main" if env == ENV_DEMO else "tradovate-live-main",
        name="Tradovate Demo Main" if env == ENV_DEMO else "Tradovate Live Main",
        broker=BROKER_TRADOVATE,
        environment=env,
        allocated_capital=capital,
        risk_per_trade_pct=risk_pct,
        is_enabled=True,
        adapter=TradovateExecutionAdapter(),
        live_allowed=live_allowed,
    )


# ── Public factory ────────────────────────────────────────────────


def build_execution_router_from_env(
    account: PaperAccount | None = None,
    kill_switch_check: Callable[[], bool] | None = None,
    session_factory: Callable | None = None,
) -> ExecutionRouter:
    """Build the router according to the current router mode.

    * ``legacy_single`` — exactly the pre-Phase-1 behaviour: one target,
      either paper (default) or IG demo (when ``FIBOKEI_LIVE_EXECUTION_ENABLED=true``).
      No fan-out.

    * ``env_global_fanout`` — every enabled account becomes a target. All
      bots fan out to all enabled targets.

    * ``db_targets`` (Phase 2) — per-bot targets read from the database.
      Requires ``session_factory``. Falls back to ``env_global_fanout`` if
      no factory is supplied (e.g. in tests that don't need DB-backed
      targets).
    """
    mode = get_router_mode()

    if mode == ROUTER_MODE_DB_TARGETS and session_factory is None:
        logger.warning(
            "FIBOKEI_EXECUTION_ROUTER_MODE=db_targets requires a session_factory; "
            "falling back to env_global_fanout."
        )
        mode = ROUTER_MODE_ENV_GLOBAL_FANOUT

    if mode == ROUTER_MODE_DB_TARGETS:
        # Phase 2: per-bot targets resolved from DB on every dispatch.
        return _build_db_router(
            account=account,
            kill_switch_check=kill_switch_check,
            session_factory=session_factory,
        )

    targets: list[ResolvedTarget] = []
    if mode == ROUTER_MODE_LEGACY_SINGLE:
        # Mirror the legacy ``get_execution_adapter`` decision: if live
        # execution is enabled, use IG; otherwise paper.
        from fibokei.core.feature_flags import FeatureFlags
        flags = FeatureFlags()
        if flags.live_execution_enabled:
            ig_target = _build_legacy_ig_target()
            targets.append(ig_target)
        else:
            paper_target = _build_legacy_paper_target(account)
            targets.append(paper_target)
    else:
        # env_global_fanout — fan out across all enabled accounts.
        for builder in (
            lambda: _build_paper_target(account),
            _build_ig_target,
            _build_tradovate_target,
        ):
            target = builder()
            if target is not None:
                targets.append(target)
        if not targets:
            logger.warning(
                "env_global_fanout selected but no execution accounts are enabled. "
                "Falling back to legacy paper-only behaviour."
            )
            targets.append(_build_legacy_paper_target(account))

    router = ExecutionRouter(
        mode=mode,
        targets=targets,
        kill_switch_check=kill_switch_check,
    )

    target_summary = ", ".join(
        f"{t.broker}:{t.environment}({'on' if t.is_enabled else 'off'})" for t in targets
    )
    logger.info(
        "ExecutionRouter built: mode=%s targets=[%s]", mode, target_summary,
    )
    if mode == ROUTER_MODE_ENV_GLOBAL_FANOUT and len(targets) > 1:
        logger.warning(
            "Phase-1 env_global_fanout active: ALL running bots will fan out to ALL "
            "enabled accounts (%d). This is intentional for Phase 1; per-bot target "
            "selection arrives in Phase 2.",
            len(targets),
        )
    return router


# ── Legacy single-target builders (preserve existing behaviour) ────


def _build_legacy_paper_target(account: PaperAccount | None) -> ResolvedTarget:
    from fibokei.execution.paper_adapter import PaperExecutionAdapter

    paper = account or PaperAccount()
    return ResolvedTarget(
        target_id="paper-legacy",
        name="Paper (legacy)",
        broker=BROKER_PAPER,
        environment=ENV_PAPER,
        # Use the live PaperAccount equity/balance as the sizing capital so
        # legacy callers see no change — this is the only target in legacy
        # mode and the bot's existing dynamic sizing path has historically
        # used the live account.
        allocated_capital=paper.equity if paper.equity > 0 else paper.initial_balance,
        risk_per_trade_pct=_float_env("FIBOKEI_LEGACY_RISK_PCT", 1.0),
        is_enabled=True,
        adapter=PaperExecutionAdapter(account=paper),
    )


def _build_legacy_ig_target() -> ResolvedTarget:
    from fibokei.execution.ig_adapter import IGExecutionAdapter

    return ResolvedTarget(
        target_id="ig-legacy-demo",
        name="IG Demo (legacy)",
        broker=BROKER_IG,
        environment=ENV_DEMO,
        allocated_capital=_float_env("FIBOKEI_IG_ACCOUNT_CAPITAL", 1000.0),
        risk_per_trade_pct=_float_env("FIBOKEI_LEGACY_RISK_PCT", 1.0),
        is_enabled=True,
        adapter=IGExecutionAdapter(),
        live_allowed=False,  # Demo only in legacy.
    )


# ── Phase 2: db_targets builder ──────────────────────────────────


# Cached adapter instances per (broker, environment) so each broker's
# session/HTTP client is reused across signals. Keyed on the unique account
# id so two IG demo accounts (rare) keep separate adapters.
from fibokei.execution.adapter import ExecutionAdapter  # noqa: E402

_ADAPTER_CACHE: dict[int, ExecutionAdapter] = {}


def _adapter_for_account(account_row, paper_account: PaperAccount | None):
    """Return (cached) adapter instance for an ExecutionAccountModel row.

    Each broker gets one adapter per account row. Reused across dispatches
    so authenticated sessions persist between signals.
    """
    cached = _ADAPTER_CACHE.get(account_row.id)
    if cached is not None:
        return cached
    if account_row.broker == BROKER_PAPER:
        from fibokei.execution.paper_adapter import PaperExecutionAdapter
        adapter = PaperExecutionAdapter(account=paper_account)
    elif account_row.broker == BROKER_IG:
        from fibokei.execution.ig_adapter import IGExecutionAdapter
        adapter = IGExecutionAdapter()
    elif account_row.broker == BROKER_TRADOVATE:
        from fibokei.execution.tradovate_adapter import TradovateExecutionAdapter
        adapter = TradovateExecutionAdapter()
    else:
        logger.warning(
            "Unknown broker '%s' for account %s; using paper",
            account_row.broker, account_row.name,
        )
        from fibokei.execution.paper_adapter import PaperExecutionAdapter
        adapter = PaperExecutionAdapter(account=paper_account)
    _ADAPTER_CACHE[account_row.id] = adapter
    return adapter


def _resolved_target_from_db(target_row, account_row, paper_account):
    """Convert a (target, account) DB pair into a :class:`ResolvedTarget`."""
    capital = (
        target_row.allocation_override
        if target_row.allocation_override is not None
        else account_row.allocated_capital
    )
    risk_pct = (
        target_row.risk_per_trade_pct_override
        if target_row.risk_per_trade_pct_override is not None
        else account_row.risk_per_trade_pct
    )
    # Live execution requires both the account-level live_allowed flag AND
    # the global FIBOKEI_LIVE_EXECUTION_ENABLED master lock to be true.
    live_master = _bool_env("FIBOKEI_LIVE_EXECUTION_ENABLED", False)
    live_allowed = (
        bool(account_row.live_allowed)
        and account_row.environment == ENV_LIVE
        and live_master
    )
    return ResolvedTarget(
        target_id=f"acct-{account_row.id}",
        name=account_row.name,
        broker=account_row.broker,
        environment=account_row.environment,
        allocated_capital=float(capital),
        risk_per_trade_pct=float(risk_pct),
        is_enabled=bool(target_row.is_enabled and account_row.is_enabled),
        adapter=_adapter_for_account(account_row, paper_account),
        live_allowed=live_allowed,
    )


def _build_db_router(
    account: PaperAccount | None,
    kill_switch_check: Callable[[], bool] | None,
    session_factory: Callable | None,
) -> ExecutionRouter:
    """Phase 2 router with per-bot targets resolved from the database."""
    from fibokei.db.repository import (
        get_default_execution_account,
        list_targets_with_accounts,
    )

    paper_account = account

    def target_provider(bot_id: str) -> list[ResolvedTarget]:
        """Resolve a fresh list of ResolvedTarget for the given bot.

        Reads ``bot_execution_targets`` joined to ``execution_accounts``;
        disabled rows on either side are excluded by the query. Bots with
        no explicit targets fall back to the default Paper account so
        existing bots continue to run safely after the Phase 2 migration.
        """
        if session_factory is None:
            return []
        with session_factory() as session:
            pairs = list_targets_with_accounts(session, bot_id=bot_id)
            if pairs:
                return [
                    _resolved_target_from_db(t, a, paper_account)
                    for t, a in pairs
                ]
            # No explicit targets — fall back to the default account.
            default_account = get_default_execution_account(session)
            if default_account is None or not default_account.is_enabled:
                return []
            class _SyntheticTarget:  # mimics BotExecutionTargetModel duck-typed for resolved
                is_enabled = True
                allocation_override = None
                risk_per_trade_pct_override = None
            return [
                _resolved_target_from_db(_SyntheticTarget(), default_account, paper_account)
            ]

    # Build a small static snapshot of currently-enabled accounts for the
    # router summary (System page). The provider is the source of truth at
    # dispatch time; this list is informational only.
    static_snapshot: list[ResolvedTarget] = []
    if session_factory is not None:
        try:
            from fibokei.db.repository import list_execution_accounts

            with session_factory() as session:
                for acct in list_execution_accounts(session, enabled_only=True):
                    class _Synthetic:
                        is_enabled = True
                        allocation_override = None
                        risk_per_trade_pct_override = None
                    static_snapshot.append(
                        _resolved_target_from_db(_Synthetic(), acct, paper_account)
                    )
        except Exception:
            logger.exception("Failed to build static snapshot of execution accounts")

    # Phase 4: per-account risk engine. Reads parent-child audit tables so
    # daily/weekly stops and max-open-positions are enforced per account.
    account_risk_engine = None
    if session_factory is not None:
        try:
            from fibokei.execution.account_risk import AccountRiskEngine
            account_risk_engine = AccountRiskEngine(session_factory)
        except Exception:
            logger.exception("Failed to build AccountRiskEngine; risk checks disabled")

    router = ExecutionRouter(
        mode=ROUTER_MODE_DB_TARGETS,
        targets=static_snapshot,
        kill_switch_check=kill_switch_check,
        target_provider=target_provider,
        account_risk_engine=account_risk_engine,
    )
    logger.info(
        "ExecutionRouter built: mode=db_targets accounts_enabled=%d "
        "(per-bot targets resolved on dispatch)",
        len(static_snapshot),
    )
    return router
