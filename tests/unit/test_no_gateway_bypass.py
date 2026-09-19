"""Structural proof, over the AST, that no order path bypasses the risk gateway.

V1's ``RiskEngine.check_trade_allowed`` had zero call sites. No unit test would
have caught that, because every test that called it passed. The only test that
catches "nobody calls it" is one that reads the whole source tree and asks.

Three properties are asserted here:

1. ``Order(...)`` is constructed in exactly ONE place in ``src/`` --
   :meth:`fiboki.broker.execution_service.ExecutionService.submit`.
2. That function also calls ``self.gateway.evaluate``, and the call appears
   BEFORE the ``Order`` construction in source order.
3. Broker adapters neither size nor construct risk decisions.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"

#: The single module permitted to construct an Order, and the function in it.
AUTHORISED_ORDER_SITE = ("broker/execution_service.py", "submit")


def _python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _order_constructions(tree: ast.AST) -> list[ast.Call]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name == "Order":
                out.append(node)
    return out


def _enclosing_function(tree: ast.AST, node: ast.AST) -> ast.FunctionDef | None:
    best: ast.FunctionDef | None = None
    for candidate in ast.walk(tree):
        if not isinstance(candidate, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        end = candidate.end_lineno or candidate.lineno
        if candidate.lineno <= node.lineno <= end and (
            best is None or candidate.lineno > best.lineno
        ):
            best = candidate
    return best


def test_order_is_constructed_in_exactly_one_place() -> None:
    sites: list[tuple[str, int, str]] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _order_constructions(tree):
            fn = _enclosing_function(tree, call)
            sites.append((_rel(path), call.lineno, fn.name if fn else "<module>"))

    assert sites, "no Order construction found at all -- has the module moved?"
    unauthorised = [
        s for s in sites if (s[0], s[2]) != AUTHORISED_ORDER_SITE
    ]
    assert not unauthorised, (
        "Order() constructed outside the execution service. Every order must be "
        f"born downstream of a RiskDecision. Offending sites: {unauthorised}"
    )


def test_the_order_site_calls_the_gateway_first() -> None:
    path = SRC / "broker" / "execution_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = _order_constructions(tree)
    assert len(calls) == 1
    order_call = calls[0]

    fn = _enclosing_function(tree, order_call)
    assert fn is not None and fn.name == "submit"

    gateway_calls = [
        node.lineno
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith("evaluate")
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "gateway"
    ]
    assert gateway_calls, "submit() does not call self.gateway.evaluate at all"
    assert min(gateway_calls) < order_call.lineno, (
        "the gateway is called AFTER the Order is constructed; permission must "
        "precede the instruction"
    )


def test_close_path_also_goes_through_the_gateway() -> None:
    """Exits are orders too. V1's kill switch abandoned open positions."""
    path = SRC / "broker" / "execution_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    close_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "close"
    )
    evaluated = [
        node
        for node in ast.walk(close_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "evaluate_exit"
    ]
    assert evaluated, "ExecutionService.close does not consult the risk gateway"


@pytest.mark.parametrize(
    "module",
    ["broker/paper.py", "broker/oanda.py", "broker/ig.py", "broker/simulated_venue.py"],
)
def test_adapters_do_not_size(module: str) -> None:
    """An adapter that calls a sizer is re-deciding size. That was V1's bug."""
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    forbidden = {"size_trade", "size_for", "PortfolioSizer"}
    offenders = [
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in forbidden)
            or (isinstance(node.func, ast.Attribute) and node.func.attr in forbidden)
        )
    ]
    assert not offenders, f"{module} sizes orders: {offenders}"


@pytest.mark.parametrize(
    "module",
    ["broker/paper.py", "broker/oanda.py", "broker/ig.py", "broker/simulated_venue.py"],
)
def test_adapters_do_not_construct_risk_decisions(module: str) -> None:
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "RiskDecision")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "RiskDecision")
        )
    ]
    assert not offenders, (
        f"{module} constructs a RiskDecision at lines {offenders}. Permission is "
        "granted by the gateway and nowhere else."
    )


def test_every_declared_gateway_check_has_an_implementation() -> None:
    """A check named in CHECKS but not implemented would fail closed at runtime;
    it should fail loudly here instead."""
    from fiboki.risk.gateway import RiskGateway

    missing = [c for c in RiskGateway.CHECKS if not hasattr(RiskGateway, f"_check_{c}")]
    assert not missing, f"declared but unimplemented gateway checks: {missing}"
    assert len(set(RiskGateway.CHECKS)) == len(RiskGateway.CHECKS), "duplicate check names"
    assert set(RiskGateway.EXIT_CHECKS) <= set(RiskGateway.CHECKS)
