"""Compile a StrategySpec into the common Strategy interface.

The compiled strategy is deterministic: identical spec + identical data
→ identical signals (Phase 20 gate). All rules evaluate on the closed
candle at ``idx`` and may not read rows beyond it.
"""

from __future__ import annotations

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.strategies.base import Strategy
from fibokei.strategies.factory.primitives import PRIMITIVES
from fibokei.strategies.factory.spec import StrategySpec


class CompiledStrategy(Strategy):
    """A Strategy generated from a StrategySpec."""

    def __init__(self, spec: StrategySpec):
        self._spec = spec
        # Build the indicator set once, deduplicated by indicator name.
        indicators: dict[str, object] = {}
        rule_sets = (
            spec.entry_rules + spec.confirmation_rules
            + spec.invalidation_rules + spec.filters
        )
        for rule in rule_sets:
            prim = PRIMITIVES[rule.primitive]
            for factory in prim.requires:
                ind = factory(rule.params)
                indicators[ind.name] = ind
        # Stop/target/trailing models may need ATR/Ichimoku even if no rule
        # references them. Target-side ATR was previously missing and would
        # cause atr_multiple targets to KeyError at signal time when no rule
        # already required ATR.
        if (
            spec.stop.model == "atr_multiple"
            or spec.target.model == "atr_multiple"
            or spec.trailing.model == "atr"
        ):
            from fibokei.indicators.atr import ATR
            ind = ATR(period=spec.stop.atr_period)
            indicators.setdefault(ind.name, ind)
        if spec.stop.model == "kijun" or spec.trailing.model == "kijun":
            from fibokei.indicators.ichimoku import IchimokuCloud
            ind = IchimokuCloud()
            indicators.setdefault(ind.name, ind)
        # Stable iteration order for deterministic indicator computation.
        self._indicators = [indicators[k] for k in sorted(indicators)]

    # ── Identity ──────────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return f"factory_{self._spec.spec_id}_v{self._spec.version}"

    @property
    def strategy_name(self) -> str:
        return self._spec.name

    @property
    def strategy_family(self) -> str:
        return self._spec.family

    @property
    def description(self) -> str:
        return self._spec.hypothesis

    @property
    def logic_summary(self) -> str:
        entries = " AND ".join(r.primitive for r in self._spec.entry_rules)
        return f"entry: {entries}; stop: {self._spec.stop.model}; target: {self._spec.target.model}"

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        if not self._spec.timeframes:
            return list(Timeframe)
        return [Timeframe(tf) for tf in self._spec.timeframes]

    @property
    def supports_long(self) -> bool:
        return self._spec.direction in ("long", "both")

    @property
    def supports_short(self) -> bool:
        return self._spec.direction in ("short", "both")

    @property
    def config(self) -> dict:
        return {"spec_hash": self._spec.content_hash, "version": self._spec.version}

    @property
    def spec(self) -> StrategySpec:
        return self._spec

    # ── Data preparation ──────────────────────────────────────────

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        for ind in self._indicators:
            df = ind.compute(df)
        return df

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        return "unknown"

    # ── Rule evaluation ───────────────────────────────────────────

    def _eval_rules(self, rules, df, idx, direction: str) -> bool:
        return all(
            PRIMITIVES[r.primitive].fn(df, idx, r.params, direction)
            for r in rules
        )

    def _direction_at(self, df: pd.DataFrame, idx: int) -> str | None:
        """Return 'long'/'short' if all entry+confirmation+filter rules pass."""
        candidates = []
        if self.supports_long:
            candidates.append("long")
        if self.supports_short:
            candidates.append("short")
        for direction in candidates:
            if not self._eval_rules(self._spec.entry_rules, df, idx, direction):
                continue
            if not self._eval_rules(self._spec.confirmation_rules, df, idx, direction):
                continue
            if not self._eval_rules(self._spec.filters, df, idx, direction):
                continue
            if self._spec.invalidation_rules and self._eval_rules(
                self._spec.invalidation_rules, df, idx, direction
            ):
                continue
            return direction
        return None

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        return self._direction_at(df, idx) is not None

    # ── Stops & targets ───────────────────────────────────────────

    def _stop_price(self, df, idx, entry: float, direction: str) -> float | None:
        s = self._spec.stop
        if s.model == "atr_multiple":
            atr = float(df["atr"].iloc[idx])
            if pd.isna(atr) or atr <= 0:
                return None
            dist = s.multiple * atr
        elif s.model == "fixed_pct":
            dist = entry * (s.multiple / 100.0)
        else:  # kijun
            kijun = float(df["kijun_sen"].iloc[idx])
            if pd.isna(kijun):
                return None
            return kijun if (
                (direction == "long" and kijun < entry)
                or (direction == "short" and kijun > entry)
            ) else None
        return entry - dist if direction == "long" else entry + dist

    def generate_signal(self, df: pd.DataFrame, idx: int, context: dict) -> Signal | None:
        direction = self._direction_at(df, idx)
        if direction is None:
            return None
        entry = float(df["close"].iloc[idx])
        stop = self._stop_price(df, idx, entry, direction)
        if stop is None:
            return None  # stops are mandatory — no stop, no signal
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return None
        t = self._spec.target
        if t.model == "rr_multiple":
            tp_dist = t.multiple * stop_dist
        else:  # atr_multiple
            atr = float(df["atr"].iloc[idx])
            if pd.isna(atr) or atr <= 0:
                return None
            tp_dist = t.multiple * atr
        tp = entry + tp_dist if direction == "long" else entry - tp_dist

        ts = df["timestamp"].iloc[idx] if "timestamp" in df.columns else df.index[idx]
        return Signal(
            timestamp=pd.Timestamp(ts).to_pydatetime(),
            instrument=context.get("instrument", ""),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=Direction.LONG if direction == "long" else Direction.SHORT,
            setup_type=self._spec.entry_rules[0].primitive,
            proposed_entry=entry,
            stop_loss=stop,
            take_profit_primary=tp,
            confidence_score=0.5,
            rationale_summary=self.logic_summary,
        )

    def validate_signal(self, signal: Signal, context: dict) -> Signal:
        if signal.stop_loss == signal.proposed_entry:
            signal.signal_valid = False
            signal.invalidation_reason = "zero stop distance"
        return signal

    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        trailing = None
        if self._spec.trailing.model == "atr":
            trailing = f"atr_x{self._spec.trailing.multiple}"
        elif self._spec.trailing.model == "kijun":
            trailing = "kijun"
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            trailing_stop_rule=trailing,
            risk_pct=self._spec.risk_pct,
            max_bars_in_trade=self._spec.max_bars_in_trade,
        )

    # ── Position management ───────────────────────────────────────

    def manage_position(self, position: dict, df: pd.DataFrame, idx: int, context: dict) -> dict:
        tr = self._spec.trailing
        if tr.model == "none":
            return position
        direction = position.get("direction", "long")
        close = float(df["close"].iloc[idx])
        new_stop = None
        if tr.model == "atr":
            atr = float(df["atr"].iloc[idx])
            if not pd.isna(atr) and atr > 0:
                new_stop = close - tr.multiple * atr if direction in ("long", "LONG") \
                    else close + tr.multiple * atr
        elif tr.model == "kijun":
            kijun = float(df["kijun_sen"].iloc[idx])
            if not pd.isna(kijun):
                new_stop = kijun
        if new_stop is not None:
            cur = position.get("stop_loss")
            if cur is None:
                position["stop_loss"] = new_stop
            elif direction in ("long", "LONG") and new_stop > cur:
                position["stop_loss"] = new_stop
            elif direction in ("short", "SHORT") and new_stop < cur:
                position["stop_loss"] = new_stop
        return position

    def generate_exit(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> ExitReason | None:
        bars_held = idx - position.get("entry_idx", idx)
        if bars_held >= self._spec.max_bars_in_trade:
            return ExitReason.TIME_STOP_EXIT
        if self._spec.exit_on_opposite_signal:
            opposite = self._direction_at(df, idx)
            held = position.get("direction", "long").lower()
            if opposite is not None and opposite != held:
                return ExitReason.OPPOSITE_SIGNAL_EXIT
        return None

    def score_confidence(self, signal: Signal, context: dict) -> float:
        return 0.5

    def explain_decision(self, context: dict) -> str:
        s = self._spec
        parts = [f"Factory strategy '{s.name}' v{s.version} ({s.content_hash})."]
        parts.append(
            "Entry when ALL hold: "
            + "; ".join(
                f"{r.primitive}({r.params})" if r.params else r.primitive
                for r in s.entry_rules
            )
        )
        if s.confirmation_rules:
            parts.append(
                "Confirmed by: " + "; ".join(r.primitive for r in s.confirmation_rules)
            )
        if s.filters:
            parts.append("Filters: " + "; ".join(r.primitive for r in s.filters))
        if s.invalidation_rules:
            parts.append(
                "Invalidated by: " + "; ".join(r.primitive for r in s.invalidation_rules)
            )
        parts.append(
            f"Stop: {s.stop.model} x{s.stop.multiple}; "
            f"target: {s.target.model} x{s.target.multiple}; "
            f"trailing: {s.trailing.model}; max bars {s.max_bars_in_trade}."
        )
        return " ".join(parts)


def compile_spec(spec: StrategySpec) -> CompiledStrategy:
    """Compile a validated StrategySpec into a Strategy instance."""
    return CompiledStrategy(spec)
