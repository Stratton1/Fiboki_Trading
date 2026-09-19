"""The boundary between agent-produced TEXT and anything the platform runs.

A strategy proposed by an agent is a :class:`~fiboki.strategy.dsl.StrategyDocument`
-- that is, **data**.  It is parsed, never interpreted.  Three properties make
that claim structural rather than aspirational:

1.  Agent text enters the system only through ``json.loads``.  There is no
    ``eval``, ``exec``, ``compile``, ``pickle.loads`` or ``__import__`` call
    anywhere in :mod:`fiboki.agents`; :func:`assert_no_dynamic_execution` walks
    the package's ASTs and proves it, and a test runs that proof.
2.  ``StrategyDocument`` and every model beneath it are declared with
    ``extra="forbid"``, so an unknown field is a validation error, not an
    ignored extra.  A document that carries a field the schema does not know
    about cannot be constructed at all.
3.  Before validation, every string in the payload is scanned for code-bearing
    syntax.  This is deliberately conservative -- a hypothesis that happens to
    contain ``exec(`` is rejected -- because the cost of a false positive is a
    rewrite and the cost of a false negative is a code path we swore did not
    exist.

Structural-shape limits (depth, node count, string length) are applied first,
so a hostile payload cannot exhaust memory before validation rejects it.
"""
from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fiboki.strategy.compiler import CompilationError, CompiledStrategy, compile_strategy
from fiboki.strategy.dsl import StrategyDocument

#: Maximum nesting depth of a proposed document.
MAX_DEPTH = 24
#: Maximum number of JSON nodes (containers + scalars).
MAX_NODES = 20_000
#: Maximum length of any single string field.  ``hypothesis`` is long by design.
MAX_STRING_CHARS = 20_000
#: Maximum serialised size of the whole payload.
MAX_PAYLOAD_BYTES = 1_000_000


class SandboxRejection(ValueError):
    """A proposed artefact was refused at the boundary.

    ``code`` is machine-readable so the audit ledger records *why* a proposal
    was refused, not merely that it was.
    """

    def __init__(self, code: str, detail: str, *, path: str = "") -> None:
        self.code = code
        self.detail = detail
        self.path = path
        where = f" at {path}" if path else ""
        super().__init__(f"[{code}]{where} {detail}")


# ---------------------------------------------------------------------------
# Code-bearing content
# ---------------------------------------------------------------------------

#: Patterns that indicate a string is trying to be executed rather than read.
#: Each requires call syntax or a statement form, so ordinary quant prose
#: ("we evaluate the signal", "import of volatility") does not trip them.
_CODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dunder_import", re.compile(r"__import__\s*\(")),
    ("eval_call", re.compile(r"\beval\s*\(")),
    ("exec_call", re.compile(r"\bexec\s*\(")),
    ("compile_call", re.compile(r"\bcompile\s*\(")),
    ("os_system", re.compile(r"\bos\s*\.\s*(system|popen|exec\w*|spawn\w*)\s*\(")),
    ("subprocess", re.compile(r"\bsubprocess\s*\.")),
    ("pickle", re.compile(r"\bpickle\s*\.\s*loads?\s*\(")),
    ("marshal", re.compile(r"\bmarshal\s*\.\s*loads?\s*\(")),
    ("import_statement", re.compile(r"(?m)^\s*(import|from)\s+[A-Za-z_][\w.]*")),
    ("lambda_expression", re.compile(r"\blambda\b[^\n:]{0,60}:")),
    ("dunder_attribute", re.compile(r"__(class|globals|builtins|subclasses|bases|code)__")),
    ("builtin_introspection", re.compile(r"\b(globals|locals|getattr|setattr|vars)\s*\(")),
    ("open_call", re.compile(r"\bopen\s*\(\s*['\"]")),
    ("shell_substitution", re.compile(r"\$\(|`[^`]{1,200}`")),
    ("script_tag", re.compile(r"(?i)<\s*script\b")),
    ("js_uri", re.compile(r"(?i)\bjavascript\s*:")),
    ("python_shebang", re.compile(r"(?m)^#!\s*/.*(python|sh|bash)")),
    ("null_byte", re.compile(r"\x00")),
)


def scan_for_code(text: str, *, path: str = "") -> None:
    """Raise :class:`SandboxRejection` if ``text`` reads as executable code."""
    for code, pattern in _CODE_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            raise SandboxRejection(
                "code_bearing_field",
                (
                    f"matched {code} pattern {match.group(0)!r}. Agent-produced text "
                    "is data; a DSL field is never a place to smuggle code."
                ),
                path=path or "<root>",
            )


def _walk(node: Any, path: str, depth: int, counter: list[int]) -> None:
    counter[0] += 1
    if counter[0] > MAX_NODES:
        raise SandboxRejection("too_many_nodes", f"payload exceeds {MAX_NODES} nodes", path=path)
    if depth > MAX_DEPTH:
        raise SandboxRejection("too_deep", f"nesting exceeds depth {MAX_DEPTH}", path=path)
    if isinstance(node, str):
        if len(node) > MAX_STRING_CHARS:
            raise SandboxRejection(
                "string_too_long",
                f"string of {len(node)} chars exceeds {MAX_STRING_CHARS}",
                path=path,
            )
        scan_for_code(node, path=path)
        return
    if isinstance(node, bool | int | float) or node is None:
        return
    if isinstance(node, Mapping):
        for key in node:
            if not isinstance(key, str):
                raise SandboxRejection(
                    "non_string_key", f"object key {key!r} is not a string", path=path
                )
            scan_for_code(key, path=f"{path}.{key}")
            _walk(node[key], f"{path}.{key}", depth + 1, counter)
        return
    if isinstance(node, Sequence):
        for i, item in enumerate(node):
            _walk(item, f"{path}[{i}]", depth + 1, counter)
        return
    raise SandboxRejection(
        "unsupported_type",
        f"value of type {type(node).__name__} is not JSON data",
        path=path,
    )


def parse_payload(payload: Mapping[str, Any] | str | bytes) -> dict[str, Any]:
    """Turn agent output into a plain JSON object.  Parses only; never evaluates."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SandboxRejection("not_utf8", str(exc)) from exc
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise SandboxRejection(
                "payload_too_large", f"payload exceeds {MAX_PAYLOAD_BYTES} bytes"
            )
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SandboxRejection("not_json", f"payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SandboxRejection(
            "not_an_object",
            f"a strategy proposal must be a JSON object, got {type(payload).__name__}",
        )
    return {str(k): v for k, v in payload.items()}


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcceptedStrategy:
    """A proposal that survived the boundary, with its derived facts."""

    document: StrategyDocument
    compiled: CompiledStrategy
    content_hash: str
    warmup_period: int
    complexity_score: float

    @property
    def strategy_id(self) -> str:
        return self.document.strategy_id


def validate_strategy_payload(
    payload: Mapping[str, Any] | str | bytes,
) -> AcceptedStrategy:
    """Validate an agent-proposed strategy document.

    Order matters: shape limits, then code scanning, then schema validation
    (which rejects unknown fields), then compilation.  The first failure wins
    and carries a machine-readable code.

    There is no "validate but do not compile" mode: a proposal nobody can
    realise is a proposal nobody can reason about, so compilation is part of
    acceptance rather than a later, skippable step.
    """
    data = parse_payload(payload)
    _walk(data, "", 0, [0])

    try:
        document = StrategyDocument.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        unknown = [".".join(str(p) for p in e["loc"]) for e in errors if e["type"] == "extra_forbidden"]
        if unknown:
            raise SandboxRejection(
                "unknown_field",
                (
                    f"document declares fields the schema does not know: {unknown}. "
                    "extra='forbid' is the reason an invented field cannot ride along."
                ),
            ) from exc
        summary = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in errors[:8]
        )
        raise SandboxRejection("schema_invalid", summary) from exc
    except ValueError as exc:  # pragma: no cover - pydantic wraps most of these
        raise SandboxRejection("schema_invalid", str(exc)) from exc

    try:
        compiled = compile_strategy(document)
    except (CompilationError, ValueError) as exc:
        raise SandboxRejection("does_not_compile", str(exc)) from exc

    return AcceptedStrategy(
        document=document,
        compiled=compiled,
        content_hash=document.content_hash(),
        warmup_period=compiled.warmup_period,
        complexity_score=document.complexity_score,
    )


# ---------------------------------------------------------------------------
# Proving the absence of dynamic execution
# ---------------------------------------------------------------------------

#: Builtins that turn text into code.
_FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset({"eval", "exec", "compile", "__import__"})
#: Module attributes that turn text (or bytes) into behaviour.
_FORBIDDEN_ATTRIBUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("os", "execv"),
        ("os", "execve"),
        ("os", "spawnv"),
        ("pickle", "load"),
        ("pickle", "loads"),
        ("marshal", "loads"),
        ("subprocess", "run"),
        ("subprocess", "call"),
        ("subprocess", "Popen"),
        ("subprocess", "check_output"),
    }
)
#: Modules an agent-facing package has no business importing.
_FORBIDDEN_IMPORTS: frozenset[str] = frozenset({"subprocess", "pickle", "marshal", "shelve"})


def _agents_package_root() -> Path:
    return Path(__file__).resolve().parent


def find_dynamic_execution(root: Path | None = None) -> list[str]:
    """Return a finding per dynamic-execution construct found under ``root``.

    AST-based rather than text-based, so this module's own pattern strings do
    not produce false positives -- the check looks for *call sites*, not words.
    """
    base = root or _agents_package_root()
    findings: list[str] = []
    for source_file in sorted(base.rglob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        rel = source_file.relative_to(base.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALL_NAMES:
                    findings.append(f"{rel}:{node.lineno}: call to {func.id}()")
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    pair = (func.value.id, func.attr)
                    if pair in _FORBIDDEN_ATTRIBUTES:
                        findings.append(f"{rel}:{node.lineno}: call to {pair[0]}.{pair[1]}()")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in _FORBIDDEN_IMPORTS:
                        findings.append(f"{rel}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in _FORBIDDEN_IMPORTS:
                    findings.append(f"{rel}:{node.lineno}: from {node.module} import ...")
    return findings


def assert_no_dynamic_execution(root: Path | None = None) -> None:
    """Raise if any module under ``root`` can turn text into behaviour."""
    findings = find_dynamic_execution(root)
    if findings:
        raise SandboxRejection(
            "dynamic_execution_present",
            "the agents package must contain no path from text to execution:\n  "
            + "\n  ".join(findings),
        )


__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_PAYLOAD_BYTES",
    "MAX_STRING_CHARS",
    "AcceptedStrategy",
    "SandboxRejection",
    "assert_no_dynamic_execution",
    "find_dynamic_execution",
    "parse_payload",
    "scan_for_code",
    "validate_strategy_payload",
]
