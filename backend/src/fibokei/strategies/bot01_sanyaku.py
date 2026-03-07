"""BOT-01: Pure Sanyaku Confluence strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.strategies.base import Strategy


class PureSanyakuConfluence(Strategy):
    """Pure Sanyaku Confluence — all three Ichimoku confirmations must align.

    Entry requires simultaneous:
    1. Price closed above/below Kumo (cloud)
    2. Tenkan-sen crossed above/below Kijun-sen (within last 3 bars)
    3. Chikou Span is above/below price from 26 periods ago
    """

    @property
    def strategy_id(self) -> str:
        return "bot01_sanyaku"

    @property
    def strategy_name(self) -> str:
        return "Pure Sanyaku Confluence"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

    @property
    def complexity_level(self) -> str:
        return "standard"

    @property
    def supports_long(self) -> bool:
        return True

    @property
    def supports_short(self) -> bool:
        return True

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish", "breakout_candidate"]

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr", "market_regime"]

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = IchimokuCloud().compute(df)
        df = ATR().compute(df)
        df = MarketRegime().compute(df)
        return df

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        return df["regime"].iloc[idx]

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        """Check all three Sanyaku conditions."""
        if idx < 78:  # Need Ichimoku warmup
            return False

        row = df.iloc[idx]
        close = row["close"]
        tenkan = row["tenkan_sen"]
        kijun = row["kijun_sen"]
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]

        # Check for NaN
        if any(pd.isna(v) for v in [close, tenkan, kijun, span_a, span_b]):
            return False

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

        # Determine direction
        bullish = close > cloud_top and tenkan > kijun
        bearish = close < cloud_bottom and tenkan < kijun

        if not bullish and not bearish:
            return False

        # Check TK cross within last 3 bars
        tk_cross_found = False
        for j in range(max(0, idx - 2), idx + 1):
            if j < 1:
                continue
            tk_curr = df["tenkan_sen"].iloc[j]
            kj_curr = df["kijun_sen"].iloc[j]
            tk_prev = df["tenkan_sen"].iloc[j - 1]
            kj_prev = df["kijun_sen"].iloc[j - 1]
            if any(pd.isna(v) for v in [tk_curr, kj_curr, tk_prev, kj_prev]):
                continue
            if bullish and tk_prev <= kj_prev and tk_curr > kj_curr:
                tk_cross_found = True
                break
            if bearish and tk_prev >= kj_prev and tk_curr < kj_curr:
                tk_cross_found = True
                break

        if not tk_cross_found:
            return False

        # Check Chikou Span confirmation
        # Chikou at bar idx is close[idx+26] (future), but we check
        # if current close is above/below the close from 26 bars ago
        if idx < 26:
            return False
        price_26_ago = df["close"].iloc[idx - 26]
        if pd.isna(price_26_ago):
            return False

        if bullish and close <= price_26_ago:
            return False
        if bearish and close >= price_26_ago:
            return False

        context["direction"] = Direction.LONG if bullish else Direction.SHORT
        return True

    def generate_signal(
        self, df: pd.DataFrame, idx: int, context: dict
    ) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        direction = context["direction"]
        atr_val = row["atr"]
        kijun = row["kijun_sen"]
        close = row["close"]
        regime = self.detect_market_regime(df, idx)

        # Stop loss: kijun or 1.5x ATR, whichever is wider
        if direction == Direction.LONG:
            stop_kijun = kijun
            stop_atr = close - 1.5 * atr_val
            stop_loss = min(stop_kijun, stop_atr)
        else:
            stop_kijun = kijun
            stop_atr = close + 1.5 * atr_val
            stop_loss = max(stop_kijun, stop_atr)

        # Take profit: 2x risk distance
        risk = abs(close - stop_loss)
        if direction == Direction.LONG:
            take_profit = close + 2 * risk
        else:
            take_profit = close - 2 * risk

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="sanyaku_confluence",
            entry_type="market",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.0,  # Will be scored
            regime_label=regime,
        )

        signal = self.validate_signal(signal, context)
        if not signal.signal_valid:
            return None

        signal.confidence_score = self.score_confidence(signal, context)
        return signal

    def validate_signal(self, signal: Signal, context: dict) -> Signal:
        if signal.regime_label in ("no_trade", "consolidation"):
            signal.signal_valid = False
            signal.invalidation_reason = f"Regime {signal.regime_label} not allowed"
            return signal

        if abs(signal.proposed_entry - signal.stop_loss) < 1e-10:
            signal.signal_valid = False
            signal.invalidation_reason = "Zero risk distance"
            return signal

        return signal

    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            max_bars_in_trade=50,
            risk_pct=context.get("risk_pct", 1.0),
        )

    def manage_position(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> dict:
        return position

    def generate_exit(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> ExitReason | None:
        if idx < 1:
            return None

        direction = position.get("direction")
        tk = df["tenkan_sen"].iloc[idx]
        kj = df["kijun_sen"].iloc[idx]
        tk_prev = df["tenkan_sen"].iloc[idx - 1]
        kj_prev = df["kijun_sen"].iloc[idx - 1]

        if any(pd.isna(v) for v in [tk, kj, tk_prev, kj_prev]):
            return None

        # TK cross reversal
        if direction == Direction.LONG:
            if tk_prev >= kj_prev and tk < kj:
                return ExitReason.OPPOSITE_SIGNAL_EXIT
        elif direction == Direction.SHORT:
            if tk_prev <= kj_prev and tk > kj:
                return ExitReason.OPPOSITE_SIGNAL_EXIT

        # Chikou crosses back through price
        close = df["close"].iloc[idx]
        if idx >= 26:
            price_26_ago = df["close"].iloc[idx - 26]
            if not pd.isna(price_26_ago):
                if direction == Direction.LONG and close < price_26_ago:
                    return ExitReason.INDICATOR_INVALIDATION_EXIT
                if direction == Direction.SHORT and close > price_26_ago:
                    return ExitReason.INDICATOR_INVALIDATION_EXIT

        # Time stop
        bars_held = position.get("bars_in_trade", 0)
        max_bars = position.get("max_bars_in_trade", 50)
        if bars_held >= max_bars:
            return ExitReason.TIME_STOP_EXIT

        return None

    def score_confidence(self, signal: Signal, context: dict) -> float:
        score = 0.5  # Base score for Sanyaku confluence

        # Bonus for trending regime
        if signal.regime_label in ("trending_bullish", "trending_bearish"):
            score += 0.2
        elif signal.regime_label == "breakout_candidate":
            score += 0.1

        return min(score, 1.0)

    def explain_decision(self, context: dict) -> str:
        direction = context.get("direction", "unknown")
        return f"Sanyaku confluence {direction}: all three conditions confirmed"
