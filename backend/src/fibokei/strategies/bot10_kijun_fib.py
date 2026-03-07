"""BOT-10: Kijun + 38.2% Shallow Continuation strategy."""

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


class KijunFibContinuation(Strategy):
    """Shallow pullback continuation using Kijun + Fib 38.2% confluence.

    Enters trend continuations where the pullback is shallow (reaching
    only the 38.2% retracement) and the Kijun-sen provides additional
    support/resistance confirmation.
    """

    def __init__(self, kijun_tolerance_atr: float = 0.5):
        self.kijun_tolerance_atr = kijun_tolerance_atr

    @property
    def strategy_id(self) -> str:
        return "bot10_kijun_fib"

    @property
    def strategy_name(self) -> str:
        return "Kijun + 38.2% Shallow Continuation"

    @property
    def strategy_family(self) -> str:
        return "hybrid"

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def valid_market_regimes(self) -> list[str]:
        return [
            "trending_bullish", "trending_bearish",
            "pullback_bullish", "pullback_bearish",
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

    def _kijun_near_fib382(self, df, idx) -> bool:
        """Check if Kijun-sen is near the 38.2% Fibonacci level."""
        row = df.iloc[idx]
        kijun = row["kijun_sen"]
        atr_val = row["atr"]

        if "fib_0382" not in df.columns:
            return False
        fib_382 = row.get("fib_0382")
        if any(pd.isna(v) for v in [kijun, atr_val, fib_382]):
            return False

        tol = self.kijun_tolerance_atr * atr_val
        return abs(kijun - fib_382) <= tol

    def detect_setup(self, df, idx, context) -> bool:
        if idx < 78:
            return False

        row = df.iloc[idx]
        close = row["close"]
        tenkan = row["tenkan_sen"]
        kijun = row["kijun_sen"]
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]
        atr_val = row["atr"]

        if any(pd.isna(v) for v in [close, tenkan, kijun, span_a, span_b, atr_val]):
            return False

        if not self._kijun_near_fib382(df, idx):
            return False

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

        # Price near Kijun (shallow pullback zone)
        if abs(close - kijun) > self.kijun_tolerance_atr * atr_val:
            return False

        # Bullish: price above cloud, near Kijun from above
        if close > cloud_top and tenkan > kijun:
            context["direction"] = Direction.LONG
            return True

        # Bearish: price below cloud, near Kijun from below
        if close < cloud_bottom and tenkan < kijun:
            context["direction"] = Direction.SHORT
            return True

        return False

    def generate_signal(self, df, idx, context) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        direction = context["direction"]
        close = row["close"]
        atr_val = row["atr"]
        kijun = row["kijun_sen"]
        regime = self.detect_market_regime(df, idx)

        if direction == Direction.LONG:
            stop_loss = kijun - 1.0 * atr_val
            take_profit = close + 2.0 * abs(close - stop_loss)
        else:
            stop_loss = kijun + 1.0 * atr_val
            take_profit = close - 2.0 * abs(stop_loss - close)

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="kijun_fib_382_continuation",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.65,
            regime_label=regime,
            supporting_factors=["Kijun + Fib 38.2% confluence"],
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
            max_bars_in_trade=40,
            risk_pct=context.get("risk_pct", 1.0),
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context):
        if idx < 1:
            return None
        close = df["close"].iloc[idx]
        tenkan = df["tenkan_sen"].iloc[idx]
        if pd.isna(tenkan):
            return None
        direction = position.get("direction")
        # Tenkan trailing stop
        if direction == Direction.LONG and close < tenkan:
            return ExitReason.TRAILING_STOP_HIT
        if direction == Direction.SHORT and close > tenkan:
            return ExitReason.TRAILING_STOP_HIT
        bars = position.get("bars_in_trade", 0)
        if bars >= position.get("max_bars_in_trade", 40):
            return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.65

    def explain_decision(self, context):
        return "Kijun + Fibonacci 38.2% shallow continuation setup"
