"""BOT-17: Gartley Harmonic Reversal — 5-point XABCD pattern detection.

Identifies Gartley harmonic patterns using swing point analysis:
- B at 61.8% of XA
- C at 38.2%-88.6% of AB
- D at 78.6% of XA (aligned with 127.2% BC extension)

Enters at the Potential Reversal Zone (D point) with RSI confirmation.
SL at X point + ATR buffer. TP1 at 38.2% AD, TP2 at 61.8% AD, TP3 trailing.
"""

import numpy as np
import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.strategies.base import Strategy


class GartleyHarmonicReversal(Strategy):
    """Gartley XABCD harmonic pattern with RSI reversal confirmation."""

    def __init__(
        self,
        fractal_period: int = 10,
        b_tolerance: float = 0.05,
        c_min: float = 0.382,
        c_max: float = 0.886,
        d_tolerance: float = 0.05,
        rsi_period: int = 14,
        rsi_oversold: int = 30,
        rsi_overbought: int = 70,
        max_bars: int = 50,
    ):
        self.fractal_period = fractal_period
        self.b_tolerance = b_tolerance  # tolerance around 61.8%
        self.c_min = c_min
        self.c_max = c_max
        self.d_tolerance = d_tolerance  # tolerance around 78.6%
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.max_bars = max_bars

    # ── Identity ──────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "bot17_gartley"

    @property
    def strategy_name(self) -> str:
        return "Gartley Harmonic Reversal"

    @property
    def strategy_family(self) -> str:
        return "fibonacci"

    @property
    def description(self) -> str:
        return (
            "Gartley XABCD harmonic pattern detection at the Potential Reversal "
            "Zone with RSI confirmation. Counter-trend precision entries."
        )

    @property
    def valid_market_regimes(self) -> list[str]:
        return ["trending_bullish", "trending_bearish", "ranging"]

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return [Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4]

    @property
    def requires_fibonacci(self) -> bool:
        return True

    @property
    def complexity_level(self) -> str:
        return "advanced"

    def get_required_indicators(self) -> list[str]:
        return ["fractals", "rsi", "atr", "harmonic_xabcd"]

    # ── Data Preparation ──────────────────────────────────────

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def _detect_swings(self, df: pd.DataFrame) -> list[tuple[int, float, str]]:
        """Detect swing highs and lows using fractal logic.
        Returns list of (index, price, type) where type is 'H' or 'L'."""
        swings = []
        half = self.fractal_period // 2
        n = len(df)
        for i in range(half, n - half):
            is_high = all(
                df["high"].iloc[i] >= df["high"].iloc[j]
                for j in range(i - half, i + half + 1)
                if j != i
            )
            is_low = all(
                df["low"].iloc[i] <= df["low"].iloc[j]
                for j in range(i - half, i + half + 1)
                if j != i
            )
            if is_high:
                swings.append((i, df["high"].iloc[i], "H"))
            if is_low:
                swings.append((i, df["low"].iloc[i], "L"))
        return swings

    def _find_gartley(self, swings: list, idx: int, direction: str) -> dict | None:
        """Try to find a Gartley pattern ending near idx.

        For bullish: X(low) -> A(high) -> B(low) -> C(high) -> D(low near idx)
        For bearish: X(high) -> A(low) -> B(high) -> C(low) -> D(high near idx)
        """
        # Need at least 4 preceding alternating swings
        # Get the last 5 alternating swing points before idx
        if direction == "bullish":
            # D is a low, C is high, B is low, A is high, X is low
            pattern_types = ["L", "H", "L", "H", "L"]  # X, A, B, C, D
        else:
            # D is a high, C is low, B is high, A is low, X is high
            pattern_types = ["H", "L", "H", "L", "H"]

        # Collect recent swings up to idx
        relevant = [s for s in swings if s[0] <= idx]
        if len(relevant) < 5:
            return None

        # Try the last 5 swings that alternate correctly
        candidates = relevant[-8:]  # look at last 8 swings for flexibility
        filtered = []
        for s in reversed(candidates):
            if not filtered:
                if s[2] == pattern_types[4]:  # D type
                    filtered.append(s)
            elif len(filtered) == 1:
                if s[2] == pattern_types[3]:  # C type
                    filtered.append(s)
            elif len(filtered) == 2:
                if s[2] == pattern_types[2]:  # B type
                    filtered.append(s)
            elif len(filtered) == 3:
                if s[2] == pattern_types[1]:  # A type
                    filtered.append(s)
            elif len(filtered) == 4:
                if s[2] == pattern_types[0]:  # X type
                    filtered.append(s)
                    break

        if len(filtered) != 5:
            return None

        # Reverse so it's X, A, B, C, D order
        filtered.reverse()
        x_idx, x_price, _ = filtered[0]
        a_idx, a_price, _ = filtered[1]
        b_idx, b_price, _ = filtered[2]
        c_idx, c_price, _ = filtered[3]
        d_idx, d_price, _ = filtered[4]

        # D must be near current idx
        if abs(d_idx - idx) > self.fractal_period + 2:
            return None

        # Validate Fibonacci ratios
        xa_range = abs(a_price - x_price)
        if xa_range == 0:
            return None

        # B at ~61.8% of XA
        if direction == "bullish":
            b_ratio = (a_price - b_price) / xa_range
        else:
            b_ratio = (b_price - a_price) / xa_range

        if abs(b_ratio - 0.618) > self.b_tolerance:
            return None

        # C at 38.2%-88.6% of AB
        ab_range = abs(b_price - a_price)
        if ab_range == 0:
            return None

        if direction == "bullish":
            c_ratio = (c_price - b_price) / ab_range
        else:
            c_ratio = (b_price - c_price) / ab_range

        if c_ratio < self.c_min or c_ratio > self.c_max:
            return None

        # D at ~78.6% of XA
        if direction == "bullish":
            d_ratio = (a_price - d_price) / xa_range
        else:
            d_ratio = (d_price - a_price) / xa_range

        if abs(d_ratio - 0.786) > self.d_tolerance:
            return None

        # Also check D aligns with ~127.2% BC extension
        bc_range = abs(c_price - b_price)
        if bc_range > 0:
            if direction == "bullish":
                d_bc_ext = (c_price - d_price) / bc_range
            else:
                d_bc_ext = (d_price - c_price) / bc_range
            # Allow wider tolerance for extension alignment
            if abs(d_bc_ext - 1.272) > 0.3:
                return None

        return {
            "x": x_price, "a": a_price, "b": b_price,
            "c": c_price, "d": d_price,
            "x_idx": x_idx, "d_idx": d_idx,
            "b_ratio": b_ratio, "c_ratio": c_ratio,
            "d_ratio": d_ratio,
        }

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # RSI
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(span=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(span=self.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["rsi_prev"] = df["rsi"].shift(1)

        # ATR
        df = ATR(period=14).compute(df)

        # Detect swings and store for later use
        self._swings = self._detect_swings(df)

        return df

    # ── Regime Detection ──────────────────────────────────────

    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        # Gartley works in any regime — it's a reversal pattern
        return "harmonic_setup"

    # ── Setup Detection ───────────────────────────────────────

    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        if idx < 60:
            return False
        row = df.iloc[idx]
        rsi = row.get("rsi", 50)
        rsi_prev = row.get("rsi_prev", 50)
        if pd.isna(rsi) or pd.isna(rsi_prev):
            return False

        # Try bullish Gartley
        bull = self._find_gartley(self._swings, idx, "bullish")
        if bull and rsi_prev < self.rsi_oversold and rsi >= self.rsi_oversold:
            self._current_pattern = bull
            self._current_direction = "bullish"
            return True

        # Try bearish Gartley
        bear = self._find_gartley(self._swings, idx, "bearish")
        if bear and rsi_prev > self.rsi_overbought and rsi <= self.rsi_overbought:
            self._current_pattern = bear
            self._current_direction = "bearish"
            return True

        return False

    # ── Signal Generation ─────────────────────────────────────

    def generate_signal(
        self, df: pd.DataFrame, idx: int, context: dict
    ) -> Signal | None:
        if not self.detect_setup(df, idx, context):
            return None

        row = df.iloc[idx]
        pat = self._current_pattern
        atr = row.get("atr", 0)
        if pd.isna(atr) or atr <= 0:
            return None

        instrument = context.get("instrument", "UNKNOWN")
        timeframe = context.get("timeframe", Timeframe.H1)

        ad_range = abs(pat["a"] - pat["d"])

        if self._current_direction == "bullish":
            direction = Direction.LONG
            entry = row["close"]
            sl = pat["x"] - atr  # Below X + ATR buffer
            tp1 = pat["d"] + 0.382 * ad_range  # 38.2% of AD
            tp2 = pat["d"] + 0.618 * ad_range  # 61.8% of AD
        else:
            direction = Direction.SHORT
            entry = row["close"]
            sl = pat["x"] + atr  # Above X + ATR buffer
            tp1 = pat["d"] - 0.382 * ad_range
            tp2 = pat["d"] - 0.618 * ad_range

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
            setup_type="gartley_harmonic",
            entry_type="market",
            proposed_entry=entry,
            stop_loss=sl,
            take_profit_primary=tp1,
            take_profit_secondary=tp2,
            confidence_score=self.score_confidence(None, {
                "rr": reward / risk,
                "b_ratio": pat["b_ratio"],
                "d_ratio": pat["d_ratio"],
                "rsi": row["rsi"],
            }),
            regime_label=f"gartley_{self._current_direction}",
            rationale_summary=f"Gartley {self._current_direction}: D at {pat['d_ratio']:.3f} XA, B at {pat['b_ratio']:.3f} XA, RSI trigger",
            supporting_factors=[
                f"B at {pat['b_ratio']:.3f} of XA (target 0.618)",
                f"C at {pat['c_ratio']:.3f} of AB",
                f"D at {pat['d_ratio']:.3f} of XA (target 0.786)",
                f"RSI reversal at {row['rsi']:.0f}",
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
            break_even_rule="move_to_entry_after_tp1",
            max_bars_in_trade=self.max_bars,
            partial_close_pcts=[0.5, 0.3],  # 50% at TP1, 30% at TP2, 20% trails
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

        # Stop loss
        if direction == "LONG" and low <= sl:
            return ExitReason.STOP_LOSS_HIT
        if direction == "SHORT" and high >= sl:
            return ExitReason.STOP_LOSS_HIT

        # Take profit
        if tp_targets:
            if direction == "LONG" and high >= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT
            if direction == "SHORT" and low <= tp_targets[0]:
                return ExitReason.TAKE_PROFIT_HIT

        # Time stop
        if bars >= self.max_bars:
            return ExitReason.TIME_STOP_EXIT

        return None

    # ── Scoring & Explanation ─────────────────────────────────

    def score_confidence(self, signal, context: dict) -> float:
        score = 0.5
        rr = context.get("rr", 1.0)
        if rr >= 2.0:
            score += 0.15
        elif rr >= 1.5:
            score += 0.10
        # Tighter B ratio = more precise pattern
        b_dev = abs(context.get("b_ratio", 0.618) - 0.618)
        if b_dev < 0.02:
            score += 0.15
        elif b_dev < 0.04:
            score += 0.10
        # Tighter D ratio
        d_dev = abs(context.get("d_ratio", 0.786) - 0.786)
        if d_dev < 0.02:
            score += 0.10
        return min(score, 1.0)

    def explain_decision(self, context: dict) -> str:
        return (
            "Gartley Harmonic Reversal: Identified a valid XABCD pattern where "
            "B terminates at 61.8% of XA and D completes at 78.6% of XA with "
            "127.2% BC extension confluence. RSI confirmed the reversal at the "
            "Potential Reversal Zone."
        )
