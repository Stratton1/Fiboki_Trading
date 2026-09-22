# Strategy Factory Gen‑1 — Audit & Build Plan

**Date:** 2026-06-20
**Scope:** 25 traditional strategy families + 10 curated hybrids, built *systematically*
on Fiboki's existing declarative factory — research‑factory only, never straight to live.

## Key finding: the systematic factory already exists
`backend/src/fibokei/strategies/factory/` provides exactly the architecture the
brief calls for — so strategies are **specs (data), not 25 hand‑coded files**:
- **`spec.py`** — `StrategySpec` (typed, versioned, content‑hashable, mutable by an
  evolution engine) with `RuleSpec`, `StopSpec`, `TargetSpec`, `TrailingSpec`.
- **`primitives.py`** — composable rule blocks: pure `(df, idx, params, direction)→bool`
  functions evaluated on the **closed candle** (`<= idx`, no look‑ahead, enforced by
  tests). Each primitive declares the indicators it needs.
- **`compiler.py`** — `compile_spec(spec) → Strategy` (deterministic; same spec+data → same signals).

**Decision:** extend this — add missing indicators + primitives, then define the 25
families and 10 hybrids as specs. Do **not** build a parallel framework.

## Existing inventory
**Indicators (`indicators/`):** ichimoku, atr, fibonacci, swing, candles, regime,
volatility, moving_averages (SMA, EMA, **RSI**).

**Primitives (10):** `ema_cross`, `price_vs_ema`, `rsi_threshold`, `atr_max`, `atr_min`,
`price_vs_kumo`, `tenkan_kijun_cross`, `chikou_open_space`, `higher_close_streak`, `session_window`.

**Strategies (21 registered):** 12 canonical (bot01–12) + 9 extended (bot13,15–22).
Stops/targets/trailing supported via `StopSpec`/`TargetSpec`/`TrailingSpec` (incl. ATR trailing).

## 25‑family coverage map
| # | Family | Status | Needs |
|---|--------|--------|-------|
| 1 | SMA trend filter | 🟡 partial | `price_vs_sma` primitive (SMA exists) |
| 2 | EMA trend filter | ✅ | `price_vs_ema` |
| 3 | SMA crossover | 🟡 | `sma_cross` primitive |
| 4 | EMA crossover | ✅ | `ema_cross` |
| 5 | Price above/below MA | ✅ | `price_vs_ema/sma` |
| 6 | MACD signal cross | ⛔ | MACD indicator + `macd_cross` |
| 7 | MACD zero‑line | ⛔ | MACD + `macd_zero` |
| 8 | RSI mean reversion | ✅ | `rsi_threshold` |
| 9 | RSI trend continuation | ✅ | `rsi_threshold` |
| 10 | Stochastic | ⛔ | Stochastic + `stoch_threshold` |
| 11 | Bollinger mean reversion | ⛔ | Bollinger + `bb_revert` |
| 12 | Bollinger breakout | ⛔ | Bollinger + `bb_breakout` |
| 13 | ATR volatility breakout | 🟡 | `atr_breakout` (ATR exists) |
| 14 | ATR trailing‑stop trend | ✅ | `TrailingSpec` (atr) |
| 15 | ADX trend‑strength filter | ⛔ | ADX + `adx_filter` |
| 16 | Donchian breakout | ⛔ | Donchian + `donchian_breakout` |
| 17 | Keltner breakout | ⛔ | Keltner + `keltner_breakout` |
| 18 | Parabolic SAR reversal | ⛔ | PSAR + `psar_flip` |
| 19 | CCI mean reversion | ⛔ | CCI + `cci_threshold` |
| 20 | Momentum / ROC continuation | 🟡 | ROC + `roc_threshold` (`higher_close_streak` partial) |
| 21 | Pivot point bounce | ⛔ | Pivots + `pivot_bounce` |
| 22 | S/R swing breakout | 🟡 | `sr_breakout` (swing exists) |
| 23 | S/R bounce | 🟡 | `sr_bounce` (swing exists) |
| 24 | VWAP bias | ⛔ | VWAP + `vwap_bias` (volume — research‑limited where volume unreliable) |
| 25 | OBV / volume confirmation | ⛔ | OBV + Volume MA + `obv_confirm` (volume‑limited) |

**Summary:** ~6 families buildable from existing primitives now; ~13 indicators and ~19
primitives missing. **Volume caveat:** FX has no true volume — VWAP/OBV/Volume strategies
must be marked `research_limited` and validated only on instruments with reliable volume.

## Build plan (focused commits, disciplined order)
1. **Indicator foundation** — add MACD, Stochastic, Bollinger, ADX, Donchian, Keltner,
   PSAR, CCI, ROC, VWAP, Volume MA, OBV, Pivots (centralised in `indicators/`, with
   known‑value/sanity tests). Reuse SMA/EMA/RSI/ATR.
2. **Primitives** — one small, look‑ahead‑safe primitive per family, each declaring its indicator.
3. **Gen‑1 specs** — 25 `StrategySpec` definitions (deterministic defaults, ATR/structure
   stops, max‑bars or clear exits). Tier them `traditional_gen1`.
4. **Hybrids** — 10 curated specs (secondary indicator *confirms/filters*, never contradicts), `hybrid_gen1`.
5. **Registry** — register compiled specs; tier them (canonical / traditional_gen1 /
   hybrid_gen1 / experimental); add registry tests; surface tier metadata for grouped UI.
6. **Research runbook** — small smoke (FX majors × H1/H4 × traditional_gen1) → medium
   (+ Gold, M30/H1/H4) → indices → full canonical universe; then OOS → Monte Carlo → scenario → shortlist.
7. **Promotion rules** — min trades (≥80), positive expectancy, profit factor, max‑DD cap,
   OOS degradation, MC confidence, no realism warnings, no concentration. Lifecycle:
   rejected → research_watchlist → paper_candidate → paper_running → demo_candidate → (live, human‑gated).

## First research run (recommended)
**Traditional Gen‑1 only · FX majors only · H1 + H4 · min 80 trades · rank by composite score** —
not raw PnL. Then expand to FX+Gold (M30/H1/H4), then indices, then full canonical.

## Non‑negotiables (carried from RULES)
Closed‑candle signals only · no intrabar/repaint/lookahead · indicators centralised ·
risk centralised (not in strategies) · strategies broker‑agnostic · no live without human sign‑off ·
rank by robustness, never raw profit · every strategy testable + explainable.
