"""BOT-19: Fibonacci & Bollinger Band Exhaustion — dual confluence reversal.

Enters when price simultaneously touches the 61.8% Fibonacci retracement
and a Bollinger Band extremity, confirming exhaustion. Candle must close
back inside the band. SL at 78.6%, TP1 at middle BB, TP2 at 127.2% ext.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class FibBBExhaustion(Strategy):
    """Fibonacci 61.8% + Bollinger Band exhaustion confluence."""

    def __init__(self, swing_lookback: int = 50, bb_period: int = 20, bb_std: float = 2.0, max_bars: int = 30):
        self.swing_lookback = swing_lookback
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.max_bars = max_bars

    @property
    def strategy_id(self) -> str:
        return "bot19_fib_bb"

    @property
    def strategy_name(self) -> str:
        return "Fibonacci & Bollinger Band Exhaustion"

    @property
    def strategy_family(self) -> str:
        return "fibonacci"

    @property
    def description(self) -> str:
        return "Dual confluence: Fib 61.8% retracement + Bollinger Band exhaustion."

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
        return ["fibonacci", "bollinger_bands", "atr"]

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # Bollinger Bands
        df["bb_mid"] = df["close"].rolling(window=self.bb_period).mean()
        bb_std = df["close"].rolling(window=self.bb_period).std()
        df["bb_upper"] = df["bb_mid"] + self.bb_std * bb_std
        df["bb_lower"] = df["bb_mid"] - self.bb_std * bb_std
        df["bb_upper_prev"] = df["bb_upper"].shift(1)
        df["bb_lower_prev"] = df["bb_lower"].shift(1)

        # EMA for trend
        df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

        # Swing high/low + Fibonacci
        df["swing_high"] = df["high"].rolling(window=self.swing_lookback).max()
        df["swing_low"] = df["low"].rolling(window=self.swing_lookback).min()
        sr = df["swing_high"] - df["swing_low"]
        valid = sr > 0
        df["fib_618_long"] = np.where(valid, df["swing_high"] - 0.618 * sr, np.nan)
        df["fib_786_long"] = np.where(valid, df["swing_high"] - 0.786 * sr, np.nan)
        df["fib_ext_long"] = np.where(valid, df["swing_high"] + 0.272 * sr, np.nan)
        df["fib_618_short"] = np.where(valid, df["swing_low"] + 0.618 * sr, np.nan)
        df["fib_786_short"] = np.where(valid, df["swing_low"] + 0.786 * sr, np.nan)
        df["fib_ext_short"] = np.where(valid, df["swing_low"] - 0.272 * sr, np.nan)

        df = ATR(period=14).compute(df)
        return df

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
        atr = row.get("atr", 0)
        if pd.isna(atr) or atr <= 0:
            return False

        if regime == "trending_bullish":
            fib = row.get("fib_618_long")
            bb_low = row.get("bb_lower")
            if pd.isna(fib) or pd.isna(bb_low):
                return False
            # Price near both fib 61.8% and lower BB
            near_fib = abs(row["close"] - fib) <= 1.5 * atr
            touched_bb = row["low"] <= bb_low and row["close"] > bb_low  # closed back inside
            return near_fib and touched_bb

        if regime == "trending_bearish":
            fib = row.get("fib_618_short")
            bb_up = row.get("bb_upper")
            if pd.isna(fib) or pd.isna(bb_up):
                return False
            near_fib = abs(row["close"] - fib) <= 1.5 * atr
            touched_bb = row["high"] >= bb_up and row["close"] < bb_up
            return near_fib and touched_bb

        return False

    def generate_signal(self, df: pd.DataFrame, idx: int, context: dict) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        atr = row.get("atr", 0)
        instrument = context.get("instrument", "UNKNOWN")
        timeframe = context.get("timeframe", Timeframe.H1)

        if regime == "trending_bullish":
            direction = Direction.LONG
            entry = row["close"]
            sl = row["fib_786_long"]
            tp1 = row["bb_mid"]
            tp2 = row["fib_ext_long"]
        else:
            direction = Direction.SHORT
            entry = row["close"]
            sl = row["fib_786_short"]
            tp1 = row["bb_mid"]
            tp2 = row["fib_ext_short"]

        if pd.isna(sl) or pd.isna(tp1) or pd.isna(tp2):
            return None
        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        if risk <= 0 or reward / risk < 0.8:
            return None

        return self.validate_signal(Signal(
            timestamp=df.index[idx] if hasattr(df.index[idx], "isoformat") else pd.Timestamp.now(),
            instrument=instrument, timeframe=timeframe, strategy_id=self.strategy_id,
            direction=direction, setup_type="fib_bb_exhaustion", entry_type="market",
            proposed_entry=entry, stop_loss=sl, take_profit_primary=tp1, take_profit_secondary=tp2,
            confidence_score=min(0.65 + (0.1 if reward/risk >= 1.5 else 0), 1.0),
            regime_label=regime,
            rationale_summary=f"Fib 61.8% + BB exhaustion {direction.value}, R:R {reward/risk:.1f}",
            supporting_factors=["Fib 61.8% confluence with BB", "Candle closed inside band", f"R:R {reward/risk:.1f}"],
        ), context)

    def validate_signal(self, signal: Signal, context: dict) -> Signal:
        return signal

    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        return TradePlan(
            entry_price=signal.proposed_entry, stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary] + ([signal.take_profit_secondary] if signal.take_profit_secondary else []),
            max_bars_in_trade=self.max_bars, partial_close_pcts=[0.5],
        )

    def manage_position(self, position: dict, df: pd.DataFrame, idx: int, context: dict) -> dict:
        return position

    def generate_exit(self, position: dict, df: pd.DataFrame, idx: int, context: dict) -> ExitReason | None:
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

    def score_confidence(self, signal, context: dict) -> float:
        return 0.65

    def explain_decision(self, context: dict) -> str:
        return "Fib 61.8% aligned with Bollinger Band extremity — exhaustion confirmed by candle closing back inside band."
