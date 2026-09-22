"""BOT-06: N-Wave Structural Targeting strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.indicators.swing import SwingDetector
from fibokei.strategies.base import Strategy


class NWaveStructural(Strategy):
    """Wave-structure continuation with Hosoda-style N-wave target projection.

    Identifies A-B-C structure, enters at point C pivot, targets N = C + (B - A).
    """

    @property
    def strategy_id(self) -> str:
        return "bot06_nwave"

    @property
    def strategy_name(self) -> str:
        return "N-Wave Structural Targeting"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

    @property
    def valid_market_regimes(self) -> list[str]:
        return [
            "trending_bullish", "trending_bearish",
            "pullback_bullish", "pullback_bearish",
        ]

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr", "swing_detector", "market_regime"]

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = IchimokuCloud().compute(df)
        df = ATR().compute(df)
        df = SwingDetector().compute(df)
        df = MarketRegime().compute(df)
        return df

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        return df["regime"].iloc[idx]

    def _find_abc(self, df: pd.DataFrame, idx: int) -> dict | None:
        """Find most recent A-B-C wave structure from swing points."""
        # Collect recent swing points before idx
        swings = []
        for i in range(max(0, idx - 60), idx + 1):
            sh = df["swing_high"].iloc[i]
            sl = df["swing_low"].iloc[i]
            if not pd.isna(sh):
                swings.append(("high", i, sh))
            if not pd.isna(sl):
                swings.append(("low", i, sl))

        if len(swings) < 3:
            return None

        # Take last 3 swings as potential A-B-C
        a_type, a_idx, a_val = swings[-3]
        b_type, b_idx, b_val = swings[-2]
        c_type, c_idx, c_val = swings[-1]

        # Bullish N-wave: A=low, B=high, C=higher low
        if a_type == "low" and b_type == "high" and c_type == "low":
            if b_val > a_val and c_val > a_val and c_val < b_val:
                target = c_val + (b_val - a_val)
                return {
                    "direction": Direction.LONG,
                    "a": a_val, "b": b_val, "c": c_val,
                    "c_idx": c_idx, "target": target,
                }

        # Bearish N-wave: A=high, B=low, C=lower high
        if a_type == "high" and b_type == "low" and c_type == "high":
            if b_val < a_val and c_val < a_val and c_val > b_val:
                target = c_val - (a_val - b_val)
                return {
                    "direction": Direction.SHORT,
                    "a": a_val, "b": b_val, "c": c_val,
                    "c_idx": c_idx, "target": target,
                }

        return None

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < 78:
            return False

        wave = self._find_abc(df, idx)
        if wave is None:
            return False

        # C must be recent (within last 10 bars)
        if idx - wave["c_idx"] > 10:
            return False

        # Price must be moving away from C in the expected direction
        close = df["close"].iloc[idx]
        if wave["direction"] == Direction.LONG and close <= wave["c"]:
            return False
        if wave["direction"] == Direction.SHORT and close >= wave["c"]:
            return False

        context["direction"] = wave["direction"]
        context["wave"] = wave
        return True

    def generate_signal(self, df, idx, context) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        direction = context["direction"]
        wave = context["wave"]
        close = row["close"]
        atr_val = row["atr"]
        regime = self.detect_market_regime(df, idx)

        if direction == Direction.LONG:
            stop_loss = wave["c"] - 0.5 * atr_val
            take_profit = wave["target"]
        else:
            stop_loss = wave["c"] + 0.5 * atr_val
            take_profit = wave["target"]

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="nwave_structural",
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
        bars = position.get("bars_in_trade", 0)
        if bars >= position.get("max_bars_in_trade", 60):
            return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.55

    def explain_decision(self, context):
        wave = context.get("wave", {})
        return f"N-Wave {context.get('direction', '?')}: target={wave.get('target', '?')}"
