"""BOT-15: 38.2% Momentum Continuation — shallow Fibonacci pullback + MACD.

Identifies aggressive trends via 20/50 EMA, waits for a shallow pullback
into the 23.6%-50% Fibonacci zone (centered on 38.2%), and enters when
MACD histogram confirms momentum resumption. Wider catch zone = more trades.

SL at 78.6%, TP1 at 0% (swing extreme, 50% close + breakeven), TP2 at
127.2% extension. MACD cross bailout if momentum reverses before TP1.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class MomentumContinuation(Strategy):
    """Shallow Fibonacci pullback (23.6%-50%) with MACD momentum trigger."""

    def __init__(
        self,
        swing_lookback: int = 30,
        ema_fast: int = 20,
        ema_slow: int = 50,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        max_bars: int = 30,
    ):
        self.swing_lookback = swing_lookback
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.max_bars = max_bars

    # ── Identity ──────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "bot15_momentum_cont"

    @property
    def strategy_name(self) -> str:
        return "38.2% Momentum Continuation"

    @property
    def strategy_family(self) -> str:
        return "fibonacci"

    @property
    def description(self) -> str:
        return (
            "Shallow Fibonacci pullback (23.6%-50%) in EMA-confirmed trends "
            "with MACD histogram cross as momentum trigger. Wider zone = more trades."
        )

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish"]

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return [Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1]

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def complexity_level(self) -> str:
        return "standard"

    def get_required_indicators(self) -> list[str]:
        return ["ema_20", "ema_50", "macd", "fibonacci", "atr"]

    # ── Data Preparation ──────────────────────────────────────

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # EMAs
        df["ema_fast"] = df["close"].ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.ema_slow, adjust=False).mean()

        # MACD
        ema12 = df["close"].ewm(span=self.macd_fast, adjust=False).mean()
        ema26 = df["close"].ewm(span=self.macd_slow, adjust=False).mean()
        df["macd_line"] = ema12 - ema26
        df["macd_signal"] = df["macd_line"].ewm(span=self.macd_signal, adjust=False).mean()
        df["macd_hist"] = df["macd_line"] - df["macd_signal"]
        df["macd_hist_prev"] = df["macd_hist"].shift(1)
        df["macd_cross_bull"] = (df["macd_hist"] > 0) & (df["macd_hist_prev"] <= 0)
        df["macd_cross_bear"] = (df["macd_hist"] < 0) & (df["macd_hist_prev"] >= 0)

        # Swing high/low over lookback
        df["swing_high"] = df["high"].rolling(window=self.swing_lookback).max()
        df["swing_low"] = df["low"].rolling(window=self.swing_lookback).min()

        # Fibonacci levels
        swing_range = df["swing_high"] - df["swing_low"]
        valid = swing_range > 0

        # Long side: retracement from swing_high down
        df["fib_236_long"] = np.where(valid, df["swing_high"] - 0.236 * swing_range, np.nan)
        df["fib_382_long"] = np.where(valid, df["swing_high"] - 0.382 * swing_range, np.nan)
        df["fib_50_long"] = np.where(valid, df["swing_high"] - 0.500 * swing_range, np.nan)
        df["fib_786_long"] = np.where(valid, df["swing_high"] - 0.786 * swing_range, np.nan)
        df["fib_0_long"] = df["swing_high"]  # TP1
        df["fib_ext_long"] = np.where(valid, df["swing_high"] + 0.272 * swing_range, np.nan)  # 127.2% ext

        # Short side: retracement from swing_low up
        df["fib_236_short"] = np.where(valid, df["swing_low"] + 0.236 * swing_range, np.nan)
        df["fib_382_short"] = np.where(valid, df["swing_low"] + 0.382 * swing_range, np.nan)
        df["fib_50_short"] = np.where(valid, df["swing_low"] + 0.500 * swing_range, np.nan)
        df["fib_786_short"] = np.where(valid, df["swing_low"] + 0.786 * swing_range, np.nan)
        df["fib_0_short"] = df["swing_low"]  # TP1
        df["fib_ext_short"] = np.where(valid, df["swing_low"] - 0.272 * swing_range, np.nan)  # 127.2% ext

        # ATR
        df = ATR(period=14).compute(df)

        return df

    # ── Regime Detection ──────────────────────────────────────

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        row = df.iloc[idx]
        if pd.isna(row.get("ema_fast")) or pd.isna(row.get("ema_slow")):
            return "unknown"
        if row["ema_fast"] > row["ema_slow"]:
            return "trending_bullish"
        if row["ema_fast"] < row["ema_slow"]:
            return "trending_bearish"
        return "ranging"

    # ── Setup Detection ───────────────────────────────────────

    def _pullback_in_zone_long(self, df: pd.DataFrame, idx: int) -> bool:
        """Current or previous bar low touched the 23.6%-50% zone."""
        for i in [idx, idx - 1]:
            if i < 0:
                continue
            row = df.iloc[i]
            low = row["low"]
            fib_236 = row.get("fib_236_long")
            fib_50 = row.get("fib_50_long")
            if pd.isna(fib_236) or pd.isna(fib_50):
                continue
            # fib_236 > fib_50 (both below swing high)
            if fib_50 <= low <= fib_236:
                return True
        return False

    def _pullback_in_zone_short(self, df: pd.DataFrame, idx: int) -> bool:
        """Current or previous bar high touched the 23.6%-50% zone."""
        for i in [idx, idx - 1]:
            if i < 0:
                continue
            row = df.iloc[i]
            high = row["high"]
            fib_236 = row.get("fib_236_short")
            fib_50 = row.get("fib_50_short")
            if pd.isna(fib_236) or pd.isna(fib_50):
                continue
            # fib_236 < fib_50 (both above swing low)
            if fib_236 <= high <= fib_50:
                return True
        return False

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < self.ema_slow + 5:
            return False
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)

        if regime == "trending_bullish":
            return self._pullback_in_zone_long(df, idx) and bool(row.get("macd_cross_bull", False))
        if regime == "trending_bearish":
            return self._pullback_in_zone_short(df, idx) and bool(row.get("macd_cross_bear", False))
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
        timeframe = context.get("timeframe", Timeframe.M15)

        if regime == "trending_bullish":
            direction = Direction.LONG
            entry = row["close"]
            sl = row["fib_786_long"]
            tp1 = row["fib_0_long"]  # Swing high
            tp2 = row["fib_ext_long"]  # 127.2% extension
        else:
            direction = Direction.SHORT
            entry = row["close"]
            sl = row["fib_786_short"]
            tp1 = row["fib_0_short"]  # Swing low
            tp2 = row["fib_ext_short"]  # 127.2% extension

        if pd.isna(sl) or pd.isna(tp1) or pd.isna(tp2):
            return None

        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        if risk <= 0 or reward / risk < 0.8:
            return None

        signal = Signal(
            timestamp=df.index[idx] if hasattr(df.index[idx], "isoformat") else pd.Timestamp.now(),
            instrument=instrument,
            timeframe=timeframe,
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="momentum_continuation_382",
            entry_type="market",
            proposed_entry=entry,
            stop_loss=sl,
            take_profit_primary=tp1,
            take_profit_secondary=tp2,
            confidence_score=self.score_confidence(None, {
                "regime": regime, "rr": reward / risk,
                "macd_hist": row.get("macd_hist", 0),
            }),
            regime_label=regime,
            rationale_summary=f"38.2% momentum pullback {direction.value}: MACD trigger, R:R {reward/risk:.1f}",
            supporting_factors=[
                f"EMA trend: 20>{50 if regime == 'trending_bullish' else '20<50'}",
                "Price pulled back to 23.6%-50% zone",
                "MACD histogram cross confirmed",
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
            trailing_stop_rule="breakeven_after_tp1",
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

        # MACD reversal bailout (before TP1)
        macd_cross_bull = row.get("macd_cross_bull", False)
        macd_cross_bear = row.get("macd_cross_bear", False)
        if direction == "LONG" and macd_cross_bear and bars >= 2:
            return ExitReason.INDICATOR_INVALIDATION_EXIT
        if direction == "SHORT" and macd_cross_bull and bars >= 2:
            return ExitReason.INDICATOR_INVALIDATION_EXIT

        # Time stop
        if bars >= self.max_bars:
            return ExitReason.TIME_STOP_EXIT

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
        macd = abs(context.get("macd_hist", 0))
        if macd > 0:
            score += 0.10
        return min(score, 1.0)

    def explain_decision(self, context: dict) -> str:
        return (
            "38.2% Momentum Continuation: Price made a shallow pullback into the "
            "23.6%-50% Fibonacci zone within an EMA-confirmed trend. MACD histogram "
            "cross confirmed momentum is resuming in the trend direction."
        )
