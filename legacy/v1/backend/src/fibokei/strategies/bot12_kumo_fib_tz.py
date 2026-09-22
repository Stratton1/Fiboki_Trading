"""BOT-12: Kumo Twist + Fibonacci Time Zone Anticipator strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.fibonacci import FibonacciTimeZones
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.indicators.swing import SwingDetector
from fibokei.strategies.base import Strategy


class KumoFibTimeZone(Strategy):
    """Advanced timing strategy combining Kumo twist with Fibonacci time zones.

    Enters when a projected Kumo twist coincides with a Fibonacci time zone
    projection from the last significant swing. This dual timing confluence
    increases the probability that a reversal or continuation move is imminent.
    """

    @property
    def strategy_id(self) -> str:
        return "bot12_kumo_fib_tz"

    @property
    def strategy_name(self) -> str:
        return "Kumo Twist + Fibonacci Time Zone"

    @property
    def strategy_family(self) -> str:
        return "hybrid"

    @property
    def complexity_level(self) -> str:
        return "advanced"

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def valid_market_regimes(self) -> list[str]:
        return [
            "reversal_candidate", "breakout_candidate",
            "trending_bullish", "trending_bearish",
            "volatility_expansion",
        ]

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr", "swing_detector", "fibonacci_time_zones", "market_regime"]

    def prepare_data(self, df):
        return df.copy()

    def compute_indicators(self, df):
        df = IchimokuCloud().compute(df)
        df = ATR().compute(df)
        df = SwingDetector().compute(df)
        df = FibonacciTimeZones().compute(df)
        df = MarketRegime().compute(df)
        return df

    def detect_market_regime(self, df, idx):
        return df["regime"].iloc[idx]

    def _cloud_twist_ahead(self, df, idx) -> str | None:
        """Check if span A/B relationship reverses within next few bars."""
        if idx < 78 or idx + 1 >= len(df):
            return None

        sa_now = df["senkou_span_a"].iloc[idx]
        sb_now = df["senkou_span_b"].iloc[idx]
        if pd.isna(sa_now) or pd.isna(sb_now):
            return None

        for offset in range(1, min(6, len(df) - idx)):
            sa_fut = df["senkou_span_a"].iloc[idx + offset]
            sb_fut = df["senkou_span_b"].iloc[idx + offset]
            if pd.isna(sa_fut) or pd.isna(sb_fut):
                continue
            if (sa_now > sb_now) and (sa_fut < sb_fut):
                return "bearish_twist"
            if (sa_now < sb_now) and (sa_fut > sb_fut):
                return "bullish_twist"

        return None

    def _is_fib_time_zone(self, df, idx) -> bool:
        """Check if current bar is at a Fibonacci time zone."""
        if "fib_time_zone" not in df.columns:
            return False
        return bool(df["fib_time_zone"].iloc[idx])

    def detect_setup(self, df, idx, context) -> bool:
        if idx < 78:
            return False

        # Dual timing: both Kumo twist and Fib time zone
        twist = self._cloud_twist_ahead(df, idx)
        if twist is None:
            return False

        if not self._is_fib_time_zone(df, idx):
            return False

        row = df.iloc[idx]
        close = row["close"]
        tenkan = row["tenkan_sen"]
        kijun = row["kijun_sen"]

        if any(pd.isna(v) for v in [close, tenkan, kijun]):
            return False

        if twist == "bullish_twist" and tenkan >= kijun:
            context["direction"] = Direction.LONG
            context["twist"] = twist
            return True
        if twist == "bearish_twist" and tenkan <= kijun:
            context["direction"] = Direction.SHORT
            context["twist"] = twist
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

        if pd.isna(atr_val):
            return None

        if direction == Direction.LONG:
            stop_loss = close - 2.0 * atr_val
            take_profit = close + 3.0 * atr_val
        else:
            stop_loss = close + 2.0 * atr_val
            take_profit = close - 3.0 * atr_val

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="kumo_fib_time_zone",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.6,
            regime_label=regime,
            supporting_factors=[
                f"Cloud twist: {context['twist']}",
                "Fibonacci time zone confluence",
            ],
        )
        return self.validate_signal(signal, context)

    def validate_signal(self, signal, context):
        if signal.regime_label == "no_trade":
            return None
        if abs(signal.proposed_entry - signal.stop_loss) < 1e-10:
            return None
        return signal

    def build_trade_plan(self, signal, context):
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            max_bars_in_trade=45,
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
        if bars >= position.get("max_bars_in_trade", 45):
            return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.6

    def explain_decision(self, context):
        return "Kumo twist + Fibonacci time zone dual timing confluence"
