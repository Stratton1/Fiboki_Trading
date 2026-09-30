# The research ledger the API serves

`FIBOKI_EXPERIMENT_DB` = `var/experiments.sqlite`. Built by
`scripts/build_research_ledger.py` (idempotent; re-running it adds nothing), and
rebuildable from scratch because the ledger is runtime state under `var/` and is
not committed.

**604 rows.** What each one is, and how much of it is original:

| Rows | What | Provenance |
|---:|---|---|
| 14 | hypothesis rows: 5 pre-registrations + 9 campaign status changes | documents ORIGINAL; ledger rows re-filed |
| 16 | prior XAUUSD H4 ladder runs from `research/reports/xauusd_h4/` | **ORIGINAL** `ValidationReport` attached in full |
| 2 | K1 and K2 campaign summary rows | RECONSTRUCTED from the campaign report |
| 572 | K1 (30) and K2 (542) campaign cells | RECONSTRUCTED from the campaign report |

599 rejected, 5 pending (the pre-registrations). 16 distinct dataset versions.
16 rows carry a complete validation report; 72 carry a real structural
fingerprint.

## Why anything is reconstructed at all

The campaigns wrote their ledgers to `<out>/experiments.sqlite` beside their
reports. Those SQLite files were produced on a container scratch path and are not
on this host: `research/reports/campaign_k1_xauusd_h4/` and
`research/reports/campaign_k2_multi_instrument/` hold the JSON reports, the
markdown and the run logs, and no ledger. So the API's ledger started empty while
a large body of real work sat on disk in JSON.

Original artefacts are filed verbatim. The 16 prior ladder runs are complete
`ValidationReport` objects and go in whole, through the same
`run_discovery_campaign.backfill` the campaigns themselves used -- one
implementation, not a copy. The five hypothesis documents in
`research/hypotheses/` are the originals and are re-filed as written.

## Exactly what a reconstructed row loses

Every reconstructed row is tagged `reconstructed` and carries
`original_experiment_id`, `reconstructed_from` and a `reconstruction_note` in its
outputs. 572 of the 574 carry the id the campaign originally minted, so a row
here can be matched back to the campaign report line for line.

Recovered verbatim from the report: strategy id and content hash, dataset
version, hypothesis id, origin operator, parents, generation, rationale, trial
counts (own, external, and the count used for deflation), evaluations, engine
runs, trade count, verdict, the rung it died at, the binding constraint, the full
gate-value map, the deflation threshold and the cross-trial Sharpe variance.

**Not** recovered, and therefore EMPTY rather than guessed:

* **Mutant strategy documents.** The campaign report records a mutant's content
  hash but never its document, so a mutant cell has no `structure_hash` and no
  structural tokens. That is why only 72 rows have a fingerprint: the seed-named
  cells and the backfilled runs, where the document is on disk. Novelty checks
  keyed on `structure_hash` therefore cannot see the 500 mutant cells; they can
  see them by `strategy_content_hash`.
* **Per-cell validation reports.** The ledger derives `rejected_at_rung` from an
  attached report, so that column is empty on reconstructed rows. The rung is in
  `outputs['died_at_rung']` verbatim and restated in the conclusion. No report
  was synthesised to fill the column -- a fabricated report is worse than a blank
  field.
* **Original timestamps.** `ExperimentLedger.create` stamps `created_at` itself,
  so every reconstructed row is dated the day it was rebuilt. The campaign's own
  `created_at` is preserved on the summary row as
  `outputs['original_created_at']`.

## What was deliberately not filed as an experiment

K1's 35 and K2's 273 **skipped** cells, and the refused mutations. A cell that
was never evaluated is not an experiment, and filing 308 of them would inflate
the ledger's own count of work done. They are kept in full on the campaign
summary rows (`outputs['skipped']`, `outputs['rejected_mutations']`) so the
novelty and mutation refusals stay auditable.

## Validation page

`/api/research/validation` reads `research/reports/<strategy_id>.json`. Those four
paths are symlinks to the **current-engine, production-gate-set** report for each
strategy (`xauusd_h4/<id>__production__ds_3c1473cbf.json`) -- not copies, so they
cannot drift, and not the `__ds_45aeaa0e6` set, which that directory's README
marks superseded and says must not be quoted. `rsi_band_mean_reversion` has no
ladder run and is correctly shown as `not_validated`.

---

## Pre-registration: K3, `k3_multi_instrument_h4_engine_v3` (filed 2026-09-29, before any K3 compute)

K3 re-runs K2's search under `engine_v3_realism`. It exists because every K1 and K2
figure is superseded (PLATFORM_STATUS §3 and §4.1): the engine, the account currency, the
sizing path and the blackout source have all changed since those campaigns ran. Nothing below
may be edited after the first K3 cell runs; a change is a new campaign id.

**What is fixed**

| | |
|---|---|
| Campaign id | `k3_multi_instrument_h4_engine_v3` |
| Universe | K2's 16 H4 series: AUDJPY AUDUSD DE40 EURGBP EURJPY EURUSD GBPJPY GBPUSD NZDUSD UK100 US500 USDCAD USDCHF USDJPY XAGUSD XAUUSD (AUDJPY, UK100 and XAGUSD are again in no seed's universe and will be skipped as `out_of_universe`) |
| Seeds and hypotheses | unchanged since K2: the five documents in `research/strategies/` (content hashes 8483154d0f17, fd2a53ed9490, 75caa48f3c20, 78083597c616, 0a1f5a26c24d, checked against K2's run.log) and the five in `research/hypotheses/` |
| Search | `--generations 1`, `--max-grid-points 8`, `--max-values-per-axis 2`, `--folds 4`, `SWEEP_AXES` as in the script, `--max-evaluations 100000` (K2's) |
| Gates | production, `v2.0.0-audit` (`min_trades` 400). **Uncalibrated**: the E-1 gate calibration study (`research/preregistration/gate_calibration_e1.json`) has not run, so the gate set's false-discovery rate and power are unknown and a K3 verdict is conditional on them |
| Account and FX | GBP; quote currencies converted by `build_research_fx_source`: daily rates from the GBP crosses (D1 where stored, otherwise the last H4 bar to close each UTC day, stamped at that close), and via USD (USDxxx and GBPUSD, each leg within 4 days) only where the direct cross has no fresh rate. The label, `fx_coverage.json` and run.log record each pair's derivation and, per series, how many bars convert directly, via USD, or not at all |
| Calendar | `--calendar official`, policy "enforce where covered" (below) |
| Construction | `run_validation`'s default, `construction_v2` via `research_construction_policy()` |
| Holdout | one look, untouched by K1 and K2 on these series; K3 does not spend it unless a cell clears rungs 0 to 5 |

**External prior trials: 4,026**

    4,026 =   350  declared by K2 (166 planned by K1 + 184 in research/reports/xauusd_h4/)
          +   326  K1's true trial count (166 planned + 160 ledger prior)
          + 3,350  planned by K2

K3's ledger is new and sees none of them, and the V2 store's dataset version ids differ from
K1's and K2's, so they are declared by hand. **326 trials are counted twice** (K1 is inside the
350 and is added again), K2 re-planned K1's 30 XAUUSD mutant cells, and K1/K2 ran on earlier
engines; all three raise N and make the deflation harder, and are stated rather than netted off.
The non-duplicated figure is 3,700 (K2's own true N). Not counted, as K2 argued: K2's aborted
first attempt (141 cells, same plan, same bars). If K3's plan reproduces K2's 3,350 planned
trials, K3's true N is about 7,376; the exact figure is whatever `--plan-only` prints, and it is
fixed before the first evaluation.

The bars are the same HistData series as K1/K2, migrated into `var/datastore` from the same V1
store (`research/reports/v2_store_migration/README.md`, which verifies XAUUSD H4 at 26,837 bars;
the other fifteen are asserted by provenance, not byte-compared here).

**What changed since K2** (any one of these moves stored numbers)

- `engine_v3_realism` (`fiboki.backtest.version.ENGINE_V3_REASONS`): FCA/ESMA leverage by
  currency set; BID bars converted to `synthetic_mid` at half the typical spread (K2 traded BID
  as mid); cost-inclusive `fixed_fractional_v2` sizing and the leverage clip; business-day
  financing with triple days; per-asset-class minimum stops; Sharpe on daily 17:00 New York
  equity (bar-return and Lo-adjusted figures beside it); UTC index required.
- Research in GBP, FX from daily GBP-cross closes as-of bar close with 4-day staleness and a
  recorded via-USD fallback before a cross starts (K2: USD account, H4 bid closes of the USD
  pairs, 7-day staleness).
- Event blackouts enforced from the official calendar where it covers (K1 and K2 enforced none).
- Sizing through portfolio construction (`construction_v2`, equal risk, PROBATIONARY, regime
  `unknown` x0.6, unmeasured correlation 0.30), identical to paper; K2 sized flat.
- Walk-forward efficiency on log growth per day (P2-11) and the plateau ratio formula (P2-12).

Because all of these change at once, a K2-to-K3 difference on a cell is not evidence about a
strategy, and no attribution to a single change will be claimed.

**Calendar policy, and why it is honest**

The official calendar declares 2024-01-01 to 2026-12-04 and carries USD, EUR, GBP and JPY; the
universe starts in 2000. `run_cell` already expresses "enforce where covered": with the official
calendar as the blackout source and `allow_empty_calendar=True`, `run_validation` skips only its
coverage refusal and the engine still applies every event the calendar holds
(`tests/unit/test_discovery_campaign_calendar.py::test_the_opt_out_is_explicit_and_serialised`).
So no change to `validation/run.py` or the calendar module was needed. The script adds the
record and the limits: per series it writes the share of bars inside the declared span and the
currencies the calendar does not carry to `calendar_coverage.json`, the head of run.log and the
campaign notes; it sets `allow_empty_calendar` only when a series is partly uncovered; and it
REFUSES a series with bars after the declared end or one whose currencies the calendar carries
none of. One deviation from "lift the refusal only before the declared start", stated: AUD, CAD,
CHF and NZD are never carried, so AUDUSD, NZDUSD, USDCAD, USDCHF and AUDJPY run with blackouts
for their USD or JPY leg only. Refusing them would shrink the universe below K2's. With the data
ending 2025-12-31, two years of each series are inside the span: roughly 8% of bars for the
series that start in 2000 and 12 to 13% for those that start in 2009 or 2010. Blackouts are
absent on the rest, and the report says so per series. Follow-up: a dated pre-2024 calendar (USER_ACTION_NOTE in
`fiboki.marketstate.calendar`) and the four missing currencies.

**FX preconditions, and how they were closed before any compute**

The first draft of this entry named two blockers; both were closed the same day, before K3 ran:

1. `var/datastore` holds H4 and H1 only (the V1 store has no D1). `build_research_fx_source` now
   derives a daily rate from a pair's validated H4 bars (else H1) when no D1 dataset exists: the
   last H4 bar to close in each UTC day, stamped at that bar's close, never its open
   (`fiboki.core.money.daily_rates_from_intraday_closes`; look-ahead test in
   `tests/unit/test_research_fx_intraday.py`). The label tags each such pair
   `[derived:H4 last close per UTC day]` and `fx_coverage.json` carries the derivation.
2. The GBP crosses start later than the series they convert (GBPJPY 2002-05-01, GBPCHF
   2002-08-19, GBPCAD 2007-09-30, EURGBP 2002-03-03, against USDJPY, USDCHF and USDCAD from 2000
   and EURJPY from 2002-03-03). Where the direct cross has no fresh rate, the conversion now uses
   quote->USD times USD->GBP (for example 1/USDJPY x 1/GBPUSD), each leg within the 4-day
   staleness guard, recorded as route `via_usd`
   (`SeriesFxSource(fallback_via_pivot=True)`, golden arithmetic in
   `tests/golden/test_golden_fx_via_usd.py`). The direct cross always wins when it has a fresh
   rate. Consequence, stated: the early years of USDJPY, USDCHF, USDCAD (to 2007-09) and two
   months of EURJPY are converted at an implied cross (two bid closes multiplied) rather than a
   quoted one; the per-series count of `via_usd` bars is in `fx_coverage.json` and run.log.

The pre-flight (`--plan-only`) still refuses (exit 3) if a pair is missing at every timeframe,
and lists as `NO FX COVERAGE` any series with bars that neither route can convert.

**Decision rule, fixed now.** The primary outcome is the number of cells that clear every rung
under `v2.0.0-audit` at K3's true N. Zero survivors: the rule families have no demonstrated edge
net of engine_v3 costs on this universe, as in K1 and K2. Any survivor: reported as a candidate
only; it is not promoted before E-1 has calibrated the gates, and its holdout look is its one
look.

**Command** (on the Mac, from the repository root; the script writes
`research/reports/campaign_k3_multi_instrument/run.log` itself, so do not `tee` into it):

```
.venv/bin/python research/run_discovery_campaign.py \
    --data-root var/datastore \
    --out research/reports/campaign_k3_multi_instrument \
    --cache var/eval_cache_k3 \
    --gates production \
    --campaign-id k3_multi_instrument_h4_engine_v3 \
    --instruments AUDJPY AUDUSD DE40 EURGBP EURJPY EURUSD GBPJPY GBPUSD \
                  NZDUSD UK100 US500 USDCAD USDCHF USDJPY XAGUSD XAUUSD \
    --timeframes H4 \
    --max-evaluations 100000 \
    --account-ccy GBP \
    --calendar official \
    --engine-version-check \
    --external-prior-trials 4026 \
    --external-prior-trials-reason "4026 = 350 declared by K2 (166 planned by K1 + 184 in research/reports/xauusd_h4/) + 326 K1 true trial count (166 planned + 160 ledger prior) + 3350 planned by K2, all on the same 16 HistData H4 series, migrated into var/datastore from the same V1 store under new dataset version ids, so this new ledger sees none of them. K1's 326 is counted twice and K2 re-planned K1's 30 XAUUSD mutant cells; K1 and K2 ran on earlier engines. Double counting raises N and is stated, not netted off. Not counted: K2's aborted first attempt (141 cells, same plan, same bars). See research/reports/RESEARCH_LEDGER.md, K3 pre-registration." \
    --plan-only
```

Read `run.log`, `fx_coverage.json` and `calendar_coverage.json`; if the preconditions hold,
re-run the identical command without `--plan-only`. Re-running resumes from `checkpoint.json`.

**K3 addendum (2026-09-29, before the run):** the dry run on the Mac's `var/datastore` showed 4 to 145 bars per series between 2000-05-30 and 2006-01-03 with no fresh GBP rate on either route (the H4-derived GBP crosses in that store begin on 2006-01-03, and pre-2006 HistData has holiday gaps longer than the 4-day staleness guard). Rather than invent a rate, K3 runs with `--bars-from 2006-01-04T00:00:00Z`, a recorded campaign-level trim written to run.log and the campaign notes. Consequence: about 6 of 26 years are excluded for the seven series that start in 2000 to 2002; the dataset version ids are unchanged. Pre-2006 data was also the segment with the most V1 store defects.

## Pre-registration: K4, `k4_tsmom_dual_horizon_engine_v3` (filed 2026-09-29, before any K4 compute)

K4 runs ONE new document, `tsmom_dual_horizon` (content hash `6fd25ce7669f`, structure hash
`bd40affab19f`; `is_reparameterisation` is False against all five seeds, pinned by
`tests/unit/test_tsmom_seed.py`), under exactly K3's engine, account, FX, calendar, construction,
gates and search budget, on K3's 16 H4 series (its universe carries ten of them: the seven USD
majors, XAUUSD, US500, DE40; the rest skip as `out_of_universe`). It is the first document added
since the roster was frozen for K1, and it exists to test a different bet, the sign of past
returns, not to widen the search around the same ones. Nothing below may be edited after the
first K4 cell runs; a change is a new campaign id.

**What is fixed**

| | |
|---|---|
| Campaign id | `k4_tsmom_dual_horizon_engine_v3` |
| Command | K3's command with `--seeds tsmom_dual_horizon --campaign-id k4_tsmom_dual_horizon_engine_v3 --out research/reports/campaign_k4_tsmom --cache var/eval_cache_k4` and the K3 `--bars-from 2006-01-04T00:00:00Z` trim; `--plan-only` first, and the printed true N is the declared N |
| Seed | `research/strategies/tsmom_dual_horizon.json` as committed (hashes above); declared domain 3 x 3 x 2 x 2 = 36 cells per instrument-timeframe before the script's grid cap |
| Search, gates, account, FX, calendar, construction, holdout | identical to K3 (above), including the uncalibrated `v2.0.0-audit` gate set; a K4 verdict is conditional on E-1 in the same way |
| Timeframes | H4 only in K4 (the store has no D1). The document's primary bet is D1; K4 therefore tests the SHORT-horizon expression (126 H4 bars is about three weeks) and cannot confirm or refute the D1 claim. A D1 campaign needs D1 bars in the store first |
| Runs after | K3 completes; the MacBook cannot hold both (BUILD_LOG 2026-09-29, memory) |

**External prior trials: 7,376, K3's true N.** K4 shares K3's bars and its question ("does
any rule family have an edge net of costs on these series?"), so the honest prior is
everything spent on them: K3's true trial count as printed in its report, 7,376 = 3,350
planned by K3 + 4,026 declared before K3 (which already includes K1 and K2). It is not
4,026 + 7,376: that would count K3's declaration twice. (Corrected 2026-09-30 before any K4
compute; the first draft of this paragraph said "4,026 plus K3's true N".)

**What K4 can and cannot say.** Zero survivors would be consistent with Huang et al. (2020)
and with the document's own evidence-against; it would not be evidence about D1 TSMOM. One or
more cells clearing rungs 0 to 5 would be the first in this repository, and the holdout is
spent only once on the single best cell by DSR, chosen before the look.

**K4 result (2026-09-30, `research/reports/campaign_k4_tsmom/`).** Planned 1,120, true N
8,496 as printed by `--plan-only` and used by the run. 80 cells (ten instruments in the
document's universe; six skipped `out_of_universe`), every one dead at rung 0: 47 on
`min_trades` (observed median 315, min 91, max 397, required 400) and 33 on non-positive
expectancy at the declared defaults. `survivors: []`, holdout unconsumed. Two honest readings,
both stated: the sign-of-returns family trades four to five times as often on H4 as the K3
families (median 315 against 68) and still does not reach the uncalibrated 400 bar; and where it
does trade enough, its default expectancy on H4 is not positive net of costs. Neither is
evidence about the D1 bet, which the store cannot yet test. No threshold is being moved.

## Pre-registration: K5, `k5_generated_families_oanda_mid` (filed 2026-09-30, before any K5 compute)

K5 is the first campaign on OANDA practice MID bars (`fiboki data oanda-backfill`, 2026-09-30:
123 instruments, H1/H4/D1, 2005-01 to 2026-09-29, source `oanda_practice`; `read_latest` now
returns these versions for every pair, so HistData BID with synthetic mid is no longer what
research reads). It carries two rosters at once: the six seed documents, re-run on the broker's
own prices, and the 100 generated documents from `research/generate_families.py`
(`research/generated/`, manifest sha256 `4f87aaedb5fbb63887e462953793d006a6d28f68fa0188f3d27233d6f513148e`, generator families-v1, RNG seed 20260930,
310 grammar cells sampled to 100, at least 12 per family). Nothing below may be edited after the
first K5 cell runs; a change is a new campaign id.

**What is fixed**

| | |
|---|---|
| Campaign id | `k5_generated_families_oanda_mid` |
| Universe | K3's 16 series, now OANDA MID: AUDJPY AUDUSD DE40 EURGBP EURJPY EURUSD GBPJPY GBPUSD NZDUSD UK100 US500 USDCAD USDCHF USDJPY XAGUSD XAUUSD |
| Timeframes | **H4 and D1** (D1 exists for the first time; the TSMOM D1 bet and every generated document that declares D1 are tested at their intended horizon) |
| Roster | 6 seeds (content hashes as committed at c0f1521) + 100 generated (`--generated-dir research/generated`), out-of-universe cells skipped |
| Search, gates, account, FX, calendar, construction, holdout | as K3/K4 (`--gates production`, `v2.0.0-audit`, GBP, official calendar enforce-where-covered, construction_v2, one-look holdout) EXCEPT `--generations 0`: no mutation or pairwise-combination layer. Amended 2026-09-30 before any compute: the first `--plan-only` with K3's `--generations 1` planned 100,000 trials (the budget cap; 1,955 seed cells plus 5,159 combinations and about 2,700 mutations of 106 documents), roughly 35 hours on the MacBook, and a mutation layer over an already-designed grammar is a second search with no new hypothesis. K5 tests exactly the 106 documents as written. FX converts through D1 GBP crosses where stored |
| Bars from | 2006-01-04 (K3's trim, kept so the FX coverage argument stays the same) |
| External prior trials | **8,496** = K4's true N (which contains K3's 7,376 and the 4,026 before it). The OANDA versions are new dataset ids, so the ledger sees no prior; the prior is declared because these are the same sixteen markets and the same question |

**What K5 can say.** Two things, separately: whether any seed clears the ladder on the
broker's own mid prices (K3 said no on HistData); and whether any of 100 recombinations of
known mechanisms clears it. A generated survivor is a HYPOTHESIS, not a strategy: it must be
re-authored as a seed with a written economic story, and its holdout look is spent then, not
here. The deflation threshold rises with the 100 documents, deliberately: that is the price of
looking at a hundred things.

**Multiple-testing warning, stated before the numbers.** The true N will be on the order of
20,000 once K5's planned trials are added; every later campaign on these bars declares at
least that.

**K5 result (2026-09-30, `research/reports/campaign_k5/`).** Planned 12,408 (`--generations 0`),
true N 20,904, 1,955 cells (6 seeds + 100 generated over 16 OANDA MID series on H4 and D1),
2,947 engine backtests, deflation threshold 0.0308 per bar, `survivors: []`, holdout unspent.
Died at: rung 0 sanity 1,920 (1,706 on `min_trades`, observed median 90, maximum 399, **not one
cell reached 400**; 214 on non-positive default expectancy), rung 1: 6, rung 2 walk-forward: 25,
rung 4 robustness: 4 (all on `parameter_plateau`, observed 1.61 to 1.83 against 1.25: two
XAUUSD H4 cells, one GBPJPY H4, and the Donchian seed on XAUUSD H4). Nothing reached deflation.
Every one of the 781 D1 cells died at rung 0: at a daily horizon these rules produce tens of
trades, not hundreds. The 35 H4 cells that passed rung 0 came from the Ichimoku-variant (12),
momentum-oscillator (8), trend-pullback (5), session-breakout (2) families and the Donchian seed
(2); none from band mean reversion or volatility breakout.

**Reading across K3, K4 and K5 (7,376 → 8,496 → 20,904 trials; 2,577 cells; zero survivors).**
The dominant cause is not the strategies, it is the evidence bar meeting the sample: `min_trades
>= 400` has now killed 2,173 of 2,577 cells before any statistical test ran, and the plateau
ratio has killed every cell that survived it. Neither threshold has been calibrated (E-1,
`research/preregistration/gate_calibration_e1.json`, has not run), so the platform currently
cannot say whether these families have an edge; it can only say they do not clear an
uncalibrated bar. **Decision recorded:** no threshold moves; the next research action is E-1,
pre-registered, run on the OANDA MID bars, before any further campaign. A gate lowered until
something passes would be V1 again. Every later campaign on these bars declares at least 20,904.
