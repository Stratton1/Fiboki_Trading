"""Composable rule primitives.

Each primitive is a pure function ``(df, idx, params, direction) -> bool``
evaluated on the CLOSED candle at ``idx``. Primitives must only read rows
``<= idx`` (no look-ahead — enforced by tests that mutate future bars).

Each registry entry declares the indicators it needs so the compiler can
build the indicator set for a spec automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class Primitive:
    name: str
    fn: Callable[[pd.DataFrame, int, dict, str], bool]
    description: str
    # Indicator requirements: callables building Indicator instances from params
    requires: tuple = field(default_factory=tuple)  # tuple of (factory, param_keys)
    param_schema: dict = field(default_factory=dict)  # name -> (type, default)


def _col(df: pd.DataFrame, name: str, idx: int) -> float:
    val = df[name].iloc[idx]
    return float(val) if pd.notna(val) else float("nan")


# ── Ichimoku primitives ───────────────────────────────────────────


def price_vs_kumo(df, idx, params, direction) -> bool:
    """Long: close above cloud; short: close below cloud."""
    close = _col(df, "close", idx)
    a, b = _col(df, "senkou_span_a", idx), _col(df, "senkou_span_b", idx)
    if pd.isna(a) or pd.isna(b):
        return False
    top, bot = max(a, b), min(a, b)
    clearance = float(params.get("clearance", 0.0))
    if direction == "long":
        return close > top * (1 + clearance)
    return close < bot * (1 - clearance)


def tenkan_kijun_cross(df, idx, params, direction) -> bool:
    """Tenkan crossed Kijun on this closed bar (bullish for long)."""
    if idx < 1:
        return False
    t0, k0 = _col(df, "tenkan_sen", idx - 1), _col(df, "kijun_sen", idx - 1)
    t1, k1 = _col(df, "tenkan_sen", idx), _col(df, "kijun_sen", idx)
    if any(pd.isna(v) for v in (t0, k0, t1, k1)):
        return False
    if direction == "long":
        return t0 <= k0 and t1 > k1
    return t0 >= k0 and t1 < k1


def chikou_open_space(df, idx, params, direction) -> bool:
    """Current close clear of price ``shift`` bars ago (Chikou free)."""
    shift = int(params.get("shift", 26))
    if idx < shift:
        return False
    close = _col(df, "close", idx)
    past_high = _col(df, "high", idx - shift)
    past_low = _col(df, "low", idx - shift)
    if direction == "long":
        return close > past_high
    return close < past_low


# ── Moving-average primitives ─────────────────────────────────────


def ema_cross(df, idx, params, direction) -> bool:
    """Fast EMA crossed slow EMA on this closed bar."""
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    f_col, s_col = f"ema_{fast}", f"ema_{slow}"
    if idx < 1:
        return False
    f0, s0 = _col(df, f_col, idx - 1), _col(df, s_col, idx - 1)
    f1, s1 = _col(df, f_col, idx), _col(df, s_col, idx)
    if any(pd.isna(v) for v in (f0, s0, f1, s1)):
        return False
    if direction == "long":
        return f0 <= s0 and f1 > s1
    return f0 >= s0 and f1 < s1


def price_vs_ema(df, idx, params, direction) -> bool:
    """Close above (long) / below (short) an EMA."""
    period = int(params.get("period", 50))
    val = _col(df, f"ema_{period}", idx)
    if pd.isna(val):
        return False
    close = _col(df, "close", idx)
    return close > val if direction == "long" else close < val


# ── Oscillator primitives ─────────────────────────────────────────


def rsi_threshold(df, idx, params, direction) -> bool:
    """Long: RSI <= oversold (reversal) or >= momentum if mode=momentum."""
    period = int(params.get("period", 14))
    mode = params.get("mode", "reversal")
    val = _col(df, f"rsi_{period}", idx)
    if pd.isna(val):
        return False
    if mode == "momentum":
        level = float(params.get("level", 55.0))
        return val >= level if direction == "long" else val <= 100 - level
    oversold = float(params.get("oversold", 30.0))
    overbought = float(params.get("overbought", 70.0))
    return val <= oversold if direction == "long" else val >= overbought


# ── Volatility / structure primitives ─────────────────────────────


def atr_min(df, idx, params, direction) -> bool:
    """ATR at least ``min_pct`` of price (avoid dead markets)."""
    atr = _col(df, "atr", idx)
    close = _col(df, "close", idx)
    if pd.isna(atr) or close <= 0:
        return False
    return (atr / close) >= float(params.get("min_pct", 0.0005))


def atr_max(df, idx, params, direction) -> bool:
    """ATR at most ``max_pct`` of price (avoid chaos)."""
    atr = _col(df, "atr", idx)
    close = _col(df, "close", idx)
    if pd.isna(atr) or close <= 0:
        return False
    return (atr / close) <= float(params.get("max_pct", 0.05))


def higher_close_streak(df, idx, params, direction) -> bool:
    """N consecutive higher (long) / lower (short) closes into this bar."""
    n = int(params.get("bars", 2))
    if idx < n:
        return False
    closes = df["close"].iloc[idx - n: idx + 1].to_list()
    pairs = zip(closes, closes[1:])
    if direction == "long":
        return all(b > a for a, b in pairs)
    return all(b < a for a, b in pairs)


def session_window(df, idx, params, direction) -> bool:
    """Bar's UTC hour within [start_hour, end_hour)."""
    ts = df["timestamp"].iloc[idx] if "timestamp" in df.columns else df.index[idx]
    hour = pd.Timestamp(ts).hour
    start = int(params.get("start_hour", 7))
    end = int(params.get("end_hour", 17))
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # overnight window


# ── Registry ──────────────────────────────────────────────────────


def _ichimoku_req(params: dict):
    from fibokei.indicators.ichimoku import IchimokuCloud
    return IchimokuCloud()


def _atr_req(params: dict):
    from fibokei.indicators.atr import ATR
    return ATR(period=int(params.get("atr_period", params.get("period", 14))))


def _ema_req_fast(params: dict):
    from fibokei.indicators.moving_averages import EMA
    return EMA(period=int(params.get("fast", 12)))


def _ema_req_slow(params: dict):
    from fibokei.indicators.moving_averages import EMA
    return EMA(period=int(params.get("slow", 26)))


def _ema_req_single(params: dict):
    from fibokei.indicators.moving_averages import EMA
    return EMA(period=int(params.get("period", 50)))


def _rsi_req(params: dict):
    from fibokei.indicators.moving_averages import RSI
    return RSI(period=int(params.get("period", 14)))


PRIMITIVES: dict[str, Primitive] = {
    p.name: p
    for p in [
        Primitive("price_vs_kumo", price_vs_kumo,
                  "Close above (long) / below (short) the Ichimoku cloud",
                  requires=(_ichimoku_req,)),
        Primitive("tenkan_kijun_cross", tenkan_kijun_cross,
                  "Tenkan/Kijun cross on the closed bar",
                  requires=(_ichimoku_req,)),
        Primitive("chikou_open_space", chikou_open_space,
                  "Close clear of price N bars ago (Chikou free)"),
        Primitive("ema_cross", ema_cross,
                  "Fast EMA crossed slow EMA on the closed bar",
                  requires=(_ema_req_fast, _ema_req_slow)),
        Primitive("price_vs_ema", price_vs_ema,
                  "Close above (long) / below (short) an EMA",
                  requires=(_ema_req_single,)),
        Primitive("rsi_threshold", rsi_threshold,
                  "RSI reversal or momentum threshold",
                  requires=(_rsi_req,)),
        Primitive("atr_min", atr_min, "Minimum volatility filter",
                  requires=(_atr_req,)),
        Primitive("atr_max", atr_max, "Maximum volatility filter",
                  requires=(_atr_req,)),
        Primitive("higher_close_streak", higher_close_streak,
                  "N consecutive directional closes"),
        Primitive("session_window", session_window,
                  "UTC trading-session window"),
    ]
}


def primitive_names() -> list[str]:
    return sorted(PRIMITIVES)
