"""BOT-14: Fractal Golden Pocket Scalper — HFT Fibonacci scalping.

Uses Williams Fractals to auto-identify micro swing points, draws
Fibonacci retracement between opposing fractals, and enters at the
61.8% "Golden Pocket" level with VWAP trend filter and volume
confirmation. Designed for M1/M5 scalping with tight exits.

SL at 78.6%, TP1 at 38.2% (75% close), TP2 at 0% (swing extreme).
Max 5 bars failsafe. No ATR buffering — instant cut on invalidation.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class FractalGoldenPocketScalper(Strategy):
    """HFT scalper: Fractal-based Fibonacci Golden Pocket (61.8%-65%) entries."""

    def __init__(
        self,
        fractal_period: int = 5,
        vwap_session_bars: int = 390,  # ~6.5h for M1
        volume_avg_period: int = 20,
        max_bars: int = 5,
    ):
        self.fractal_period = fractal_period
        self.vwap_session_bars = vwap_session_bars
        self.volume_avg_period = volume_avg_period
        self.max_bars = max_bars

    # ── Identity ──────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "bot14_fractal_scalper"

    @property
    def strategy_name(self) -> str:
        return "Fractal Golden Pocket Scalper"

    @property
    def strategy_family(self) -> str:
        return "fibonacci"

    @property
    def description(self) -> str:
        return (
            "HFT scalper using Williams Fractals to auto-draw Fibonacci "
            "retracement, entering at the 61.8% Golden Pocket with VWAP "
            "trend filter and volume confirmation."
        )

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish"]

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return [Timeframe.M1, Timeframe.M5, Timeframe.M15]

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def complexity_level(self) -> str:
        return "advanced"

    def get_required_indicators(self) -> list[str]:
        return ["williams_fractals", "vwap", "volume_avg", "fibonacci"]

    # ── Data Preparation ──────────────────────────────────────

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        half = self.fractal_period // 2

        # Williams Fractals (period 5: 2 bars either side)
        up_fractal = np.full(n, np.nan)
        down_fractal = np.full(n, np.nan)

        for i in range(half, n - half):
            # Up fractal: high[i] is highest of surrounding bars
            is_up = True
            for j in range(i - half, i + half + 1):
                if j == i:
                    continue
                if df["high"].iloc[j] >= df["high"].iloc[i]:
                    is_up = False
                    break
            if is_up:
                up_fractal[i] = df["high"].iloc[i]

            # Down fractal: low[i] is lowest of surrounding bars
            is_down = True
            for j in range(i - half, i + half + 1):
                if j == i:
                    continue
                if df["low"].iloc[j] <= df["low"].iloc[i]:
                    is_down = False
                    break
            if is_down:
                down_fractal[i] = df["low"].iloc[i]

        df["up_fractal"] = up_fractal
        df["down_fractal"] = down_fractal

        # Forward-fill last known fractals for reference
        df["last_up_fractal"] = df["up_fractal"].ffill()
        df["last_down_fractal"] = df["down_fractal"].ffill()

        # VWAP — cumulative (typical_price * volume) / cumulative(volume)
        if "volume" in df.columns and df["volume"].sum() > 0:
            typical = (df["high"] + df["low"] + df["close"]) / 3
            cum_tp_vol = (typical * df["volume"]).cumsum()
            cum_vol = df["volume"].cumsum().replace(0, np.nan)
            df["vwap"] = cum_tp_vol / cum_vol
        else:
            # Fallback: use 50-period EMA as pseudo-VWAP when no volume
            df["vwap"] = df["close"].ewm(span=50, adjust=False).mean()

        # Volume average
        if "volume" in df.columns:
            df["vol_avg"] = df["volume"].rolling(window=self.volume_avg_period).mean()
            df["vol_above_avg"] = df["volume"] > df["vol_avg"]
        else:
            df["vol_above_avg"] = True  # No volume data → skip filter

        # Fibonacci levels between last opposing fractals
        swing_range = df["last_up_fractal"] - df["last_down_fractal"]
        valid = swing_range > 0

        # Long fib (retracement from up_fractal down toward down_fractal)
        df["fib_618_long"] = np.where(valid, df["last_up_fractal"] - 0.618 * swing_range, np.nan)
        df["fib_650_long"] = np.where(valid, df["last_up_fractal"] - 0.650 * swing_range, np.nan)
        df["fib_786_long"] = np.where(valid, df["last_up_fractal"] - 0.786 * swing_range, np.nan)
        df["fib_382_long"] = np.where(valid, df["last_up_fractal"] - 0.382 * swing_range, np.nan)
        df["fib_0_long"] = df["last_up_fractal"]  # Swing high

        # Short fib (retracement from down_fractal up toward up_fractal)
        df["fib_618_short"] = np.where(valid, df["last_down_fractal"] + 0.618 * swing_range, np.nan)
        df["fib_650_short"] = np.where(valid, df["last_down_fractal"] + 0.650 * swing_range, np.nan)
        df["fib_786_short"] = np.where(valid, df["last_down_fractal"] + 0.786 * swing_range, np.nan)
        df["fib_382_short"] = np.where(valid, df["last_down_fractal"] + 0.382 * swing_range, np.nan)
        df["fib_0_short"] = df["last_down_fractal"]  # Swing low

        # ATR for reference (not used in SL but useful for scoring)
        df = ATR(period=14).compute(df)

        return df

    # ── Regime Detection ──────────────────────────────────────

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        row = df.iloc[idx]
        vwap = row.get("vwap")
        if pd.isna(vwap):
            return "unknown"
        if row["close"] > vwap:
            return "trending_bullish"
        if row["close"] < vwap:
            return "trending_bearish"
        return "ranging"

    # ── Setup Detection ───────────────────────────────────────

    def _in_golden_pocket_long(self, row) -> bool:
        """Price is in the 61.8%-65% zone (long side)."""
        price = row["close"]
        fib_618 = row.get("fib_618_long")
        fib_650 = row.get("fib_650_long")
        if pd.isna(fib_618) or pd.isna(fib_650):
            return False
        # fib_618 > fib_650 (both below swing high)
        return fib_650 <= price <= fib_618

    def _in_golden_pocket_short(self, row) -> bool:
        """Price is in the 61.8%-65% zone (short side)."""
        price = row["close"]
        fib_618 = row.get("fib_618_short")
        fib_650 = row.get("fib_650_short")
        if pd.isna(fib_618) or pd.isna(fib_650):
            return False
        # fib_618 < fib_650 (both above swing low)
        return fib_618 <= price <= fib_650

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < 30:
            return False
        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        vol_ok = row.get("vol_above_avg", True)

        if regime == "trending_bullish":
            return self._in_golden_pocket_long(row) and vol_ok
        if regime == "trending_bearish":
            return self._in_golden_pocket_short(row) and vol_ok
        return False

    # ── Signal Generation ─────────────────────────────────────

    def generate_signal(
        self, df: pd.DataFrame, idx: int, context: dict
    ) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        regime = self.detect_market_regime(df, idx)
        instrument = context.get("instrument", "UNKNOWN")
        timeframe = context.get("timeframe", Timeframe.M5)

        if regime == "trending_bullish":
            direction = Direction.LONG
            entry = row["fib_618_long"]  # Enter at 61.8%
            sl = row["fib_786_long"]  # SL at 78.6%
            tp1 = row["fib_382_long"]  # TP1 at 38.2%
            tp2 = row["fib_0_long"]  # TP2 at swing high (0%)
        else:
            direction = Direction.SHORT
            entry = row["fib_618_short"]
            sl = row["fib_786_short"]
            tp1 = row["fib_382_short"]
            tp2 = row["fib_0_short"]

        if pd.isna(entry) or pd.isna(sl) or pd.isna(tp1):
            return None

        # Use close as actual entry (market order simulation)
        entry = row["close"]

        # Validate risk:reward
        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        if risk <= 0 or reward / risk < 0.8:
            return None

        signal = Signal(
            timestamp=df.index[idx] if hasattr(df.index[idx], "isoformat") else pd.Timestamp.now(),
            instrument=instrument,
            timeframe=timeframe,
            strategy_id=self.strategy_id,
            direction=direction,
            setup_type="fractal_golden_pocket",
            entry_type="market",
            proposed_entry=entry,
            stop_loss=sl,
            take_profit_primary=tp1,
            take_profit_secondary=tp2,
            confidence_score=self.score_confidence(None, {
                "regime": regime, "rr": reward / risk if risk > 0 else 0,
                "vol_ok": row.get("vol_above_avg", True),
            }),
            regime_label=regime,
            rationale_summary=f"Fractal Golden Pocket {direction.value} at 61.8%: VWAP {regime}, R:R {reward/risk:.1f}",
            supporting_factors=[
                f"VWAP trend: {regime}",
                "Price at 61.8% Golden Pocket",
                f"Volume above avg: {row.get('vol_above_avg', 'N/A')}",
                f"R:R to TP1: {reward/risk:.1f}",
            ],
        )
        return self.validate_signal(signal, context)

    def validate_signal(self, signal: Signal, context: dict) -> Signal:
        return signal

    # ── Trade Plan ────────────────────────────────────────────

    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary]
            + ([signal.take_profit_secondary] if signal.take_profit_secondary else []),
            max_bars_in_trade=self.max_bars,
            partial_close_pcts=[0.75],  # Close 75% at TP1
        )

    # ── Position Management ───────────────────────────────────

    def manage_position(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> dict:
        return position

    def generate_exit(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> ExitReason | None:
        row = df.iloc[idx]
        direction = position.get("direction")
        sl = position.get("stop_loss", 0)
        tp_targets = position.get("take_profit_targets", [])
        bars = position.get("bars_in_trade", 0)

        high = row["high"]
        low = row["low"]

        # Stop loss — immediate cut, no buffer
        if direction == "LONG" and low <= sl:
            return ExitReason.STOP_LOSS_HIT
        if direction == "SHORT" and high >= sl:
            return ExitReason.STOP_LOSS_HIT

        # Take profit (TP1 at 38.2%)
        if tp_targets:
            if direction == "LONG" and high >= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT
            if direction == "SHORT" and low <= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT

        # Time stop — 5 bars max
        if bars >= self.max_bars:
            return ExitReason.TIME_STOP_EXIT

        return None

    # ── Scoring & Explanation ─────────────────────────────────

    def score_confidence(self, signal, context: dict) -> float:
        score = 0.5
        regime = context.get("regime", "")
        rr = context.get("rr", 1.0)
        if regime in ("trending_bullish", "trending_bearish"):
            score += 0.15
        if rr >= 1.5:
            score += 0.15
        elif rr >= 1.0:
            score += 0.10
        if context.get("vol_ok", False):
            score += 0.10
        return min(score, 1.0)

    def explain_decision(self, context: dict) -> str:
        return (
            "Fractal Golden Pocket Scalper: Williams Fractals identified micro "
            "swing points, Fibonacci drawn automatically. Price entered the 61.8% "
            "Golden Pocket zone with VWAP trend alignment and sufficient volume."
        )
