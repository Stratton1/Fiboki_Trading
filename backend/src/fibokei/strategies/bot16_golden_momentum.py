"""BOT-16: Golden Zone & Momentum Confluence — Fib 61.8% + RSI + MACD.

Waits for price to pull back to the 61.8% Fibonacci "Golden Zone" in
a trending market, then requires both RSI exhaustion (oversold/overbought)
AND MACD cross confirmation before entering. Triple confluence = high
probability setups.

SL at 78.6%, TP1 at 0% (swing extreme), TP2 at 127.2% or 161.8% extension.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class GoldenMomentumConfluence(Strategy):
    """Fibonacci 61.8% pullback with RSI exhaustion + MACD cross confirmation."""

    def __init__(
        self,
        swing_lookback: int = 50,
        rsi_period: int = 14,
        rsi_oversold: int = 30,
        rsi_overbought: int = 70,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        max_bars: int = 40,
    ):
        self.swing_lookback = swing_lookback
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.max_bars = max_bars

    # ── Identity ──────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "bot16_golden_momentum"

    @property
    def strategy_name(self) -> str:
        return "Golden Zone & Momentum Confluence"

    @property
    def strategy_family(self) -> str:
        return "fibonacci"

    @property
    def description(self) -> str:
        return (
            "Triple confluence: Fibonacci 61.8% Golden Zone pullback + RSI "
            "exhaustion + MACD cross. High probability trend continuation."
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
        return ["fibonacci", "rsi", "macd", "atr"]

    # ── Data Preparation ──────────────────────────────────────

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # RSI
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(span=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(span=self.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))

        # MACD
        ema_fast = df["close"].ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.macd_slow, adjust=False).mean()
        df["macd_line"] = ema_fast - ema_slow
        df["macd_signal_line"] = df["macd_line"].ewm(span=self.macd_signal, adjust=False).mean()
        df["macd_prev"] = df["macd_line"].shift(1)
        df["macd_sig_prev"] = df["macd_signal_line"].shift(1)
        # MACD bullish cross: macd crosses above signal
        df["macd_bull_cross"] = (df["macd_line"] > df["macd_signal_line"]) & (df["macd_prev"] <= df["macd_sig_prev"])
        # MACD bearish cross: macd crosses below signal
        df["macd_bear_cross"] = (df["macd_line"] < df["macd_signal_line"]) & (df["macd_prev"] >= df["macd_sig_prev"])

        # Swing high/low
        df["swing_high"] = df["high"].rolling(window=self.swing_lookback).max()
        df["swing_low"] = df["low"].rolling(window=self.swing_lookback).min()
        swing_range = df["swing_high"] - df["swing_low"]
        valid = swing_range > 0

        # Trend detection via swing position
        mid = (df["swing_high"] + df["swing_low"]) / 2
        df["trend_bull"] = df["close"] > mid
        df["trend_bear"] = df["close"] < mid

        # Fibonacci levels — Long (retracement from high)
        df["fib_618_long"] = np.where(valid, df["swing_high"] - 0.618 * swing_range, np.nan)
        df["fib_786_long"] = np.where(valid, df["swing_high"] - 0.786 * swing_range, np.nan)
        df["fib_0_long"] = df["swing_high"]  # TP1
        df["fib_ext_1272_long"] = np.where(valid, df["swing_high"] + 0.272 * swing_range, np.nan)
        df["fib_ext_1618_long"] = np.where(valid, df["swing_high"] + 0.618 * swing_range, np.nan)

        # Fibonacci levels — Short (retracement from low)
        df["fib_618_short"] = np.where(valid, df["swing_low"] + 0.618 * swing_range, np.nan)
        df["fib_786_short"] = np.where(valid, df["swing_low"] + 0.786 * swing_range, np.nan)
        df["fib_0_short"] = df["swing_low"]  # TP1
        df["fib_ext_1272_short"] = np.where(valid, df["swing_low"] - 0.272 * swing_range, np.nan)
        df["fib_ext_1618_short"] = np.where(valid, df["swing_low"] - 0.618 * swing_range, np.nan)

        # ATR
        df = ATR(period=14).compute(df)

        return df

    # ── Regime Detection ──────────────────────────────────────

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        row = df.iloc[idx]
        if row.get("trend_bull"):
            return "trending_bullish"
        if row.get("trend_bear"):
            return "trending_bearish"
        return "ranging"

    # ── Setup Detection ───────────────────────────────────────

    def _at_golden_zone_long(self, row) -> bool:
        """Price near or touching the 61.8% level (long side)."""
        close = row["close"]
        fib_618 = row.get("fib_618_long")
        fib_786 = row.get("fib_786_long")
        if pd.isna(fib_618) or pd.isna(fib_786):
            return False
        # Allow a zone around 61.8%: between 50% and 70% of the range
        zone_top = fib_618 + abs(fib_618 - fib_786) * 0.3
        zone_bottom = fib_618 - abs(fib_618 - fib_786) * 0.3
        return zone_bottom <= close <= zone_top

    def _at_golden_zone_short(self, row) -> bool:
        """Price near or touching the 61.8% level (short side)."""
        close = row["close"]
        fib_618 = row.get("fib_618_short")
        fib_786 = row.get("fib_786_short")
        if pd.isna(fib_618) or pd.isna(fib_786):
            return False
        zone_top = fib_618 + abs(fib_618 - fib_786) * 0.3
        zone_bottom = fib_618 - abs(fib_618 - fib_786) * 0.3
        return zone_bottom <= close <= zone_top

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < self.swing_lookback + 5:
            return False
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        rsi = row.get("rsi", 50)
        if pd.isna(rsi):
            return False

        if regime == "trending_bullish":
            return (
                self._at_golden_zone_long(row)
                and rsi <= self.rsi_oversold
                and bool(row.get("macd_bull_cross", False))
            )
        if regime == "trending_bearish":
            return (
                self._at_golden_zone_short(row)
                and rsi >= self.rsi_overbought
                and bool(row.get("macd_bear_cross", False))
            )
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
            sl = row["fib_786_long"]
            tp1 = row["fib_0_long"]
            tp2 = row["fib_ext_1618_long"]  # 161.8% extension
        else:
            direction = Direction.SHORT
            entry = row["close"]
            sl = row["fib_786_short"]
            tp1 = row["fib_0_short"]
            tp2 = row["fib_ext_1618_short"]

        if pd.isna(sl) or pd.isna(tp1) or pd.isna(tp2):
            return None

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
            setup_type="golden_momentum_confluence",
            entry_type="market",
            proposed_entry=entry,
            stop_loss=sl,
            take_profit_primary=tp1,
            take_profit_secondary=tp2,
            confidence_score=self.score_confidence(None, {
                "regime": regime, "rr": reward / risk,
                "rsi": row.get("rsi", 50),
            }),
            regime_label=regime,
            rationale_summary=f"Golden Zone + RSI + MACD confluence {direction.value}: RSI={row['rsi']:.0f}, R:R {reward/risk:.1f}",
            supporting_factors=[
                f"Trend: {regime}",
                f"Price at 61.8% Golden Zone",
                f"RSI exhaustion: {row['rsi']:.0f}",
                "MACD cross confirmed",
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
        sl = position.get("stop_loss", 0)
        tp_targets = position.get("take_profit_targets", [])
        bars = position.get("bars_in_trade", 0)

        high = row["high"]
        low = row["low"]

        # Stop loss
        if direction == "LONG" and low <= sl:
            return ExitReason.STOP_LOSS_HIT
        if direction == "SHORT" and high >= sl:
            return ExitReason.STOP_LOSS_HIT

        # Take profit
        if tp_targets:
            if direction == "LONG" and high >= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT
            if direction == "SHORT" and low <= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT

        # Time stop
        if bars >= self.max_bars:
            return ExitReason.TIME_STOP_EXIT

        return None

    # ── Scoring & Explanation ─────────────────────────────────

    def score_confidence(self, signal, context: dict) -> float:
        score = 0.6  # Triple confluence = higher base
        rr = context.get("rr", 1.0)
        if rr >= 2.0:
            score += 0.15
        elif rr >= 1.5:
            score += 0.10
        rsi = context.get("rsi", 50)
        if rsi <= 25 or rsi >= 75:
            score += 0.10  # Extreme exhaustion
        return min(score, 1.0)

    def explain_decision(self, context: dict) -> str:
        return (
            "Golden Zone & Momentum Confluence: Price pulled back to the 61.8% "
            "Fibonacci level in a trending market. RSI confirmed exhaustion "
            "(oversold for longs, overbought for shorts) and MACD line crossed "
            "the signal line confirming momentum shift back to the trend."
        )
