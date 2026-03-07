"""BOT-11: Sanyaku + Fib Extension Targets strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.fibonacci import FibonacciExtension
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.indicators.swing import SwingDetector
from fibokei.strategies.base import Strategy


class SanyakuFibExtension(Strategy):
    """Sanyaku entry with Fibonacci extension profit targets.

    Uses Sanyaku confluence for entry timing, then manages exit
    with Fibonacci extension levels (1.0, 1.618, 2.618) for
    partial position closure at each target.
    """

    @property
    def strategy_id(self) -> str:
        return "bot11_sanyaku_fib_ext"

    @property
    def strategy_name(self) -> str:
        return "Sanyaku + Fib Extension Targets"

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
            "breakout_candidate", "volatility_expansion",
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

    def _sanyaku_conditions(self, df, idx) -> Direction | None:
        """Check three-line confluence (same as BOT-01 core logic)."""
        row = df.iloc[idx]
        close = row["close"]
        tenkan = row["tenkan_sen"]
        kijun = row["kijun_sen"]
        span_a = row["senkou_span_a"]
        span_b = row["senkou_span_b"]

        if any(pd.isna(v) for v in [close, tenkan, kijun, span_a, span_b]):
            return None

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

        # Check TK cross within last 3 bars
        tk_cross = False
        for offset in range(1, min(4, idx + 1)):
            tk_prev = df["tenkan_sen"].iloc[idx - offset]
            kj_prev = df["kijun_sen"].iloc[idx - offset]
            if pd.isna(tk_prev) or pd.isna(kj_prev):
                continue
            if (tenkan > kijun and tk_prev <= kj_prev) or (tenkan < kijun and tk_prev >= kj_prev):
                tk_cross = True
                break

        if not tk_cross:
            return None

        if close > cloud_top and tenkan > kijun:
            return Direction.LONG
        if close < cloud_bottom and tenkan < kijun:
            return Direction.SHORT

        return None

    def _find_abc_swings(self, df, idx) -> tuple | None:
        """Find A-B-C swing structure for extension calculation."""
        swings = []
        for i in range(max(0, idx - 80), idx + 1):
            sh = df["swing_high"].iloc[i]
            sl = df["swing_low"].iloc[i]
            if not pd.isna(sh):
                swings.append(("high", sh, i))
            if not pd.isna(sl):
                swings.append(("low", sl, i))

        if len(swings) < 3:
            return None

        last3 = swings[-3:]
        types = [s[0] for s in last3]
        prices = [s[1] for s in last3]

        # Bullish ABC: low, high, higher_low
        if types == ["low", "high", "low"] and prices[2] > prices[0]:
            return (prices[0], prices[1], prices[2], "bullish")

        # Bearish ABC: high, low, lower_high
        if types == ["high", "low", "high"] and prices[2] < prices[0]:
            return (prices[0], prices[1], prices[2], "bearish")

        return None

    def detect_setup(self, df, idx, context) -> bool:
        if idx < 78:
            return False

        direction = self._sanyaku_conditions(df, idx)
        if direction is None:
            return False

        abc = self._find_abc_swings(df, idx)
        if abc is None:
            return False

        a, b, c, abc_direction = abc
        # Direction must match ABC structure
        if direction == Direction.LONG and abc_direction != "bullish":
            return False
        if direction == Direction.SHORT and abc_direction != "bearish":
            return False

        # Compute extension targets
        ext = FibonacciExtension()
        levels = ext.compute_extensions(a, b, c)

        context["direction"] = direction
        context["extension_levels"] = levels
        context["abc"] = {"a": a, "b": b, "c": c}
        return True

    def generate_signal(self, df, idx, context) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        direction = context["direction"]
        close = row["close"]
        atr_val = row["atr"]
        kijun = row["kijun_sen"]
        regime = self.detect_market_regime(df, idx)
        levels = context["extension_levels"]

        if direction == Direction.LONG:
            stop_loss = kijun - 1.0 * atr_val
            take_profit = levels["1.618"]
        else:
            stop_loss = kijun + 1.0 * atr_val
            take_profit = levels["1.618"]

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="sanyaku_fib_extension",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.7,
            regime_label=regime,
            supporting_factors=[
                f"Fib ext 1.0={levels['1.0']:.5f}",
                f"Fib ext 1.618={levels['1.618']:.5f}",
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
        levels = context.get("extension_levels", {})
        targets = []
        for key in ["1.0", "1.618", "2.618"]:
            if key in levels:
                targets.append(levels[key])
        if not targets:
            targets = [signal.take_profit_primary]

        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=targets,
            max_bars_in_trade=60,
            risk_pct=context.get("risk_pct", 1.0),
            partial_close_pcts=[0.5, 0.3, 0.2] if len(targets) >= 3 else None,
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
        if bars >= position.get("max_bars_in_trade", 60):
            return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.7

    def explain_decision(self, context):
        return "Sanyaku entry with Fibonacci extension profit targets"
