"""BOT-02: Kijun-sen Pullback strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.candles import CandlestickPatterns
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.indicators.swing import SwingDetector
from fibokei.strategies.base import Strategy


class KijunPullback(Strategy):
    """Trend continuation using Kijun as pullback support/resistance.

    Waits for retracement to Kijun, then enters on reversal candle
    confirmation for better reward-to-risk than breakout entry.
    """

    @property
    def strategy_id(self) -> str:
        return "bot02_kijun_pullback"

    @property
    def strategy_name(self) -> str:
        return "Kijun-sen Pullback"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["pullback_bullish", "pullback_bearish", "trending_bullish", "trending_bearish"]

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr", "swing_detector", "candlestick_patterns", "market_regime"]

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = IchimokuCloud().compute(df)
        df = ATR().compute(df)
        df = SwingDetector().compute(df)
        df = CandlestickPatterns().compute(df)
        df = MarketRegime().compute(df)
        return df

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        return df["regime"].iloc[idx]

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < 78:
            return False

        row = df.iloc[idx]
        close = row["close"]
        kijun = row["kijun_sen"]
        tenkan = row["tenkan_sen"]
        atr_val = row["atr"]
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]

        if any(pd.isna(v) for v in [close, kijun, tenkan, atr_val, span_a, span_b]):
            return False

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

        # Bullish pullback: price above cloud, tenkan > kijun, price near kijun
        if close > cloud_top and tenkan > kijun:
            distance = abs(close - kijun)
            if distance < 0.5 * atr_val:
                # Reversal candle confirmation
                if row.get("bullish_pin_bar", False) or row.get("bullish_engulfing", False):
                    context["direction"] = Direction.LONG
                    return True

        # Bearish pullback: price below cloud, tenkan < kijun, price near kijun
        if close < cloud_bottom and tenkan < kijun:
            distance = abs(close - kijun)
            if distance < 0.5 * atr_val:
                if row.get("bearish_pin_bar", False) or row.get("bearish_engulfing", False):
                    context["direction"] = Direction.SHORT
                    return True

        return False

    def generate_signal(self, df: pd.DataFrame, idx: int, context: dict) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        direction = context["direction"]
        atr_val = row["atr"]
        kijun = row["kijun_sen"]
        close = row["close"]
        regime = self.detect_market_regime(df, idx)

        if direction == Direction.LONG:
            stop_loss = kijun - atr_val
            last_sh = row.get("last_swing_high")
            take_profit = last_sh if not pd.isna(last_sh) else close + 2 * atr_val
        else:
            stop_loss = kijun + atr_val
            last_sl = row.get("last_swing_low")
            take_profit = last_sl if not pd.isna(last_sl) else close - 2 * atr_val

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="kijun_pullback",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.6,
            regime_label=regime,
        )
        return self.validate_signal(signal, context)

    def validate_signal(self, signal: Signal, context: dict) -> Signal | None:
        if signal.regime_label in ("no_trade", "consolidation"):
            return None
        if abs(signal.proposed_entry - signal.stop_loss) < 1e-10:
            return None
        return signal

    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            max_bars_in_trade=40,
            risk_pct=context.get("risk_pct", 1.0),
        )

    def manage_position(self, position: dict, df: pd.DataFrame, idx: int, context: dict) -> dict:
        return position

    def generate_exit(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> ExitReason | None:
        if idx < 1:
            return None
        # Kijun trailing: exit if close crosses Kijun against direction
        close = df["close"].iloc[idx]
        kijun = df["kijun_sen"].iloc[idx]
        if pd.isna(kijun):
            return None

        direction = position.get("direction")
        if direction == Direction.LONG and close < kijun:
            return ExitReason.INDICATOR_INVALIDATION_EXIT
        if direction == Direction.SHORT and close > kijun:
            return ExitReason.INDICATOR_INVALIDATION_EXIT

        bars = position.get("bars_in_trade", 0)
        if bars >= position.get("max_bars_in_trade", 40):
            return ExitReason.TIME_STOP_EXIT

        return None

    def score_confidence(self, signal: Signal, context: dict) -> float:
        return 0.6

    def explain_decision(self, context: dict) -> str:
        return "Kijun pullback with reversal candle confirmation"
