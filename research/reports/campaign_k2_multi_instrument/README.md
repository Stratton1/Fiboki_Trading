# Campaign `k2_multi_instrument_h4` — the first multi-instrument discovery campaign

**Nothing survived.** 542 candidate cells, 13 instruments, one timeframe, zero
promotions. That is the expected outcome and it is reported here as a result.

What is new is not the verdict but the depth. K1 never got a candidate past
rung 2. K2 executed **rung 3 (purged CV), rung 4 (robustness) and rung 5
(deflation) against real market data for the first time**, and rung 5 applied
the campaign's true trial count of **3,700** exactly as designed. The holdout
was never reached and remains **unspent on every one of the sixteen datasets**.

| | |
|---|---|
| Universe offered | 16 HistData H4 series, 539,059 bars |
| Universe actually claimed by a seed | **13** (AUDJPY, UK100 and XAGUSD are in no seed's declared universe) |
| Gate set | `v2.0.0-audit` (production; `min_trades` = 400) — **no diagnostic override was run** |
| Account | USD, `IG_REALISTIC`, quote currencies converted by a real FX series |
| Candidate cells evaluated | 542 |
| Cells skipped | 273 (258 out of universe, 15 refused mutations) |
| **True trial count** | **3,700** = 3,350 planned here + 350 declared prior |
| Ladder evaluations requested | 1,456 |
| Engine backtests actually run | 1,454 (a cold cache: nothing was replayed) |
| Deepest rung reached | **RUNG 5 DEFLATION** (one cell) |
| Survivors | **0** |
| Holdout | **unconsumed** — 0 looks spent |
| Wall clock | 7,333 s |

Reproduce with:

```
python research/ingest_histdata_universe.py \
    --histdata data/canonical/histdata \
    --data-root <a fiboki data root> \
    --out research/reports/campaign_k2_multi_instrument

python research/run_discovery_campaign.py \
    --data-root <that data root> \
    --out <an output directory> \
    --cache <an evaluation cache directory> \
    --gates production \
    --campaign-id k2_multi_instrument_h4 \
    --instruments AUDJPY AUDUSD DE40 EURGBP EURJPY EURUSD GBPJPY GBPUSD \
                  NZDUSD UK100 US500 USDCAD USDCHF USDJPY XAGUSD XAUUSD \
    --timeframes H4 \
    --max-evaluations 100000 \
    --external-prior-trials 350 \
    --external-prior-trials-reason "<see below>"

python research/analyse_campaign_k2.py \
    --campaign <out>/campaign_k2_multi_instrument_h4.json \
    --ingest research/reports/campaign_k2_multi_instrument/ingest_manifest.json \
    --out research/reports/campaign_k2_multi_instrument
```

Files here: `ingest_manifest.json` (every series, its integrity report and its
dataset version), `fx_coverage.json` (which pair converts which quote currency),
`campaign_k2_multi_instrument_h4.json` / `.md` (the machine-readable report and
the same facts rendered), `analysis.json` (per-instrument, per-family and
per-rung breakdown), `regime_breakdown__donchian_breakout_atr_m217dad55__GBPJPY.json`
(the regime diagnostic described below), and `run.log` (the console transcript).

---

## 1. The trial count, and the one number a reader must be able to rebuild

    true N = 3,350 (planned across the 542 queued cells, fixed before any compute)
           +   350 (declared prior, spent on these same bars before this campaign)
           = 3,700

Every cell was handed `external_trial_count = 3,700 − its own trials` through
`LadderConfig`. The one cell that reached rung 5 recorded
`n_trials_used_for_deflation = 3700`, so the correction was applied at full
campaign scale rather than against that strategy's own two-point sweep.

### Where the 350 comes from, and why it is not in the ledger

A `DatasetVersion` id hashes the content checksum **together with its lineage**,
so re-ingesting identical bytes through a different path mints a new id for the
same bars. `_prior_trial_count` keys on the version id. K2's fresh ingest of the
XAUUSD H4 parquet produced `ds_817053e6d99…`; K1's 326 trials were recorded
against `ds_3c1473cbf…`. Nothing was wrong with either id and the bars are the
same bars — running `research/run_xauusd_h4_ladder.py`'s `ingest()` against
`data/canonical/histdata` today reproduces `ds_817053e6d99…` over the same
26,837 bars, 2009-03-15 .. 2025-12-31, and `research/reports/xauusd_h4/README.md`
already records that the differing ids are fresh lineage nodes over identical
bytes. A campaign that let those trials vanish would be committing the exact
under-counting Phase K exists to prevent, in a new form.

`CampaignSpec.external_prior_trials` is the declared correction:

    350 = 166 trials planned by K1
        + 184 trials in research/reports/xauusd_h4/ (all four earlier ladder runs)

It is deliberately blunt. It **cannot be negative** and it is **refused without
a reason**, so it can only ever make the deflation harder, and the reason string
is written into the report where a reader can check it.

**Two things are double counted, and both are stated rather than netted off.**
K1 itself counted 160 of that 184 (the remaining 24 are the superseded-engine
set on `ds_45aeaa0e6…`), and K2 re-plans the 30 XAUUSD mutant cells K1 already
ran. Both errors raise N and therefore raise the bar. Counting them down would
have required a judgement nobody could audit.

**One thing is deliberately *not* counted.** A first attempt at this campaign
was stopped after 141 completed cells (see §3). It ran the identical plan on the
identical bars, and every cell it completed was on a USD-quoted instrument where
the FX source added afterwards is a no-op. Those are the same trials this run
performed, not additional ones.

### The threshold that would have applied

> `E[max SR]` for a search of 3,700 trials, at the largest cross-trial Sharpe
> variance observed in this campaign (3.657 × 10⁻⁵, i.e. sd ≈ 0.00605), is
> **0.02183**.

Per BAR, not annualised — the same basis as every Sharpe the ladder computes.
At K1's measured variance the same formula gives 0.02698 for N = 3,700 against
0.02183 for N = 326, i.e. **the bar rose about 24% purely because the search got
bigger**. That is correct and it is the whole point of counting trials.

K1's own reported threshold was 0.0218303 and K2's is 0.0218313 — they agree to
four decimal places by coincidence, because K1's measured variance was larger
than K2's by almost exactly the factor by which K2's N is larger. That is two
different inputs landing on nearly the same output, not a result. The N and the
variance behind each are both recorded so the arithmetic can be redone.

---

## 2. What the data layer found

All 18 series (16 at H4, plus EURUSD and XAUUSD at H1) went
provider → RAW → integrity → CANONICAL. **All 18 are usable. None was rejected.**

**One repair was authorised, and only one.** EURUSD carries a single
non-positive-price sentinel bar at **2001-09-12T01:00:00Z** in both its H1 and
its H4 file — the defect the brief named. It is classified `CRITICAL`, which
would have made the dataset `REJECTED` and `DataStore.read_latest` would have
refused to serve it. It was dropped under an explicit `RepairPlan` naming the
reason and the actor, the removed timestamp is in `ingest_manifest.json`, and
the repaired frame is a CANONICAL dataset whose lineage points back at the
unrepaired RAW bytes. **539,059 H4 bars ingested, 539,058 stored.** Nothing else
was repaired.

**What was reported and deliberately left alone**, because it describes the
market and the calendar rather than corruption:

| Defect | Severity | Scale | Reading |
|---|---|---|---|
| `volume_always_zero` | warning | every bar of every series | HistData ships no volume at all; stored as the absent marker `-1` |
| `misaligned_bar_start` | warning | every bar of every H4 series | H4 bars sit on a 21:00-UTC grid, not a midnight-UTC one — a consequence of the +5h EST-no-DST correction, not an error |
| `off_session_bar` | warning | 1,152 (NZDUSD) – 3,784 (XAUUSD) | metals and indices trade outside the FX session model |
| `unexpected_gap` | warning | 37 (NZDUSD) – 2,126 (UK100) | UK100 and DE40 are exchange-hours instruments read against an FX calendar |
| `return_outlier` | warning | 10 – 59 per series | real moves, at a z-threshold of 12 |
| `stale_run` | warning | 9 on each of the pre-2001 majors | flat runs in thin early data |

`misaligned_bar_start` firing on 100% of H4 bars and 0% of H1 bars is worth a
second look by someone: it is consistent with the alignment check anchoring at
midnight UTC while the corrected HistData H4 grid is anchored at 21:00 UTC. It
is a warning, so it blocks nothing, and it was not changed here.

---

## 3. A platform guardrail fired, and it was right

The first attempt at this campaign errored on **133 of its first 267 cells**:

> `GBPJPY is quoted in JPY but the account currency is USD and no FX source was
> supplied. Pass one, or run in JPY. A 1.0 conversion here would mis-state every
> monetary figure in the report by the exchange rate.`

Nine of the sixteen series are quoted in JPY, GBP, CHF, CAD or EUR.
`run_validation` has always refused them without a rate source, but
`CampaignRunner` had no way to supply one: `run_cell` never forwarded `fx`. A
"multi-instrument" campaign was therefore quietly a seven-instrument campaign,
and the other nine did not fail — they errored, cell by cell, which the
checkpoint correctly refuses to treat as done.

The fix is in `src/fiboki/discovery/campaign.py`: the runner takes an
`FxRateSource`, forwards it to `run_cell`, and **refuses to start if a source is
supplied without `CampaignSpec.fx_label`**, because the source object cannot go
in a JSON report and the label is the only record a reader gets of how
currencies were converted. The campaign then ran with:

    SeriesFxSource(EURUSD->EUR, GBPUSD->GBP, USDCAD->CAD, USDCHF->CHF,
                   USDJPY->JPY; bid closes, HistData H4, as-of backward)

built from the same ingested bars, with the inverse derived by `SeriesFxSource`
rather than by hand. `fx_coverage.json` records every pair, its dataset version,
its observation count and its date range, and the builder checks that no
instrument's first bar predates its converting series' first observation — none
does.

**This conversion is bid-to-bid, not mid-to-mid**, because there is no ask
series. That is an approximation and it is not corrected anywhere.

---

## 4. Which rungs executed, and what they found

| Rung | Cells that executed it | Cells it rejected |
|---|---|---|
| 0 SANITY | 542 | 517 |
| 1 IN_SAMPLE_SCREEN | 25 | 4 |
| 2 WALK_FORWARD | 21 | 19 |
| **3 PURGED_CV** | **2** | **0** |
| **4 ROBUSTNESS** | **2** | **1** |
| **5 DEFLATION** | **1** | **1** |
| 6 HOLDOUT | 0 | — |

Rungs 3, 4 and 5 had never run against real market data before this campaign.

**Rung 3 (purged CV) passed both cells it saw.** Two cells is not evidence that
the rung is lenient; it is two cells. What it does establish is that the rung
executes end to end on real bars, with real purging and embargo, without
erroring.

**Rung 4 (robustness) rejected one cell on `parameter_plateau`**:
`donchian_breakout_atr_m427fba0e` on EURJPY, observed 2.5 against a required
≤ 1.25. The optimum is a spike, not a plateau — the neighbourhood of the
selected parameters does not behave like the selected parameters.

**Rung 5 (deflation) rejected the one cell that reached it, and this is the
result the campaign was built to produce:**

| | |
|---|---|
| Candidate | `donchian_breakout_atr_m217dad55` on GBPJPY H4 (a `combine` mutant) |
| Trades at declared defaults | 688 |
| Selected per-bar Sharpe | **0.01026** |
| `E[max SR]` for N = 3,700 at its measured dispersion | **0.02183** |
| Deflated Sharpe (a probability) | **0.02198** vs required > 0.95 |
| `n_trials_used_for_deflation` | **3,700** |

Its Sharpe was **below** what a search of 3,700 trials is expected to produce
from strategies with no edge at all. Deflated against the whole search it
retains a 2% probability of being real. Under V1's arithmetic — deflating
against a strategy's own sweep — a two-point sweep would have made this look
very different. That gap is the defect Phase K exists to close, and here it
closed on real data.

---

## 5. The distribution of rejection reasons

| Binding constraint | Cells | Share of all 542 |
|---|---|---|
| `min_trades` (< 400) | 402 | 74.2% |
| expectancy ≤ 0 at declared defaults | 115 | 21.2% |
| `walk_forward_efficiency` (< 50) | 17 | 3.1% |
| rung 1: no positive in-sample parameterisation | 4 | 0.7% |
| `oos_window_hit_rate` (< 0.60) | 2 | 0.4% |
| `parameter_plateau` (> 1.25) | 1 | 0.2% |
| `deflated_sharpe` (≤ 0.95) | 1 | 0.2% |

**The 400-trade minimum still dominates, and the brief's premise needs
qualifying.** It was reachable — 140 of 542 cells (25.8%) produced 400 or more
trades at their declared defaults, against a handful in K1 — and that is exactly
why the deeper rungs ran. But `min_trades` was still 74.2% of all rejections,
against roughly 70% in K1. Sixteen instruments did not dissolve the constraint;
they let a minority of cells past it. The right reading is that most of these
documents produce 50–300 trades in 20 years on an H4 clock, whatever the
instrument, and that is a property of the rule families and of the
`avoid_rollover_hour` restriction every seed declares — not of the data supply.

The second-largest bucket is new information: **115 cells had enough trades and
a negative expectancy**, which is a different kind of rejection from "not enough
evidence". AUDUSD is the sharpest case — 13 cells cleared 400 trades and *none*
cleared rung 0, all on expectancy.

Eight cells produced **zero trades**. They are rejected on `min_trades` like any
other, which is correct but slightly flattens a real distinction between "traded
rarely" and "never traded at all".

---

## 6. Did any instrument or family look systematically better?

| Instrument | Bars | Cells | ≥ 400 trades | Cleared rung 0 | Median trades | Deepest rung |
|---|---|---|---|---|---|---|
| GBPJPY | 37,863 | 37 | 12 | **9** | 286 | **RUNG 5 DEFLATION** |
| DE40 | 18,061 | 37 | 7 | **7** | 174 | RUNG 2 WALK_FORWARD |
| XAUUSD | 26,837 | 37 | 10 | 5 | 202 | RUNG 2 WALK_FORWARD |
| EURUSD | 40,540 | 50 | 13 | 2 | 179 | RUNG 2 WALK_FORWARD |
| EURJPY | 38,175 | 37 | 12 | 1 | 281 | RUNG 4 ROBUSTNESS |
| USDCHF | 40,494 | 50 | 13 | 1 | 199 | RUNG 2 WALK_FORWARD |
| AUDUSD | 39,010 | 50 | 13 | 0 | 235 | RUNG 0 SANITY |
| GBPUSD | 40,543 | 50 | 13 | 0 | 213 | RUNG 0 SANITY |
| USDCAD | 39,635 | 50 | 13 | 0 | 172 | RUNG 0 SANITY |
| USDJPY | 40,561 | 50 | 13 | 0 | 209 | RUNG 0 SANITY |
| NZDUSD | 32,714 | 50 | 12 | 0 | 190 | RUNG 0 SANITY |
| US500 | 24,066 | 37 | 9 | 0 | 211 | RUNG 0 SANITY |
| EURGBP | 38,170 | 7 | 0 | 0 | 202 | RUNG 0 SANITY |

**Yes, and the pattern is about the instruments' ranges, not about an edge.**
The two JPY crosses carry the highest median trade counts (286 and 281 against
172–235 for the USD majors) and GBPJPY produced nine of the twenty-five cells
that cleared rung 0. GBPJPY and EURJPY are the widest-ranging pairs in this
universe; a Donchian breakout on a wider-ranging instrument fires more often and
survives a fixed ATR stop more often. DE40 is the mirror image: the *fewest*
bars of any series (18,061) but the highest clear-rate per cell (7 of 37),
because an index's trend persistence suits a channel breakout. Neither is
evidence of an edge. Both are the search finding the instruments whose bar
statistics happen to fit a fixed 400-trade gate and a fixed stop model.

Every cell past rung 2 came from **one family**: `donchian_breakout_atr` and its
mutants, except two `fib_golden_pocket_pullback` mutants at rung 2.

| Family | Cells | Cleared rung 0 | Median trades |
|---|---|---|---|
| `donchian_breakout_atr` | 127 | 23 | 711 |
| `fib_golden_pocket_pullback` | 120 | 2 | 314 |
| `rsi_band_mean_reversion` | 77 | 0 | 200 |
| `ichimoku_kumo_trend` | 103 | 0 | 68 |
| `macd_ema_trend_hybrid` | 115 | 0 | 55 |

The ichimoku and MACD families produce a median of 68 and 55 trades in 20 years.
They cannot reach 400 on any instrument in this universe, at any parameter
setting inside their declared domains. **Running them again on more instruments
of the same kind will not change that**; it is a fact about the documents.

**No timeframe comparison is offered.** H1 exists for only EURUSD and XAUUSD, so
a comparison would be two instruments against sixteen. Both H1 series were
ingested and are available; neither was used.

---

## 7. The regime diagnostic — on a *rejected* cell

**This is not a promotion and must never be quoted as one.**
`donchian_breakout_atr_m217dad55` on GBPJPY was rejected at rung 5. It is
analysed here only because it is the one cell that got far enough for the
question to mean anything, and because that question — *is the P&L a regime
artefact?* — is the one V1's USDJPY result almost certainly failed.

The binding reproduced exactly: rerunning the walk-forward-selected parameters
(`channel_period = 60`) over the research window gives a per-bar Sharpe of
`0.01025734016392154`, identical to the `selected_sharpe` the campaign recorded.
419 trades, net 4,967 USD. (688 was the trade count at *declared defaults*; 419
is the *selected* binding.) The holdout was not touched.

| Axis | Where the money is | Where it is not |
|---|---|---|
| direction | `neutral` 285 trades, +5,985, t = 2.54 | `up` 59 trades, **−990**; `strong_up` −525; `strong_down` −260 |
| volatility | `low` 83 trades, +3,898, t = 2.84 | `very_low` −577 |
| persistence | `random` +2,980; `mean_reverting` +1,919 | **`trending` 108 trades, +68, t = 0.05** |
| liquidity | `deep` +5,067, t = 2.42 | `normal` −1,480 |
| stress | `calm` +3,985 | `elevated` +7 over 61 trades |

**This is a channel-breakout system that makes no money when the market trends.**
Its entire P&L sits in `neutral`-direction, `low`-volatility,
`random`/`mean_reverting` bars — precisely the regimes in which a breakout
strategy has no mechanism. 120% of its net P&L comes from one directional
bucket; take `neutral` away and it loses money. No single-regime t-statistic
exceeds 2.84 even before any multiple-testing correction across the five axes
and nineteen buckets shown.

The method works, and on the deepest cell this campaign produced it says: this
was the sample, not a mechanism. Had this cell cleared rung 5, this table is why
it should still not have been promoted.

Note what is *not* reported: a per-regime Sharpe. Sharpe needs an equity curve
and a time base, and computing one from a bag of trade P&Ls is exactly the
inflation V1 shipped. `pnl_tstat` answers the question people use Sharpe for
here and degrades honestly on small samples; `sufficient` marks the four buckets
below 30 trades.

---

## 8. What was skipped, and why

**258 out of universe**, declined at planning time so no compute was spent. This
is what the seeds actually claim:

| Seed | Declared universe ∩ available H4 | Claims |
|---|---|---|
| `donchian_breakout_atr` | 12 | EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURJPY GBPJPY XAUUSD US500 DE40 |
| `ichimoku_kumo_trend` | 12 | as above |
| `macd_ema_trend_hybrid` | 12 | as above |
| `fib_golden_pocket_pullback` | 12 | as above |
| `rsi_band_mean_reversion` | 8 | EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD **EURGBP** |

**AUDJPY, UK100 and XAGUSD are claimed by nothing** — 50 skipped cells each.
Three of the sixteen instruments the data layer ingested were never testable,
and that is a gap in the seed library rather than a result about those markets.
EURGBP is claimed only by the mean-reversion family, which is why it carries 7
cells against 37–50 elsewhere.

**15 mutations refused before any compute** — 8 `combine` (each pair of parents
declares a parameter of the same name with a different domain), 5
`remove_filter` (the parents declare no filters), 2 `change_regime_gate` (the
gates' bounds are parameter references, not literals).

**0 non-novel.** K2 ran with a fresh ledger and the K1 reports were deliberately
**not** backfilled into it. Backfilling would have keyed them to
`ds_3c1473cbf…`, and the novelty check would then have announced "rediscovery on
NEW DATA" about data that is not new. Running the XAUUSD cells again and counting
K1's trials separately is the honest configuration, and it means 37 XAUUSD cells
here duplicate work K1 did.

---

## 9. What this does and does not demonstrate

It demonstrates that five rule families, expressed as 542 controlled variants
across 13 instruments on H4 over up to 25 years, do not clear the production
promotion gates under an `IG_REALISTIC` cost model against a search of 3,700
trials. It demonstrates that the ladder's deeper rungs execute correctly on real
market data, and that the deflation applies the campaign-wide trial count.

It does **not** demonstrate that the underlying effects do not exist, that
another timeframe would behave the same way, or that a different cost model
would give the same answer.

**What a single historical window on bid-priced data with static spreads does
not show.** These are one realisation of history, not a sample from it: there is
one 2008, one 2015 CHF unpeg, one 2020. The prices are HistData **bid** closes,
so every long entry and every short exit is modelled from the wrong side of a
spread that is then charged again as a static cost; the FX conversion applied to
nine instruments is bid-to-bid for the same reason. Slippage is zero by default,
overnight financing is charged only as the profile configures it, the economic
calendar carries no dated events so every `EventRestriction` blackout other than
the rollover hour is inert, and the intrabar path is unknowable so stop-versus-
target ordering inside a bar is a modelling assumption. A result that survived
all of that would still be a statement about one path through one dataset.

The holdout is **untouched on all sixteen datasets**. No candidate reached rung 6,
so no look was spent, and every holdout segment remains available.

---

## 10. What is and is not comparable to K1

**Comparable.** The gate set is the same (`v2.0.0-audit`, `min_trades` 400) with
the same fingerprint. The cost model, account currency, risk fraction, holdout
fraction (20%), walk-forward fold count (4) and grid-capping policy are
unchanged. The five seed documents and the mutation operator set are byte-
identical. The XAUUSD H4 bars are the same bars. The rejection reasons mean the
same things.

**Not comparable, and the reasons matter.**

1. **The deflation bar is a different bar.** K1's N was 326; K2's is 3,700. A
   candidate's deflated Sharpe under K2 is a harder number than the same
   candidate's under K1, by construction. Comparing deflated Sharpes across the
   two campaigns compares two different corrections.
2. **K2 converts currencies; K1 did not have to.** K1 ran one USD-quoted
   instrument with `IdentityFxSource`. Nine of K2's instruments carry a real
   FX conversion, which K1 never exercised. Monetary figures on those nine are
   not on the same footing as K1's.
3. **K2's XAUUSD cells are re-runs, not new evidence.** 37 XAUUSD cells here
   repeat work K1 did, because K2's ledger was deliberately fresh. They agree —
   the `m3ecf77ae` mutant dies at rung 2 on `walk_forward_efficiency` 39.03 in
   both, to two decimal places — which is a reproducibility check, not an
   independent confirmation.
4. **K1's "non-novel" skips have no counterpart here.** K1 skipped 7 cells as
   rediscoveries; K2 skipped 0, for the reason in §8. K1's 30 evaluated cells
   were therefore all mutants, while K2 evaluates seeds and mutants alike.
5. **Per-rung counts are not rates.** K1 reached rung 2 on 4 of 30 cells; K2 on
   21 of 542. The K2 fraction is smaller and that does not mean K2's candidates
   were worse — K2's population includes four families that cannot reach 400
   trades on any instrument, which K1's XAUUSD-only universe largely excluded by
   the novelty check.
6. **K1's directory `research/reports/xauusd_h4/` is prior work for K2, counted
   in the 350.** Quoting a number from that directory as an independent result
   alongside K2 double counts it a second time.

**What genuinely improved.** K1's rejections were dominated by rung 0 and its
deepest cell died at rung 2, so rungs 3–6 were untested code against real data.
K2 executed rungs 3, 4 and 5 on real bars and rejected at each of them. The
campaign still rejected everything, and it now rejects everything for reasons
drawn from the whole ladder rather than only from its first two rungs.

---

## 11. Platform gaps this campaign surfaced

Three things this run found that are about the platform rather than about any
strategy. Two were fixed here; one is recorded and left alone.

**Fixed — the campaign could not supply an FX source.** `run_cell` never
forwarded `fx` to `run_validation`, so nine of sixteen instruments were
unreachable from a campaign in a USD account. See §3.
(`src/fiboki/discovery/campaign.py`, covered by
`tests/unit/test_discovery_campaign_fx.py`.)

**Fixed — a re-ingest silently erased the search history.** Prior trials are
keyed on dataset version id, which changes on every fresh ingest of identical
bytes, so a campaign after a re-ingest reported zero prior trials. See §1.
(`CampaignSpec.external_prior_trials`, covered by
`tests/unit/test_discovery_declared_prior_trials.py`.)

**Recorded, not fixed — a timestamp-resolution mismatch between the data layer
and the regime segmenter.** `DataStore` returns a `datetime64[us, UTC]` index
(pyarrow's parquet precision) while `Trade` timestamps are `datetime64[ns, UTC]`.
`RegimeSeries.segment_trades` joins the two with `pd.merge_asof`, which refuses
mismatched resolutions and raises

    MergeError: incompatible merge keys [0] datetime64[ns, UTC] and
    datetime64[us, UTC], must be the same type

so **any regime analysis of stored bars fails until one side is converted**.
`research/analyse_survivor_regimes.py` normalises the bar index to nanoseconds
and says so in a comment; no value changes, because H4 bar starts carry no
sub-second component. The proper fix belongs in the data layer or in
`segment_trades` and is outside this campaign's scope.

**Also worth someone's attention, changed nothing here.**
`misaligned_bar_start` fires on 100% of H4 bars in every series and on 0% of H1
bars, which is consistent with `IntegrityConfig.alignment_anchor_utc_minutes`
anchoring at midnight UTC while the corrected HistData H4 grid is anchored at
21:00 UTC. It is a warning, so it blocks nothing and no dataset was rejected
because of it — but a check that fires on every row of every dataset carries no
information in its current form.
