"""Versioned, declarative Strategy schema.

A strategy in Fiboki V2 is **data**, not a Python class. That buys three things
V1 could not have: documents can be generated and mutated by a search process,
duplicates can be detected by content hash, and the whole population can be
audited by reading one directory.

Design decisions worth defending:

* ``hypothesis`` is mandatory and long. A rule set with no economic story is a
  curve fit waiting to be discovered. The seed documents in
  ``research/strategies/`` also state where the published evidence is *against*
  the idea.
* ``stop`` is mandatory at the schema level. There is no way to express a
  stopless strategy, so there is no code path that can produce one.
* ``take_profits`` is a LIST with allocation fractions. V1's engine could only
  ever use ``targets[0]``, which made every "scale out at 1R then run" idea
  unrepresentable and quietly untested.
* Every parameter carries an explicit DOMAIN, so a sweep is defined by the
  strategy rather than by whatever the sweeping script happened to guess.
* ``content_hash`` covers the semantic content only -- rename a strategy and the
  hash is unchanged, because it is the same strategy.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from fiboki.core import instruments
from fiboki.core.enums import Timeframe
from fiboki.strategy.primitives import (
    PARAM_REF_KEY,
    IndicatorOperand,
    ParamRef,
    Rule,
    UnboundParameterError,
    collect_indicators,
    count_rules,
    is_ref,
    literal_number,
    resolve_refs,
)

SCHEMA_VERSION = "2.0.0"


def strategy_key_version() -> str:
    """The version stamped beside any key DERIVED from a strategy document.

    ``StrategyDocument.semantic_payload()`` includes ``schema_version``, so every
    content hash moves when :data:`SCHEMA_VERSION` is bumped; the structural hash
    is read off the same dump and moves with any field rename. One version covers
    all of them because they all break together, and it lives here rather than in
    ``core/`` because this module is what makes it true.

    Persisted beside such a key so that a stored key and a freshly computed one
    can be told apart from "different inputs" -- see
    :mod:`fiboki.core.versioned_key` for the rule and the import-time check.
    """
    return f"dsl:{SCHEMA_VERSION}"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
#: Rule sets with no economic story are curve fits. Enforced, not suggested.
MIN_HYPOTHESIS_CHARS = 120


class BindingError(ValueError):
    """A parameter binding that cannot be honoured."""


class UnknownParameterError(BindingError):
    """A value was supplied for something the document does not declare."""


class OutOfDomainError(BindingError):
    """A value outside the domain the document declared for that parameter."""


class InfeasibleBindingError(BindingError):
    """Every value is in its own domain, but the combination is not a strategy.

    Two parameters whose declared ranges overlap (a ``fast`` that may exceed a
    ``slow``) make some corner of the declared grid unrealisable. The document is
    the thing at fault, not the caller, so the error names the whole binding.
    """


class StrategyFamily(str, Enum):
    ICHIMOKU = "ichimoku"
    TREND_FOLLOWING = "trend_following"
    BREAKOUT = "breakout"
    MEAN_REVERSION = "mean_reversion"
    MOMENTUM = "momentum"
    FIBONACCI = "fibonacci"
    VOLATILITY = "volatility"
    HYBRID = "hybrid"


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"


# ------------------------------------------------------------- parameters


class ParameterSpec(BaseModel):
    """A tunable parameter and the domain a sweep may explore."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["int", "float", "choice", "bool"]
    default: Any
    description: str = ""
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] | None = None

    @model_validator(mode="after")
    def _domain_is_explicit(self) -> ParameterSpec:
        if self.kind in ("int", "float"):
            if self.min_value is None or self.max_value is None:
                raise ValueError(f"{self.kind} parameter needs min_value and max_value")
            if self.min_value > self.max_value:
                raise ValueError("min_value must be <= max_value")
            if self.step is not None and self.step <= 0:
                raise ValueError("step must be > 0")
            if not isinstance(self.default, int | float) or isinstance(self.default, bool):
                raise ValueError("numeric parameter default must be a number")
            if not self.min_value <= float(self.default) <= self.max_value:
                raise ValueError(
                    f"default {self.default} is outside [{self.min_value}, {self.max_value}]"
                )
            if self.kind == "int" and float(self.default) != int(self.default):
                raise ValueError("int parameter default must be integral")
        elif self.kind == "choice":
            if not self.choices:
                raise ValueError("choice parameter needs a non-empty choices list")
            if self.default not in self.choices:
                raise ValueError(f"default {self.default!r} is not in choices")
        elif self.kind == "bool":
            if not isinstance(self.default, bool):
                raise ValueError("bool parameter default must be a bool")
        return self

    def assert_in_domain(self, name: str, value: Any) -> Any:
        """Check and normalise one value against this declared domain.

        Bounds, not grid membership: a ``step`` describes how a SWEEP enumerates
        the domain, while the domain itself is the closed interval the author
        declared. Refusing an off-grid value would make a document's own
        ``min_value``/``max_value`` a lie.
        """
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise OutOfDomainError(f"{name}: {value!r} is not a bool")
            return value
        if self.kind == "choice":
            if value not in (self.choices or ()):
                raise OutOfDomainError(
                    f"{name}: {value!r} is not one of {list(self.choices or ())}"
                )
            return value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise OutOfDomainError(f"{name}: {value!r} is not a number")
        numeric = float(value)
        lo, hi = float(self.min_value), float(self.max_value)  # type: ignore[arg-type]
        if not lo <= numeric <= hi:
            raise OutOfDomainError(
                f"{name}: {value!r} is outside the declared domain [{lo:g}, {hi:g}]. "
                "Widen the domain in the document if the strategy really admits "
                "this value -- do not sweep outside what the author claimed."
            )
        if self.kind == "int":
            if numeric != int(numeric):
                raise OutOfDomainError(f"{name}: {value!r} is not integral")
            return int(numeric)
        return numeric

    def domain(self) -> tuple[Any, ...]:
        """Enumerate the sweep domain. Floats without a step are not enumerable."""
        if self.kind == "bool":
            return (False, True)
        if self.kind == "choice":
            return tuple(self.choices or ())
        step = self.step or (1.0 if self.kind == "int" else None)
        if step is None:
            raise ValueError(
                "float parameter without a step has a continuous domain; "
                "sample it rather than enumerating"
            )
        values: list[Any] = []
        v = float(self.min_value or 0.0)
        while v <= float(self.max_value or 0.0) + 1e-12:
            values.append(int(round(v)) if self.kind == "int" else round(v, 10))
            v += step
        return tuple(values)


# ------------------------------------------------------------ exit models


class StopModel(BaseModel):
    """Mandatory. There is no representation of a strategy without a stop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "atr_multiple", "fixed_pips", "percent", "swing_structure", "indicator_level"
    ]
    value: Annotated[float, Field(gt=0.0)] | ParamRef
    #: ATR spec used by atr_multiple, by swing_structure's buffer and by
    #: indicator_level's buffer.
    atr: IndicatorOperand | None = None
    #: Level source for indicator_level; swing source for swing_structure.
    level: IndicatorOperand | None = None
    buffer_atr: Annotated[float, Field(ge=0.0)] | ParamRef = 0.0
    #: Hard floor so a structure stop cannot collapse to zero distance.
    min_distance_atr: Annotated[float, Field(ge=0.0)] | ParamRef = 0.1

    @model_validator(mode="after")
    def _sources_present(self) -> StopModel:
        if self.kind == "atr_multiple" and self.atr is None:
            raise ValueError("atr_multiple stop needs an 'atr' operand")
        if self.kind == "indicator_level" and self.level is None:
            raise ValueError("indicator_level stop needs a 'level' operand")
        if self.kind == "swing_structure" and self.level is None:
            raise ValueError(
                "swing_structure stop needs a 'level' operand pointing at a "
                "SwingDetector output (last_swing_low / last_swing_high)"
            )
        # A reference is treated as POSSIBLY positive, so the atr operand is
        # required. Refusing later, after binding, would let a document validate
        # today and fail mid-sweep tomorrow.
        buffer_atr = literal_number(self.buffer_atr)
        if (buffer_atr is None or buffer_atr > 0) and self.atr is None:
            raise ValueError("buffer_atr > 0 needs an 'atr' operand")
        min_distance_atr = literal_number(self.min_distance_atr)
        if (
            (min_distance_atr is None or min_distance_atr > 0)
            and self.atr is None
            and self.kind != "atr_multiple"
        ):
            raise ValueError("min_distance_atr needs an 'atr' operand")
        return self


class TakeProfitLeg(BaseModel):
    """One scale-out leg. ``allocation`` is the fraction of the position closed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["r_multiple", "atr_multiple", "fixed_pips", "percent", "indicator_level"]
    value: Annotated[float, Field(gt=0.0)] | ParamRef
    allocation: float = Field(gt=0.0, le=1.0)
    atr: IndicatorOperand | None = None
    level: IndicatorOperand | None = None
    label: str = ""

    @model_validator(mode="after")
    def _sources_present(self) -> TakeProfitLeg:
        if self.kind == "atr_multiple" and self.atr is None:
            raise ValueError("atr_multiple take-profit needs an 'atr' operand")
        if self.kind == "indicator_level" and self.level is None:
            raise ValueError("indicator_level take-profit needs a 'level' operand")
        return self


class TrailingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["none", "atr_chandelier", "indicator_line", "breakeven_after_r", "percent"]
    value: Annotated[float, Field(ge=0.0)] | ParamRef = 0.0
    activate_after_r: Annotated[float, Field(ge=0.0)] | ParamRef = 0.0
    atr: IndicatorOperand | None = None
    level: IndicatorOperand | None = None

    @model_validator(mode="after")
    def _sources_present(self) -> TrailingModel:
        if self.kind == "atr_chandelier":
            if self.atr is None:
                raise ValueError("atr_chandelier trailing needs an 'atr' operand")
            value = literal_number(self.value)
            if value is not None and value <= 0:
                raise ValueError("atr_chandelier trailing needs value > 0")
        if self.kind == "indicator_line" and self.level is None:
            raise ValueError("indicator_line trailing needs a 'level' operand")
        return self


class PositionManagement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_concurrent_positions: int = Field(default=1, ge=1, le=20)
    allow_pyramiding: bool = False
    max_pyramid_legs: int = Field(default=1, ge=1, le=10)
    move_stop_to_breakeven_at_r: Annotated[float, Field(ge=0.0)] | ParamRef | None = None
    allow_reversal_on_opposite_signal: bool = False
    max_bars_in_trade: Annotated[int, Field(ge=1)] | ParamRef | None = None
    cooldown_bars_after_exit: Annotated[int, Field(ge=0)] | ParamRef = 0

    @model_validator(mode="after")
    def _pyramiding_consistent(self) -> PositionManagement:
        if not self.allow_pyramiding and self.max_pyramid_legs != 1:
            raise ValueError("max_pyramid_legs must be 1 when pyramiding is disabled")
        return self


class SessionRestriction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    windows: tuple[tuple[int, int], ...] = ()
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    #: Refuse new entries within this many bars of the weekly close.
    block_bars_before_weekend: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _valid(self) -> SessionRestriction:
        for start, end in self.windows:
            if not (0 <= start <= 23 and 0 <= end <= 23):
                raise ValueError("session window hours must be 0..23 UTC")
        if any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("weekdays must be 0..6 (Monday=0)")
        return self


class EventRestriction(BaseModel):
    """Event blackouts. Enforced by the engine; declared here so it is auditable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    block_minutes_before: int = Field(default=0, ge=0)
    block_minutes_after: int = Field(default=0, ge=0)
    blocked_event_tags: tuple[str, ...] = ()
    avoid_month_end: bool = False
    avoid_rollover_hour: bool = True


class MutationRecord(BaseModel):
    """How this document was derived from its parent, for lineage auditing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parent_hash: str = ""
    operator: str = ""
    description: str = ""
    generation: int = Field(default=0, ge=0)


class RuleSet(BaseModel):
    """Direction-tagged rules. ``both`` strategies declare each side explicitly."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    long: tuple[Rule, ...] = ()
    short: tuple[Rule, ...] = ()

    def for_direction(self, direction: Literal["long", "short"]) -> tuple[Rule, ...]:
        return self.long if direction == "long" else self.short

    def all_rules(self) -> tuple[Rule, ...]:
        return (*self.long, *self.short)


# --------------------------------------------------------------- document


class StrategyDocument(BaseModel):
    """One strategy, fully described."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    strategy_id: str
    name: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=MIN_HYPOTHESIS_CHARS)
    family: StrategyFamily
    universe: tuple[str, ...]
    timeframes: tuple[Timeframe, ...]
    direction: TradeDirection

    regime: tuple[Rule, ...] = ()
    setup: RuleSet = Field(default_factory=RuleSet)
    entry: RuleSet
    confirmation: RuleSet = Field(default_factory=RuleSet)
    filters: tuple[Rule, ...] = ()
    invalidation: RuleSet = Field(default_factory=RuleSet)

    stop: StopModel
    take_profits: tuple[TakeProfitLeg, ...] = ()
    trailing: TrailingModel | None = None
    position_management: PositionManagement = Field(default_factory=PositionManagement)
    sessions: SessionRestriction | None = None
    events: EventRestriction = Field(default_factory=EventRestriction)

    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    #: The parameter values this document was BOUND with, or ``None`` for an
    #: unbound template. Semantic, and therefore part of the content hash: two
    #: bindings of one template are two strategies, and the experiment ledger and
    #: the holdout registry must treat them as such. Recording it here rather
    #: than inferring it from the substituted literals also means a parameter
    #: that happens not to be referenced still distinguishes its bindings, so a
    #: report can never claim a sweep explored something it did not.
    binding: dict[str, Any] | None = None
    parent_strategy_ids: tuple[str, ...] = ()
    mutation: MutationRecord | None = None
    notes: str = ""
    author: str = ""

    # ------------------------------------------------------- validation

    @model_validator(mode="before")
    @classmethod
    def _drop_computed(cls, data: Any) -> Any:
        """``complexity_score`` is emitted on dump but is always RECOMPUTED on load.

        Keeping it in the serialised form makes a directory of documents
        greppable; refusing it as an input makes it impossible for a hand-edited
        file to claim a complexity it does not have. Loaders that want to catch
        such an edit compare the raw value -- see
        ``StrategyRegistry.load_directory``.
        """
        if isinstance(data, dict) and "complexity_score" in data:
            data = {k: v for k, v in data.items() if k != "complexity_score"}
        return data

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version {v!r} != {SCHEMA_VERSION!r}; migrate the document "
                "explicitly rather than loading it under the wrong schema"
            )
        return v

    @field_validator("strategy_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(
                f"strategy_id {v!r} must be a lowercase slug: ^[a-z][a-z0-9_]{{2,63}}$"
            )
        return v

    @field_validator("universe")
    @classmethod
    def _known_instruments(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("universe must list at least one instrument")
        unknown = [s for s in v if not instruments.exists(s)]
        if unknown:
            raise ValueError(
                f"universe contains unregistered instruments {unknown}; register them "
                "in core/instruments.py rather than guessing contract specs"
            )
        return tuple(dict.fromkeys(s.upper() for s in v))

    @field_validator("timeframes")
    @classmethod
    def _timeframes(cls, v: tuple[Timeframe, ...]) -> tuple[Timeframe, ...]:
        if not v:
            raise ValueError("at least one permitted timeframe is required")
        return tuple(dict.fromkeys(v))

    @model_validator(mode="after")
    def _binding_is_coherent(self) -> StrategyDocument:
        if self.binding is None:
            return self
        unknown = sorted(set(self.binding) - set(self.parameters))
        if unknown:
            raise ValueError(
                f"binding names parameters {unknown} that this document does not "
                "declare; a binding that refers to nothing is a typo, not a value"
            )
        for name, value in self.binding.items():
            self.parameters[name].assert_in_domain(name, value)
        return self

    @model_validator(mode="after")
    def _coherent(self) -> StrategyDocument:
        want_long = self.direction in (TradeDirection.LONG, TradeDirection.BOTH)
        want_short = self.direction in (TradeDirection.SHORT, TradeDirection.BOTH)
        if want_long and not self.entry.long:
            raise ValueError(f"direction={self.direction.value} but entry.long is empty")
        if want_short and not self.entry.short:
            raise ValueError(f"direction={self.direction.value} but entry.short is empty")
        if not want_long and self.entry.long:
            raise ValueError("entry.long is populated but direction excludes long")
        if not want_short and self.entry.short:
            raise ValueError("entry.short is populated but direction excludes short")

        total = sum(leg.allocation for leg in self.take_profits)
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"take-profit allocations sum to {total:.4f} > 1.0; a strategy cannot "
                "close more than the whole position"
            )
        return self

    # ------------------------------------------------------- parameter binding

    def unbound_parameters(self) -> tuple[str, ...]:
        """Every parameter this document still REFERENCES but has not been given.

        Empty means the document is fully bound and may be compiled. Non-empty
        means it is a template: :func:`fiboki.strategy.compiler.compile_strategy`
        refuses it, so there is no path by which a half-bound strategy reaches
        the engine.
        """
        found: set[str] = set()
        _collect_refs(self.model_dump(mode="json"), found)
        return tuple(sorted(found))

    def default_values(self) -> dict[str, Any]:
        """The value each declared parameter takes when nothing overrides it."""
        return {name: self.parameters[name].default for name in sorted(self.parameters)}

    def bind(self, values: Mapping[str, Any]) -> StrategyDocument:
        """Return a NEW document with every reference resolved to a concrete value.

        Three refusals, all of them at bind time rather than at run time:

        * a value for a parameter the document does not declare -- a typo in a
          sweep definition would otherwise be swept silently and change nothing;
        * a value outside the domain the document declared -- the domain is the
          author's statement of what this strategy IS, and a sweep that leaves it
          is testing a different strategy;
        * a reference with no value supplied -- never defaulted, because a sweep
          that silently ran the default while reporting a swept value is the
          exact failure this whole mechanism exists to prevent.

        Parameters that are declared but never referenced may be omitted; they
        are recorded at their declared default so the binding is complete, and
        they still change the content hash, because the ledger has to be able to
        tell the two runs apart even when the engine cannot.
        """
        unknown = sorted(set(values) - set(self.parameters))
        if unknown:
            raise UnknownParameterError(
                f"{self.strategy_id}: cannot bind {unknown}; this document declares "
                f"{sorted(self.parameters)}"
            )
        referenced = set(self.unbound_parameters())
        undeclared = sorted(referenced - set(self.parameters))
        if undeclared:
            raise UnboundParameterError(
                f"{self.strategy_id}: references {undeclared}, which the document "
                "does not declare as parameters. The reference is a bug in the "
                "document, not something a caller can supply."
            )
        missing = sorted(referenced - set(values))
        if missing:
            raise UnboundParameterError(
                f"{self.strategy_id}: no value supplied for referenced parameter(s) "
                f"{missing}. They are NOT defaulted: a run that quietly used the "
                "default while the report said otherwise is the failure this "
                "mechanism exists to prevent. Pass document.default_values() | "
                "overrides if the defaults are what you meant."
            )

        effective: dict[str, Any] = {}
        for name in sorted(self.parameters):
            spec = self.parameters[name]
            raw = values.get(name, spec.default)
            effective[name] = spec.assert_in_domain(name, raw)

        payload = self.model_dump(mode="json")
        payload.pop("complexity_score", None)
        # ``parameters`` keeps its DOMAINS: the bound document must still be able
        # to say what could have been swept. Only the references are replaced.
        specs = payload.pop("parameters")
        resolved = resolve_refs(payload, effective)
        resolved["parameters"] = specs
        resolved["binding"] = effective
        try:
            return type(self).model_validate(resolved)
        except ValueError as exc:
            # The document is valid and every value is inside its declared
            # domain, and the result is STILL not a strategy. That means the
            # domains are jointly incoherent -- classically a fast/slow pair
            # whose ranges overlap, so some corner of the declared grid asks for
            # fast >= slow. A campaign must be able to catch this per cell and
            # record the cell as infeasible instead of dying; a bare pydantic
            # error buried under a sweep is how a 2,000-cell run dies at 3am.
            raise InfeasibleBindingError(
                f"{self.strategy_id}: the binding "
                + ", ".join(f"{k}={effective[k]!r}" for k in sorted(effective))
                + " lies inside every declared domain but does not produce a valid "
                f"document: {exc}"
            ) from exc

    def bind_defaults(self) -> StrategyDocument:
        """Bind every declared parameter to the value the document itself declares."""
        return self.bind(self.default_values())

    def is_bound(self) -> bool:
        return not self.unbound_parameters()

    # ------------------------------------------------------------ derived

    def all_rules(self) -> tuple[Rule, ...]:
        return (
            *self.regime,
            *self.setup.all_rules(),
            *self.entry.all_rules(),
            *self.confirmation.all_rules(),
            *self.filters,
            *self.invalidation.all_rules(),
        )

    def rule_indicators(self) -> tuple[Any, ...]:
        return collect_indicators(self.all_rules())

    def exit_operands(self) -> tuple[IndicatorOperand, ...]:
        found: list[IndicatorOperand] = []
        for model in (self.stop, self.trailing, *self.take_profits):
            if model is None:
                continue
            for attr in ("atr", "level"):
                op = getattr(model, attr, None)
                if op is not None:
                    found.append(op)
        return tuple(found)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complexity_score(self) -> float:
        """Derived, never supplied: a stored score can disagree with the rules.

        1.0 per rule (nested rules counted), 0.5 per tunable parameter, 0.5 per
        take-profit leg beyond the first, 1.0 for a trailing model, 1.0 for
        pyramiding. Higher means more ways to overfit.
        """
        score = float(count_rules(self.all_rules()))
        score += 0.5 * len(self.parameters)
        score += 0.5 * max(0, len(self.take_profits) - 1)
        if self.trailing is not None and self.trailing.kind != "none":
            score += 1.0
        if self.position_management.allow_pyramiding:
            score += 1.0
        return round(score, 3)

    # -------------------------------------------------------------- hash

    #: Fields that name or attribute a strategy but do not change its behaviour.
    NON_SEMANTIC: ClassVar[tuple[str, ...]] = (
        "strategy_id",
        "name",
        "hypothesis",
        "notes",
        "author",
        "parent_strategy_ids",
        "mutation",
        "complexity_score",
    )

    def semantic_payload(self) -> dict[str, Any]:
        """Canonical, order-stable view of everything that changes behaviour.

        Rule lists are sorted by their own canonical JSON, so two documents that
        AND the same conditions in a different order hash identically -- which
        is what "duplicate" means to a search process.
        """
        data = self.model_dump(mode="json")
        for field in self.NON_SEMANTIC:
            data.pop(field, None)
        return _canonicalise(data)

    def content_hash(self) -> str:
        blob = json.dumps(
            self.semantic_payload(), sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def short_hash(self) -> str:
        return self.content_hash()[:12]

    # --------------------------------------------------------- (de)serial

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=indent, sort_keys=False)

    @classmethod
    def from_json(cls, blob: str) -> StrategyDocument:
        return cls.model_validate(json.loads(blob))


def _collect_refs(node: Any, out: set[str]) -> None:
    """Names referenced anywhere in a DUMPED document.

    Walks the dumped form rather than the model tree so that a reference in any
    position -- a rule threshold, a regime bound, a nested indicator parameter,
    an exit multiple -- is found by one function that cannot fall behind the
    schema when a field is added.
    """
    if isinstance(node, dict):
        if is_ref(node):
            out.add(str(node[PARAM_REF_KEY]))
            return
        for value in node.values():
            _collect_refs(value, out)
        return
    if isinstance(node, list):
        for value in node:
            _collect_refs(value, out)


_RULE_LIST_FIELDS = frozenset({"regime", "filters", "long", "short", "rules"})


def _canonicalise(node: Any, key: str | None = None) -> Any:
    """Recursively canonicalise a dumped document.

    ``complexity_score`` is a computed field, so it reappears in nested dumps;
    it is stripped wherever it occurs. Rule lists are sorted by canonical JSON.
    """
    if isinstance(node, dict):
        out = {
            k: _canonicalise(v, k)
            for k, v in node.items()
            if k != "complexity_score"
        }
        return out
    if isinstance(node, list):
        items = [_canonicalise(v) for v in node]
        if key in _RULE_LIST_FIELDS and all(isinstance(i, dict) for i in items):
            items.sort(key=lambda d: json.dumps(d, sort_keys=True, separators=(",", ":")))
        return items
    return node


__all__ = [
    "MIN_HYPOTHESIS_CHARS",
    "SCHEMA_VERSION",
    "BindingError",
    "EventRestriction",
    "InfeasibleBindingError",
    "MutationRecord",
    "OutOfDomainError",
    "ParamRef",
    "ParameterSpec",
    "PositionManagement",
    "RuleSet",
    "SessionRestriction",
    "StopModel",
    "StrategyDocument",
    "StrategyFamily",
    "TakeProfitLeg",
    "TradeDirection",
    "TrailingModel",
    "UnboundParameterError",
    "UnknownParameterError",
    "strategy_key_version",
]
