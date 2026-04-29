"""BOT-13: Chikou Session Guard — evidence-based evolution of BOT-04.

Derived from analysis of 389 live paper trades across BOT-04 and BOT-06.

Key findings that drive the design changes vs BOT-04:
  - indicator_invalidation_exit (Tenkan cross) fired 109 times at 32.1% win rate,
    costing £341 — BOT-04 exits the instant close crosses Tenkan (too reactive).
  - 1–2 bar trades win at 78.8%; 3+ bar trades drop to ~44% — edge degrades fast.
  - Entry hours 11:00, 18:00, 21:00 UTC hit 87–91% win rate; 00:00 and 12:00 hit
    41–40% — session timing is a significant discriminator.
  - Monday–Wednesday 65–71% win rate; Friday 53%; Sunday 17% — day filter adds edge.
  - XAGUSD: 1 winner in 10 trades (10% win rate) — excluded from this strategy.

Changes vs BOT-04:
  1. Session filter: only enter during London/NY active sessions (UTC).
  2. Tenkan grace period: require 2 consecutive closes through Tenkan before
     triggering indicator_invalidation_exit — eliminates single-bar whipsaws.
  3. Breakeven stop: after 2 bars in profit, move stop to entry price.
  4. Day filter: block Sunday entries; restrict Friday to before 14:00 UTC.
  5. Tighter max_bars_in_trade: 30 instead of 60 (edge fades after ~10 bars).
"""

from datetime import timezone

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.indicators.atr import ATR
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.strategies.base import Strategy

# UTC hours considered "active" based on win-rate analysis of 389 trades.
# Excluded: 00:00 (41.7%), 12:00–13:00 (London lunch, 40–47%), 22:00–23:59 (50%).
_ACTIVE_HOURS = frozenset(range(1, 12)) | frozenset(range(17, 22))  # 01–11, 17–21 UTC

# Minimum consecutive Tenkan crosses before triggering invalidation exit.
_TENKAN_GRACE_BARS = 2


class ChikouSessionGuard(Strategy):
    """Chikou Momentum with session timing, Tenkan grace, and breakeven stop.

    Uses identical entry logic to BOT-04 (Chikou open-space momentum) but adds
    three evidence-based filters derived from live trade analysis:

    Session filter  — avoids low-win-rate hours (midnight, London lunch).
    Tenkan grace    — requires N consecutive crosses before exiting, reducing
                      the 109 whipsaw exits that cost £341 in BOT-04.
    Breakeven stop  — after 2 profitable bars, locks in entry price as floor.
    """

    def __init__(self, open_space_bars: int = 5, tenkan_grace_bars: int = _TENKAN_GRACE_BARS):
        self.open_space_bars = open_space_bars
        self.tenkan_grace_bars = tenkan_grace_bars

    @property
    def strategy_id(self) -> str:
        return "bot13_chikou_session"

    @property
    def strategy_name(self) -> str:
        return "Chikou Session Guard"

    @property
    def strategy_family(self) -> str:
        return "ichimoku"

    @property
    def description(self) -> str:
        return (
            "Chikou Momentum entry with session-timing filter, Tenkan grace exit, "
            "and breakeven stop after 2 bars. Derived from 389-trade live analysis."
        )

    @property
    def logic_summary(self) -> str:
        return (
            "Enter: Chikou open space (same as BOT-04) + London/NY session hours + "
            "Mon–Fri before Fri 14:00 UTC. "
            "Exit: TP at 3×ATR, SL at Tenkan±0.5×ATR, breakeven after 2 bars in profit, "
            "2-bar Tenkan grace (no single-candle whipsaw exits), 30-bar time stop."
        )

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

    # ------------------------------------------------------------------ #
    #  Session / day filters                                               #
    # ------------------------------------------------------------------ #

    def _is_active_session(self, df: pd.DataFrame, idx: int) -> bool:
        """Return True only during high-win-rate UTC session hours."""
        ts = df.index[idx]
        try:
            if ts.tzinfo is None:
                hour = ts.hour
                dow = ts.weekday()
            else:
                utc_ts = ts.astimezone(timezone.utc)
                hour = utc_ts.hour
                dow = utc_ts.weekday()
        except Exception:
            return True  # fail open — don't block on timezone edge cases

        # Sunday (dow=6): historical win rate 16.7% — skip entirely
        if dow == 6:
            return False

        # Friday (dow=4): 53.2% win rate, only enter before 14:00 UTC
        if dow == 4 and hour >= 14:
            return False

        return hour in _ACTIVE_HOURS

    # ------------------------------------------------------------------ #
    #  Entry: Chikou open space (identical to BOT-04)                     #
    # ------------------------------------------------------------------ #

    def _chikou_has_open_space(
        self, df: pd.DataFrame, idx: int, direction: Direction
    ) -> bool:
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

            if direction == Direction.LONG:
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

        # Session / day gate — evaluated before any indicator work
        if not self._is_active_session(df, idx):
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

        if close > cloud_top and tenkan > kijun:
            if self._chikou_has_open_space(df, idx, Direction.LONG):
                context["direction"] = Direction.LONG
                return True

        if close < cloud_bottom and tenkan < kijun:
            if self._chikou_has_open_space(df, idx, Direction.SHORT):
                context["direction"] = Direction.SHORT
                return True

        return False

    def generate_signal(self, df: pd.DataFrame, idx: int, context: dict) -> Signal | None:
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
            setup_type="chikou_session_guard",
            proposed_entry=close,
            stop_loss=stop_loss,
            take_profit_primary=take_profit,
            confidence_score=self.score_confidence(None, context),
            regime_label=regime,
        )
        return self.validate_signal(signal, context)

    def validate_signal(self, signal: Signal, context: dict) -> Signal | None:
        if signal is None:
            return None
        if signal.regime_label in ("no_trade", "consolidation"):
            return None
        if abs(signal.proposed_entry - signal.stop_loss) < 1e-10:
            return None
        return signal

    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            max_bars_in_trade=30,      # tighter than BOT-04's 60: edge fades past bar 10
            risk_pct=context.get("risk_pct", 1.0),
        )

    # ------------------------------------------------------------------ #
    #  Position management: breakeven stop after 2 profitable bars        #
    # ------------------------------------------------------------------ #

    def manage_position(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> dict:
        """Move stop to breakeven after 2+ bars in profit."""
        bars = position.get("bars_in_trade", 0)
        if bars < 2:
            return position

        direction = position.get("direction")
        entry = position.get("entry_price", 0.0)
        current_stop = position.get("stop_loss", 0.0)
        close = df["close"].iloc[idx]

        if direction == Direction.LONG:
            if close > entry and current_stop < entry:
                position["stop_loss"] = entry  # lock breakeven
        elif direction == Direction.SHORT:
            if close < entry and current_stop > entry:
                position["stop_loss"] = entry  # lock breakeven

        return position

    # ------------------------------------------------------------------ #
    #  Exit: Tenkan grace — require N consecutive crosses before exiting  #
    # ------------------------------------------------------------------ #

    def generate_exit(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> ExitReason | None:
        if idx < self.tenkan_grace_bars:
            return None

        bars = position.get("bars_in_trade", 0)
        if bars >= position.get("max_bars_in_trade", 30):
            return ExitReason.TIME_STOP_EXIT

        direction = position.get("direction")
        tenkan_now = df["tenkan_sen"].iloc[idx]
        if pd.isna(tenkan_now):
            return None

        close_now = df["close"].iloc[idx]

        # Check N consecutive closes through Tenkan before exiting.
        # This prevents the single-candle whipsaws that cost BOT-04 £341.
        def _crossed(bar_idx: int) -> bool:
            c = df["close"].iloc[bar_idx]
            t = df["tenkan_sen"].iloc[bar_idx]
            if pd.isna(t):
                return False
            if direction == Direction.LONG:
                return c < t
            return c > t

        consecutive = all(
            _crossed(idx - k)
            for k in range(self.tenkan_grace_bars)
            if (idx - k) >= 0
        )

        if consecutive:
            return ExitReason.INDICATOR_INVALIDATION_EXIT

        return None

    def score_confidence(self, signal: Signal | None, context: dict) -> float:
        """Slightly higher confidence on Mon–Wed; lower on Thursday/Friday."""
        base = 0.60
        try:
            # context may carry timestamp from the bar
            ts = context.get("bar_timestamp")
            if ts is not None:
                dow = ts.weekday() if ts.tzinfo is None else ts.astimezone(timezone.utc).weekday()
                if dow <= 2:    # Mon–Wed
                    base = 0.65
                elif dow == 4:  # Friday
                    base = 0.55
        except Exception:
            pass
        return base

    def explain_decision(self, context: dict) -> str:
        return (
            "Chikou open-space momentum entry filtered to London/NY sessions "
            "with 2-bar Tenkan grace exit and breakeven stop after 2 bars."
        )
