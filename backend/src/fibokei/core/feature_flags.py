"""Feature flags for Fiboki platform."""

from __future__ import annotations

import os


class FeatureFlags:
    """Runtime feature toggles controlled via environment variables."""

    # ── Pre-existing flags (legacy single-broker path) ──────────────

    @property
    def live_execution_enabled(self) -> bool:
        """Whether live broker execution is enabled (IG demo).

        Misnomer: this flag does NOT mean real-money trading. It means
        "use the IG adapter at all" — IG production is hard-blocked at the
        IGClient layer. With this off, the worker hands every bot a
        ``PaperExecutionAdapter`` regardless of router mode.
        """
        return os.environ.get("FIBOKEI_LIVE_EXECUTION_ENABLED", "false").lower() == "true"

    @property
    def ig_paper_mode(self) -> bool:
        """Whether IG adapter should use paper/demo mode (always True in V1)."""
        return os.environ.get("FIBOKEI_IG_PAPER_MODE", "true").lower() == "true"

    @property
    def execution_mode(self) -> str:
        """Legacy single-broker execution-mode label.

        Returns "paper", "ig_demo", or "ig_live". Used by the existing
        ``/execution/mode`` and ``/system/status`` endpoints. The new
        multi-broker router exposes its own mode and target list via
        ``ExecutionRouter.summary()``.
        """
        if not self.live_execution_enabled:
            return "paper"
        if self.ig_paper_mode:
            return "ig_demo"
        return "ig_live"  # Blocked by adapter — should never reach this

    # ── Phase 1: router-mode flag ───────────────────────────────────

    @property
    def execution_router_mode(self) -> str:
        """Phase 1 router-mode flag.

        ``legacy_single`` (default) — preserves pre-Phase-1 behaviour.
        ``env_global_fanout`` — every enabled execution account receives
            every bot signal. Operator must opt in deliberately.
        ``db_targets`` — Phase-2 placeholder. Currently behaves like
            ``env_global_fanout``.
        """
        from fibokei.execution.targets import ROUTER_MODE_LEGACY_SINGLE, VALID_ROUTER_MODES

        raw = os.environ.get(
            "FIBOKEI_EXECUTION_ROUTER_MODE", ROUTER_MODE_LEGACY_SINGLE
        ).strip().lower()
        if raw not in VALID_ROUTER_MODES:
            return ROUTER_MODE_LEGACY_SINGLE
        return raw


def get_execution_adapter():
    """Return the appropriate execution adapter based on legacy feature flags.

    .. note::
       This function is **legacy** — kept so the existing IG-demo single-
       broker path continues to work for callers that haven't migrated to
       the router. The worker now uses
       :func:`fibokei.execution.router_factory.build_execution_router_from_env`
       which supports fan-out.
    """
    from fibokei.execution.paper_adapter import PaperExecutionAdapter

    flags = FeatureFlags()
    if flags.live_execution_enabled:
        from fibokei.execution.ig_adapter import IGExecutionAdapter
        return IGExecutionAdapter()
    return PaperExecutionAdapter()
