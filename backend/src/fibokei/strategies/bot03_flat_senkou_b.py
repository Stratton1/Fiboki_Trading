"""BOT-03: Flat Senkou Span B Bounce strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.strategies.base import Strategy


class FlatSenkouBBounce(Strategy):
    """Cloud equilibrium bounce at flat Senkou Span B levels.

    Detects flat Span B (low slope) and trades bounces off that level.
    """

    def __init__(self, flat_lookback: int = 10, flat_threshold: float = 0.1):
        self.flat_lookback = flat_lookback
        self.flat_threshold = flat_threshold

    @property
    def strategy_id(self) -> str:
        return "bot03_flat_senkou_b"

    @property
    def strategy_name(self) -> str:
        return "Flat Senkou Span B Bounce"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["consolidation", "pullback_bullish", "pullback_bearish", "breakout_candidate"]

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

    def _is_span_b_flat(self, df: pd.DataFrame, idx: int) -> bool:
        """Check if Senkou Span B is flat over the lookback window."""
        if idx < self.flat_lookback:
            return False
        span_b_vals = df["senkou_span_b"].iloc[idx - self.flat_lookback : idx + 1]
        if span_b_vals.isna().any():
            return False
        atr_val = df["atr"].iloc[idx]
        if pd.isna(atr_val) or atr_val < 1e-10:
            return False
        # Flat if range of span_b values < threshold * ATR
        span_range = span_b_vals.max() - span_b_vals.min()
        return span_range < self.flat_threshold * atr_val

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < 78:
            return False

        if not self._is_span_b_flat(df, idx):
            return False

        row = df.iloc[idx]
        close = row["close"]
        span_b = row["senkou_span_b"]
        span_a = row["senkou_span_a"]
        atr_val = row["atr"]

        if any(pd.isna(v) for v in [close, span_b, span_a, atr_val]):
            return False

        # Price must be near the flat Span B level (within 0.5 ATR)
        distance = abs(close - span_b)
        if distance > 0.5 * atr_val:
            return False

        # Determine direction based on approach side
        if close > span_b:
            context["direction"] = Direction.LONG  # Bouncing up off support
        else:
            context["direction"] = Direction.SHORT  # Bouncing down off resistance

        return True

    def generate_signal(self, df: pd.DataFrame, idx: int, context: dict) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        direction = context["direction"]
        close = row["close"]
        atr_val = row["atr"]
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]
        regime = self.detect_market_regime(df, idx)

        if direction == Direction.LONG:
            stop_loss = min(span_a, span_b) - 0.5 * atr_val
            take_profit = close + 2 * abs(close - stop_loss)
        else:
            stop_loss = max(span_a, span_b) + 0.5 * atr_val
            take_profit = close - 2 * abs(stop_loss - close)

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="flat_senkou_b_bounce",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.5,
            regime_label=regime,
        )
        return self.validate_signal(signal, context)

    def validate_signal(self, signal: Signal, context: dict) -> Signal | None:
        if signal.regime_label == "no_trade":
            return None
        if abs(signal.proposed_entry - signal.stop_loss) < 1e-10:
            return None
        return signal

    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            max_bars_in_trade=30,
            risk_pct=context.get("risk_pct", 1.0),
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context) -> ExitReason | None:
        if idx < 1:
            return None

        close = df["close"].iloc[idx]
        span_a = df["senkou_span_a"].iloc[idx]
        span_b = df["senkou_span_b"].iloc[idx]
        if any(pd.isna(v) for v in [span_a, span_b]):
            return None

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)
        direction = position.get("direction")

        if direction == Direction.LONG and close < cloud_bottom:
            return ExitReason.INDICATOR_INVALIDATION_EXIT
        if direction == Direction.SHORT and close > cloud_top:
            return ExitReason.INDICATOR_INVALIDATION_EXIT

        bars = position.get("bars_in_trade", 0)
        if bars >= position.get("max_bars_in_trade", 30):
            return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.5

    def explain_decision(self, context):
        return "Flat Senkou Span B bounce trade"
