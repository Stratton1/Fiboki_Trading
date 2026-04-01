"""BOT-13: Golden Zone Pullback — Fibonacci retracement trend continuation.

Identifies established trends via 50/200 EMA, auto-draws Fibonacci
retracement from swing high/low over a lookback period, waits for
pullback into the 50%-61.8% "Golden Zone", and enters when RSI
confirms momentum is returning to the main trend direction.

Exit uses Fibonacci levels: SL at 78.6% + ATR buffer, TP1 at swing
extreme (0% level), TP2 at -27.2% extension. Trailing stop via 50 EMA
after TP1 hit. Max 40 bars failsafe.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class GoldenZonePullback(Strategy):
    """Fibonacci Golden Zone (50%-61.8%) pullback with EMA trend filter."""

    def __init__(
        self,
        swing_lookback: int = 60,
        ema_fast: int = 50,
        ema_slow: int = 200,
        rsi_period: int = 14,
        rsi_long_threshold: int = 40,
        rsi_short_threshold: int = 60,
        atr_sl_buffer: float = 0.5,
        max_bars: int = 40,
    ):
        self.swing_lookback = swing_lookback
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_long_threshold = rsi_long_threshold
        self.rsi_short_threshold = rsi_short_threshold
        self.atr_sl_buffer = atr_sl_buffer
        self.max_bars = max_bars

    # ── Identity ──────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "bot13_golden_zone"

    @property
    def strategy_name(self) -> str:
        return "Golden Zone Pullback"

    @property
    def strategy_family(self) -> str:
        return "fibonacci"

    @property
    def description(self) -> str:
        return (
            "Trend continuation using Fibonacci 50%-61.8% retracement "
            "pullback with EMA trend filter and RSI momentum trigger."
        )

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish"]

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return [Timeframe.H1, Timeframe.H4]

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def complexity_level(self) -> str:
        return "standard"

    def get_required_indicators(self) -> list[str]:
        return ["ema_50", "ema_200", "rsi_14", "atr"]

    # ── Data Preparation ──────────────────────────────────────

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # EMAs
        df["ema_fast"] = df["close"].ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.ema_slow, adjust=False).mean()

        # RSI
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(span=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(span=self.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["rsi_prev"] = df["rsi"].shift(1)

        # ATR
        df = ATR(period=14).compute(df)

        # Swing high/low over lookback window
        df["swing_high"] = df["high"].rolling(window=self.swing_lookback).max()
        df["swing_low"] = df["low"].rolling(window=self.swing_lookback).min()

        # Fibonacci levels (computed relative to trend direction)
        # For longs: fib drawn from swing_low (1.0) to swing_high (0.0)
        # For shorts: fib drawn from swing_high (1.0) to swing_low (0.0)
        swing_range = df["swing_high"] - df["swing_low"]
        # Long fib levels (retracement from high)
        df["fib_50_long"] = df["swing_high"] - 0.500 * swing_range
        df["fib_618_long"] = df["swing_high"] - 0.618 * swing_range
        df["fib_786_long"] = df["swing_high"] - 0.786 * swing_range
        df["fib_0_long"] = df["swing_high"]  # TP1 for longs
        df["fib_ext_long"] = df["swing_high"] + 0.272 * swing_range  # TP2 (-27.2% ext)

        # Short fib levels (retracement from low)
        df["fib_50_short"] = df["swing_low"] + 0.500 * swing_range
        df["fib_618_short"] = df["swing_low"] + 0.618 * swing_range
        df["fib_786_short"] = df["swing_low"] + 0.786 * swing_range
        df["fib_0_short"] = df["swing_low"]  # TP1 for shorts
        df["fib_ext_short"] = df["swing_low"] - 0.272 * swing_range  # TP2 (-27.2% ext)

        return df

    # ── Regime Detection ──────────────────────────────────────

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        row = df.iloc[idx]
        if pd.isna(row.get("ema_fast")) or pd.isna(row.get("ema_slow")):
            return "unknown"
        if row["close"] > row["ema_slow"] and row["ema_fast"] > row["ema_slow"]:
            return "trending_bullish"
        if row["close"] < row["ema_slow"] and row["ema_fast"] < row["ema_slow"]:
            return "trending_bearish"
        return "ranging"

    # ── Setup Detection ───────────────────────────────────────

    def _in_golden_zone_long(self, row) -> bool:
        """Price is between 50% and 61.8% retracement (long side)."""
        price = row["close"]
        fib_50 = row.get("fib_50_long")
        fib_618 = row.get("fib_618_long")
        if pd.isna(fib_50) or pd.isna(fib_618):
            return False
        # For longs: fib_50 > fib_618 (both below swing high)
        return fib_618 <= price <= fib_50

    def _in_golden_zone_short(self, row) -> bool:
        """Price is between 50% and 61.8% retracement (short side)."""
        price = row["close"]
        fib_50 = row.get("fib_50_short")
        fib_618 = row.get("fib_618_short")
        if pd.isna(fib_50) or pd.isna(fib_618):
            return False
        # For shorts: fib_50 < fib_618 (both above swing low)
        return fib_50 <= price <= fib_618

    def _rsi_trigger_long(self, row) -> bool:
        """RSI dropped below threshold then crossed back above."""
        rsi = row.get("rsi")
        rsi_prev = row.get("rsi_prev")
        if pd.isna(rsi) or pd.isna(rsi_prev):
            return False
        return rsi_prev < self.rsi_long_threshold and rsi >= self.rsi_long_threshold

    def _rsi_trigger_short(self, row) -> bool:
        """RSI rose above threshold then crossed back below."""
        rsi = row.get("rsi")
        rsi_prev = row.get("rsi_prev")
        if pd.isna(rsi) or pd.isna(rsi_prev):
            return False
        return rsi_prev > self.rsi_short_threshold and rsi <= self.rsi_short_threshold

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < self.ema_slow + 10:
            return False
        regime = self.detect_market_regime(df, idx)
        row = df.iloc[idx]
        if regime == "trending_bullish":
            return self._in_golden_zone_long(row) and self._rsi_trigger_long(row)
        if regime == "trending_bearish":
            return self._in_golden_zone_short(row) and self._rsi_trigger_short(row)
        return False

    # ── Signal Generation ─────────────────────────────────────

    def generate_signal(
        self, df: pd.DataFrame, idx: int, context: dict
    ) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        atr = row.get("atr", 0)
        if pd.isna(atr) or atr <= 0:
            return None

        instrument = context.get("instrument", "UNKNOWN")
        timeframe = context.get("timeframe", Timeframe.H1)

        if regime == "trending_bullish":
            direction = Direction.LONG
            entry = row["close"]
            sl = row["fib_786_long"] - self.atr_sl_buffer * atr
            tp1 = row["fib_0_long"]  # Swing high
            tp2 = row["fib_ext_long"]  # -27.2% extension
        else:
            direction = Direction.SHORT
            entry = row["close"]
            sl = row["fib_786_short"] + self.atr_sl_buffer * atr
            tp1 = row["fib_0_short"]  # Swing low
            tp2 = row["fib_ext_short"]  # -27.2% extension

        # Validate risk:reward (at least 1:1 to TP1)
        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        if risk <= 0 or reward / risk < 1.0:
            return None

        signal = Signal(
            timestamp=df.index[idx] if hasattr(df.index[idx], "isoformat") else pd.Timestamp.now(),
            instrument=instrument,
            timeframe=timeframe,
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="golden_zone_pullback",
            entry_type="market",
            proposed_entry=entry,
            stop_loss=sl,
            take_profit_primary=tp1,
            take_profit_secondary=tp2,
            confidence_score=self.score_confidence(None, {
                "regime": regime, "rsi": row["rsi"], "atr": atr, "rr": reward / risk,
            }),
            regime_label=regime,
            rationale_summary=f"Golden Zone pullback in {regime}: RSI trigger at {row['rsi']:.1f}, R:R {reward/risk:.1f}",
            supporting_factors=[
                f"EMA trend: {regime}",
                f"Price in 50%-61.8% zone",
                f"RSI momentum trigger",
                f"R:R to TP1: {reward/risk:.1f}",
            ],
        )
        return self.validate_signal(signal, context)

    def validate_signal(self, signal: Signal, context: dict) -> Signal:
        return signal

    # ── Trade Plan ────────────────────────────────────────────

    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary]
            + ([signal.take_profit_secondary] if signal.take_profit_secondary else []),
            trailing_stop_rule="trail_ema50_after_tp1",
            break_even_rule="move_to_entry_after_tp1",
            max_bars_in_trade=self.max_bars,
            partial_close_pcts=[0.5],
        )

    # ── Position Management ───────────────────────────────────

    def manage_position(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> dict:
        return position

    def generate_exit(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> ExitReason | None:
        row = df.iloc[idx]
        direction = position.get("direction")
        entry_price = position.get("entry_price", 0)
        sl = position.get("stop_loss", 0)
        tp_targets = position.get("take_profit_targets", [])
        bars = position.get("bars_in_trade", 0)

        price = row["close"]
        high = row["high"]
        low = row["low"]

        # Stop loss check
        if direction == "LONG" and low <= sl:
            return ExitReason.STOP_LOSS_HIT
        if direction == "SHORT" and high >= sl:
            return ExitReason.STOP_LOSS_HIT

        # Take profit check (TP1)
        if tp_targets:
            if direction == "LONG" and high >= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT
            if direction == "SHORT" and low <= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT

        # Time stop
        if bars >= self.max_bars:
            return ExitReason.TIME_STOP_EXIT

        # Trailing stop via 50 EMA (simplified: exit if price crosses wrong side)
        ema_fast = row.get("ema_fast")
        if not pd.isna(ema_fast):
            if direction == "LONG" and price < ema_fast and bars > 10:
                return ExitReason.TRAILING_STOP_HIT
            if direction == "SHORT" and price > ema_fast and bars > 10:
                return ExitReason.TRAILING_STOP_HIT

        return None

    # ── Scoring & Explanation ─────────────────────────────────

    def score_confidence(self, signal, context: dict) -> float:
        score = 0.5
        regime = context.get("regime", "")
        rr = context.get("rr", 1.0)
        if regime in ("trending_bullish", "trending_bearish"):
            score += 0.15
        if rr >= 2.0:
            score += 0.15
        elif rr >= 1.5:
            score += 0.10
        rsi = context.get("rsi", 50)
        if 35 <= rsi <= 45 or 55 <= rsi <= 65:
            score += 0.10
        return min(score, 1.0)

    def explain_decision(self, context: dict) -> str:
        return (
            "Golden Zone Pullback: Price retraced to the 50%-61.8% Fibonacci zone "
            "within an EMA-confirmed trend. RSI momentum trigger confirmed the "
            "pullback is ending and trend is resuming."
        )
