"""BOT-21: Fibonacci Arc Breakout & Scalp — curved level breakouts.

Approximates Fibonacci Arcs using time-weighted Fibonacci levels that
decay with distance from the swing point. Enters on breakout above/below
arc levels, exits at the next arc. Designed for M5/M15 scalping.

Note: True arcs require geometric projection. This approximation uses
Fibonacci retracement levels that widen over time (sqrt decay) to
simulate the curvature of arc projections.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class FibArcBreakout(Strategy):
    """Fibonacci Arc approximation breakout scalper."""

    def __init__(self, swing_lookback: int = 30, arc_decay: float = 0.02, max_bars: int = 10):
        self.swing_lookback = swing_lookback
        self.arc_decay = arc_decay
        self.max_bars = max_bars

    @property
    def strategy_id(self) -> str:
        return "bot21_fib_arc"

    @property
    def strategy_name(self) -> str:
        return "Fibonacci Arc Breakout Scalper"

    @property
    def strategy_family(self) -> str:
        return "fibonacci"

    @property
    def description(self) -> str:
        return "Fibonacci Arc approximation breakout for scalping. Arc-to-arc targets."

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish", "breakout_candidate"]

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return [Timeframe.M5, Timeframe.M15, Timeframe.M30]

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def complexity_level(self) -> str:
        return "advanced"

    def get_required_indicators(self) -> list[str]:
        return ["fibonacci_arcs", "atr"]

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        df["swing_high"] = df["high"].rolling(window=self.swing_lookback).max()
        df["swing_low"] = df["low"].rolling(window=self.swing_lookback).min()

        # Approximate Fibonacci Arcs: levels that expand with sqrt of time
        # from the swing point. This creates a curved effect.
        sr = df["swing_high"] - df["swing_low"]
        valid = sr > 0

        # Time since swing high/low (approximate with rolling argmax/argmin)
        swing_high_idx = df["high"].rolling(window=self.swing_lookback).apply(lambda x: x.argmax(), raw=True)
        bars_since_high = np.arange(n) - swing_high_idx.fillna(0).values.astype(int)
        bars_since_high = np.clip(bars_since_high, 1, self.swing_lookback)

        # Arc expansion factor (sqrt decay)
        expansion = 1 + self.arc_decay * np.sqrt(bars_since_high)

        # Arc levels expand from swing high downward (for longs)
        df["arc_236_long"] = np.where(valid, df["swing_high"] - 0.236 * sr * expansion, np.nan)
        df["arc_382_long"] = np.where(valid, df["swing_high"] - 0.382 * sr * expansion, np.nan)
        df["arc_50_long"] = np.where(valid, df["swing_high"] - 0.500 * sr * expansion, np.nan)

        # Arc levels expand from swing low upward (for shorts)
        df["arc_236_short"] = np.where(valid, df["swing_low"] + 0.236 * sr * expansion, np.nan)
        df["arc_382_short"] = np.where(valid, df["swing_low"] + 0.382 * sr * expansion, np.nan)
        df["arc_50_short"] = np.where(valid, df["swing_low"] + 0.500 * sr * expansion, np.nan)

        # Previous values for crossover detection
        df["arc_382_long_prev"] = pd.Series(df["arc_382_long"]).shift(1)
        df["arc_382_short_prev"] = pd.Series(df["arc_382_short"]).shift(1)

        df = ATR(period=14).compute(df)
        return df

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        row = df.iloc[idx]
        mid = (row.get("swing_high", 0) + row.get("swing_low", 0)) / 2
        if row["close"] > mid:
            return "trending_bullish"
        return "trending_bearish"

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < self.swing_lookback + 5:
            return False
        row = df.iloc[idx]

        # Long: price breaks above 38.2% arc (was below, now above)
        arc_long = row.get("arc_382_long")
        arc_long_prev = row.get("arc_382_long_prev")
        if not pd.isna(arc_long) and not pd.isna(arc_long_prev):
            prev_close = df["close"].iloc[idx - 1] if idx > 0 else row["close"]
            if prev_close < arc_long_prev and row["close"] > arc_long:
                self._setup_direction = "long"
                return True

        # Short: price breaks below 38.2% arc
        arc_short = row.get("arc_382_short")
        arc_short_prev = row.get("arc_382_short_prev")
        if not pd.isna(arc_short) and not pd.isna(arc_short_prev):
            prev_close = df["close"].iloc[idx - 1] if idx > 0 else row["close"]
            if prev_close > arc_short_prev and row["close"] < arc_short:
                self._setup_direction = "short"
                return True

        return False

    def generate_signal(self, df: pd.DataFrame, idx: int, context: dict) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None
        row = df.iloc[idx]
        atr = row.get("atr", 0)
        if pd.isna(atr) or atr <= 0:
            return None
        instrument = context.get("instrument", "UNKNOWN")
        timeframe = context.get("timeframe", Timeframe.M5)

        if self._setup_direction == "long":
            direction = Direction.LONG
            entry = row["close"]
            sl = row["arc_50_long"] if not pd.isna(row.get("arc_50_long")) else entry - 2 * atr
            tp1 = row["arc_236_long"] if not pd.isna(row.get("arc_236_long")) else entry + 1.5 * atr
        else:
            direction = Direction.SHORT
            entry = row["close"]
            sl = row["arc_50_short"] if not pd.isna(row.get("arc_50_short")) else entry + 2 * atr
            tp1 = row["arc_236_short"] if not pd.isna(row.get("arc_236_short")) else entry - 1.5 * atr

        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        if risk <= 0 or reward / risk < 0.5:
            return None

        return self.validate_signal(Signal(
            timestamp=df.index[idx] if hasattr(df.index[idx], "isoformat") else pd.Timestamp.now(),
            instrument=instrument, timeframe=timeframe, strategy_id=self.strategy_id,
            direction=direction, setup_type="fib_arc_breakout", entry_type="market",
            proposed_entry=entry, stop_loss=sl, take_profit_primary=tp1,
            confidence_score=min(0.55 + (0.1 if reward/risk >= 1.0 else 0), 1.0),
            regime_label=self._setup_direction,
            rationale_summary=f"Fib Arc breakout {direction.value}: arc-to-arc scalp, R:R {reward/risk:.1f}",
            supporting_factors=["Price broke through 38.2% Fibonacci Arc", f"Target: next arc level", f"R:R {reward/risk:.1f}"],
        ), context)

    def validate_signal(self, signal, context):
        return signal

    def build_trade_plan(self, signal, context):
        return TradePlan(
            entry_price=signal.proposed_entry, stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            max_bars_in_trade=self.max_bars,
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context):
        row = df.iloc[idx]
        d, sl, tp = position.get("direction"), position.get("stop_loss", 0), position.get("take_profit_targets", [])
        bars = position.get("bars_in_trade", 0)
        if d == "LONG" and row["low"] <= sl: return ExitReason.STOP_LOSS_HIT
        if d == "SHORT" and row["high"] >= sl: return ExitReason.STOP_LOSS_HIT
        if tp:
            if d == "LONG" and row["high"] >= tp[0]: return ExitReason.TAKE_PROFIT_HIT
            if d == "SHORT" and row["low"] <= tp[0]: return ExitReason.TAKE_PROFIT_HIT
        if bars >= self.max_bars: return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.55

    def explain_decision(self, context):
        return "Fibonacci Arc breakout: price broke through a curved Fibonacci level, targeting the next arc."
