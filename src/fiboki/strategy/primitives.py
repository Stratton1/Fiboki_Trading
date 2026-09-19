"""The rule vocabulary a Strategy DSL document compiles from.

Every primitive does two things:

* **evaluates** itself against a single bar, reading only that bar and bars
  before it, and
* **declares which indicators it needs**, so the compiler derives a strategy's
  indicator set (and therefore its warmup) automatically. V1 hardcoded a warmup
  of 98 bars for every bot because it read an attribute that did not exist; here
  the warmup is a derived fact that cannot drift from the rules.

Look-ahead is prevented **structurally, twice over**:

1. Operand offsets are ``ge=0`` at the schema level -- a negative offset (i.e.
   a future bar) cannot be expressed in a valid document at all.
2. :class:`EvalContext` is constructed over a frame already truncated at the
   evaluation bar, so even a buggy primitive has no future rows to read.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypeAlias

import pandas as pd
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from fiboki.indicators.base import Indicator
from fiboki.indicators.registry import UnknownIndicatorError, create

Comparator = Literal[">", ">=", "<", "<=", "==", "!="]
PriceField = Literal["open", "high", "low", "close"]

_COMPARATORS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


class SpecError(ValueError):
    """A DSL fragment that cannot be realised against the indicator library."""


class UnboundParameterError(SpecError):
    """A parameter reference was read as if it were a number."""


# ------------------------------------------------------- parameter binding

#: The JSON key that marks a parameter reference. Chosen to be impossible as an
#: indicator parameter name, so a reference can never be confused for one.
PARAM_REF_KEY = "$param"


class ParamRef(BaseModel):
    """A placeholder standing in for a value the strategy DECLARED but has not
    yet been given.

    Serialises as ``{"$param": "rsi_period"}``. It is a *model*, not a number,
    which is the whole point: a document holding one cannot be evaluated by
    accident. Every arithmetic path in this module reads through
    :meth:`EvalContext.read` or a comparator, and both refuse a ``ParamRef``
    loudly rather than coercing it. The compiler refuses earlier still --
    :func:`fiboki.strategy.compiler.compile_strategy` will not build a document
    that still contains one -- so a half-bound strategy has no path to the
    engine at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    param: str = Field(
        validation_alias=AliasChoices(PARAM_REF_KEY, "param"),
        min_length=1,
        max_length=64,
    )

    @field_validator("param")
    @classmethod
    def _name(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", v):
            raise ValueError(
                f"parameter reference {v!r} must be a lowercase identifier; it has "
                "to match a key of StrategyDocument.parameters exactly"
            )
        return v

    @model_serializer
    def _serialise(self) -> dict[str, str]:
        return {PARAM_REF_KEY: self.param}

    def __hash__(self) -> int:
        return hash((PARAM_REF_KEY, self.param))

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"${self.param}"


def is_ref(value: Any) -> bool:
    """True for a parameter reference in either model or dumped form."""
    if isinstance(value, ParamRef):
        return True
    return isinstance(value, dict) and set(value) == {PARAM_REF_KEY}


def _number(value: Any, where: str) -> float:
    """Read a bindable field as a number, refusing an unresolved reference."""
    if is_ref(value):
        raise UnboundParameterError(
            f"{where} still holds the unresolved parameter reference {value!r}. "
            "Bind the document before evaluating it: a half-bound strategy must "
            "never run."
        )
    return float(value)


#: A field whose literal may instead name a declared parameter.
NumberOrRef: TypeAlias = float | ParamRef
IntOrRef: TypeAlias = int | ParamRef


def _refs_in(node: Any) -> list[ParamRef]:
    """Every :class:`ParamRef` reachable from a model, mapping or sequence."""
    out: list[ParamRef] = []
    _walk_refs(node, out)
    return out


def _walk_refs(node: Any, out: list[ParamRef]) -> None:
    if isinstance(node, ParamRef):
        out.append(node)
        return
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            _walk_refs(getattr(node, name), out)
        return
    if isinstance(node, dict):
        if is_ref(node):
            out.append(ParamRef.model_validate(node))
            return
        for value in node.values():
            _walk_refs(value, out)
        return
    if isinstance(node, list | tuple | set):
        for value in node:
            _walk_refs(value, out)


def _param_repr(value: Any) -> str:
    """``repr`` for an indicator parameter, stable for references.

    ``ParamRef.__repr__`` is ``$name``, so ``rsi($rsi_period)`` and ``rsi(14)``
    are different identities -- which they are.
    """
    return repr(value)


def resolve_refs(node: Any, values: Mapping[str, Any]) -> Any:
    """Recursively replace every ``{"$param": name}`` in DUMPED data.

    Operates on plain JSON-shaped data rather than on models, so one function
    covers every place a reference can appear -- rule thresholds, regime bounds,
    indicator parameters nested inside operands, exit models -- with no
    per-field wiring that could fall out of date when a field is added.
    """
    if isinstance(node, dict):
        if is_ref(node):
            name = node[PARAM_REF_KEY]
            if name not in values:
                raise UnboundParameterError(
                    f"no value supplied for parameter {name!r}; an unresolved "
                    "reference is never silently defaulted"
                )
            return values[name]
        return {k: resolve_refs(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_refs(v, values) for v in node]
    return node


# --------------------------------------------------------------- indicators


class IndicatorSpec(BaseModel):
    """A registry key plus parameters: the identity of one indicator instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    indicator: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("indicator")
    @classmethod
    def _known(cls, v: str) -> str:
        from fiboki.indicators.registry import INDICATORS

        if v not in INDICATORS:
            raise ValueError(f"unknown indicator {v!r}; registered: {sorted(INDICATORS)}")
        return v

    @property
    def unbound_parameters(self) -> tuple[str, ...]:
        """Names of declared parameters this spec is still waiting for."""
        return tuple(sorted({r.param for r in _refs_in(self.params)}))

    @model_validator(mode="after")
    def _instantiable(self) -> IndicatorSpec:
        if self.unbound_parameters:
            # The params are references, so there is no indicator to build yet.
            # The check is not skipped, only DEFERRED: it runs again the moment
            # the document is bound, because binding re-validates the whole
            # document, and the compiler refuses an unbound one outright.
            return self
        try:
            self.build()
        except (TypeError, ValueError, UnknownIndicatorError) as exc:
            raise ValueError(f"indicator {self.indicator!r} rejects params: {exc}") from exc
        return self

    def build(self) -> Indicator:
        if self.unbound_parameters:
            raise UnboundParameterError(
                f"indicator {self.indicator!r} cannot be built: its parameters "
                f"{list(self.unbound_parameters)} are unresolved references"
            )
        return create(self.indicator, self.params)

    @property
    def key(self) -> str:
        """Canonical identity, stable across dict ordering."""
        inner = ",".join(f"{k}={_param_repr(self.params[k])}" for k in sorted(self.params))
        return f"{self.indicator}({inner})"

    def __hash__(self) -> int:  # params is a dict, so hash the canonical key
        return hash(self.key)


# ----------------------------------------------------------------- operands


class IndicatorOperand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["indicator"] = "indicator"
    spec: IndicatorSpec
    #: Output column: the bare suffix ("upper"), the full column name, or "" for
    #: the indicator's primary output.
    output: str = ""
    #: Bars BACK from the evaluation bar. Never negative -- see module docstring.
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _resolvable(self) -> IndicatorOperand:
        if not self.spec.unbound_parameters:
            self.column  # noqa: B018 - property raises if unresolvable
        return self

    @property
    def column(self) -> str:
        ind = self.spec.build()
        outputs = ind.output_columns
        if not self.output:
            return outputs[0]
        if self.output in outputs:
            return self.output
        qualified = f"{ind.name}_{self.output}"
        if qualified in outputs:
            return qualified
        raise SpecError(
            f"{self.spec.key}: output {self.output!r} is not one of {list(outputs)}"
        )

    def __hash__(self) -> int:
        return hash((self.spec.key, self.output, self.offset))


class PriceOperand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["price"] = "price"
    field: PriceField = "close"
    offset: int = Field(default=0, ge=0)


class ConstantOperand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["constant"] = "constant"
    value: NumberOrRef


Operand = Annotated[
    IndicatorOperand | PriceOperand | ConstantOperand, Field(discriminator="kind")
]


# ------------------------------------------------------------------ context


@dataclass(frozen=True, slots=True)
class EvalContext:
    """Everything a rule may see: a frame ending at the evaluation bar.

    ``frame`` is already sliced to ``[0 .. idx]``. There is no API on this
    object that reaches past its last row.
    """

    frame: pd.DataFrame
    bar_time: pd.Timestamp

    @property
    def pos(self) -> int:
        return len(self.frame) - 1

    def read(self, operand: Any) -> float:
        kind = operand.kind
        if kind == "constant":
            return _number(operand.value, "ConstantOperand.value")
        i = self.pos - operand.offset
        if i < 0:
            return math.nan
        column = operand.field if kind == "price" else operand.column
        if column not in self.frame.columns:
            raise SpecError(
                f"column {column!r} missing from the prepared frame; the compiler "
                "should have computed it"
            )
        value = self.frame[column].iat[i]
        return math.nan if value is None else float(value)


def _finite(*values: float) -> bool:
    return all(isinstance(v, float) and math.isfinite(v) for v in values)


# -------------------------------------------------------------------- rules


class _BaseRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:  # pragma: no cover
        raise NotImplementedError

    def max_lookback(self) -> int:  # pragma: no cover
        raise NotImplementedError

    def evaluate(self, ctx: EvalContext) -> bool:  # pragma: no cover
        raise NotImplementedError


def _specs_of(*operands: Any) -> tuple[IndicatorSpec, ...]:
    return tuple(o.spec for o in operands if getattr(o, "kind", None) == "indicator")


def _offset_of(*operands: Any) -> int:
    return max((getattr(o, "offset", 0) for o in operands), default=0)


class ThresholdRule(_BaseRule):
    """Operand compared with a fixed number (e.g. ``rsi < 30``)."""

    op: Literal["threshold"] = "threshold"
    operand: Operand
    comparator: Comparator
    value: NumberOrRef

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return _specs_of(self.operand)

    def max_lookback(self) -> int:
        return _offset_of(self.operand)

    def evaluate(self, ctx: EvalContext) -> bool:
        threshold = _number(self.value, "ThresholdRule.value")
        a = ctx.read(self.operand)
        return bool(_COMPARATORS[self.comparator](a, threshold)) if _finite(a) else False


class IndicatorVsIndicatorRule(_BaseRule):
    """One indicator compared with another (e.g. ``tenkan > kijun``)."""

    op: Literal["indicator_vs_indicator"] = "indicator_vs_indicator"
    left: IndicatorOperand
    right: IndicatorOperand
    comparator: Comparator

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return _specs_of(self.left, self.right)

    def max_lookback(self) -> int:
        return _offset_of(self.left, self.right)

    def evaluate(self, ctx: EvalContext) -> bool:
        a, b = ctx.read(self.left), ctx.read(self.right)
        return bool(_COMPARATORS[self.comparator](a, b)) if _finite(a, b) else False


class IndicatorVsPriceRule(_BaseRule):
    """An indicator compared with an OHLC field (e.g. ``close > cloud_top``)."""

    op: Literal["indicator_vs_price"] = "indicator_vs_price"
    indicator: IndicatorOperand
    price: PriceOperand = Field(default_factory=PriceOperand)
    #: Read as ``indicator <comparator> price``.
    comparator: Comparator

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return _specs_of(self.indicator)

    def max_lookback(self) -> int:
        return _offset_of(self.indicator, self.price)

    def evaluate(self, ctx: EvalContext) -> bool:
        a, b = ctx.read(self.indicator), ctx.read(self.price)
        return bool(_COMPARATORS[self.comparator](a, b)) if _finite(a, b) else False


class CrossoverRule(_BaseRule):
    """``fast`` crosses ``slow``. Needs bar i and bar i-1, never bar i+1."""

    op: Literal["crossover"] = "crossover"
    fast: Operand
    slow: Operand
    direction: Literal["above", "below"]

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return _specs_of(self.fast, self.slow)

    def max_lookback(self) -> int:
        return _offset_of(self.fast, self.slow) + 1

    def evaluate(self, ctx: EvalContext) -> bool:
        now_f, now_s = ctx.read(self.fast), ctx.read(self.slow)
        prev_ctx = EvalContext(ctx.frame.iloc[:-1], ctx.bar_time)
        if len(prev_ctx.frame) == 0:
            return False
        prev_f, prev_s = prev_ctx.read(self.fast), prev_ctx.read(self.slow)
        if not _finite(now_f, now_s, prev_f, prev_s):
            return False
        if self.direction == "above":
            return prev_f <= prev_s and now_f > now_s
        return prev_f >= prev_s and now_f < now_s


class RegimeGateRule(_BaseRule):
    """An inclusive band on a regime metric (ADX, realised vol, ATR, ...).

    Semantically distinct from a threshold: a regime gate says "this strategy is
    only meaningful in this market state", and the compiler keeps regime rules
    separate from entry rules so research can measure them independently.
    """

    op: Literal["regime_gate"] = "regime_gate"
    metric: IndicatorOperand
    min_value: NumberOrRef | None = None
    max_value: NumberOrRef | None = None
    invert: bool = False

    @model_validator(mode="after")
    def _bounded(self) -> RegimeGateRule:
        if self.min_value is None and self.max_value is None:
            raise ValueError("regime_gate needs at least one of min_value/max_value")
        if (
            self.min_value is not None
            and self.max_value is not None
            and not (is_ref(self.min_value) or is_ref(self.max_value))
            and self.min_value > self.max_value
        ):
            raise ValueError("regime_gate min_value must be <= max_value")
        return self

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return _specs_of(self.metric)

    def max_lookback(self) -> int:
        return _offset_of(self.metric)

    def evaluate(self, ctx: EvalContext) -> bool:
        lo = None if self.min_value is None else _number(self.min_value, "regime_gate.min_value")
        hi = None if self.max_value is None else _number(self.max_value, "regime_gate.max_value")
        v = ctx.read(self.metric)
        if not _finite(v):
            return False
        inside = (lo is None or v >= lo) and (hi is None or v <= hi)
        return (not inside) if self.invert else inside


class SessionWindowRule(_BaseRule):
    """UTC hour-of-day / weekday window. Bar times are UTC by contract."""

    op: Literal["session_window"] = "session_window"
    start_hour_utc: int = Field(ge=0, le=23)
    end_hour_utc: int = Field(ge=0, le=23)
    #: Monday = 0 ... Sunday = 6.
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if not v:
            raise ValueError("session_window needs at least one weekday")
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("weekdays must be 0..6 (Monday=0)")
        return tuple(sorted(set(v)))

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return ()

    def max_lookback(self) -> int:
        return 0

    def evaluate(self, ctx: EvalContext) -> bool:
        ts = ctx.bar_time
        if ts.tzinfo is None:
            raise SpecError("session_window requires tz-aware UTC bar times")
        ts = ts.tz_convert("UTC")
        if ts.weekday() not in self.weekdays:
            return False
        h = ts.hour
        if self.start_hour_utc <= self.end_hour_utc:
            return self.start_hour_utc <= h <= self.end_hour_utc
        return h >= self.start_hour_utc or h <= self.end_hour_utc  # wraps midnight


class AllOfRule(_BaseRule):
    op: Literal["all_of"] = "all_of"
    rules: tuple[Rule, ...]

    @field_validator("rules")
    @classmethod
    def _nonempty(cls, v: tuple[Any, ...]) -> tuple[Any, ...]:
        if not v:
            raise ValueError("all_of needs at least one rule")
        return v

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return tuple(s for r in self.rules for s in r.required_indicators())

    def max_lookback(self) -> int:
        return max(r.max_lookback() for r in self.rules)

    def evaluate(self, ctx: EvalContext) -> bool:
        return all(r.evaluate(ctx) for r in self.rules)


class AnyOfRule(_BaseRule):
    op: Literal["any_of"] = "any_of"
    rules: tuple[Rule, ...]

    @field_validator("rules")
    @classmethod
    def _nonempty(cls, v: tuple[Any, ...]) -> tuple[Any, ...]:
        if not v:
            raise ValueError("any_of needs at least one rule")
        return v

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return tuple(s for r in self.rules for s in r.required_indicators())

    def max_lookback(self) -> int:
        return max(r.max_lookback() for r in self.rules)

    def evaluate(self, ctx: EvalContext) -> bool:
        return any(r.evaluate(ctx) for r in self.rules)


class NotRule(_BaseRule):
    op: Literal["not"] = "not"
    rule: Rule

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return self.rule.required_indicators()

    def max_lookback(self) -> int:
        return self.rule.max_lookback()

    def evaluate(self, ctx: EvalContext) -> bool:
        return not self.rule.evaluate(ctx)


Rule = Annotated[
    ThresholdRule
    | IndicatorVsIndicatorRule
    | IndicatorVsPriceRule
    | CrossoverRule
    | RegimeGateRule
    | SessionWindowRule
    | AllOfRule
    | AnyOfRule
    | NotRule,
    Field(discriminator="op"),
]

AllOfRule.model_rebuild()
AnyOfRule.model_rebuild()
NotRule.model_rebuild()

RULE_TYPES: tuple[type[_BaseRule], ...] = (
    ThresholdRule,
    IndicatorVsIndicatorRule,
    IndicatorVsPriceRule,
    CrossoverRule,
    RegimeGateRule,
    SessionWindowRule,
    AllOfRule,
    AnyOfRule,
    NotRule,
)


def collect_indicators(rules: Any) -> tuple[IndicatorSpec, ...]:
    """Unique indicator specs required by an iterable (or nesting) of rules."""
    seen: dict[str, IndicatorSpec] = {}
    for rule in rules:
        for spec in rule.required_indicators():
            seen.setdefault(spec.key, spec)
    return tuple(seen[k] for k in sorted(seen))


def count_rules(rules: Any) -> int:
    """Total rule count including nested rules -- the complexity-score input."""
    total = 0
    for rule in rules:
        total += 1
        if isinstance(rule, AllOfRule | AnyOfRule):
            total += count_rules(rule.rules)
        elif isinstance(rule, NotRule):
            total += count_rules([rule.rule])
    return total
