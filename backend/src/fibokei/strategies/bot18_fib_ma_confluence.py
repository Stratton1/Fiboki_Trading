"""BOT-18: Fibonacci & Moving Average Confluence — Fib level + MA dynamic support.

Enters when a Fibonacci retracement level (61.8%) aligns with a key
moving average acting as dynamic support/resistance. Confirmed by
short-term MA crossing long-term MA in the trend direction.

SL below/above the confluence zone. TP at 127.2% Fibonacci extension.
Best on H4/Daily with multi-timeframe trend alignment.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class FibMAConfluence(Strategy):
    """Fibonacci 61.8% + Moving Average confluence with MA crossover trigger."""

    def __init__(
        self,
        swing_lookback: int = 50,
        ema_short: int = 20,
        ema_long: int = 50,
        ema_trend: int = 200,
        confluence_tolerance_atr: float = 1.0,
        max_bars: int = 40,
    ):
        self.swing_lookback = swing_lookback
        self.ema_short = ema_short
        self.ema_long = ema_long
        self.ema_trend = ema_trend
        self.confluence_tolerance_atr = confluence_tolerance_atr
        self.max_bars = max_bars

    # ── Identity ──────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "bot18_fib_ma_confluence"

    @property
    def strategy_name(self) -> str:
        return "Fibonacci & MA Confluence"

    @property
    def strategy_family(self) -> str:
        return "hybrid"

    @property
    def description(self) -> str:
        return (
            "Fibonacci 61.8% retracement confluent with a moving average "
            "as dynamic support/resistance. MA crossover confirms entry."
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
        return ["ema_20", "ema_50", "ema_200", "fibonacci", "atr"]

    # ── Data Preparation ──────────────────────────────────────

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # EMAs
        df["ema_short"] = df["close"].ewm(span=self.ema_short, adjust=False).mean()
        df["ema_long"] = df["close"].ewm(span=self.ema_long, adjust=False).mean()
        df["ema_trend"] = df["close"].ewm(span=self.ema_trend, adjust=False).mean()

        # MA crossover detection
        df["ema_short_prev"] = df["ema_short"].shift(1)
        df["ema_long_prev"] = df["ema_long"].shift(1)
        df["ma_bull_cross"] = (df["ema_short"] > df["ema_long"]) & (df["ema_short_prev"] <= df["ema_long_prev"])
        df["ma_bear_cross"] = (df["ema_short"] < df["ema_long"]) & (df["ema_short_prev"] >= df["ema_long_prev"])

        # Swing high/low
        df["swing_high"] = df["high"].rolling(window=self.swing_lookback).max()
        df["swing_low"] = df["low"].rolling(window=self.swing_lookback).min()
        swing_range = df["swing_high"] - df["swing_low"]
        valid = swing_range > 0

        # Fibonacci levels
        df["fib_618_long"] = np.where(valid, df["swing_high"] - 0.618 * swing_range, np.nan)
        df["fib_786_long"] = np.where(valid, df["swing_high"] - 0.786 * swing_range, np.nan)
        df["fib_0_long"] = df["swing_high"]
        df["fib_ext_long"] = np.where(valid, df["swing_high"] + 0.272 * swing_range, np.nan)

        df["fib_618_short"] = np.where(valid, df["swing_low"] + 0.618 * swing_range, np.nan)
        df["fib_786_short"] = np.where(valid, df["swing_low"] + 0.786 * swing_range, np.nan)
        df["fib_0_short"] = df["swing_low"]
        df["fib_ext_short"] = np.where(valid, df["swing_low"] - 0.272 * swing_range, np.nan)

        # ATR
        df = ATR(period=14).compute(df)

        return df

    # ── Regime Detection ──────────────────────────────────────

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        row = df.iloc[idx]
        if pd.isna(row.get("ema_trend")):
            return "unknown"
        if row["close"] > row["ema_trend"]:
            return "trending_bullish"
        if row["close"] < row["ema_trend"]:
            return "trending_bearish"
        return "ranging"

    # ── Setup Detection ───────────────────────────────────────

    def _fib_ma_confluence_long(self, row, atr) -> bool:
        """Check if 61.8% fib level aligns with 20 or 50 EMA."""
        fib = row.get("fib_618_long")
        if pd.isna(fib) or pd.isna(atr) or atr <= 0:
            return False
        tol = self.confluence_tolerance_atr * atr
        # Check if either EMA is near the fib level
        ema_s = row.get("ema_short")
        ema_l = row.get("ema_long")
        near_short = not pd.isna(ema_s) and abs(ema_s - fib) <= tol
        near_long = not pd.isna(ema_l) and abs(ema_l - fib) <= tol
        if not (near_short or near_long):
            return False
        # Price must be near the fib level too
        return abs(row["close"] - fib) <= tol * 1.5

    def _fib_ma_confluence_short(self, row, atr) -> bool:
        """Check if 61.8% fib level aligns with 20 or 50 EMA (short side)."""
        fib = row.get("fib_618_short")
        if pd.isna(fib) or pd.isna(atr) or atr <= 0:
            return False
        tol = self.confluence_tolerance_atr * atr
        ema_s = row.get("ema_short")
        ema_l = row.get("ema_long")
        near_short = not pd.isna(ema_s) and abs(ema_s - fib) <= tol
        near_long = not pd.isna(ema_l) and abs(ema_l - fib) <= tol
        if not (near_short or near_long):
            return False
        return abs(row["close"] - fib) <= tol * 1.5

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < self.ema_trend + 5:
            return False
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        atr = row.get("atr", 0)

        if regime == "trending_bullish":
            return self._fib_ma_confluence_long(row, atr) and bool(row.get("ma_bull_cross", False))
        if regime == "trending_bearish":
            return self._fib_ma_confluence_short(row, atr) and bool(row.get("ma_bear_cross", False))
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
        timeframe = context.get("timeframe", Timeframe.H4)

        if regime == "trending_bullish":
            direction = Direction.LONG
            entry = row["close"]
            sl = row["fib_786_long"] - 0.5 * atr
            tp1 = row["fib_0_long"]  # Swing high
            tp2 = row["fib_ext_long"]  # 127.2% extension
        else:
            direction = Direction.SHORT
            entry = row["close"]
            sl = row["fib_786_short"] + 0.5 * atr
            tp1 = row["fib_0_short"]
            tp2 = row["fib_ext_short"]

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
            setup_type="fib_ma_confluence",
            entry_type="market",
            proposed_entry=entry,
            stop_loss=sl,
            take_profit_primary=tp1,
            take_profit_secondary=tp2,
            confidence_score=self.score_confidence(None, {
                "regime": regime, "rr": reward / risk,
            }),
            regime_label=regime,
            rationale_summary=f"Fib 61.8% + MA confluence {direction.value}: MA cross confirmed, R:R {reward/risk:.1f}",
            supporting_factors=[
                f"200 EMA trend: {regime}",
                "Fib 61.8% aligns with 20/50 EMA",
                "MA crossover confirmed",
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

        if direction == "LONG" and low <= sl:
            return ExitReason.STOP_LOSS_HIT
        if direction == "SHORT" and high >= sl:
            return ExitReason.STOP_LOSS_HIT

        if tp_targets:
            if direction == "LONG" and high >= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT
            if direction == "SHORT" and low <= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT

        # MA cross reversal bailout
        ma_bull = row.get("ma_bull_cross", False)
        ma_bear = row.get("ma_bear_cross", False)
        if direction == "LONG" and ma_bear and bars >= 3:
            return ExitReason.INDICATOR_INVALIDATION_EXIT
        if direction == "SHORT" and ma_bull and bars >= 3:
            return ExitReason.INDICATOR_INVALIDATION_EXIT

        if bars >= self.max_bars:
            return ExitReason.TIME_STOP_EXIT

        return None

    # ── Scoring & Explanation ─────────────────────────────────

    def score_confidence(self, signal, context: dict) -> float:
        score = 0.6  # Dual confluence = higher base
        rr = context.get("rr", 1.0)
        if rr >= 2.0:
            score += 0.15
        elif rr >= 1.5:
            score += 0.10
        return min(score, 1.0)

    def explain_decision(self, context: dict) -> str:
        return (
            "Fibonacci & MA Confluence: The 61.8% Fibonacci retracement level "
            "aligned with a key moving average (20/50 EMA) creating a confluence "
            "zone. A short-term MA crossover confirmed the trend resumption."
        )
