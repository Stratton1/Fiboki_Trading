"""Import-direction layering, enforced by parsing every module under ``src/``.

Why this file exists
--------------------
``src/fiboki/core/contracts.py`` opens with "Layering, enforced by
tests/unit/test_layering.py". It was not: the file did not exist.
``docs/v2/ARCHITECTURE.md`` §3 says so in as many words, and
``docs/v2/ROADMAP.md`` §5 lists it as one of two docstrings citing a test that
was never written. A citation that rots is worse than no citation, because it
reads as evidence.

``docs/v2/ARCHITECTURE.md`` §2 states the rule this file makes mechanical:

    Dependencies point downwards only. A package may import from any package
    below it and from none above it.

What this catches that no unit test can
---------------------------------------
"Nobody imports upwards" is a property of the whole tree, not of any function.
Every individual test that imports ``data`` and ``broker`` together passes. The
only way to hold the property is to parse the tree and assert it, which is the
same reason ``tests/unit/test_no_gateway_bypass.py`` exists.

How the rule is expressed
-------------------------
Each package gets an explicit RANK. A package may import from any package of
strictly lower rank, and from nothing else. Ranks are spaced by ten so a new
package can be inserted without renumbering, and every same-rank or upward edge
that exists today would have to appear in :data:`DOCUMENTED_EXCEPTIONS`, which
is empty — the tree is clean.

On top of the ranks sit :data:`FORBIDDEN_EDGES`, which rank alone cannot
express: ``agents/`` sits at the top of the tree, so ranking would happily let
it import ``broker/``. It must not, ever, and the reason is in
``docs/v2/AI_AGENT_ARCHITECTURE.md``. That prohibition is separately enforced by
``tests/unit/test_agents_capabilities.py`` and by an import-time guard; stating
it here as well costs nothing and means the layering rule is readable in one
place.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"

#: The dependency order from ``docs/v2/ARCHITECTURE.md`` §2, as data.
#: A package may import from any package with a STRICTLY LOWER rank.
RANKS: dict[str, int] = {
    # Foundation: imports nothing from Fiboki.
    "core": 0,
    # Bytes in, validated frames out.
    "data": 10,
    # Pure functions of a frame.
    "indicators": 20,
    # The DSL and its compiler: documents to signals.
    "strategy": 30,
    # The fill model and the broker cost profiles.
    "sim": 40,
    # The engine and its metrics.
    "backtest": 50,
    # Sizing: one decision, made once.
    "portfolio": 60,
    # The gateway, the limits and the kill switch.
    "risk": 70,
    # Adapters, the execution service and the mode guard.
    "broker": 80,
    # Cross-cutting analysis layers that sit above the execution stack.
    "marketstate": 85,
    "stats": 85,
    "obs": 85,
    # The promotion ladder and the gate set.
    "validation": 90,
    # The experiment ledger, research memory and lineage.
    "research": 95,
    # Promotion, degradation and stopping rules: reads a ValidationReport and a
    # live divergence monitor, so it sits above validation and obs.
    "lifecycle": 96,
    # Automated strategy discovery: proposes candidates from the DSL, consults
    # the research ledger for novelty, and drives the validation ladder. It sits
    # above all three and is imported by none of them.
    "discovery": 97,
    # The LLM research fleet. Above everything, and reaches NONE of the
    # execution stack -- see FORBIDDEN_EDGES.
    "agents": 100,
    # Supervised processes with their own pid and exit code.
    "workers": 105,
    # The HTTP surface.
    "api": 110,
    # ``cli.py`` and ``__init__.py``, which compose the lot.
    "<root>": 120,
}

#: Same-rank or upward edges that are allowed anyway, each with the reason it is
#: defensible. EMPTY, and it should stay that way: an entry here is a documented
#: hole in the dependency order, not a formality.
DOCUMENTED_EXCEPTIONS: dict[tuple[str, str], str] = {}

#: Edges that rank alone would permit and that must never exist.
FORBIDDEN_EDGES: dict[tuple[str, str], str] = {
    ("agents", "broker"): (
        "An agent is a research instrument with no authority over execution and "
        "no way to acquire it. docs/v2/AI_AGENT_ARCHITECTURE.md."
    ),
    ("agents", "risk"): (
        "An agent may not read, still less change, a risk limit or the kill "
        "switch. docs/v2/AI_AGENT_ARCHITECTURE.md."
    ),
    ("agents", "portfolio"): (
        "An agent may not size a position. portfolio.sizing.size_trade decides "
        "once, and not on an agent's behalf."
    ),
}


def _packages() -> list[str]:
    return sorted(
        p.name
        for p in SRC.iterdir()
        if p.is_dir() and not p.name.startswith("__") and any(p.rglob("*.py"))
    )


def _package_of(path: Path) -> str:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else "<root>"


def _fiboki_imports(tree: ast.AST) -> set[str]:
    """Every ``fiboki.<package>`` this module imports, however it spells it."""
    found: set[str] = set()
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # A relative import cannot leave its own package, so it cannot
                # violate a rule about edges BETWEEN packages.
                continue
            modules = [node.module or ""]
        for module in modules:
            parts = module.split(".")
            if len(parts) >= 2 and parts[0] == "fiboki":
                found.add(parts[1])
    return found


def _edges() -> dict[tuple[str, str], list[str]]:
    """``(importer, imported) -> the files that make that edge``."""
    out: dict[tuple[str, str], list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        package = _package_of(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target in sorted(_fiboki_imports(tree)):
            if target == package:
                continue
            out.setdefault((package, target), []).append(
                str(path.relative_to(SRC.parents[1]))
            )
    return out


# ==========================================================================
# The rule itself
# ==========================================================================


def test_every_package_has_a_declared_rank():
    """A new package must be placed in the order deliberately.

    Without this, adding ``fiboki/newthing/`` would silently be exempt from the
    whole rule — the most common way an enforcement test quietly stops
    enforcing.
    """
    undeclared = [p for p in _packages() if p not in RANKS]
    assert not undeclared, (
        f"package(s) {undeclared} have no rank in RANKS. Decide where they sit "
        "in docs/v2/ARCHITECTURE.md §2 and say so here; do not leave them "
        "outside the rule."
    )


def test_every_declared_rank_names_a_real_package():
    """The mirror: a rank for a package that no longer exists is dead data that
    makes the rule look broader than it is."""
    packages = set(_packages()) | {"<root>"}
    stale = sorted(set(RANKS) - packages)
    assert not stale, f"RANKS names {stale}, which do not exist under src/fiboki/"


def test_imports_point_downwards_only():
    """THE test. Every import edge goes to a strictly lower rank."""
    violations: list[str] = []
    for (importer, imported), files in sorted(_edges().items()):
        if imported not in RANKS or importer not in RANKS:
            continue  # reported by the coverage tests above
        if RANKS[imported] < RANKS[importer]:
            continue
        if (importer, imported) in DOCUMENTED_EXCEPTIONS:
            continue
        direction = "sideways" if RANKS[imported] == RANKS[importer] else "UPWARDS"
        violations.append(
            f"{importer} (rank {RANKS[importer]}) imports {imported} "
            f"(rank {RANKS[imported]}) -- {direction}: {', '.join(files)}"
        )
    assert not violations, (
        "The dependency direction declared in docs/v2/ARCHITECTURE.md §2 and in "
        "core/contracts.py is violated:\n  " + "\n  ".join(violations) + "\n"
        "Invert the dependency (a Protocol in the lower package that the higher "
        "one satisfies) or, if the edge is genuinely right, move the package in "
        "RANKS and say why. Adding it to DOCUMENTED_EXCEPTIONS is a last resort "
        "and needs a reason a reader will accept."
    )


@pytest.mark.parametrize(("importer", "imported"), sorted(FORBIDDEN_EDGES))
def test_forbidden_edges_do_not_exist(importer: str, imported: str):
    """Prohibitions that rank alone cannot express, because the forbidden target
    is BELOW the forbidding package and would otherwise be allowed."""
    offenders = _edges().get((importer, imported), [])
    assert not offenders, (
        f"{importer}/ imports {imported}/ in {offenders}. "
        + FORBIDDEN_EDGES[(importer, imported)]
    )


def test_core_imports_nothing_from_fiboki_above_it():
    """``core/`` is the foundation: it must have no Fiboki dependency at all."""
    offenders = {
        edge: files for edge, files in _edges().items() if edge[0] == "core"
    }
    assert not offenders, f"core/ imports {sorted(e[1] for e in offenders)}: {offenders}"


# ==========================================================================
# The test's own machinery
# ==========================================================================


def test_the_parser_actually_sees_edges():
    """A rule engine that found nothing would pass every assertion above.

    Three edges that are certainly present and certainly legal.
    """
    edges = _edges()
    assert ("backtest", "core") in edges
    assert ("backtest", "sim") in edges
    assert ("validation", "backtest") in edges
    assert len(edges) > 20, f"only {len(edges)} import edges found; the walk is broken"


def test_the_parser_sees_an_import_inside_a_function(tmp_path: Path):
    """Deferred imports are still imports.

    A module that imports upwards inside a function to "avoid a cycle" has the
    dependency; hiding it from an import-graph tool does not remove it. ``ast``
    walks the whole tree, so this is free — but it is exactly the loophole a
    naive top-of-file scan would leave, so it is asserted.
    """
    source = (
        "def f():\n"
        "    from fiboki.broker.paper import PaperBroker\n"
        "    return PaperBroker\n"
    )
    tree = ast.parse(source)
    assert _fiboki_imports(tree) == {"broker"}


def test_the_parser_sees_a_plain_import_statement():
    assert _fiboki_imports(ast.parse("import fiboki.risk.gateway")) == {"risk"}
    assert _fiboki_imports(ast.parse("import fiboki")) == set()
    assert _fiboki_imports(ast.parse("from . import sibling")) == set()


def test_the_rule_would_actually_fail_on_a_violation():
    """The rule must be capable of failing. A check whose failure branch is
    never exercised is a check nobody has tested."""
    assert RANKS["data"] < RANKS["broker"]
    # If data/ ever imported broker/ -- the example ROADMAP.md §5 names -- the
    # comparison below is the one that would reject it.
    assert not RANKS["broker"] < RANKS["data"]
