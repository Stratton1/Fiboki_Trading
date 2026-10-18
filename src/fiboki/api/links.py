"""Workstation routes the API links to: one table, so a deep link cannot rot.

Every ``deep_link`` and ``href`` the API emits is built here. Before this
module the attention queue linked to ``/risk``, ``/system`` and ``/journal``,
none of which was a page; an operator clicking the top item of the queue got a
404. ``ROUTE_PATTERNS`` mirrors the workstation's page routes, and
``tests/api/test_operator_core.py`` checks every link the API can emit against
it; the workstation's own ``lib/routes.ts`` is checked against the pages on
disk by its Playwright source rules. Two tables, each tested against the thing
it describes.

Entity routes owned by other screens are linked by their agreed paths
(``/research/strategies/<id>``, ``/research/runs/<run_id>``) even where the
page is built by another work stream; the path is the contract.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

__all__ = [
    "ALERTS",
    "COMMAND",
    "FLEET",
    "JOURNAL",
    "RISK",
    "ROUTE_PATTERNS",
    "SYSTEM",
    "chart",
    "incident",
    "journal_trade",
    "lifecycle",
    "matches_route",
    "research_run",
    "session",
    "strategy",
]

COMMAND = "/"
FLEET = "/trading/fleet"
RISK = "/trading/risk"
EXPOSURE = "/trading/exposure"
JOURNAL = "/trading/execution"
CANDIDATES = "/trading/candidates"
SYSTEM = "/system"
ALERTS = "/alerts"
DATA_QUALITY = "/markets/data-quality"

#: Page routes of the workstation, with ``[param]`` for a dynamic segment.
ROUTE_PATTERNS: tuple[str, ...] = (
    "/",
    "/alerts",
    "/intelligence/agents",
    "/intelligence/research-memory",
    "/intelligence/runs",
    "/lifecycle/[hash]",
    "/login",
    "/market-pulse",
    "/markets",
    "/markets/[symbol]",
    "/markets/correlations",
    "/markets/data-quality",
    "/markets/regimes",
    "/research",
    "/research/datasets",
    "/research/experiments",
    "/research/hypotheses",
    "/research/compare",
    "/research/correlations",
    "/research/jobs",
    "/research/parameter-lab",
    "/research/runs",
    "/research/runs/[run_id]",
    "/research/strategies",
    "/research/strategies/[strategy_id]",
    "/research/walk-forward",
    "/research/validation",
    "/system",
    "/system/broker-health",
    "/system/data-health",
    "/system/functions",
    "/system/incidents/[id]",
    "/system/legend",
    "/system/logs",
    "/system/services",
    "/system/settings",
    "/system/workers",
    "/trading/candidates",
    "/trading/execution",
    "/trading/exposure",
    "/trading/fleet",
    "/trading/portfolio",
    "/trading/risk",
)


def _q(value: str) -> str:
    return quote(str(value), safe="")


def lifecycle(content_hash: str) -> str:
    return f"/lifecycle/{_q(content_hash)}"


def incident(incident_id: str) -> str:
    return f"/system/incidents/{_q(incident_id)}"


def strategy(strategy_id: str) -> str:
    """The strategy's tearsheet (built by the research work stream)."""
    return f"/research/strategies/{_q(strategy_id)}"


def research_run(run_id: str) -> str:
    return f"/research/runs/{_q(run_id)}"


def chart(
    symbol: str,
    *,
    timeframe: str | None = None,
    run: str | None = None,
    trade: str | None = None,
) -> str:
    params = {k: v for k, v in (("tf", timeframe), ("run", run), ("trade", trade)) if v}
    query = f"?{urlencode(params)}" if params else ""
    return f"/markets/{_q(symbol.upper())}{query}"


def journal_trade(trade_id: str) -> str:
    return f"{JOURNAL}?{urlencode({'row': trade_id})}"


def session(session_id: str) -> str:
    return f"{FLEET}?{urlencode({'session': session_id})}"


def matches_route(link: str) -> bool:
    """True when ``link`` (path plus optional query) is one of the pages."""
    path = link.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        return False
    parts = [p for p in path.split("/") if p]
    for pattern in ROUTE_PATTERNS:
        want = [p for p in pattern.split("/") if p]
        if len(want) != len(parts):
            continue
        if all(w.startswith("[") or w == p for w, p in zip(want, parts, strict=True)):
            return True
    return False
