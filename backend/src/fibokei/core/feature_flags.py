"""Feature flags for Fiboki platform."""

import os


class FeatureFlags:
    """Runtime feature toggles controlled via environment variables."""

    @property
    def live_execution_enabled(self) -> bool:
        """Whether live broker execution is enabled (IG demo)."""
        return os.environ.get("FIBOKEI_LIVE_EXECUTION_ENABLED", "false").lower() == "true"

    @property
    def ig_paper_mode(self) -> bool:
        """Whether IG adapter should use paper/demo mode (always True in V1)."""
        return os.environ.get("FIBOKEI_IG_PAPER_MODE", "true").lower() == "true"

    @property
    def execution_mode(self) -> str:
        """Return the current execution mode label."""
        if not self.live_execution_enabled:
            return "paper"
        if self.ig_paper_mode:
            return "ig_demo"
        return "ig_live"  # Blocked by adapter — should never reach this


def get_execution_adapter():
    """Return the appropriate execution adapter based on feature flags."""
    from fibokei.execution.paper_adapter import PaperExecutionAdapter

    flags = FeatureFlags()
    if flags.live_execution_enabled:
        from fibokei.execution.ig_adapter import IGExecutionAdapter
        return IGExecutionAdapter()
    return PaperExecutionAdapter()
