"""Execution layer — broker adapters and the multi-broker fan-out router."""

from fibokei.execution.adapter import ExecutionAdapter
from fibokei.execution.paper_adapter import PaperExecutionAdapter
from fibokei.execution.router import ExecutionRouter
from fibokei.execution.router_factory import (
    build_execution_router_from_env,
    get_router_mode,
)
from fibokei.execution.targets import (
    ATTEMPT_ERROR,
    ATTEMPT_FILLED,
    ATTEMPT_PAPER_FILLED,
    ATTEMPT_REJECTED,
    ATTEMPT_SKIPPED,
    BROKER_IG,
    BROKER_PAPER,
    BROKER_TRADOVATE,
    ENV_DEMO,
    ENV_LIVE,
    ENV_PAPER,
    ROUTER_MODE_DB_TARGETS,
    ROUTER_MODE_ENV_GLOBAL_FANOUT,
    ROUTER_MODE_LEGACY_SINGLE,
    ExecutionAttempt,
    NormalisedTradePlan,
    ResolvedTarget,
    UnsupportedSymbol,
)

__all__ = [
    "ExecutionAdapter",
    "ExecutionAttempt",
    "ExecutionRouter",
    "NormalisedTradePlan",
    "PaperExecutionAdapter",
    "ResolvedTarget",
    "UnsupportedSymbol",
    "build_execution_router_from_env",
    "get_router_mode",
    # Status / vocabulary constants
    "ATTEMPT_ERROR",
    "ATTEMPT_FILLED",
    "ATTEMPT_PAPER_FILLED",
    "ATTEMPT_REJECTED",
    "ATTEMPT_SKIPPED",
    "BROKER_IG",
    "BROKER_PAPER",
    "BROKER_TRADOVATE",
    "ENV_DEMO",
    "ENV_LIVE",
    "ENV_PAPER",
    "ROUTER_MODE_DB_TARGETS",
    "ROUTER_MODE_ENV_GLOBAL_FANOUT",
    "ROUTER_MODE_LEGACY_SINGLE",
]
