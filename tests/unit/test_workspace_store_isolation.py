"""Workspace documents and price alerts are display state, never decision input.

``fiboki.api.workspace_store`` keeps layouts, watchlists and grid column sets an
operator chose by hand; ``fiboki.obs.price_alerts`` keeps alert rules and
notifications. A watchlist is a list of symbols to LOOK at, and an alert is a
message to a human: if a decision package imported either, a list an operator
edited in the browser could become a trading universe, or a level they typed
could become a stop, without a reviewed strategy document. This parses every
module under the decision packages and fails on any such import; it also keeps
the alert evaluator itself away from the execution layer.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
DECISION_PACKAGES = (
    "strategy", "backtest", "risk", "portfolio", "broker", "validation", "research",
    "sim", "indicators", "marketstate", "workers", "entrypoints", "agents", "lifecycle",
)
FORBIDDEN = (
    "fiboki.api.workspace_store",
    "fiboki.api.workspace_layouts",
    "fiboki.api.routers.workspace",
    "fiboki.obs.price_alerts",
)
EXECUTION = ("fiboki.broker", "fiboki.risk", "fiboki.portfolio", "fiboki.strategy")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
            out.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return out


def _hits(names: list[str], prefixes: tuple[str, ...]) -> list[str]:
    return [n for n in names if any(n == f or n.startswith(f + ".") for f in prefixes)]


def test_no_decision_package_imports_workspace_or_alert_state() -> None:
    offenders: list[str] = []
    scanned = 0
    for package in DECISION_PACKAGES:
        for path in sorted((SRC / package).rglob("*.py")):
            scanned += 1
            offenders += [f"{path.relative_to(SRC)}: {n}" for n in _hits(_imports(path), FORBIDDEN)]
    assert scanned > 100, "the scan found too few modules; the paths are wrong"
    assert offenders == []


def test_the_scan_catches_an_import(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text("from fiboki.api.workspace_store import WorkspaceStore\n", encoding="utf-8")
    assert _hits(_imports(planted), FORBIDDEN)
