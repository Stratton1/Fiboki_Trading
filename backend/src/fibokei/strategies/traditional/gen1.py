"""Traditional Gen-1 strategies — 25 classic indicator families.

Phase 4 of the Strategy Factory. Each family is a declarative ``StrategySpec``
(entry primitive(s) + ATR stop + RR target + max-bars/opposite-signal exit),
compiled into the common ``Strategy`` interface by ``compile_spec``. They are
**research-tier** (``family="traditional_gen1"``): registered and backtestable,
but never auto-promoted to paper/demo/live without passing the promotion gates.

Architecture notes (carried from RULES):
- All rules evaluate on CLOSED candles only (enforced by primitive tests).
- Indicators are centralised in ``indicators/`` and pulled in automatically by
  the compiler from each primitive's declared requirements.
- Stops/targets/risk are spec-level (central risk caps still apply) — no risk
  logic is duplicated in the families themselves.
- Deterministic: identical spec + data → identical signals (content-hashable).

Volume caveat: FX has no true volume, so VWAP/OBV families are tagged
``[research_limited: volume]`` and must only be trusted on instruments with
reliable volume.
"""

from __future__ import annotations

from fibokei.strategies.base import Strategy
from fibokei.strategies.factory.compiler import CompiledStrategy
from fibokei.strategies.factory.spec import (
    RuleSpec,
    StopSpec,
    StrategySpec,
    TargetSpec,
    TrailingSpec,
)

FAMILY = "traditional_gen1"

# Deterministic defaults shared by every Gen-1 family. ATR(14) stop, 2R target.
_DEFAULT_STOP = dict(model="atr_multiple", multiple=2.0, atr_period=14)
_DEFAULT_TARGET = dict(model="rr_multiple", multiple=2.0)


def _spec(
    spec_id: str,
    name: str,
    hypothesis: str,
    entry: list[tuple[str, dict]],
    *,
    direction: str = "both",
    stop: dict | None = None,
    target: dict | None = None,
    trailing: dict | None = None,
    max_bars: int = 50,
    research_limited: bool = False,
) -> StrategySpec:
    """Build one traditional_gen1 spec with sane shared defaults."""
    note = " [research_limited: volume]" if research_limited else ""
    return StrategySpec(
        spec_id=spec_id,
        name=name,
        family=FAMILY,
        hypothesis=hypothesis + note,
        generation_method="manual",
        direction=direction,
        entry_rules=[RuleSpec(primitive=p, params=pr) for p, pr in entry],
        stop=StopSpec(**(stop or _DEFAULT_STOP)),
        target=TargetSpec(**(target or _DEFAULT_TARGET)),
        trailing=TrailingSpec(**(trailing or {"model": "none"})),
        max_bars_in_trade=max_bars,
    )


# ── The 25 traditional families ──────────────────────────────────────
TRADITIONAL_GEN1_SPECS: list[StrategySpec] = [
    # Trend / moving-average (1-5)
    _spec("trad_sma_trend", "SMA Trend Filter",
          "Trade in the direction of price relative to a slow SMA.",
          [("price_vs_sma", {"period": 50})]),
    _spec("trad_ema_trend", "EMA Trend Filter",
          "Trade in the direction of price relative to a slow EMA.",
          [("price_vs_ema", {"period": 50})]),
    _spec("trad_sma_crossover", "SMA Crossover",
          "Enter on fast/slow SMA crossover (classic trend follow).",
          [("sma_cross", {"fast": 10, "slow": 30})]),
    _spec("trad_ema_crossover", "EMA Crossover",
          "Enter on fast/slow EMA crossover (responsive trend follow).",
          [("ema_cross", {"fast": 12, "slow": 26})]),
    _spec("trad_price_vs_ma", "Price vs Moving Average",
          "Bias from close above/below a medium EMA.",
          [("price_vs_ema", {"period": 20})]),
    # MACD (6-7)
    _spec("trad_macd_cross", "MACD Signal Cross",
          "Enter when MACD line crosses its signal line.",
          [("macd_cross", {"fast": 12, "slow": 26, "signal": 9})]),
    _spec("trad_macd_zero", "MACD Zero-Line",
          "Bias from MACD line above/below zero (trend regime).",
          [("macd_zero", {"fast": 12, "slow": 26, "signal": 9})]),
    # RSI (8-9)
    _spec("trad_rsi_meanrev", "RSI Mean Reversion",
          "Fade extremes: long oversold, short overbought.",
          [("rsi_threshold",
            {"period": 14, "mode": "reversal", "oversold": 30, "overbought": 70})],
          target={"model": "rr_multiple", "multiple": 1.5}),
    _spec("trad_rsi_trend", "RSI Trend Continuation",
          "Momentum: long when RSI strong, short when weak.",
          [("rsi_threshold", {"period": 14, "mode": "momentum", "level": 55})]),
    # Stochastic (10)
    _spec("trad_stochastic", "Stochastic Reversal",
          "Fade stochastic oversold/overbought.",
          [("stoch_threshold", {"oversold": 20, "overbought": 80})],
          target={"model": "rr_multiple", "multiple": 1.5}),
    # Bollinger (11-12)
    _spec("trad_bb_meanrev", "Bollinger Mean Reversion",
          "Fade touches of the outer Bollinger bands.",
          [("bb_revert", {"period": 20, "num_std": 2.0})],
          target={"model": "rr_multiple", "multiple": 1.5}),
    _spec("trad_bb_breakout", "Bollinger Breakout",
          "Trade closes beyond the Bollinger bands (expansion).",
          [("bb_breakout", {"period": 20, "num_std": 2.0})]),
    # Volatility (13-14)
    _spec("trad_atr_breakout", "ATR Volatility Breakout",
          "Enter when price moves > 1 ATR beyond the prior close.",
          [("atr_breakout", {"mult": 1.0})]),
    _spec("trad_atr_trail", "ATR Trailing-Stop Trend",
          "Ride trend (price vs EMA50) with an ATR trailing stop.",
          [("price_vs_ema", {"period": 50})],
          trailing={"model": "atr", "multiple": 3.0}, max_bars=100),
    # ADX (15)
    _spec("trad_adx_trend", "ADX Trend Strength",
          "Trade only when ADX confirms a strong directional trend.",
          [("adx_filter", {"period": 14, "threshold": 25})]),
    # Channels (16-17)
    _spec("trad_donchian_breakout", "Donchian Breakout",
          "Breakout of the prior N-bar Donchian channel (turtle-style).",
          [("donchian_breakout", {"period": 20})]),
    _spec("trad_keltner_breakout", "Keltner Breakout",
          "Breakout of the Keltner channel (ATR-banded).",
          [("keltner_breakout", {})]),
    # PSAR (18)
    _spec("trad_psar", "Parabolic SAR Reversal",
          "Enter on a Parabolic SAR trend flip.",
          [("psar_flip", {})]),
    # CCI (19)
    _spec("trad_cci_meanrev", "CCI Mean Reversion",
          "Fade CCI extremes (+/-100).",
          [("cci_threshold", {"period": 20, "level": 100})],
          target={"model": "rr_multiple", "multiple": 1.5}),
    # ROC / momentum (20)
    _spec("trad_roc_momentum", "ROC Momentum",
          "Trade in the direction of strong rate-of-change momentum.",
          [("roc_threshold", {"period": 10, "level": 2.0})]),
    # Pivots / S-R (21-23)
    _spec("trad_pivot_bounce", "Pivot Point Bounce",
          "Bounce off classic pivot S1 (long) / R1 (short).",
          [("pivot_bounce", {"tol": 0.001})],
          target={"model": "rr_multiple", "multiple": 1.5}),
    _spec("trad_sr_breakout", "S/R Breakout",
          "Break of recent rolling support/resistance extremes.",
          [("sr_breakout", {"period": 50})]),
    _spec("trad_sr_bounce", "S/R Bounce",
          "Bounce off recent rolling support/resistance.",
          [("sr_bounce", {"period": 50, "tol": 0.001})],
          target={"model": "rr_multiple", "multiple": 1.5}),
    # Volume (24-25) — research_limited on FX (no true volume)
    _spec("trad_vwap_bias", "VWAP Bias",
          "Bias from close above/below rolling VWAP.",
          [("vwap_bias", {"period": 20})], research_limited=True),
    _spec("trad_obv_confirm", "OBV Confirmation",
          "Trade in the direction of the OBV slope (accumulation).",
          [("obv_confirm", {"lookback": 5})], research_limited=True),
]


# ── Registration bridge ──────────────────────────────────────────────
# The registry stores zero-arg callables (``type[Strategy]``) and instantiates
# them in register()/get()/list_available(). CompiledStrategy needs a spec, so
# we bind each spec into a tiny zero-arg Strategy subclass.


def _strategy_class(spec: StrategySpec) -> type[Strategy]:
    class _Gen1Strategy(CompiledStrategy):
        def __init__(self) -> None:
            super().__init__(spec)

    _Gen1Strategy.__name__ = f"Gen1_{spec.spec_id}"
    _Gen1Strategy.__qualname__ = _Gen1Strategy.__name__
    _Gen1Strategy.__doc__ = f"Traditional Gen-1 strategy: {spec.name}."
    return _Gen1Strategy


GEN1_STRATEGY_CLASSES: list[type[Strategy]] = [
    _strategy_class(s) for s in TRADITIONAL_GEN1_SPECS
]


def register_gen1(registry) -> int:
    """Register all traditional_gen1 strategies into ``registry``.

    Returns the number registered. Idempotent at the id level — re-registering
    overwrites the same id with an identical class.
    """
    for cls in GEN1_STRATEGY_CLASSES:
        registry.register(cls)
    return len(GEN1_STRATEGY_CLASSES)
