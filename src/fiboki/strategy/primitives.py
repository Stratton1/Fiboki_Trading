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
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    @model_validator(mode="after")
    def _instantiable(self) -> IndicatorSpec:
        try:
            self.build()
        except (TypeError, ValueError, UnknownIndicatorError) as exc:
            raise ValueError(f"indicator {self.indicator!r} rejects params: {exc}") from exc
        return self

    def build(self) -> Indicator:
        return create(self.indicator, self.params)

    @property
    def key(self) -> str:
        """Canonical identity, stable across dict ordering."""
        inner = ",".join(f"{k}={self.params[k]!r}" for k in sorted(self.params))
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
    value: float


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
            return float(operand.value)
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
    value: float

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return _specs_of(self.operand)

    def max_lookback(self) -> int:
        return _offset_of(self.operand)

    def evaluate(self, ctx: EvalContext) -> bool:
        a = ctx.read(self.operand)
        return bool(_COMPARATORS[self.comparator](a, self.value)) if _finite(a) else False


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
    min_value: float | None = None
    max_value: float | None = None
    invert: bool = False

    @model_validator(mode="after")
    def _bounded(self) -> RegimeGateRule:
        if self.min_value is None and self.max_value is None:
            raise ValueError("regime_gate needs at least one of min_value/max_value")
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("regime_gate min_value must be <= max_value")
        return self

    def required_indicators(self) -> tuple[IndicatorSpec, ...]:
        return _specs_of(self.metric)

    def max_lookback(self) -> int:
        return _offset_of(self.metric)

    def evaluate(self, ctx: EvalContext) -> bool:
        v = ctx.read(self.metric)
        if not _finite(v):
            return False
        inside = (self.min_value is None or v >= self.min_value) and (
            self.max_value is None or v <= self.max_value
        )
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
