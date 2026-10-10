"""A fast, signal-for-signal identical path for replaying one compiled document.

Why this exists
---------------
``CompiledStrategy.generate_signal(df, idx)`` slices ``df.iloc[: idx + 1]`` and
reads every operand with ``frame[column].iat[i]``. That is the right REFERENCE
(it makes causality structural), and it is what the 2026-09-28 audit measured
at about 274 us per bar, most of a backtest's time. A 100,000-document campaign
cannot afford it.

:class:`SignalKernel` replays the same decision two ways faster, without
changing a single decision:

1. **Arrays, not frames.** The prepared frame's columns are converted to NumPy
   arrays once; :class:`~fiboki.strategy.primitives.ArrayEvalContext` reads
   ``array[pos - offset]`` where the frame context read ``frame[col].iat[...]``.
   Same element, same ``float()``.
2. **A vectorised pre-filter.** Every rule the document's gates, setup, entry,
   confirmation and invalidation are built from is evaluated over ALL bars at
   once into a boolean mask. A bar whose mask is ``False`` cannot produce a
   signal, so the scalar decision is skipped there; a bar whose mask is
   ``True`` runs the full scalar decision
   (:meth:`~fiboki.strategy.compiler.CompiledStrategy.decide`), which alone
   decides. The mask is only ever allowed to be a SUPERSET of the bars that
   signal: a rule it cannot vectorise exactly contributes "every bar", and an
   inexact mask is never negated. So the worst a kernel bug in a mask can do
   is skip a bar that should have signalled, which is exactly what
   ``tests/unit/test_signal_kernel_parity.py`` looks for, signal by signal,
   against ``generate_signal`` on seeded documents and frames.

What the kernel does NOT change
-------------------------------
Indicator values (computed by ``CompiledStrategy.prepare`` in ``indicators/``,
as before), stop and target arithmetic, the exactly-one-side rule, warmup,
the universe and timeframe refusals: all run through the one shared
``decide`` path. Because the results are identical, nothing in the engine
fingerprint or the evaluation cache key changes and no stored result is
invalidated.

Causality
---------
Each vectorised rule reads only ``array[i - offset]`` and, for a crossover,
``array[i - 1 - offset]`` with ``offset >= 0``: element-wise over causal
indicator columns, so ``mask[i]`` depends on rows ``<= i`` only. The arrays are
shifted with NaN fill, never wrapped (``np.roll`` is not used).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.contracts import Signal
from fiboki.core.enums import Timeframe
from fiboki.strategy.dsl import TradeDirection
from fiboki.strategy.primitives import (
    AllOfRule,
    AnyOfRule,
    ArrayEvalContext,
    CrossoverRule,
    IndicatorVsIndicatorRule,
    IndicatorVsPriceRule,
    NotRule,
    RegimeGateRule,
    SessionWindowRule,
    ThresholdRule,
    bound_number,
)

__all__ = ["SignalKernel", "kernel_or_none", "rule_mask"]

_CMP = {
    ">": np.greater,
    ">=": np.greater_equal,
    "<": np.less,
    "<=": np.less_equal,
    "==": np.equal,
    "!=": np.not_equal,
}


class _Inexact(Exception):
    """This rule cannot be vectorised exactly; it contributes 'every bar'."""


class _Frame:
    """Column arrays plus the calendar fields a session rule reads."""

    def __init__(self, columns: Mapping[str, np.ndarray], index: pd.DatetimeIndex) -> None:
        self.columns = columns
        self.n = len(index)
        utc = index.tz_convert("UTC")
        self.weekday = np.asarray(utc.weekday, dtype=np.int64)
        self.hour = np.asarray(utc.hour, dtype=np.int64)
        self._float: dict[str, np.ndarray] = {}

    def floats(self, column: str) -> np.ndarray:
        cached = self._float.get(column)
        if cached is not None:
            return cached
        raw = self.columns.get(column)
        if raw is None:
            raise _Inexact(f"missing column {column}")
        try:
            # ``float(value)`` per element, as ``EvalContext.read`` does; an
            # object column holding None becomes NaN there and here.
            if raw.dtype == object:
                values = np.array(
                    [math.nan if v is None else float(v) for v in raw], dtype=np.float64
                )
            else:
                values = raw.astype(np.float64, copy=False)
        except (TypeError, ValueError) as exc:
            raise _Inexact(str(exc)) from exc
        self._float[column] = values
        return values


def _shift(values: np.ndarray, k: int) -> np.ndarray:
    """``out[i] = values[i - k]`` for ``i >= k``, NaN before. Never wraps."""
    if k == 0:
        return values
    out = np.full(values.shape[0], np.nan, dtype=np.float64)
    if k < values.shape[0]:
        out[k:] = values[: values.shape[0] - k]
    return out


def _operand(operand: Any, frame: _Frame, extra_lag: int = 0) -> np.ndarray:
    kind = operand.kind
    if kind == "constant":
        return np.full(frame.n, float(bound_number(operand.value, "ConstantOperand.value")))
    column = operand.field if kind == "price" else operand.column
    return _shift(frame.floats(column), int(operand.offset) + extra_lag)


def _finite(*arrays: np.ndarray) -> np.ndarray:
    out = np.isfinite(arrays[0])
    for a in arrays[1:]:
        out &= np.isfinite(a)
    return out


def _exact_mask(rule: Any, frame: _Frame) -> np.ndarray:
    """The bool per bar ``rule.evaluate`` would return, or raise :class:`_Inexact`."""
    if isinstance(rule, ThresholdRule):
        a = _operand(rule.operand, frame)
        threshold = bound_number(rule.value, "ThresholdRule.value")
        return _finite(a) & _CMP[rule.comparator](a, threshold)
    if isinstance(rule, IndicatorVsIndicatorRule):
        a, b = _operand(rule.left, frame), _operand(rule.right, frame)
        return _finite(a, b) & _CMP[rule.comparator](a, b)
    if isinstance(rule, IndicatorVsPriceRule):
        a, b = _operand(rule.indicator, frame), _operand(rule.price, frame)
        return _finite(a, b) & _CMP[rule.comparator](a, b)
    if isinstance(rule, CrossoverRule):
        now_f, now_s = _operand(rule.fast, frame), _operand(rule.slow, frame)
        prev_f, prev_s = _operand(rule.fast, frame, 1), _operand(rule.slow, frame, 1)
        ok = _finite(now_f, now_s, prev_f, prev_s)
        if frame.n:
            ok[0] = False  # no previous bar: ``previous()`` is None
        if rule.direction == "above":
            return ok & (prev_f <= prev_s) & (now_f > now_s)
        return ok & (prev_f >= prev_s) & (now_f < now_s)
    if isinstance(rule, RegimeGateRule):
        v = _operand(rule.metric, frame)
        inside = np.ones(frame.n, dtype=bool)
        if rule.min_value is not None:
            inside &= v >= bound_number(rule.min_value, "regime_gate.min_value")
        if rule.max_value is not None:
            inside &= v <= bound_number(rule.max_value, "regime_gate.max_value")
        if rule.invert:
            inside = ~inside
        return _finite(v) & inside
    if isinstance(rule, SessionWindowRule):
        day_ok = np.isin(frame.weekday, np.asarray(rule.weekdays, dtype=np.int64))
        h, lo, hi = frame.hour, rule.start_hour_utc, rule.end_hour_utc
        hour_ok = (h >= lo) & (h <= hi) if lo <= hi else (h >= lo) | (h <= hi)
        return day_ok & hour_ok
    if isinstance(rule, AllOfRule):
        out = np.ones(frame.n, dtype=bool)
        for child in rule.rules:
            out &= _exact_mask(child, frame)
        return out
    if isinstance(rule, AnyOfRule):
        out = np.zeros(frame.n, dtype=bool)
        for child in rule.rules:
            out |= _exact_mask(child, frame)
        return out
    if isinstance(rule, NotRule):
        return ~_exact_mask(rule.rule, frame)
    raise _Inexact(f"no vectorised form for {type(rule).__name__}")


def rule_mask(rule: Any, frame: _Frame) -> np.ndarray | None:
    """Exact mask for one rule, or ``None`` when it has no exact vectorised form."""
    try:
        return _exact_mask(rule, frame)
    except (_Inexact, KeyError, TypeError, ValueError):
        return None


def _all_superset(rules: Any, frame: _Frame) -> tuple[np.ndarray, bool]:
    """AND of the rules' masks, an inexact rule counting as every bar."""
    out = np.ones(frame.n, dtype=bool)
    exact = True
    for rule in rules:
        mask = rule_mask(rule, frame)
        if mask is None:
            exact = False
            continue
        out &= mask
    return out, exact


def _session_mask(compiled: Any, frame: _Frame) -> np.ndarray:
    sessions = compiled.document.sessions
    if sessions is None:
        return np.ones(frame.n, dtype=bool)
    out = np.isin(frame.weekday, np.asarray(tuple(sessions.weekdays), dtype=np.int64))
    if not sessions.windows:
        return out
    h = frame.hour
    any_window = np.zeros(frame.n, dtype=bool)
    for start, end in sessions.windows:
        if start <= end:
            any_window |= (h >= start) & (h <= end)
        else:
            any_window |= (h >= start) | (h <= end)
    return out & any_window


def candidate_mask(compiled: Any, frame: _Frame) -> np.ndarray:
    """A SUPERSET of the bars on which ``decide`` can return a signal."""
    doc = compiled.document
    gates, _ = _all_superset((*doc.regime, *doc.filters), frame)
    gates &= _session_mask(compiled, frame)

    side_masks: list[tuple[np.ndarray, bool]] = []
    sides = []
    if doc.direction in (TradeDirection.LONG, TradeDirection.BOTH):
        sides.append("long")
    if doc.direction in (TradeDirection.SHORT, TradeDirection.BOTH):
        sides.append("short")
    for side in sides:
        entry = doc.entry.for_direction(side)
        if not entry:
            side_masks.append((np.zeros(frame.n, dtype=bool), True))
            continue
        setup, e1 = _all_superset(doc.setup.for_direction(side), frame)
        entry_m, e2 = _all_superset(entry, frame)
        confirm, e3 = _all_superset(doc.confirmation.for_direction(side), frame)
        mask = setup & entry_m & confirm
        exact = e1 and e2 and e3
        invalid_rules = doc.invalidation.for_direction(side)
        if invalid_rules:
            invalid_masks = [rule_mask(r, frame) for r in invalid_rules]
            if any(m is None for m in invalid_masks):
                exact = False  # cannot negate an inexact mask: leave it out
            else:
                any_invalid = np.zeros(frame.n, dtype=bool)
                for m in invalid_masks:
                    any_invalid |= m  # type: ignore[operator]
                mask &= ~any_invalid
        side_masks.append((mask, exact))

    if not side_masks:
        return np.zeros(frame.n, dtype=bool)
    if len(side_masks) == 1:
        return gates & side_masks[0][0]
    (long_m, long_exact), (short_m, short_exact) = side_masks
    # ``decide`` emits only when EXACTLY one side triggers. XOR is not
    # monotone, so it is used only when both side masks are exact; otherwise
    # OR, which is a superset.
    either = (long_m ^ short_m) if (long_exact and short_exact) else (long_m | short_m)
    return gates & either


class SignalKernel:
    """Replays ``compiled.generate_signal(prepared, idx, ...)`` bar by bar, faster.

    Construct with the SAME prepared frame the reference path would be given.
    :meth:`signal_at` returns exactly what ``generate_signal`` returns on that
    frame at that index: the same ``Signal`` (field for field) or ``None``, and
    the same exception for an out-of-range index or a refused target.
    """

    def __init__(self, compiled: Any, prepared: pd.DataFrame) -> None:
        index = prepared.index
        if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
            raise ValueError("SignalKernel needs a tz-aware UTC DatetimeIndex")
        if not set(compiled.required_columns).issubset(prepared.columns):
            raise ValueError("SignalKernel needs a prepared frame (indicators computed)")
        if "close" not in prepared.columns:
            raise ValueError("SignalKernel needs a close column")
        self.compiled = compiled
        self.index = index
        self.n = len(prepared)
        self.columns: dict[str, np.ndarray] = {
            str(c): prepared[c].to_numpy() for c in prepared.columns
        }
        self.candidates = candidate_mask(compiled, _Frame(self.columns, index))
        self.bars_decided = 0

    def signal_at(
        self,
        idx: int,
        instrument: str | None = None,
        timeframe: Timeframe | str | None = None,
    ) -> Signal | None:
        if idx < 0 or idx >= self.n:
            raise IndexError(f"idx {idx} out of range for a frame of {self.n} bars")
        compiled = self.compiled
        symbol, tf = compiled.target(instrument, timeframe)
        if idx < compiled.warmup_period:
            return None
        if not self.candidates[idx]:
            return None
        self.bars_decided += 1
        ctx = ArrayEvalContext(self.columns, idx, self.index[idx])
        return compiled.decide(ctx, symbol, tf)


def kernel_or_none(compiled: Any, prepared: pd.DataFrame) -> SignalKernel | None:
    """A kernel for this frame, or ``None`` when only the reference path applies."""
    try:
        return SignalKernel(compiled, prepared)
    except ValueError:
        return None
