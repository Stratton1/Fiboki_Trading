# Strategy Factory Gen-1 — First Research Run

**Date:** 2026-06-20
**Run:** 35 Gen-1 families (25 traditional + 10 hybrid) × 7 FX majors × {H1, H4}
= **490 backtests**, min 80 trades, ranked by composite score.
**Result:** 404 qualified (≥80 trades), 86 insufficient, **0 errors**.
Artifacts: `backend/results/phase7/gen1_research_results.csv` (every combo) and
`gen1_research_summary.json` (top-40). Reproduce via
`STRATEGY_FACTORY_RESEARCH_RUNBOOK.md`.

> **This is in-sample, FX-only, untuned research. Nothing here is promoted.**
> No combination has passed OOS / walk-forward / Monte Carlo / scenario stress,
> which are required before any paper/demo candidacy.

## Headline findings

1. **Hybrids earned their keep.** The top three families are all hybrids —
   `hyb_macd_ema_trend` (0.657), `hyb_macd_rsi` (0.623), `hyb_donchian_adx`
   (0.610). Adding a same-direction confirmation/filter to a primary trigger
   measurably improved quality vs the raw single-indicator versions. (Caveat:
   some hybrids — `hyb_psar_macd`, `hyb_ema_macd`, `hyb_sma_adx` — sit near the
   bottom, so the confirm idea helps *selectively*, not universally.)
2. **H4 beats H1.** Average qualified composite score H4 0.275 vs H1 0.235, and
   H4 drawdowns are dramatically lower (top H4 combos run <1–8% DD vs 13–36% on
   H1). The H1 bias/filter families over-trade (300–1600 trades, big DD) — as
   flagged in the runbook, treat raw H1 with care.
3. **USDJPY H4 is the standout instrument/timeframe** this period — several
   MACD/SMA families post PF 1.2–1.7 at ~1% max drawdown. Worth scrutiny for
   regime-specific overfit (2023–24 USDJPY was a strong directional trend).
4. **Volume families behaved as expected.** The `research_limited` VWAP/OBV
   families (14 qualified combos) averaged 0.233 — mediocre, correctly, because
   FX volume is zero. They should only be judged on volume-bearing instruments.

## Shortlist candidates (NOT promotions)

H4 combos with profit factor > 1.2 **and** max drawdown < 15% — 13 combos across
8 families. These are the only ones worth advancing to deeper validation:

| Family | Instrument | TF | Trades | PF | Max DD% | Score |
|--------|-----------|----|--------|----|---------|-------|
| hyb_macd_ema_trend | USDJPY | H4 | 83 | 1.68 | 0.9 | 0.657 |
| hyb_macd_rsi | USDJPY | H4 | 98 | 1.54 | 0.9 | 0.623 |
| hyb_donchian_adx | USDCHF | H4 | 83 | 1.43 | 7.7 | 0.610 |
| trad_cci_meanrev | EURUSD | H4 | 243 | 1.24 | 12.7 | 0.544 |
| hyb_donchian_adx | NZDUSD | H4 | 82 | 1.32 | 7.7 | 0.521 |
| trad_sma_trend | USDJPY | H4 | 279 | 1.30 | 1.2 | 0.515 |
| hyb_macd_ema_trend | EURUSD | H4 | 95 | 1.27 | 6.6 | 0.511 |
| trad_macd_cross | USDJPY | H4 | 217 | 1.26 | 1.1 | 0.502 |
| hyb_macd_rsi | EURUSD | H4 | 108 | 1.26 | 6.9 | 0.478 |
| trad_macd_zero | USDJPY | H4 | 214 | 1.23 | 2.0 | 0.473 |
| trad_sr_breakout | USDJPY | H4 | 90 | 1.21 | 1.0 | 0.473 |
| trad_cci_meanrev | USDCAD | H4 | 122 | 1.23 | 12.3 | 0.411 |

## Best qualified score per family (top / bottom)

**Strongest:** hyb_macd_ema_trend 0.657 · hyb_macd_rsi 0.623 · hyb_donchian_adx
0.610 · trad_cci_meanrev 0.544 · trad_rsi_meanrev 0.540 · trad_sma_trend 0.515 ·
trad_macd_cross 0.502 · trad_sma_crossover 0.493 · trad_macd_zero 0.474 ·
trad_sr_breakout 0.473.

**Weakest:** hyb_psar_macd 0.293 · trad_ema_crossover 0.328 · hyb_ema_macd
0.328 · hyb_sma_adx 0.331 · hyb_keltner_adx 0.341 · trad_pivot_bounce 0.344 ·
hyb_stoch_trend 0.347 · trad_psar 0.360.

## Caveats (do not ignore)

- **In-sample only.** No out-of-sample, walk-forward, Monte Carlo, or scenario
  stress yet — the single biggest reason not to trust these rankings as predictive.
- **Untuned defaults.** Every family uses deliberately generic parameters
  (ATR×2 stop, 2R / 1.5R target). Good first-pass scores may be luck; poor ones
  may just need tuning. This run ranks *defaults*, not *potential*.
- **FX-only, 2 years.** 7 majors, 2023–2024 (USDCAD ~1 year). No metals/indices/
  energy data locally yet, so cross-asset robustness is untested.
- **Execution realism gaps** (carried from RULES): static spreads, zero slippage
  by default, no overnight financing, approximate USD→GBP. Numbers are
  comparative, not live-accurate. Per-instrument DD on USDJPY especially may be
  flattered by the strong 2023–24 trend.
- **H1 over-trading** inflates trade counts and drawdowns for the bias families.

## Recommended next steps

1. Take the ~8 shortlist families to **OOS + walk-forward** (`research/oos.py`,
   `walk_forward.py`), then **Monte Carlo** (`monte_carlo.py`) and the
   **scenario sandbox** (`scenario.py`).
2. Ingest **Gold + indices** data and re-run those survivors cross-asset.
3. Only families that survive all of the above become **paper_candidate** under
   `STRATEGY_FACTORY_PROMOTION_RULES.md` — never straight from this leaderboard.
