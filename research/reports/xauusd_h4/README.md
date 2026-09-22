# XAUUSD H4 ladder reports — two engine generations

This directory holds two sets of `ValidationReport`s for the same four seed
documents on the same HistData XAUUSD H4 bars. **They are not comparable as
results, and the older set must not be quoted.**

| Suffix | Dataset version | Engine | Status |
|---|---|---|---|
| `__ds_45aeaa0e6` | `ds_45aeaa0e6921181e43384041` | Stop and first take-profit only | **SUPERSEDED. Do not quote.** |
| `__ds_3c1473cbf` | `ds_3c1473cbf51e5a641ef41cbf` | Full DSL exit vocabulary | Current |

Both were ingested from the same provider parquet through the same
`HistDataParquetProvider` path and cover the same 26,837 bars,
2009-03-15 .. 2025-12-31. The version ids differ because a `DatasetVersion` id
hashes the content checksum **together with its lineage**, and a fresh ingest is
a fresh lineage node. The bars are the same bars.

## Why the older set is superseded

`backtest/engine.py` used to read `signal.take_profit_prices[0]` and nothing
else. It had no handling for trailing stops, `move_stop_to_breakeven_at_r`,
`max_bars_in_trade`, `cooldown_bars_after_exit`,
`allow_reversal_on_opposite_signal` or `EventRestriction`, and the per-leg
`allocation` fractions were discarded. Every `__ds_45aeaa0e6` number is
therefore a number for a **stop-and-first-target-only realisation** of these
documents — a different strategy from the one each document describes.

The clearest case is `donchian_breakout_atr`, which declares no take-profit at
all and relies entirely on an ATR chandelier. With the trail unimplemented every
position ran to its hard stop or to the end of the data, and the strategy
produced **six trades in thirteen years**.

## What changed

Rung-0 baseline: the documents' **declared defaults**, no sweep, research window
2009-03-15 .. 2022-08-22, USD account, `IG_REALISTIC`.

| Strategy | Trades | Net USD | Expectancy USD | Per-bar Sharpe |
|---|---|---|---|---|
| `donchian_breakout_atr` | 6 → **583** | 2,087.24 → 1,852.16 | 347.87 → 3.18 | 0.00167 → 0.00304 |
| `ichimoku_kumo_trend` | 71 → **49** | 720.16 → **−51.63** | 10.14 → **−1.05** | 0.00454 → −0.00042 |
| `macd_ema_trend_hybrid` | 72 → **41** | 226.40 → 259.59 | 3.14 → 6.33 | 0.00191 → 0.00249 |
| `fib_golden_pocket_pullback` | 283 → **231** | −4,166.02 → −3,829.87 | −14.72 → −16.58 | −0.01340 → −0.01501 |

Exit-reason mix under the new engine, same baseline:

| Strategy | stop | trailing | take-profit | time | partial fills |
|---|---|---|---|---|---|
| `donchian_breakout_atr` | 135 | 447 | 0 | 0 | 0 (declares no legs) |
| `ichimoku_kumo_trend` | 23 | 20 | 6 | 0 | 15 |
| `macd_ema_trend_hybrid` | 19 | 22 | 0 | 0 | 30 |
| `fib_golden_pocket_pullback` | 129 | 68 | 32 | 2 | 41 |

(`donchian_breakout_atr` also has one `end_of_data` exit.)

`trailing` counts every exit whose stop had been **moved** — by a chandelier or
by a breakeven rule. `fib_golden_pocket_pullback` declares no trail, so all 68
of its `trailing_stop` exits are breakeven stop-outs. `ExitReason` has no
`BREAKEVEN` member and its values are persisted, so the label is reused rather
than the enum widened.

### Two separate causes, and they pull in opposite directions

1. **The trail, the scale-outs and the time stop** shorten positions, which
   frees the instrument for re-entry. That is what turns six donchian trades
   into hundreds.
2. **`avoid_rollover_hour`**, which every seed document declares `true` and
   which was previously ignored entirely, now refuses any entry that would deal
   on the 21:00 UTC bar. On an H4 clock that is **one entry bar in six**, and it
   is why `ichimoku_kumo_trend` fell from 71 trades to 49 and from +720 USD to
   −52 USD. The restriction is the documents' own, and the `IG_REALISTIC`
   profile triples the spread in that window, so declining to deal there is what
   the documents asked for.

## Verdicts

**Every strategy is still rejected, under both gate sets.** Nothing was tuned to
change that, and rejection remains the expected and honest outcome for a single
instrument on a single timeframe.

| Strategy | Before | After (diagnostic, min_trades 25) | After (production, min_trades 400) |
|---|---|---|---|
| `donchian_breakout_atr` | rung 0: 6 trades | **rung 2**: WFE 38.86 vs ≥ 50 | **rung 2**: clears the 400-trade bar on 583 trades, then WFE 38.86 |
| `ichimoku_kumo_trend` | rung 2: WFE −151 | **rung 0**: expectancy −1.05 | rung 0: 49 trades vs 400 |
| `macd_ema_trend_hybrid` | rung 2: WFE +42.36 | **rung 2**: WFE 5.59 vs ≥ 50 | rung 0: 41 trades vs 400 |
| `fib_golden_pocket_pullback` | rung 0: expectancy −14.72 | rung 0: expectancy −16.58 | rung 0: expectancy −16.58 |

Two strategies got **worse** when the engine started honouring their documents:
`ichimoku_kumo_trend` crossed from a positive to a negative expectancy, and
`macd_ema_trend_hybrid` went from just under the walk-forward bar (+42.36, short
by 7.6) to well under it (+5.59). That is the direction an honest correction is
allowed to go, and it is the direction it went.

`donchian_breakout_atr` is the only one that improved its standing, and only by
becoming measurable at all: six trades could never have been evidence of
anything. It still fails.

## One thing that is NOT like-for-like

The rung-0 baselines above are directly comparable: both generations computed
them at the documents' declared defaults, with no sweep. **The rung-2
walk-forward numbers are not.** The `__ds_45aeaa0e6` run swept a restricted set
of axes (12 trials for `ichimoku_kumo_trend` and `macd_ema_trend_hybrid`); the
`__ds_3c1473cbf` run passed no `--sweep-parameters` and therefore swept every
declared axis (16 trials for `donchian_breakout_atr`, 128 for
`macd_ema_trend_hybrid`). Part of the walk-forward movement is the different
grid, not the engine. Re-running the old grid would separate the two; it has not
been done.

## Holdout

No candidate reached rung 6 in either generation, so no holdout look was spent
by either run, and the fresh registry used for the new run does not represent a
second look at a segment already seen.

## Reproducing

```
python research/run_xauusd_h4_ladder.py \
    --histdata <canonical histdata root> \
    --data-root <a scratch data root> \
    --out research/reports/xauusd_h4 \
    --gates diagnostic \
    --strategies donchian_breakout_atr ichimoku_kumo_trend \
                 macd_ema_trend_hybrid fib_golden_pocket_pullback
```

Roughly 35 minutes for the diagnostic set and 7 for the production set on one
core, with a warm evaluation cache.
