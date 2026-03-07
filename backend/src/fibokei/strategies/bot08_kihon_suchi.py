"""BOT-08: Kihon Suchi Time Cycle Confluence strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.indicators.swing import SwingDetector
from fibokei.strategies.base import Strategy

KIHON_NUMBERS = [9, 17, 26, 33, 42, 65, 76]


class KihonSuchiCycle(Strategy):
    """Time-cycle confluence using Ichimoku Kihon Suchi numbers.

    Boosts entry confidence when a breakout or bounce aligns with key
    Ichimoku time counts (9, 17, 26, etc.) from the last swing.
    """

    def __init__(self, tolerance: int = 1):
        self.tolerance = tolerance

    @property
    def strategy_id(self) -> str:
        return "bot08_kihon_suchi"

    @property
    def strategy_name(self) -> str:
        return "Kihon Suchi Time Cycle Confluence"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

    @property
    def valid_market_regimes(self) -> list[str]:
        return [
            "trending_bullish", "trending_bearish",
            "pullback_bullish", "pullback_bearish", "breakout_candidate",
        ]

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr", "swing_detector", "market_regime"]

    def prepare_data(self, df):
        return df.copy()

    def compute_indicators(self, df):
        df = IchimokuCloud().compute(df)
        df = ATR().compute(df)
        df = SwingDetector().compute(df)
        df = MarketRegime().compute(df)
        return df

    def detect_market_regime(self, df, idx):
        return df["regime"].iloc[idx]

    def _bars_since_last_swing(self, df, idx) -> int | None:
        """Count bars since the most recent swing point."""
        for i in range(idx, max(-1, idx - 100), -1):
            if not pd.isna(df["swing_high"].iloc[i]) or not pd.isna(df["swing_low"].iloc[i]):
                return idx - i
        return None

    def _is_kihon_aligned(self, bars_since: int) -> bool:
        """Check if bar count aligns with a Kihon Suchi number."""
        for num in KIHON_NUMBERS:
            if abs(bars_since - num) <= self.tolerance:
                return True
        return False

    def detect_setup(self, df, idx, context) -> bool:
        if idx < 78:
            return False

        bars_since = self._bars_since_last_swing(df, idx)
        if bars_since is None or not self._is_kihon_aligned(bars_since):
            return False

        row = df.iloc[idx]
        close = row["close"]
        tenkan = row["tenkan_sen"]
        kijun = row["kijun_sen"]
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]

        if any(pd.isna(v) for v in [close, tenkan, kijun, span_a, span_b]):
            return False

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

        # Need trend context
        if close > cloud_top and tenkan > kijun:
            context["direction"] = Direction.LONG
            return True
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
            stop_loss = kijun - 0.5 * atr_val
            take_profit = close + 2 * abs(close - stop_loss)
        else:
            stop_loss = kijun + 0.5 * atr_val
            take_profit = close - 2 * abs(stop_loss - close)

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="kihon_suchi_cycle",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.6,
            regime_label=regime,
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
        return 0.6

    def explain_decision(self, context):
        return "Kihon Suchi time cycle alignment with trend"
