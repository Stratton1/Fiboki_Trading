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


# ── Strategy Factory Gen-1 primitives ─────────────────────────────


def sma_cross(df, idx, params, direction) -> bool:
    fast, slow = int(params.get("fast", 10)), int(params.get("slow", 30))
    if idx < 1:
        return False
    f0, s0 = _col(df, f"sma_{fast}", idx - 1), _col(df, f"sma_{slow}", idx - 1)
    f1, s1 = _col(df, f"sma_{fast}", idx), _col(df, f"sma_{slow}", idx)
    if any(pd.isna(v) for v in (f0, s0, f1, s1)):
        return False
    return (f0 <= s0 and f1 > s1) if direction == "long" else (f0 >= s0 and f1 < s1)


def price_vs_sma(df, idx, params, direction) -> bool:
    val = _col(df, f"sma_{int(params.get('period', 50))}", idx)
    if pd.isna(val):
        return False
    close = _col(df, "close", idx)
    return close > val if direction == "long" else close < val


def macd_cross(df, idx, params, direction) -> bool:
    if idx < 1:
        return False
    m0, s0 = _col(df, "macd_line", idx - 1), _col(df, "macd_signal", idx - 1)
    m1, s1 = _col(df, "macd_line", idx), _col(df, "macd_signal", idx)
    if any(pd.isna(v) for v in (m0, s0, m1, s1)):
        return False
    return (m0 <= s0 and m1 > s1) if direction == "long" else (m0 >= s0 and m1 < s1)


def macd_zero(df, idx, params, direction) -> bool:
    val = _col(df, "macd_line", idx)
    if pd.isna(val):
        return False
    return val > 0 if direction == "long" else val < 0


def stoch_threshold(df, idx, params, direction) -> bool:
    k = _col(df, "stoch_k", idx)
    if pd.isna(k):
        return False
    oversold = float(params.get("oversold", 20.0))
    overbought = float(params.get("overbought", 80.0))
    return k <= oversold if direction == "long" else k >= overbought


def bb_revert(df, idx, params, direction) -> bool:
    close = _col(df, "close", idx)
    lower, upper = _col(df, "bb_lower", idx), _col(df, "bb_upper", idx)
    if any(pd.isna(v) for v in (close, lower, upper)):
        return False
    return close <= lower if direction == "long" else close >= upper


def bb_breakout(df, idx, params, direction) -> bool:
    close = _col(df, "close", idx)
    upper, lower = _col(df, "bb_upper", idx), _col(df, "bb_lower", idx)
    if any(pd.isna(v) for v in (close, upper, lower)):
        return False
    return close > upper if direction == "long" else close < lower


def atr_breakout(df, idx, params, direction) -> bool:
    if idx < 1:
        return False
    atr, close, prev = _col(df, "atr", idx), _col(df, "close", idx), _col(df, "close", idx - 1)
    if any(pd.isna(v) for v in (atr, close, prev)):
        return False
    mult = float(params.get("mult", 1.0))
    return close > prev + mult * atr if direction == "long" else close < prev - mult * atr


def adx_filter(df, idx, params, direction) -> bool:
    period = int(params.get("period", 14))
    adx = _col(df, f"adx_{period}", idx)
    pdi, mdi = _col(df, "plus_di", idx), _col(df, "minus_di", idx)
    if any(pd.isna(v) for v in (adx, pdi, mdi)):
        return False
    if adx < float(params.get("threshold", 25.0)):
        return False
    return pdi > mdi if direction == "long" else mdi > pdi


def donchian_breakout(df, idx, params, direction) -> bool:
    if idx < 1:
        return False
    close = _col(df, "close", idx)
    up, lo = _col(df, "donchian_upper", idx - 1), _col(df, "donchian_lower", idx - 1)
    if any(pd.isna(v) for v in (close, up, lo)):
        return False
    return close > up if direction == "long" else close < lo


def keltner_breakout(df, idx, params, direction) -> bool:
    close, up, lo = _col(df, "close", idx), _col(df, "kc_upper", idx), _col(df, "kc_lower", idx)
    if any(pd.isna(v) for v in (close, up, lo)):
        return False
    return close > up if direction == "long" else close < lo


def psar_flip(df, idx, params, direction) -> bool:
    if idx < 1:
        return False
    t0, t1 = _col(df, "psar_trend", idx - 1), _col(df, "psar_trend", idx)
    if any(pd.isna(v) for v in (t0, t1)):
        return False
    return (t0 <= 0 and t1 > 0) if direction == "long" else (t0 >= 0 and t1 < 0)


def cci_threshold(df, idx, params, direction) -> bool:
    val = _col(df, f"cci_{int(params.get('period', 20))}", idx)
    if pd.isna(val):
        return False
    level = float(params.get("level", 100.0))
    return val <= -level if direction == "long" else val >= level


def roc_threshold(df, idx, params, direction) -> bool:
    val = _col(df, f"roc_{int(params.get('period', 10))}", idx)
    if pd.isna(val):
        return False
    level = float(params.get("level", 0.0))
    return val >= level if direction == "long" else val <= -level


def pivot_bounce(df, idx, params, direction) -> bool:
    close, s1, r1 = _col(df, "close", idx), _col(df, "pivot_s1", idx), _col(df, "pivot_r1", idx)
    low, high = _col(df, "low", idx), _col(df, "high", idx)
    if any(pd.isna(v) for v in (close, s1, r1, low, high)):
        return False
    tol = float(params.get("tol", 0.001))
    if direction == "long":
        return low <= s1 * (1 + tol) and close > s1
    return high >= r1 * (1 - tol) and close < r1


def sr_breakout(df, idx, params, direction) -> bool:
    """Break of recent N-bar high/low (S/R via rolling extremes)."""
    if idx < 1:
        return False
    close = _col(df, "close", idx)
    up, lo = _col(df, "donchian_upper", idx - 1), _col(df, "donchian_lower", idx - 1)
    if any(pd.isna(v) for v in (close, up, lo)):
        return False
    return close > up if direction == "long" else close < lo


def sr_bounce(df, idx, params, direction) -> bool:
    """Bounce off recent support (long) / resistance (short)."""
    if idx < 1:
        return False
    close, low, high = _col(df, "close", idx), _col(df, "low", idx), _col(df, "high", idx)
    lo, up = _col(df, "donchian_lower", idx - 1), _col(df, "donchian_upper", idx - 1)
    if any(pd.isna(v) for v in (close, low, high, lo, up)):
        return False
    tol = float(params.get("tol", 0.001))
    if direction == "long":
        return low <= lo * (1 + tol) and close > lo
    return high >= up * (1 - tol) and close < up


def vwap_bias(df, idx, params, direction) -> bool:
    val = _col(df, f"vwap_{int(params.get('period', 20))}", idx)
    if pd.isna(val):
        return False
    close = _col(df, "close", idx)
    return close > val if direction == "long" else close < val


def obv_confirm(df, idx, params, direction) -> bool:
    n = int(params.get("lookback", 5))
    if idx < n:
        return False
    cur, past = _col(df, "obv", idx), _col(df, "obv", idx - n)
    if any(pd.isna(v) for v in (cur, past)):
        return False
    return cur > past if direction == "long" else cur < past


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


def _sma_req_fast(params: dict):
    from fibokei.indicators.moving_averages import SMA
    return SMA(period=int(params.get("fast", 10)))


def _sma_req_slow(params: dict):
    from fibokei.indicators.moving_averages import SMA
    return SMA(period=int(params.get("slow", 30)))


def _sma_req_single(params: dict):
    from fibokei.indicators.moving_averages import SMA
    return SMA(period=int(params.get("period", 50)))


def _macd_req(params: dict):
    from fibokei.indicators.oscillators import MACD
    return MACD(
        fast=int(params.get("fast", 12)),
        slow=int(params.get("slow", 26)),
        signal=int(params.get("signal", 9)),
    )


def _stoch_req(params: dict):
    from fibokei.indicators.oscillators import Stochastic
    return Stochastic(
        k_period=int(params.get("k_period", 14)),
        smooth=int(params.get("smooth", 3)),
        d_period=int(params.get("d_period", 3)),
    )


def _bb_req(params: dict):
    from fibokei.indicators.channels import BollingerBands
    return BollingerBands(
        period=int(params.get("period", 20)),
        num_std=float(params.get("num_std", 2.0)),
    )


def _adx_req(params: dict):
    from fibokei.indicators.trend import ADX
    return ADX(period=int(params.get("period", 14)))


def _donchian_req(params: dict):
    from fibokei.indicators.channels import DonchianChannels
    return DonchianChannels(period=int(params.get("period", 20)))


def _keltner_req(params: dict):
    from fibokei.indicators.channels import KeltnerChannels
    return KeltnerChannels()


def _psar_req(params: dict):
    from fibokei.indicators.trend import ParabolicSAR
    return ParabolicSAR()


def _cci_req(params: dict):
    from fibokei.indicators.oscillators import CCI
    return CCI(period=int(params.get("period", 20)))


def _roc_req(params: dict):
    from fibokei.indicators.oscillators import ROC
    return ROC(period=int(params.get("period", 10)))


def _pivot_req(params: dict):
    from fibokei.indicators.pivots import PivotPoints
    return PivotPoints()


def _vwap_req(params: dict):
    from fibokei.indicators.volume import VWAP
    return VWAP(period=int(params.get("period", 20)))


def _obv_req(params: dict):
    from fibokei.indicators.volume import OBV
    return OBV()


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
        # ── Gen-1 traditional primitives ──
        Primitive("sma_cross", sma_cross, "Fast SMA crossed slow SMA",
                  requires=(_sma_req_fast, _sma_req_slow)),
        Primitive("price_vs_sma", price_vs_sma, "Close above/below an SMA",
                  requires=(_sma_req_single,)),
        Primitive("macd_cross", macd_cross, "MACD line crossed signal",
                  requires=(_macd_req,)),
        Primitive("macd_zero", macd_zero, "MACD line above/below zero",
                  requires=(_macd_req,)),
        Primitive("stoch_threshold", stoch_threshold,
                  "Stochastic oversold/overbought", requires=(_stoch_req,)),
        Primitive("bb_revert", bb_revert, "Bollinger mean-reversion",
                  requires=(_bb_req,)),
        Primitive("bb_breakout", bb_breakout, "Bollinger band breakout",
                  requires=(_bb_req,)),
        Primitive("atr_breakout", atr_breakout, "ATR volatility breakout",
                  requires=(_atr_req,)),
        Primitive("adx_filter", adx_filter, "ADX trend strength + direction",
                  requires=(_adx_req,)),
        Primitive("donchian_breakout", donchian_breakout,
                  "Donchian channel breakout", requires=(_donchian_req,)),
        Primitive("keltner_breakout", keltner_breakout,
                  "Keltner channel breakout", requires=(_keltner_req,)),
        Primitive("psar_flip", psar_flip, "Parabolic SAR trend flip",
                  requires=(_psar_req,)),
        Primitive("cci_threshold", cci_threshold,
                  "CCI mean-reversion threshold", requires=(_cci_req,)),
        Primitive("roc_threshold", roc_threshold, "ROC momentum threshold",
                  requires=(_roc_req,)),
        Primitive("pivot_bounce", pivot_bounce, "Pivot S1/R1 bounce",
                  requires=(_pivot_req,)),
        Primitive("sr_breakout", sr_breakout,
                  "Support/resistance breakout (rolling extremes)",
                  requires=(_donchian_req,)),
        Primitive("sr_bounce", sr_bounce,
                  "Support/resistance bounce (rolling extremes)",
                  requires=(_donchian_req,)),
        Primitive("vwap_bias", vwap_bias, "Close vs VWAP bias",
                  requires=(_vwap_req,)),
        Primitive("obv_confirm", obv_confirm, "OBV slope confirmation",
                  requires=(_obv_req,)),
    ]
}


def primitive_names() -> list[str]:
    return sorted(PRIMITIVES)
