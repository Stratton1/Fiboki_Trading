# Campaign `k1_xauusd_h4` — the first real Phase K discovery campaign

**Nothing survived.** That is the result, it is the expected one, and it is
reported here as a result rather than as a failure.

| | |
|---|---|
| Instrument / timeframe | XAUUSD H4, 26,837 bars, 2009-03-15 .. 2025-12-31 |
| Dataset version | `ds_3c1473cbf51e5a641ef41cbf` |
| Gate set | `v2.0.0-audit` (production; `min_trades` = 400) |
| Account | USD, `IG_REALISTIC`, no FX conversion performed |
| Research window | 2009-03-15 .. 2022-08-22 (80%) |
| Holdout | 2022-08-22 .. 2025-12-31 — **unconsumed** |
| Candidate cells evaluated | 30 |
| Cells skipped | 35 (13 out of universe, 15 refused mutations, 7 non-novel) |
| **True trial count** | **326** = 166 planned here + 160 already in the ledger |
| Ladder evaluations requested | 222 |
| Survivors | **0** |

Reproduce with:

```
python research/run_discovery_campaign.py \
    --data-root <a fiboki data root holding canonical XAUUSD H4> \
    --out <an output directory> \
    --cache <an evaluation cache directory> \
    --gates production \
    --campaign-id k1_xauusd_h4 \
    --backfill-reports research/reports/xauusd_h4
```

Files here: `campaign_k1_xauusd_h4.json` (the machine-readable report, which
round-trips through `CampaignReport.from_json`), `campaign_k1_xauusd_h4.md` (the
same facts rendered), and `run.log` (the console transcript, including the plan
and every skip).

---

## The trial count, which is the point of the whole exercise

    true N = 166 (planned across the 30 queued cells)
           + 160 (already in the ledger for THIS dataset version)
           = 326

Each cell was handed `external_trial_count = 326 − its own trials` — 318 for an
eight-point cell, 324 for a two-point one — through `LadderConfig`, so rung 5
would have deflated against the whole search rather than against one strategy's
own grid. That is the exact correction V1 did not make, and it is the reason its
23,040-cell leaderboard was a sampling distribution of the maximum rather than a
ranking.

The 160 prior trials are the four earlier ladder runs recorded in
`research/reports/xauusd_h4/`, filed into this campaign's experiment ledger by
`--backfill-reports` before planning. They were run on the same bars, so they
were part of the search whichever directory they were written to.

### The threshold that would have applied

No candidate reached rung 5, so no deflated Sharpe was computed. The threshold
it would have faced is reported anyway, because rung 1 measures the dispersion
of trial Sharpes for every candidate that gets past rung 0:

> `E[max SR]` for a search of 326 trials, at the largest cross-trial Sharpe
> variance observed in this campaign (5.583 × 10⁻⁵, i.e. sd ≈ 0.00747), is
> **0.0218**.

Per BAR, not annualised — the same basis as every Sharpe the ladder computes.
A candidate whose per-bar Sharpe was at or below 0.0218 would have been showing
what a 326-trial search is expected to produce from strategies with no edge at
all. The four candidates that got as far as rung 2 had thresholds of 0.0089,
0.0090, 0.0125 and 0.0218 against their own measured dispersions.

---

## What was tried, and where it died

Four of the five seeds claim XAUUSD; `rsi_band_mean_reversion` does not, and
running it on gold would have validated a strategy nobody wrote, so it and its
nine mutants were declined rather than run. All four XAUUSD seeds had already
been run on these exact bars, so the novelty check skipped them and cited the
prior experiments. The 30 cells actually evaluated were therefore all mutants.

| Rung | Cells rejected |
|---|---|
| RUNG 0 SANITY | 26 |
| RUNG 2 WALK_FORWARD | 4 |
| RUNG 1, 3, 4, 5, 6 | 0 reached |

**Rung 0 (26 cells).** Twenty-one failed the 400-trade minimum — the ichimoku
family produces 48-60 trades in thirteen years on this cell, the MACD family
32-120, the fib family 49-375. Five had a negative expectancy at their declared
defaults: every donchian mutant that tightened the stop, changed the target
model, simplified a rule or crossed with another parent turned a small positive
expectancy negative.

**Rung 2 (4 cells).** The four survivors of rung 0 were all donchian mutants,
and all four failed the walk-forward gates:

| Mutant | Operator | Died on |
|---|---|---|
| `..._m3ecf77ae` | `add_filter` (ADX ≥ 20) | WFE 39.0 vs ≥ 50 |
| `..._m76dbb4f7` | `change_regime_gate` (ADX gate) | OOS hit rate 0.50 vs ≥ 0.60 |
| `..._m427fba0e` | `change_session_restriction` (overlap) | WFE 13.7 vs ≥ 50 |
| `..._m79f18824` | `change_confirmation_rule` (RSI midline) | OOS hit rate 0.50 vs ≥ 0.60 |

Walk-forward efficiency is out-of-sample profit rate as a percentage of the
in-sample profit rate **of the same selected parameters**. Below 50% the
selection step is destroying more than it adds — which is to say the parameters
that won each train window were, on average, worse than useless on the window
that followed.

---

## What was skipped, and why

**13 out of universe.** `rsi_band_mean_reversion`, six of its mutants and six
`combine` children whose shared universe excluded gold. Declined at planning
time, so no compute was spent.

**15 mutations refused before any compute** — 8 `combine`, 5 `remove_filter`,
2 `change_regime_gate`. The eight crosses were refused because each pair of
parents declares a parameter of the same name with a different domain
(`stop_atr_multiple`, `adx_floor`, `rsi_ceiling`); one document cannot hold two
meanings for one name, so the cross was refused rather than resolved by a rule
nobody would remember. The five `remove_filter` proposals were made against
parents that declare no filters. The two regime-gate scalings were refused
because those gates' bounds are parameter references rather than literals, so
scaling them would be a reparameterisation of a declared sweep axis rather than
a mutation.

**7 non-novel.** The four seeds, already run on these bars; two session-window
mutants and one target-shape mutant that are reparameterisations of prior work.
Each skip is itself a row in the experiment ledger, with the prior experiment
ids and their outcomes, so the record says what was declined and why rather than
being silent about it.

---

## What this does and does not demonstrate

It demonstrates that four rule families, expressed as 30 controlled variants of
four seed documents, do not clear the production promotion gates on XAUUSD H4
over 2009-2022 under an `IG_REALISTIC` cost model, against a search of 326
trials.

It does **not** demonstrate that the underlying effects do not exist, that
another instrument or timeframe would behave the same way, or that a different
cost model would give the same answer. The dominant rejection reason was the
400-trade minimum, which is a bar written for a campaign across 60 instruments
and which a single instrument on a single timeframe mostly cannot clear on its
own — so most of these cells were rejected for having too little evidence rather
than for having bad evidence. That distinction matters and it is not a
criticism of the bar: too little evidence is a correct reason to refuse
promotion.

The holdout segment (2022-08-22 onwards) is **untouched**. No candidate reached
rung 6, so no look was spent, and it remains available.

### What the campaign filed against each hypothesis

| Hypothesis | Cells | Status filed |
|---|---|---|
| `gold_range_breakout_persists` | 9 | **refuted on this cell** (rungs 0 and 2) |
| `golden_pocket_pullback_holds` | 9 | **refuted on this cell** (rung 0) |
| `kumo_state_conditions_trend` | 6 | **refuted on this cell** (rung 0) |
| `macd_confirms_ema_trend` | 6 | **refuted on this cell** (rung 0) |
| `rsi_band_reversion_in_range` | 0 | unchanged (`proposed`) — not tested here |

"Refuted on this cell" is the status the campaign wrote, and the reason it wrote
alongside it says so in as many words: it refutes the claim on XAUUSD H4, under
this cost model and this gate set. It does not refute the mechanism in general,
and a later campaign on a different instrument would be testing the claim again
rather than re-testing a settled one. The status change is an APPENDED ledger
row pointing at the previous one, so the history of each claim is readable end
to end.

## Known approximations, unchanged by this campaign

Static spreads, zero slippage by default, no overnight financing charged in this
configuration, an economic calendar with no dated events (so `EventRestriction`
blackouts other than the rollover hour are inert), and an unknowable intrabar
path. The `avoid_rollover_hour` restriction every seed declares refuses one
entry bar in six on an H4 clock, which is a large effect on trade counts and is
the documents' own choice — see `research/reports/xauusd_h4/README.md`.

## One thing a reader should know about the trial accounting

The 160 backfilled trials are counted at their reports' own `raw_trial_count`.
Two of those reports are the `diagnostic` gate-set runs of the same strategies
on the same bars, so a strict reading is that the same parameterisations were
counted twice for `donchian_breakout_atr` (16 + 16). Counting them twice makes
the deflation MORE demanding, not less, so the error is in the safe direction;
it is stated here rather than quietly corrected, because a trial count nobody
can reconstruct is the defect this phase exists to fix.
