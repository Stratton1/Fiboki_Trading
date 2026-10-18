"""Operator drawings are annotations, never strategy, risk or execution input.

``fiboki.api.drawings`` stores hand-placed geometry (a Fibonacci retracement
between two points included). If any decision-making package imported it, a
line an operator dragged on a chart could become a stop or a signal without
a reviewed strategy document, which is exactly the kind of untraceable input
the platform exists to refuse. This parses every module under the decision
packages and fails on any import of the drawing store or the chart router.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
DECISION_PACKAGES = (
    "strategy", "backtest", "risk", "portfolio", "broker", "validation", "research",
    "sim", "indicators", "marketstate", "workers", "entrypoints", "agents", "lifecycle",
)
FORBIDDEN = ("fiboki.api.drawings", "fiboki.api.routers.chart")


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


def test_no_decision_package_imports_the_drawing_store() -> None:
    offenders: list[str] = []
    scanned = 0
    for package in DECISION_PACKAGES:
        for path in sorted((SRC / package).rglob("*.py")):
            scanned += 1
            for name in _imports(path):
                if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
                    offenders.append(f"{path.relative_to(SRC)}: {name}")
    assert scanned > 100, "the scan found too few modules; the paths are wrong"
    assert offenders == []


def test_the_scan_catches_an_import(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text("from fiboki.api.drawings import DrawingStore\n", encoding="utf-8")
    assert "fiboki.api.drawings" in _imports(planted)
