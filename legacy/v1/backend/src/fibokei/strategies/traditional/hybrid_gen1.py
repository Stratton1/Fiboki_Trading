"""Hybrid Gen-1 strategies — 10 curated two-indicator combinations.

Phase 5 of the Strategy Factory. A hybrid pairs a *primary* entry trigger with
a *secondary* indicator that **confirms or filters** the trade — never one that
can contradict it. This is enforced structurally, not by convention: the
compiler's ``_direction_at`` only emits a signal when the entry rule(s),
confirmation rules and filters ALL evaluate true for the *same* direction. A
secondary that disagreed with the primary would simply suppress the signal, so
a hybrid can never trade against its own confirmation.

Like the traditional families these are **research-tier**
(``family="hybrid_gen1"``): registered and backtestable, never auto-promoted.
Each carries a mandatory ATR stop, an RR target and a max-bars/opposite-signal
exit. Determinism and closed-candle evaluation are inherited from the factory.
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

FAMILY = "hybrid_gen1"

_DEFAULT_STOP = dict(model="atr_multiple", multiple=2.0, atr_period=14)
_DEFAULT_TARGET = dict(model="rr_multiple", multiple=2.0)


def _rules(rules: list[tuple[str, dict]]) -> list[RuleSpec]:
    return [RuleSpec(primitive=p, params=pr) for p, pr in rules]


def _hybrid(
    spec_id: str,
    name: str,
    hypothesis: str,
    entry: list[tuple[str, dict]],
    *,
    confirmation: list[tuple[str, dict]] | None = None,
    filters: list[tuple[str, dict]] | None = None,
    direction: str = "both",
    target: dict | None = None,
    max_bars: int = 50,
) -> StrategySpec:
    """Build one hybrid_gen1 spec. Must declare a confirmation or a filter."""
    if not confirmation and not filters:
        raise ValueError(f"{spec_id}: a hybrid needs a confirmation or filter")
    return StrategySpec(
        spec_id=spec_id,
        name=name,
        family=FAMILY,
        hypothesis=hypothesis,
        generation_method="manual",
        direction=direction,
        entry_rules=_rules(entry),
        confirmation_rules=_rules(confirmation or []),
        filters=_rules(filters or []),
        stop=StopSpec(**_DEFAULT_STOP),
        target=TargetSpec(**(target or _DEFAULT_TARGET)),
        trailing=TrailingSpec(model="none"),
        max_bars_in_trade=max_bars,
    )


# ── The 10 curated hybrids ───────────────────────────────────────────
HYBRID_GEN1_SPECS: list[StrategySpec] = [
    _hybrid("hyb_ema_macd", "EMA Cross + MACD Confirm",
            "EMA crossover, taken only when MACD agrees on regime.",
            [("ema_cross", {"fast": 12, "slow": 26})],
            confirmation=[("macd_zero", {"fast": 12, "slow": 26, "signal": 9})]),
    _hybrid("hyb_macd_rsi", "MACD Cross + RSI Momentum",
            "MACD signal cross confirmed by RSI momentum bias.",
            [("macd_cross", {"fast": 12, "slow": 26, "signal": 9})],
            confirmation=[("rsi_threshold",
                           {"period": 14, "mode": "momentum", "level": 50})]),
    _hybrid("hyb_donchian_adx", "Donchian Breakout + ADX Filter",
            "Donchian breakout, only when ADX confirms a strong trend.",
            [("donchian_breakout", {"period": 20})],
            filters=[("adx_filter", {"period": 14, "threshold": 20})]),
    _hybrid("hyb_bb_rsi", "Bollinger Reversion + RSI Extreme",
            "Fade a band touch only when RSI is also stretched.",
            [("bb_revert", {"period": 20, "num_std": 2.0})],
            confirmation=[("rsi_threshold",
                           {"period": 14, "mode": "reversal",
                            "oversold": 35, "overbought": 65})],
            target={"model": "rr_multiple", "multiple": 1.5}),
    _hybrid("hyb_sma_adx", "SMA Cross + ADX Filter",
            "SMA crossover gated by ADX trend strength.",
            [("sma_cross", {"fast": 10, "slow": 30})],
            filters=[("adx_filter", {"period": 14, "threshold": 20})]),
    _hybrid("hyb_ema_rsi_pullback", "EMA Trend + RSI Pullback",
            "Buy dips (RSI low) in an uptrend / sell rallies in a downtrend.",
            [("rsi_threshold",
              {"period": 14, "mode": "reversal", "oversold": 40, "overbought": 60})],
            filters=[("price_vs_ema", {"period": 50})]),
    _hybrid("hyb_macd_ema_trend", "MACD Cross + EMA Trend Filter",
            "MACD cross, only in the direction of the EMA50 trend.",
            [("macd_cross", {"fast": 12, "slow": 26, "signal": 9})],
            filters=[("price_vs_ema", {"period": 50})]),
    _hybrid("hyb_stoch_trend", "Stochastic Reversal + EMA Trend",
            "Stochastic extreme taken only with the prevailing EMA50 trend.",
            [("stoch_threshold", {"oversold": 30, "overbought": 70})],
            filters=[("price_vs_ema", {"period": 50})],
            target={"model": "rr_multiple", "multiple": 1.5}),
    _hybrid("hyb_keltner_adx", "Keltner Breakout + ADX Filter",
            "Keltner channel breakout confirmed by ADX trend strength.",
            [("keltner_breakout", {})],
            filters=[("adx_filter", {"period": 14, "threshold": 20})]),
    _hybrid("hyb_psar_macd", "Parabolic SAR Flip + MACD Confirm",
            "PSAR trend flip confirmed by MACD regime.",
            [("psar_flip", {})],
            confirmation=[("macd_zero", {"fast": 12, "slow": 26, "signal": 9})]),
]


# ── Registration bridge (same pattern as traditional gen1) ───────────


def _strategy_class(spec: StrategySpec) -> type[Strategy]:
    class _HybridStrategy(CompiledStrategy):
        def __init__(self) -> None:
            super().__init__(spec)

    _HybridStrategy.__name__ = f"Hybrid_{spec.spec_id}"
    _HybridStrategy.__qualname__ = _HybridStrategy.__name__
    _HybridStrategy.__doc__ = f"Hybrid Gen-1 strategy: {spec.name}."
    return _HybridStrategy


HYBRID_GEN1_STRATEGY_CLASSES: list[type[Strategy]] = [
    _strategy_class(s) for s in HYBRID_GEN1_SPECS
]


def register_hybrid_gen1(registry) -> int:
    """Register all hybrid_gen1 strategies into ``registry``. Returns count."""
    for cls in HYBRID_GEN1_STRATEGY_CLASSES:
        registry.register(cls)
    return len(HYBRID_GEN1_STRATEGY_CLASSES)
