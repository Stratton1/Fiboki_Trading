"""Structural proof, over the AST, that retries touch READS and never WRITES.

``fiboki.broker.retry.retry_idempotent_read`` resends a request whose first
attempt failed. For a read that is harmless. For an order, a close, a partial
close or an amendment it is a second instruction to the venue sent while the
fate of the first is UNKNOWN -- which is precisely how a restart or a timeout
doubles a real position. No unit test catches "somebody decorated
``place_order`` last week"; only a test that reads the source does, which is
the same reason ``tests/unit/test_no_gateway_bypass.py`` exists.

Asserted here, over every module under ``src/fiboki``:

1. The decorator is never applied (as ``@retry_idempotent_read`` or as
   ``retry_idempotent_read(...)(fn)``) to a function whose name contains
   ``order``, ``submit``, ``close`` or ``amend``, and never to ``place_order``,
   ``close_position``, ``close_partial`` or ``amend_position`` by name.
2. A decorated function issues no non-GET request: no ``POST``/``PUT``/
   ``PATCH``/``DELETE`` literal appears in its body, and every ``_call``/
   ``request`` inside it passes ``"GET"``.
3. It IS applied in ``broker/oanda.py`` (so 1 and 2 are not vacuously true),
   every OANDA read goes through the decorated ``_get``, and every OANDA write
   goes through the undecorated ``_call``.
4. The checker itself flags a planted violation.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
DECORATOR = "retry_idempotent_read"
FORBIDDEN_SUBSTRINGS = ("order", "submit", "close", "amend")
FORBIDDEN_NAMES = frozenset({"place_order", "close_position", "close_partial", "amend_position"})
WRITE_VERBS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: OANDA adapter methods that are GETs and must use the retried path.
OANDA_READS = ("connect", "health", "account", "positions", "orders", "market_spec")
#: OANDA adapter methods that are writes and must NOT.
OANDA_WRITES = ("place_order", "close_position", "close_partial", "amend_position")


def _python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _names_decorator(node: ast.expr) -> bool:
    """``@retry_idempotent_read``, ``@retry.retry_idempotent_read``, or a call of either."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id == DECORATOR
    if isinstance(target, ast.Attribute):
        return target.attr == DECORATOR
    return False


def _expr_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ast.unparse(node)


def _forbidden(name: str) -> bool:
    lowered = name.lower()
    return name in FORBIDDEN_NAMES or any(s in lowered for s in FORBIDDEN_SUBSTRINGS)


def violations_in(source: str, filename: str = "<string>") -> list[str]:
    """Every breach of rules 1 and 2 in one module's source."""
    tree = ast.parse(source, filename=filename)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            _names_decorator(d) for d in node.decorator_list
        ):
            if _forbidden(node.name):
                out.append(f"{filename}:{node.lineno} decorates write-shaped {node.name}()")
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Constant)
                    and isinstance(inner.value, str)
                    and inner.value.upper() in WRITE_VERBS
                ):
                    out.append(
                        f"{filename}:{inner.lineno} {node.name}() is decorated and "
                        f"mentions {inner.value!r}"
                    )
                if isinstance(inner, ast.Call) and _expr_name(inner.func) in ("_call", "request"):
                    verb = inner.args[0] if inner.args else None
                    if not (isinstance(verb, ast.Constant) and verb.value == "GET"):
                        out.append(
                            f"{filename}:{inner.lineno} {node.name}() is decorated and "
                            f"issues {ast.unparse(inner)[:60]}"
                        )
        # Call form: retry_idempotent_read(...)(target)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Call)
            and _names_decorator(node.func)
        ):
            for arg in node.args:
                name = _expr_name(arg)
                if _forbidden(name):
                    out.append(f"{filename}:{node.lineno} wraps write-shaped {name}")
        # Bare call with the target as first argument: retry_idempotent_read(fn)
        if isinstance(node, ast.Call) and _names_decorator(node) and node.args:
            name = _expr_name(node.args[0])
            if _forbidden(name):
                out.append(f"{filename}:{node.lineno} wraps write-shaped {name}")
    return out


def test_retry_is_never_applied_to_a_write_anywhere_in_src() -> None:
    found: list[str] = []
    for path in _python_files():
        if path.name == "retry.py" and path.parent.name == "broker":
            continue  # the definition, not a use
        found += violations_in(path.read_text(encoding="utf-8"), str(path.relative_to(SRC)))
    assert not found, "retry applied to a write path:\n" + "\n".join(found)


def _oanda_class() -> ast.ClassDef:
    tree = ast.parse((SRC / "broker" / "oanda.py").read_text(encoding="utf-8"))
    return next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OandaAdapter"
    )


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _self_calls(fn: ast.FunctionDef) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
    ]


def test_the_oanda_adapter_retries_through_exactly_one_decorated_GET() -> None:
    cls = _oanda_class()
    decorated = [
        n.name
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and any(_names_decorator(d) for d in n.decorator_list)
    ]
    assert decorated == ["_get"], decorated
    get = _method(cls, "_get")
    calls = [c for c in _self_calls(get) if c.func.attr == "_call"]
    assert len(calls) == 1
    assert isinstance(calls[0].args[0], ast.Constant) and calls[0].args[0].value == "GET"


def test_every_oanda_read_uses_the_retried_path() -> None:
    cls = _oanda_class()
    for name in OANDA_READS:
        attrs = [c.func.attr for c in _self_calls(_method(cls, name))]
        assert "_get" in attrs, f"{name}() does not read through _get"
        direct = [
            c
            for c in _self_calls(_method(cls, name))
            if c.func.attr == "_call"
        ]
        assert not direct, f"{name}() bypasses the retried GET with a direct _call"


def test_every_oanda_write_uses_the_unretried_path() -> None:
    cls = _oanda_class()
    for name in OANDA_WRITES:
        attrs = [c.func.attr for c in _self_calls(_method(cls, name))]
        assert "_call" in attrs, f"{name}() no longer reaches the venue through _call"
        assert "_get" not in attrs, f"{name}() goes through the RETRIED path"


def test_the_checker_flags_planted_violations() -> None:
    """The check is only worth having if it fails on the thing it guards."""
    planted = '''
from fiboki.broker.retry import retry_idempotent_read

class Adapter:
    @retry_idempotent_read
    def place_order(self, order):
        return self._call("POST", "/orders", {})

    @retry_idempotent_read(retry=None)
    def fetch_everything(self):
        return self._call("PUT", "/trades/1/close")

wrapped = retry_idempotent_read(retry=None)(adapter.close_partial)
also = retry_idempotent_read(submit_order)
'''
    found = violations_in(planted)
    joined = "\n".join(found)
    assert "decorates write-shaped place_order()" in joined
    assert "mentions 'POST'" in joined
    assert "mentions 'PUT'" in joined
    assert "wraps write-shaped close_partial" in joined
    assert "wraps write-shaped submit_order" in joined


def test_the_checker_passes_a_clean_read() -> None:
    clean = '''
class Adapter:
    @retry_idempotent_read
    def _get(self, path):
        return self._call("GET", path)

fetch = retry_idempotent_read(retry=None)(provider.fetch_bars)
'''
    assert violations_in(clean) == []
