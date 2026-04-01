"""BOT-22: Fibonacci & Volume Profile Confluence — institutional level trading.

Enters when a Fibonacci retracement (50% or 61.8%) aligns with a High
Volume Node (HVN) — a price level where significant trading volume
accumulated. This indicates institutional support/resistance.

Uses a simplified Volume Profile: rolling volume-weighted price
distribution to identify HVN zones. SL below HVN, TP at POC/VAH.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class FibVolumeConfluence(Strategy):
    """Fibonacci + Volume Profile High Volume Node confluence."""

    def __init__(self, swing_lookback: int = 50, vol_profile_period: int = 100, num_bins: int = 20, max_bars: int = 40):
        self.swing_lookback = swing_lookback
        self.vol_profile_period = vol_profile_period
        self.num_bins = num_bins
        self.max_bars = max_bars

    @property
    def strategy_id(self) -> str:
        return "bot22_fib_volume"

    @property
    def strategy_name(self) -> str:
        return "Fibonacci & Volume Profile Confluence"

    @property
    def strategy_family(self) -> str:
        return "hybrid"

    @property
    def description(self) -> str:
        return "Fibonacci retracement confluent with Volume Profile HVN for institutional-level entries."

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish"]

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return [Timeframe.H1, Timeframe.H4]

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def complexity_level(self) -> str:
        return "advanced"

    def get_required_indicators(self) -> list[str]:
        return ["fibonacci", "volume_profile", "atr"]

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def _compute_volume_profile(self, df: pd.DataFrame, idx: int) -> dict:
        """Compute simplified volume profile for the last N bars.
        Returns dict with poc (Point of Control), vah (Value Area High),
        val (Value Area Low), and hvn_levels."""
        start = max(0, idx - self.vol_profile_period)
        window = df.iloc[start:idx + 1]
        if len(window) < 20:
            return {}

        has_vol = "volume" in df.columns and window["volume"].sum() > 0

        price_low = window["low"].min()
        price_high = window["high"].max()
        if price_high <= price_low:
            return {}

        bin_size = (price_high - price_low) / self.num_bins
        if bin_size <= 0:
            return {}

        # Build volume histogram
        bins = np.zeros(self.num_bins)
        bin_prices = np.array([price_low + (i + 0.5) * bin_size for i in range(self.num_bins)])

        for _, row in window.iterrows():
            typical = (row["high"] + row["low"] + row["close"]) / 3
            vol = row.get("volume", 1) if has_vol else 1
            bin_idx = min(int((typical - price_low) / bin_size), self.num_bins - 1)
            bins[bin_idx] += vol

        if bins.sum() == 0:
            return {}

        # POC = bin with highest volume
        poc_idx = bins.argmax()
        poc = bin_prices[poc_idx]

        # Value Area = 70% of total volume centered on POC
        total_vol = bins.sum()
        target = total_vol * 0.70
        cumulative = bins[poc_idx]
        lo, hi = poc_idx, poc_idx
        while cumulative < target and (lo > 0 or hi < self.num_bins - 1):
            lo_vol = bins[lo - 1] if lo > 0 else 0
            hi_vol = bins[hi + 1] if hi < self.num_bins - 1 else 0
            if lo_vol >= hi_vol and lo > 0:
                lo -= 1
                cumulative += lo_vol
            elif hi < self.num_bins - 1:
                hi += 1
                cumulative += hi_vol
            else:
                break

        val = bin_prices[lo]
        vah = bin_prices[hi]

        # HVN = bins with volume > 1.5x average
        avg_vol = bins.mean()
        hvn_levels = [bin_prices[i] for i in range(self.num_bins) if bins[i] > 1.5 * avg_vol]

        return {"poc": poc, "vah": vah, "val": val, "hvn_levels": hvn_levels}

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # EMA trend
        df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

        # Swing + Fibonacci
        df["swing_high"] = df["high"].rolling(window=self.swing_lookback).max()
        df["swing_low"] = df["low"].rolling(window=self.swing_lookback).min()
        sr = df["swing_high"] - df["swing_low"]
        valid = sr > 0
        df["fib_50_long"] = np.where(valid, df["swing_high"] - 0.500 * sr, np.nan)
        df["fib_618_long"] = np.where(valid, df["swing_high"] - 0.618 * sr, np.nan)
        df["fib_786_long"] = np.where(valid, df["swing_high"] - 0.786 * sr, np.nan)
        df["fib_50_short"] = np.where(valid, df["swing_low"] + 0.500 * sr, np.nan)
        df["fib_618_short"] = np.where(valid, df["swing_low"] + 0.618 * sr, np.nan)
        df["fib_786_short"] = np.where(valid, df["swing_low"] + 0.786 * sr, np.nan)

        df = ATR(period=14).compute(df)
        return df

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        row = df.iloc[idx]
        if pd.isna(row.get("ema_200")):
            return "unknown"
        return "trending_bullish" if row["close"] > row["ema_200"] else "trending_bearish"

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < 210:
            return False
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        atr = row.get("atr", 0)
        if pd.isna(atr) or atr <= 0:
            return False

        vp = self._compute_volume_profile(df, idx)
        if not vp:
            return False

        hvn_levels = vp.get("hvn_levels", [])
        if not hvn_levels:
            return False

        self._vp = vp

        if regime == "trending_bullish":
            for fib_col in ["fib_50_long", "fib_618_long"]:
                fib = row.get(fib_col)
                if pd.isna(fib):
                    continue
                # Check if any HVN aligns with fib level
                for hvn in hvn_levels:
                    if abs(hvn - fib) <= atr:
                        # Price must be near this zone
                        if abs(row["close"] - fib) <= 1.5 * atr:
                            # Price bouncing (close > open for bullish)
                            if row["close"] > row["open"]:
                                return True

        if regime == "trending_bearish":
            for fib_col in ["fib_50_short", "fib_618_short"]:
                fib = row.get(fib_col)
                if pd.isna(fib):
                    continue
                for hvn in hvn_levels:
                    if abs(hvn - fib) <= atr:
                        if abs(row["close"] - fib) <= 1.5 * atr:
                            if row["close"] < row["open"]:
                                return True

        return False

    def generate_signal(self, df: pd.DataFrame, idx: int, context: dict) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        atr = row.get("atr", 0)
        instrument = context.get("instrument", "UNKNOWN")
        timeframe = context.get("timeframe", Timeframe.H4)
        vp = self._vp

        if regime == "trending_bullish":
            direction = Direction.LONG
            entry = row["close"]
            sl = row["fib_786_long"] - 0.5 * atr
            tp1 = vp.get("vah", entry + 2 * atr)
            tp2 = vp.get("poc", entry + 3 * atr)
        else:
            direction = Direction.SHORT
            entry = row["close"]
            sl = row["fib_786_short"] + 0.5 * atr
            tp1 = vp.get("val", entry - 2 * atr)
            tp2 = vp.get("poc", entry - 3 * atr)

        if pd.isna(sl):
            return None
        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        if risk <= 0 or reward / risk < 0.8:
            return None

        return self.validate_signal(Signal(
            timestamp=df.index[idx] if hasattr(df.index[idx], "isoformat") else pd.Timestamp.now(),
            instrument=instrument, timeframe=timeframe, strategy_id=self.strategy_id,
            direction=direction, setup_type="fib_volume_confluence", entry_type="market",
            proposed_entry=entry, stop_loss=sl, take_profit_primary=tp1,
            take_profit_secondary=tp2,
            confidence_score=min(0.65 + (0.1 if reward/risk >= 1.5 else 0), 1.0),
            regime_label=regime,
            rationale_summary=f"Fib + Volume Profile HVN {direction.value}: institutional level, R:R {reward/risk:.1f}",
            supporting_factors=["Fib level aligns with HVN", "Volume shelf support/resistance", f"POC at {vp.get('poc',0):.4f}"],
        ), context)

    def validate_signal(self, signal, context):
        return signal

    def build_trade_plan(self, signal, context):
        return TradePlan(
            entry_price=signal.proposed_entry, stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary] + ([signal.take_profit_secondary] if signal.take_profit_secondary else []),
            max_bars_in_trade=self.max_bars,
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context):
        row = df.iloc[idx]
        d, sl, tp = position.get("direction"), position.get("stop_loss", 0), position.get("take_profit_targets", [])
        bars = position.get("bars_in_trade", 0)
        if d == "LONG" and row["low"] <= sl: return ExitReason.STOP_LOSS_HIT
        if d == "SHORT" and row["high"] >= sl: return ExitReason.STOP_LOSS_HIT
        if tp:
            if d == "LONG" and row["high"] >= tp[0]: return ExitReason.TAKE_PROFIT_HIT
            if d == "SHORT" and row["low"] <= tp[0]: return ExitReason.TAKE_PROFIT_HIT
        if bars >= self.max_bars: return ExitReason.TIME_STOP_EXIT
        return None

    def score_confidence(self, signal, context):
        return 0.65

    def explain_decision(self, context):
        return "Fib + Volume Profile: Fibonacci level aligned with institutional High Volume Node creating strong support/resistance."
