"""BOT-09: Golden Cloud Confluence strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.fibonacci import FibonacciRetracement
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.indicators.swing import SwingDetector
from fibokei.strategies.base import Strategy


class GoldenCloudConfluence(Strategy):
    """Fibonacci retracement levels that overlap with the Kumo.

    Enters when the 50% or 61.8% Fibonacci retracement level aligns
    with the Kumo cloud boundary, creating a high-probability
    confluence zone for reversals.
    """

    def __init__(self, confluence_atr_tolerance: float = 0.5):
        self.confluence_atr_tolerance = confluence_atr_tolerance

    @property
    def strategy_id(self) -> str:
        return "bot09_golden_cloud"

    @property
    def strategy_name(self) -> str:
        return "Golden Cloud Confluence"

    @property
    def strategy_family(self) -> str:
        return "hybrid"

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def valid_market_regimes(self) -> list[str]:
        return [
            "pullback_bullish", "pullback_bearish",
            "trending_bullish", "trending_bearish",
        ]

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr", "swing_detector", "fibonacci_retracement", "market_regime"]

    def prepare_data(self, df):
        return df.copy()

    def compute_indicators(self, df):
        df = IchimokuCloud().compute(df)
        df = ATR().compute(df)
        df = SwingDetector().compute(df)
        df = FibonacciRetracement().compute(df)
        df = MarketRegime().compute(df)
        return df

    def detect_market_regime(self, df, idx):
        return df["regime"].iloc[idx]

    def _fib_cloud_confluence(self, df, idx) -> dict | None:
        """Check if a key Fib level overlaps with the Kumo boundary."""
        row = df.iloc[idx]
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]
        atr_val = row["atr"]

        if any(pd.isna(v) for v in [span_a, span_b, atr_val]):
            return None

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)
        tol = self.confluence_atr_tolerance * atr_val

        # Check 50% and 61.8% levels
        for fib_col, fib_label in [("fib_05", "50%"), ("fib_0618", "61.8%")]:
            if fib_col not in df.columns:
                continue
            fib_val = row.get(fib_col)
            if pd.isna(fib_val):
                continue
            # Fib level near cloud top (support in uptrend)
            if abs(fib_val - cloud_top) <= tol:
                return {"fib_level": fib_label, "zone": "cloud_top", "price": fib_val}
            # Fib level near cloud bottom (resistance in downtrend)
            if abs(fib_val - cloud_bottom) <= tol:
                return {"fib_level": fib_label, "zone": "cloud_bottom", "price": fib_val}

        return None

    def detect_setup(self, df, idx, context) -> bool:
        if idx < 78:
            return False

        row = df.iloc[idx]
        close = row["close"]
        tenkan = row["tenkan_sen"]
        kijun = row["kijun_sen"]
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]

        if any(pd.isna(v) for v in [close, tenkan, kijun, span_a, span_b]):
            return False

        confluence = self._fib_cloud_confluence(df, idx)
        if confluence is None:
            return False

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

        # Bullish: price pulling back to cloud from above, TK bullish
        if close >= cloud_bottom and close <= cloud_top * 1.02 and tenkan > kijun:
            context["direction"] = Direction.LONG
            context["confluence"] = confluence
            return True

        # Bearish: price pulling back to cloud from below, TK bearish
        if close <= cloud_top and close >= cloud_bottom * 0.98 and tenkan < kijun:
            context["direction"] = Direction.SHORT
            context["confluence"] = confluence
            return True

        return False

    def generate_signal(self, df, idx, context) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        direction = context["direction"]
        close = row["close"]
        atr_val = row["atr"]
        regime = self.detect_market_regime(df, idx)

        if direction == Direction.LONG:
            stop_loss = min(row["senkou_span_a"], row["senkou_span_b"]) - atr_val
            take_profit = close + 2.5 * abs(close - stop_loss)
        else:
            stop_loss = max(row["senkou_span_a"], row["senkou_span_b"]) + atr_val
            take_profit = close - 2.5 * abs(stop_loss - close)

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="golden_cloud_confluence",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.7,
            regime_label=regime,
            supporting_factors=[
                f"Fib {context['confluence']['fib_level']} at {context['confluence']['zone']}"
            ],
        )
        return self.validate_signal(signal, context)

    def validate_signal(self, signal, context):
        if signal.regime_label in ("no_trade", "consolidation"):
            return None
        if abs(signal.proposed_entry - signal.stop_loss) < 1e-10:
            return None
        return signal

    def build_trade_plan(self, signal, context):
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            max_bars_in_trade=50,
            risk_pct=context.get("risk_pct", 1.0),
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context):
        if idx < 1:
            return None
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
        if bars >= position.get("max_bars_in_trade", 50):
            return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.7

    def explain_decision(self, context):
        return "Fibonacci retracement confluence with Kumo cloud boundary"
