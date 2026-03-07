"""BOT-07: Kumo Twist Anticipator strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.strategies.base import Strategy


class KumoTwistAnticipator(Strategy):
    """Forward-looking reversal strategy using projected cloud twist.

    Detects projected cloud twist 26 periods ahead, confirms price is
    extended from Kijun, and enters counter-trend on TK cross confirmation.
    """

    @property
    def strategy_id(self) -> str:
        return "bot07_kumo_twist"

    @property
    def strategy_name(self) -> str:
        return "Kumo Twist Anticipator"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

    @property
    def complexity_level(self) -> str:
        return "high"

    @property
    def valid_market_regimes(self) -> list[str]:
        return [
            "reversal_candidate", "volatility_expansion",
            "trending_bullish", "trending_bearish",
        ]

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr", "market_regime"]

    def prepare_data(self, df):
        return df.copy()

    def compute_indicators(self, df):
        df = IchimokuCloud().compute(df)
        df = ATR().compute(df)
        df = MarketRegime().compute(df)
        return df

    def detect_market_regime(self, df, idx):
        return df["regime"].iloc[idx]

    def _cloud_twist_ahead(self, df, idx) -> str | None:
        """Check if Senkou Span A and B cross within upcoming bars.

        Since spans are shifted forward by 26, the current bar's span values
        at idx represent the projected cloud at idx. We check if the relationship
        between span_a and span_b reverses compared to current.
        """
        if idx < 78 or idx + 1 >= len(df):
            return None

        sa_now = df["senkou_span_a"].iloc[idx]
        sb_now = df["senkou_span_b"].iloc[idx]

        if pd.isna(sa_now) or pd.isna(sb_now):
            return None

        # Check a few bars ahead for twist
        for offset in range(1, min(6, len(df) - idx)):
            sa_fut = df["senkou_span_a"].iloc[idx + offset]
            sb_fut = df["senkou_span_b"].iloc[idx + offset]
            if pd.isna(sa_fut) or pd.isna(sb_fut):
                continue
            # Twist: relationship reverses
            if (sa_now > sb_now) and (sa_fut < sb_fut):
                return "bearish_twist"  # Cloud turning bearish → potential short
            if (sa_now < sb_now) and (sa_fut > sb_fut):
                return "bullish_twist"  # Cloud turning bullish → potential long

        return None

    def detect_setup(self, df, idx, context) -> bool:
        if idx < 78 or idx < 2:
            return False

        twist = self._cloud_twist_ahead(df, idx)
        if twist is None:
            return False

        row = df.iloc[idx]
        close = row["close"]
        kijun = row["kijun_sen"]
        tenkan = row["tenkan_sen"]
        atr_val = row["atr"]

        if any(pd.isna(v) for v in [close, kijun, tenkan, atr_val]):
            return False

        # Price must be extended from Kijun (>2 ATR)
        dist = abs(close - kijun)
        if dist < 2.0 * atr_val:
            return False

        # Counter-direction TK cross confirms
        tk_prev = df["tenkan_sen"].iloc[idx - 1]
        kj_prev = df["kijun_sen"].iloc[idx - 1]
        if pd.isna(tk_prev) or pd.isna(kj_prev):
            return False

        if twist == "bullish_twist":
            # Need bullish TK cross (counter to bearish trend)
            if tk_prev <= kj_prev and tenkan > kijun:
                context["direction"] = Direction.LONG
                return True
        elif twist == "bearish_twist":
            if tk_prev >= kj_prev and tenkan < kijun:
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
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]
        regime = self.detect_market_regime(df, idx)

        # Target: mean reversion to cloud
        cloud_mid = (span_a + span_b) / 2 if not pd.isna(span_a) and not pd.isna(span_b) else close

        if direction == Direction.LONG:
            stop_loss = row.get("last_swing_low", close - 2 * atr_val)
            if pd.isna(stop_loss):
                stop_loss = close - 2 * atr_val
            take_profit = cloud_mid
        else:
            stop_loss = row.get("last_swing_high", close + 2 * atr_val)
            if pd.isna(stop_loss):
                stop_loss = close + 2 * atr_val
            take_profit = cloud_mid

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="kumo_twist_reversal",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.5,
            regime_label=regime,
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
            max_bars_in_trade=40,
            risk_pct=context.get("risk_pct", 1.0),
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context):
        bars = position.get("bars_in_trade", 0)
        if bars >= position.get("max_bars_in_trade", 40):
            return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.5

    def explain_decision(self, context):
        return "Kumo twist anticipation with counter-trend TK cross"
