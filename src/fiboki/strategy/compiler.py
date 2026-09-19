"""Compile a Strategy DSL document into something executable.

The compiled object exposes exactly two things the engines need:

``warmup_period``
    Derived as ``max(indicator warmups) + max(rule lookback) + 1``. V1
    hardcoded 98 bars for every bot because it summed an attribute that did not
    exist on any indicator; here the number is a consequence of the document and
    cannot drift away from it.

``generate_signal(df, idx, instrument, timeframe)``
    Returns a :class:`~fiboki.core.contracts.Signal` or ``None``.

**The compiled strategy cannot read past ``idx``.** That is enforced three ways,
not asserted in a comment:

1. Operand offsets are ``ge=0`` in the schema, so "bar idx+1" is inexpressible.
2. ``generate_signal`` slices the frame to ``df.iloc[: idx + 1]`` and hands only
   that slice to the rule evaluator. There is no reference to the full frame
   anywhere inside evaluation.
3. ``tests/unit/test_compiler_causality.py`` corrupts every bar after ``idx``
   and demands an identical signal.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction, Timeframe
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.indicators.base import Indicator
from fiboki.strategy.dsl import (
    StopModel,
    StrategyDocument,
    TakeProfitLeg,
    TradeDirection,
)
from fiboki.strategy.primitives import (
    EvalContext,
    IndicatorOperand,
    IndicatorSpec,
    Rule,
    SpecError,
)

Side = Literal["long", "short"]


class CompilationError(ValueError):
    """The document is valid on its own but cannot be realised as code."""


@dataclass(frozen=True, slots=True)
class CompiledStrategy:
    """An executable view of one :class:`StrategyDocument`."""

    document: StrategyDocument
    indicator_specs: tuple[IndicatorSpec, ...]
    indicators: tuple[Indicator, ...]
    warmup_period: int
    content_hash: str

    # ------------------------------------------------------------- setup

    @property
    def strategy_id(self) -> str:
        return self.document.strategy_id

    @property
    def required_columns(self) -> tuple[str, ...]:
        cols: list[str] = []
        for ind in self.indicators:
            cols.extend(ind.output_columns)
        return tuple(cols)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute every required indicator once over the whole history.

        Safe because every indicator in the library is proved causal by
        ``tests/unit/test_indicator_causality.py``: the value on row i is the
        same whether it was computed over ``df`` or over ``df[:i+1]``.
        """
        out = df
        for ind in self.indicators:
            out = ind.compute(out)
        return out

    # ------------------------------------------------------------ signal

    def generate_signal(
        self,
        df: pd.DataFrame,
        idx: int,
        instrument: str | None = None,
        timeframe: Timeframe | str | None = None,
    ) -> Signal | None:
        if idx < 0 or idx >= len(df):
            raise IndexError(f"idx {idx} out of range for a frame of {len(df)} bars")
        doc = self.document

        symbol = (instrument or doc.universe[0]).upper()
        if symbol not in doc.universe:
            raise CompilationError(
                f"{doc.strategy_id}: {symbol} is not in the permitted universe "
                f"{list(doc.universe)}"
            )
        tf = Timeframe(timeframe) if timeframe is not None else doc.timeframes[0]
        if tf not in doc.timeframes:
            raise CompilationError(
                f"{doc.strategy_id}: {tf.value} is not a permitted timeframe "
                f"{[t.value for t in doc.timeframes]}"
            )

        if idx < self.warmup_period:
            return None

        # ---- THE TRUNCATION. Nothing below this line can see a later bar. ----
        view = df.iloc[: idx + 1]
        if not set(self.required_columns).issubset(view.columns):
            view = self.prepare(view)

        bar_time = view.index[-1]
        if not isinstance(bar_time, pd.Timestamp) or bar_time.tzinfo is None:
            raise CompilationError(
                "strategy frames must be indexed by tz-aware UTC timestamps"
            )
        ctx = EvalContext(frame=view, bar_time=bar_time)

        if not self._gates_pass(ctx):
            return None

        sides: list[Side] = []
        if doc.direction in (TradeDirection.LONG, TradeDirection.BOTH):
            sides.append("long")
        if doc.direction in (TradeDirection.SHORT, TradeDirection.BOTH):
            sides.append("short")

        triggered = [s for s in sides if self._side_triggers(ctx, s)]
        if len(triggered) != 1:
            # Zero: no setup. Two: the document contradicts itself on this bar,
            # and guessing a direction is exactly how V1 produced trades nobody
            # could explain. Emit nothing.
            return None
        side = triggered[0]

        inst = get_instrument(symbol)
        reference = float(view["close"].iat[-1])
        if not math.isfinite(reference) or reference <= 0:
            return None

        stop = self._stop_price(ctx, side, reference, inst)
        if stop is None:
            return None

        direction = Direction.LONG if side == "long" else Direction.SHORT
        targets = self._take_profits(ctx, side, reference, stop, inst)

        try:
            return Signal(
                strategy_id=doc.strategy_id,
                instrument=symbol,
                timeframe=tf.value,
                direction=direction,
                bar_time=bar_time,
                reference_price=reference,
                stop_price=stop,
                take_profit_prices=targets,
                confidence=1.0,
                rationale=f"{doc.strategy_id}:{side}",
                features=self._features(ctx),
            )
        except ValueError:
            # Signal refuses a stop or target on the wrong side. That is a
            # degenerate bar, not a crash: skip it rather than sanitising.
            return None

    # -------------------------------------------------------------- gates

    def _gates_pass(self, ctx: EvalContext) -> bool:
        doc = self.document
        if not _all(doc.regime, ctx):
            return False
        if not _all(doc.filters, ctx):
            return False
        return self._session_allows(ctx)

    def _session_allows(self, ctx: EvalContext) -> bool:
        sessions = self.document.sessions
        if sessions is None:
            return True
        ts = ctx.bar_time.tz_convert("UTC")
        if ts.weekday() not in sessions.weekdays:
            return False
        if not sessions.windows:
            return True
        hour = ts.hour
        for start, end in sessions.windows:
            if start <= end:
                if start <= hour <= end:
                    return True
            elif hour >= start or hour <= end:
                return True
        return False

    def _side_triggers(self, ctx: EvalContext, side: Side) -> bool:
        doc = self.document
        if not _all(doc.setup.for_direction(side), ctx):
            return False
        entry = doc.entry.for_direction(side)
        if not entry or not _all(entry, ctx):
            return False
        if not _all(doc.confirmation.for_direction(side), ctx):
            return False
        return not _any(doc.invalidation.for_direction(side), ctx)

    # --------------------------------------------------------------- exits

    def _atr(self, ctx: EvalContext, operand: IndicatorOperand | None) -> float:
        if operand is None:
            return math.nan
        return ctx.read(operand)

    def _stop_price(
        self, ctx: EvalContext, side: Side, reference: float, inst: Instrument
    ) -> float | None:
        model: StopModel = self.document.stop
        sign = 1.0 if side == "long" else -1.0
        atr = self._atr(ctx, model.atr)

        if model.kind == "atr_multiple":
            if not math.isfinite(atr) or atr <= 0:
                return None
            distance = model.value * atr
        elif model.kind == "fixed_pips":
            distance = model.value * inst.pip_size
        elif model.kind == "percent":
            distance = reference * model.value / 100.0
        elif model.kind in ("swing_structure", "indicator_level"):
            level = ctx.read(model.level) if model.level is not None else math.nan
            if not math.isfinite(level):
                return None
            buffer = model.buffer_atr * atr if model.buffer_atr else 0.0
            if model.buffer_atr and not math.isfinite(buffer):
                return None
            distance = (reference - level) * sign + buffer
        else:  # pragma: no cover - Literal exhausted
            raise CompilationError(f"unhandled stop kind {model.kind!r}")

        if math.isfinite(atr) and model.min_distance_atr > 0:
            distance = max(distance, model.min_distance_atr * atr)
        if not math.isfinite(distance) or distance <= 0:
            return None

        stop = reference - sign * distance
        return stop if stop > 0 else None

    def _take_profits(
        self,
        ctx: EvalContext,
        side: Side,
        reference: float,
        stop: float,
        inst: Instrument,
    ) -> tuple[float, ...]:
        sign = 1.0 if side == "long" else -1.0
        risk = abs(reference - stop)
        prices: list[float] = []
        for leg in self.document.take_profits:
            price = self._leg_price(ctx, leg, sign, reference, risk, inst)
            if price is None:
                continue
            # Anything on the wrong side of entry is not a target; drop the leg
            # rather than let Signal reject the whole opinion.
            if (price - reference) * sign <= 0:
                continue
            prices.append(price)
        # Nearest first, de-duplicated: the engine scales out in this order.
        prices.sort(key=lambda p: (p - reference) * sign)
        deduped: list[float] = []
        for p in prices:
            if not deduped or abs(p - deduped[-1]) > 1e-12:
                deduped.append(p)
        return tuple(deduped)

    def _leg_price(
        self,
        ctx: EvalContext,
        leg: TakeProfitLeg,
        sign: float,
        reference: float,
        risk: float,
        inst: Instrument,
    ) -> float | None:
        if leg.kind == "r_multiple":
            return reference + sign * leg.value * risk
        if leg.kind == "atr_multiple":
            atr = self._atr(ctx, leg.atr)
            if not math.isfinite(atr) or atr <= 0:
                return None
            return reference + sign * leg.value * atr
        if leg.kind == "fixed_pips":
            return reference + sign * leg.value * inst.pip_size
        if leg.kind == "percent":
            return reference * (1.0 + sign * leg.value / 100.0)
        if leg.kind == "indicator_level":
            level = ctx.read(leg.level) if leg.level is not None else math.nan
            return level if math.isfinite(level) else None
        raise CompilationError(f"unhandled take-profit kind {leg.kind!r}")  # pragma: no cover

    def _features(self, ctx: EvalContext) -> dict[str, float]:
        """A small, deterministic snapshot for post-hoc analysis."""
        out: dict[str, float] = {}
        for ind in self.indicators:
            col = ind.output_columns[0]
            if col in ctx.frame.columns:
                value = ctx.frame[col].iat[-1]
                if value is not None and math.isfinite(float(value)):
                    out[col] = float(value)
        return out


def _all(rules: tuple[Rule, ...], ctx: EvalContext) -> bool:
    return all(r.evaluate(ctx) for r in rules)


def _any(rules: tuple[Rule, ...], ctx: EvalContext) -> bool:
    return any(r.evaluate(ctx) for r in rules)


# ------------------------------------------------------------------ compile


def compile_strategy(doc: StrategyDocument) -> CompiledStrategy:
    """Turn a document into a :class:`CompiledStrategy`. Deterministic."""
    specs: dict[str, IndicatorSpec] = {}
    for spec in doc.rule_indicators():
        specs.setdefault(spec.key, spec)
    for operand in doc.exit_operands():
        specs.setdefault(operand.spec.key, operand.spec)

    ordered = tuple(specs[k] for k in sorted(specs))
    indicators = tuple(spec.build() for spec in ordered)

    # Two different specs writing the same column would make the later one win
    # silently -- e.g. two SwingDetectors with different lookbacks.
    owner: dict[str, str] = {}
    for spec, ind in zip(ordered, indicators, strict=True):
        for col in ind.output_columns:
            if col in owner and owner[col] != spec.key:
                raise CompilationError(
                    f"{doc.strategy_id}: {spec.key} and {owner[col]} both write column "
                    f"{col!r}. Use one parameterisation per strategy."
                )
            owner[col] = spec.key

    # Every operand must resolve against the indicators we just built.
    for operand in (*_rule_operands(doc), *doc.exit_operands()):
        try:
            column = operand.column
        except SpecError as exc:  # pragma: no cover - schema validates first
            raise CompilationError(str(exc)) from exc
        if column not in owner:
            raise CompilationError(
                f"{doc.strategy_id}: operand needs column {column!r} which no "
                "required indicator produces"
            )

    indicator_warmup = max((i.warmup_period for i in indicators), default=1)
    rule_lookback = max((r.max_lookback() for r in doc.all_rules()), default=0)
    warmup = int(indicator_warmup + rule_lookback + 1)

    return CompiledStrategy(
        document=doc,
        indicator_specs=ordered,
        indicators=indicators,
        warmup_period=warmup,
        content_hash=doc.content_hash(),
    )


def _rule_operands(doc: StrategyDocument) -> tuple[IndicatorOperand, ...]:
    found: list[IndicatorOperand] = []

    def walk(rule: Rule) -> None:
        for attr in ("operand", "left", "right", "indicator", "metric", "fast", "slow"):
            op = getattr(rule, attr, None)
            if isinstance(op, IndicatorOperand):
                found.append(op)
        for nested in getattr(rule, "rules", ()) or ():
            walk(nested)
        inner = getattr(rule, "rule", None)
        if inner is not None:
            walk(inner)

    for rule in doc.all_rules():
        walk(rule)
    return tuple(found)
