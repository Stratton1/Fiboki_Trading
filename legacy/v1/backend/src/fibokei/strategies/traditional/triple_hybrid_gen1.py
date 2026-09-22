"""Triple-hybrid Gen-1 strategies — curated 3-indicator combinations.

A triple hybrid is a primary entry trigger plus **two** same-direction
confirm/filter rules (1 entry + 2 extra = three indicators). As with the double
hybrids, the compiler only signals when entry + confirmations + filters all
agree on one direction, so the extra indicators can only ever *gate* the
primary, never contradict it. More gates = fewer, higher-conviction trades.

These are research-tier (``family="triple_hybrid_gen1"``): registered and
backtestable, never auto-promoted. The evolution engine (``research/evolution.py``)
can also discover triple hybrids by structural growth; this module is the
hand-curated seed set.
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

FAMILY = "triple_hybrid_gen1"
_STOP = dict(model="atr_multiple", multiple=2.0, atr_period=14)


def _rules(rules: list[tuple[str, dict]]) -> list[RuleSpec]:
    return [RuleSpec(primitive=p, params=pr) for p, pr in rules]


def _triple(spec_id, name, hypothesis, entry, confirmation, filters, *,
            target_mult=2.0, max_bars=50) -> StrategySpec:
    extra = len(confirmation) + len(filters)
    if extra != 2:
        raise ValueError(f"{spec_id}: a triple hybrid needs exactly 2 extra rules")
    return StrategySpec(
        spec_id=spec_id, name=name, family=FAMILY, hypothesis=hypothesis,
        generation_method="manual", direction="both",
        entry_rules=_rules(entry),
        confirmation_rules=_rules(confirmation),
        filters=_rules(filters),
        stop=StopSpec(**_STOP),
        target=TargetSpec(model="rr_multiple", multiple=target_mult),
        trailing=TrailingSpec(model="none"),
        max_bars_in_trade=max_bars,
    )


TRIPLE_HYBRID_GEN1_SPECS: list[StrategySpec] = [
    _triple("tri_trend_macd_adx", "EMA Cross + MACD + ADX",
            "Trend entry, confirmed by MACD regime and gated by ADX strength.",
            [("ema_cross", {"fast": 12, "slow": 26})],
            [("macd_zero", {"fast": 12, "slow": 26, "signal": 9})],
            [("adx_filter", {"period": 14, "threshold": 20})]),
    _triple("tri_macd_rsi_ema", "MACD Cross + RSI + EMA Trend",
            "MACD cross, confirmed by RSI momentum, in the EMA50 trend.",
            [("macd_cross", {"fast": 12, "slow": 26, "signal": 9})],
            [("rsi_threshold", {"period": 14, "mode": "momentum", "level": 50})],
            [("price_vs_ema", {"period": 50})]),
    _triple("tri_donchian_adx_ema", "Donchian + ADX + EMA Trend",
            "Channel breakout, only in a strong ADX trend aligned with EMA50.",
            [("donchian_breakout", {"period": 20})],
            [],
            [("adx_filter", {"period": 14, "threshold": 20}),
             ("price_vs_ema", {"period": 50})]),
    _triple("tri_bb_rsi_atr", "Bollinger Reversion + RSI + ATR Floor",
            "Fade a band touch when RSI is stretched and volatility is alive.",
            [("bb_revert", {"period": 20, "num_std": 2.0})],
            [("rsi_threshold",
              {"period": 14, "mode": "reversal", "oversold": 35, "overbought": 65})],
            [("atr_min", {"min_pct": 0.0005})],
            target_mult=1.5),
    _triple("tri_sma_macd_rsi", "SMA Cross + MACD + RSI",
            "SMA crossover, double-confirmed by MACD regime and RSI momentum.",
            [("sma_cross", {"fast": 10, "slow": 30})],
            [("macd_zero", {"fast": 12, "slow": 26, "signal": 9}),
             ("rsi_threshold", {"period": 14, "mode": "momentum", "level": 50})],
            []),
    _triple("tri_stoch_ema_adx", "Stochastic + EMA Trend + ADX",
            "Stochastic extreme, with the EMA50 trend and ADX strength.",
            [("stoch_threshold", {"oversold": 25, "overbought": 75})],
            [],
            [("price_vs_ema", {"period": 50}),
             ("adx_filter", {"period": 14, "threshold": 20})]),
    _triple("tri_keltner_adx_ema", "Keltner + ADX + EMA Trend",
            "Keltner breakout, ADX-confirmed and EMA50-aligned.",
            [("keltner_breakout", {})],
            [],
            [("adx_filter", {"period": 14, "threshold": 20}),
             ("price_vs_ema", {"period": 50})]),
    _triple("tri_cci_rsi_atr", "CCI Reversion + RSI + ATR Floor",
            "Fade CCI extreme with RSI agreement and live volatility.",
            [("cci_threshold", {"period": 20, "level": 100})],
            [("rsi_threshold",
              {"period": 14, "mode": "reversal", "oversold": 35, "overbought": 65})],
            [("atr_min", {"min_pct": 0.0005})],
            target_mult=1.5),
]


def _strategy_class(spec: StrategySpec) -> type[Strategy]:
    class _TripleStrategy(CompiledStrategy):
        def __init__(self) -> None:
            super().__init__(spec)

    _TripleStrategy.__name__ = f"Triple_{spec.spec_id}"
    _TripleStrategy.__qualname__ = _TripleStrategy.__name__
    _TripleStrategy.__doc__ = f"Triple-hybrid Gen-1 strategy: {spec.name}."
    return _TripleStrategy


TRIPLE_HYBRID_GEN1_STRATEGY_CLASSES: list[type[Strategy]] = [
    _strategy_class(s) for s in TRIPLE_HYBRID_GEN1_SPECS
]


def register_triple_hybrid_gen1(registry) -> int:
    """Register all triple_hybrid_gen1 strategies. Returns count."""
    for cls in TRIPLE_HYBRID_GEN1_STRATEGY_CLASSES:
        registry.register(cls)
    return len(TRIPLE_HYBRID_GEN1_STRATEGY_CLASSES)
