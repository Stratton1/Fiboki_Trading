"""BOT-05: MTFA Sanyaku strategy."""

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.strategies.base import Strategy


class MTFASanyaku(Strategy):
    """Multi-timeframe Sanyaku: higher TF filter + lower TF execution.

    Reduces false signals by requiring macro trend alignment on a
    higher timeframe before taking Sanyaku entries on the execution TF.
    Default: H4 filter → H1 execution.
    """

    def __init__(self, htf_multiplier: int = 4):
        self.htf_multiplier = htf_multiplier

    @property
    def strategy_id(self) -> str:
        return "bot05_mtfa_sanyaku"

    @property
    def strategy_name(self) -> str:
        return "MTFA Sanyaku"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

    @property
    def requires_mtfa(self) -> bool:
        return True

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish", "breakout_candidate"]

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr", "market_regime"]

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # Compute execution timeframe indicators
        df = IchimokuCloud().compute(df)
        df = ATR().compute(df)
        df = MarketRegime().compute(df)

        # Compute higher timeframe indicators by resampling
        htf = df[["open", "high", "low", "close", "volume"]].copy()
        htf_resampled = htf.resample(f"{self.htf_multiplier}h").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()

        if len(htf_resampled) > 52:
            ichimoku_htf = IchimokuCloud()
            htf_resampled = ichimoku_htf.compute(htf_resampled)

            # Forward-fill HTF values to execution timeframe
            for col in ["tenkan_sen", "kijun_sen", "senkou_span_a", "senkou_span_b"]:
                htf_col = f"htf_{col}"
                df[htf_col] = htf_resampled[col].reindex(df.index).ffill()
        else:
            for col in ["tenkan_sen", "kijun_sen", "senkou_span_a", "senkou_span_b"]:
                df[f"htf_{col}"] = float("nan")

        return df

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        return df["regime"].iloc[idx]

    def _htf_confirms_trend(self, df: pd.DataFrame, idx: int) -> Direction | None:
        """Check if higher TF has valid Sanyaku alignment."""
        row = df.iloc[idx]
        htf_tenkan = row.get("htf_tenkan_sen")
        htf_kijun = row.get("htf_kijun_sen")
        htf_span_a = row.get("htf_senkou_span_a")
        htf_span_b = row.get("htf_senkou_span_b")
        close = row["close"]

        if any(pd.isna(v) for v in [htf_tenkan, htf_kijun, htf_span_a, htf_span_b, close]):
            return None

        htf_cloud_top = max(htf_span_a, htf_span_b)
        htf_cloud_bottom = min(htf_span_a, htf_span_b)

        if close > htf_cloud_top and htf_tenkan > htf_kijun:
            return Direction.LONG
        if close < htf_cloud_bottom and htf_tenkan < htf_kijun:
            return Direction.SHORT
        return None

    def _ltf_sanyaku(self, df: pd.DataFrame, idx: int, required_dir: Direction) -> bool:
        """Check execution TF Sanyaku conditions."""
        if idx < 78 or idx < 1:
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

        if required_dir == Direction.LONG:
            if not (close > cloud_top and tenkan > kijun):
                return False
        else:
            if not (close < cloud_bottom and tenkan < kijun):
                return False

        # Check TK cross within last 3 bars
        for j in range(max(1, idx - 2), idx + 1):
            tk = df["tenkan_sen"].iloc[j]
            kj = df["kijun_sen"].iloc[j]
            tk_prev = df["tenkan_sen"].iloc[j - 1]
            kj_prev = df["kijun_sen"].iloc[j - 1]
            if any(pd.isna(v) for v in [tk, kj, tk_prev, kj_prev]):
                continue
            if required_dir == Direction.LONG and tk_prev <= kj_prev and tk > kj:
                return True
            if required_dir == Direction.SHORT and tk_prev >= kj_prev and tk < kj:
                return True

        return False

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < 78:
            return False

        htf_dir = self._htf_confirms_trend(df, idx)
        if htf_dir is None:
            return False

        if self._ltf_sanyaku(df, idx, htf_dir):
            context["direction"] = htf_dir
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
            stop_loss = min(kijun, close - 1.5 * atr_val)
            take_profit = close + 2 * abs(close - stop_loss)
        else:
            stop_loss = max(kijun, close + 1.5 * atr_val)
            take_profit = close - 2 * abs(stop_loss - close)

        signal = Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "UNKNOWN"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="mtfa_sanyaku",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=0.7,
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
            max_bars_in_trade=50,
            risk_pct=context.get("risk_pct", 1.0),
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context) -> ExitReason | None:
        if idx < 1:
            return None
        # Lower TF Kijun trailing stop
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
        return "MTFA Sanyaku: higher TF confirmed, lower TF entry triggered"
