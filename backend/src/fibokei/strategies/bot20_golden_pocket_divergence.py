"""BOT-20: Golden Pocket & RSI Divergence — counter-trend precision.

Enters when price pulls into the 61.8%-65% Golden Pocket and RSI shows
divergence (price makes new extreme but RSI doesn't confirm). High
probability reversal at a mathematical Fibonacci zone.

SL at 78.6%, TP1 at 38.2% retracement, TP2 at 161.8% extension.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class GoldenPocketDivergence(Strategy):
    """Golden Pocket (61.8%-65%) with RSI divergence confirmation."""

    def __init__(self, swing_lookback: int = 40, rsi_period: int = 14, divergence_lookback: int = 10, max_bars: int = 30):
        self.swing_lookback = swing_lookback
        self.rsi_period = rsi_period
        self.divergence_lookback = divergence_lookback
        self.max_bars = max_bars

    @property
    def strategy_id(self) -> str:
        return "bot20_pocket_divergence"

    @property
    def strategy_name(self) -> str:
        return "Golden Pocket & RSI Divergence"

    @property
    def strategy_family(self) -> str:
        return "fibonacci"

    @property
    def description(self) -> str:
        return "Golden Pocket (61.8%-65%) with RSI divergence for precision reversals."

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish"]

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return [Timeframe.M15, Timeframe.M30, Timeframe.H1]

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def complexity_level(self) -> str:
        return "advanced"

    def get_required_indicators(self) -> list[str]:
        return ["fibonacci", "rsi", "atr"]

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

        # EMA trend
        df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

        # Swing + Fibonacci
        df["swing_high"] = df["high"].rolling(window=self.swing_lookback).max()
        df["swing_low"] = df["low"].rolling(window=self.swing_lookback).min()
        sr = df["swing_high"] - df["swing_low"]
        valid = sr > 0

        df["fib_618_long"] = np.where(valid, df["swing_high"] - 0.618 * sr, np.nan)
        df["fib_650_long"] = np.where(valid, df["swing_high"] - 0.650 * sr, np.nan)
        df["fib_786_long"] = np.where(valid, df["swing_high"] - 0.786 * sr, np.nan)
        df["fib_382_long"] = np.where(valid, df["swing_high"] - 0.382 * sr, np.nan)
        df["fib_ext_long"] = np.where(valid, df["swing_high"] + 0.618 * sr, np.nan)

        df["fib_618_short"] = np.where(valid, df["swing_low"] + 0.618 * sr, np.nan)
        df["fib_650_short"] = np.where(valid, df["swing_low"] + 0.650 * sr, np.nan)
        df["fib_786_short"] = np.where(valid, df["swing_low"] + 0.786 * sr, np.nan)
        df["fib_382_short"] = np.where(valid, df["swing_low"] + 0.382 * sr, np.nan)
        df["fib_ext_short"] = np.where(valid, df["swing_low"] - 0.618 * sr, np.nan)

        df = ATR(period=14).compute(df)
        return df

    def _bullish_divergence(self, df: pd.DataFrame, idx: int) -> bool:
        """Price makes lower low but RSI makes higher low."""
        lb = self.divergence_lookback
        if idx < lb + 2:
            return False
        price_now = df["low"].iloc[idx]
        rsi_now = df["rsi"].iloc[idx]
        if pd.isna(rsi_now):
            return False
        # Find lowest price in lookback
        for i in range(idx - lb, idx):
            if df["low"].iloc[i] <= price_now:
                # Price made a lower low or equal
                rsi_then = df["rsi"].iloc[i]
                if not pd.isna(rsi_then) and rsi_now > rsi_then:
                    return True  # RSI higher = bullish divergence
        return False

    def _bearish_divergence(self, df: pd.DataFrame, idx: int) -> bool:
        """Price makes higher high but RSI makes lower high."""
        lb = self.divergence_lookback
        if idx < lb + 2:
            return False
        price_now = df["high"].iloc[idx]
        rsi_now = df["rsi"].iloc[idx]
        if pd.isna(rsi_now):
            return False
        for i in range(idx - lb, idx):
            if df["high"].iloc[i] >= price_now:
                rsi_then = df["rsi"].iloc[i]
                if not pd.isna(rsi_then) and rsi_now < rsi_then:
                    return True
        return False

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        row = df.iloc[idx]
        if pd.isna(row.get("ema_200")):
            return "unknown"
        return "trending_bullish" if row["close"] > row["ema_200"] else "trending_bearish"

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < 210:
            return False
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)

        if regime == "trending_bullish":
            fib_618 = row.get("fib_618_long")
            fib_650 = row.get("fib_650_long")
            if pd.isna(fib_618) or pd.isna(fib_650):
                return False
            in_pocket = fib_650 <= row["close"] <= fib_618
            return in_pocket and self._bullish_divergence(df, idx)

        if regime == "trending_bearish":
            fib_618 = row.get("fib_618_short")
            fib_650 = row.get("fib_650_short")
            if pd.isna(fib_618) or pd.isna(fib_650):
                return False
            in_pocket = fib_618 <= row["close"] <= fib_650
            return in_pocket and self._bearish_divergence(df, idx)

        return False

    def generate_signal(self, df: pd.DataFrame, idx: int, context: dict) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        instrument = context.get("instrument", "UNKNOWN")
        timeframe = context.get("timeframe", Timeframe.H1)

        if regime == "trending_bullish":
            direction = Direction.LONG
            entry, sl = row["close"], row["fib_786_long"]
            tp1, tp2 = row["fib_382_long"], row["fib_ext_long"]
        else:
            direction = Direction.SHORT
            entry, sl = row["close"], row["fib_786_short"]
            tp1, tp2 = row["fib_382_short"], row["fib_ext_short"]

        if pd.isna(sl) or pd.isna(tp1):
            return None
        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        if risk <= 0 or reward / risk < 0.8:
            return None

        return self.validate_signal(Signal(
            timestamp=df.index[idx] if hasattr(df.index[idx], "isoformat") else pd.Timestamp.now(),
            instrument=instrument, timeframe=timeframe, strategy_id=self.strategy_id,
            direction=direction, setup_type="golden_pocket_divergence", entry_type="market",
            proposed_entry=entry, stop_loss=sl, take_profit_primary=tp1,
            take_profit_secondary=tp2 if not pd.isna(tp2) else None,
            confidence_score=min(0.7 + (0.1 if reward/risk >= 2.0 else 0), 1.0),
            regime_label=regime,
            rationale_summary=f"Golden Pocket + RSI divergence {direction.value}, R:R {reward/risk:.1f}",
            supporting_factors=["Price in 61.8%-65% Golden Pocket", "RSI divergence confirmed", f"R:R {reward/risk:.1f}"],
        ), context)

    def validate_signal(self, signal, context):
        return signal

    def build_trade_plan(self, signal, context):
        return TradePlan(
            entry_price=signal.proposed_entry, stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary] + ([signal.take_profit_secondary] if signal.take_profit_secondary else []),
            max_bars_in_trade=self.max_bars, partial_close_pcts=[0.5],
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
        return 0.7

    def explain_decision(self, context):
        return "Golden Pocket + RSI divergence: price in 61.8%-65% zone with momentum divergence confirming reversal."
