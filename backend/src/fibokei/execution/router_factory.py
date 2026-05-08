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
) -> ExecutionRouter:
    """Build the router according to the current router mode.

    * ``legacy_single`` — exactly the pre-Phase-1 behaviour: one target,
      either paper (default) or IG demo (when ``FIBOKEI_LIVE_EXECUTION_ENABLED=true``).
      No fan-out.

    * ``env_global_fanout`` — every enabled account becomes a target. All
      bots fan out to all enabled targets.

    * ``db_targets`` — Phase 2; for now we log a warning and fall back to
      ``env_global_fanout``.
    """
    mode = get_router_mode()

    if mode == ROUTER_MODE_DB_TARGETS:
        logger.warning(
            "FIBOKEI_EXECUTION_ROUTER_MODE=db_targets is a Phase-2 placeholder. "
            "Falling back to env_global_fanout for now."
        )
        mode = ROUTER_MODE_ENV_GLOBAL_FANOUT

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
