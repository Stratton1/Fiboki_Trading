"""BOT-04: Chikou Open Space Momentum strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.strategies.base import Strategy


class ChikouMomentum(Strategy):
    """Momentum breakout when Chikou Span has clear open space.

    Enters aggressively when Chikou has no price/cloud obstruction
    in the 26-bar lookback zone for at least N bars.
    """

    def __init__(self, open_space_bars: int = 5):
        self.open_space_bars = open_space_bars

    @property
    def strategy_id(self) -> str:
        return "bot04_chikou_momentum"

    @property
    def strategy_name(self) -> str:
        return "Chikou Open Space Momentum"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

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

    def _chikou_has_open_space(
        self, df: pd.DataFrame, idx: int, direction: Direction
    ) -> bool:
        """Check if Chikou Span has open space (no obstruction) for N bars."""
        # Chikou at bar idx is plotted at idx-26, so we check
        # price and cloud at bars [idx-26-N+1 ... idx-26]
        chikou_idx = idx - 26
        if chikou_idx < self.open_space_bars:
            return False

        current_close = df["close"].iloc[idx]

        for j in range(chikou_idx - self.open_space_bars + 1, chikou_idx + 1):
            if j < 0 or j >= len(df):
                return False
            hist_high = df["high"].iloc[j]
            hist_low = df["low"].iloc[j]
            span_a = df["senkou_span_a"].iloc[j]
            span_b = df["senkou_span_b"].iloc[j]

            if any(pd.isna(v) for v in [hist_high, hist_low]):
                return False

            # Check if chikou (current close) is obstructed by price or cloud
            if direction == Direction.LONG:
                # Chikou must be above price and cloud
                if current_close <= hist_high:
                    return False
                if not pd.isna(span_a) and current_close <= max(span_a, span_b):
                    return False
            else:
                if current_close >= hist_low:
                    return False
                if not pd.isna(span_a) and current_close >= min(span_a, span_b):
                    return False

        return True

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
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

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

        # Bullish: price above cloud, TK bullish
        if close > cloud_top and tenkan > kijun:
            if self._chikou_has_open_space(df, idx, Direction.LONG):
                context["direction"] = Direction.LONG
                return True

        # Bearish: price below cloud, TK bearish
        if close < cloud_bottom and tenkan < kijun:
            if self._chikou_has_open_space(df, idx, Direction.SHORT):
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
        regime = self.detect_market_regime(df, idx)

        if direction == Direction.LONG:
            stop_loss = row["tenkan_sen"] - 0.5 * atr_val
            take_profit = close + 3 * atr_val
        else:
            stop_loss = row["tenkan_sen"] + 0.5 * atr_val
            take_profit = close - 3 * atr_val

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="chikou_open_space",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.55,
            regime_label=regime,
        )
        return self.validate_signal(signal, context)

    def validate_signal(self, signal, context) -> Signal | None:
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
            max_bars_in_trade=60,
            risk_pct=context.get("risk_pct", 1.0),
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context) -> ExitReason | None:
        if idx < 1:
            return None
        # Tenkan trailing: exit on close through Tenkan
        close = df["close"].iloc[idx]
        tenkan = df["tenkan_sen"].iloc[idx]
        if pd.isna(tenkan):
            return None

        direction = position.get("direction")
        if direction == Direction.LONG and close < tenkan:
            return ExitReason.INDICATOR_INVALIDATION_EXIT
        if direction == Direction.SHORT and close > tenkan:
            return ExitReason.INDICATOR_INVALIDATION_EXIT

        bars = position.get("bars_in_trade", 0)
        if bars >= position.get("max_bars_in_trade", 60):
            return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.55

    def explain_decision(self, context):
        return "Chikou open space momentum breakout"
