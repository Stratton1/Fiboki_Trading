# Validation Standard

**Snapshot:** 2026-09-19T05:05Z (`pytest tests/ -q` → 2683 passed, 2 skipped). Packages: `src/fiboki/validation/`,
`src/fiboki/stats/`. Tests: `tests/integration/test_validation_ladder.py`,
`test_stats_promotion_gate.py`, `tests/unit/test_validation_{gates,report,evaluation}.py`,
`test_holdout_registry.py`, `test_walk_forward_transfers.py`, `test_{sharpe,pbo,spa,cv,bootstrap,multiple_testing,stability,stress}.py`,
`tests/property/test_stats_properties.py`.

All numeric arguments below were computed in this session against the implemented functions,
not quoted from memory. The commands are reproducible from the repository.

---

## 1. The problem, quantified

V1 searched 23,040 strategy × instrument × timeframe combinations and ranked them by raw
performance, with no multiple-testing correction of any kind.

**At α = 0.05 and N = 23,040 you expect 1,152 spurious survivors.** That is not an estimate,
it is `N × α`. A leaderboard produced that way is not a discovery mechanism; it is a sampling
distribution of the maximum.

**Bonferroni demands t ≥ 4.594.** The per-test threshold is `α/N = 2.17 × 10⁻⁶`, and
`Φ⁻¹(1 − 2.17 × 10⁻⁶) = 4.5944` (one-sided). Converting that into a per-trade Sharpe
requirement, `SR_required = t / √n_trades`:

| Trades | Required per-trade Sharpe |
|---:|---:|
| 80 | **0.514** |
| 400 | **0.230** |
| 1,000 | 0.145 |
| 2,000 | 0.103 |

Read those three rows together. **V1's 80-trade minimum combined with 23,040 trials demanded a
per-trade Sharpe that essentially no genuine H1/H4 FX system produces.** The gate as it stood
was statistically vacuous: it could not be cleared by a real edge, only by luck. That is why
V2's `min_trades` gate is **400**, and why the audit's other recommendation — shrink the search
— matters equally. Either the trade minimum rises substantially or the trial count falls
substantially, and in practice you need both.

The same argument in the Sharpe-deflation frame. `stats/sharpe.expected_max_sharpe(N, V)`
implements the Bailey–López de Prado extreme-value approximation

```
SR₀ = √V · [ (1 − γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ],   γ = 0.5772156649
```

Computed in this session at a cross-trial Sharpe standard deviation of 0.5:

| N | SR₀ |
|---:|---:|
| 100 | 1.265 |
| 1,000 | 1.628 |
| **23,040** | **2.030** |

The first two reproduce the published worked values (1.27 and 1.63). At V1's search scale,
**anything ranked first below an annualised Sharpe of 2.03 is indistinguishable from the best
of 23,040 coin-flip sequences.** V1's top-of-leaderboard Sharpes, once honestly re-annualised,
were 1.41 and below. They were inside the noise envelope and were promoted anyway.

## 2. The ladder

`validation/ladder.py`. Seven ordered rungs, fail-fast, each able to reject. The order is by
how cheaply each rung kills a bad idea, so the expensive rungs are only ever paid for by
candidates that survived the cheap ones.

| Rung | Name | Provenance | Evidence? | What it does |
|---:|---|---|---|---|
| 0 | `SANITY` | backtest | yes | One evaluation at the document's declared defaults. Enough trades, positive expectancy, no degenerate metrics. |
| 1 | `IN_SAMPLE_SCREEN` | backtest | **no** | Sweeps the declared domains. Produces the trial family rungs 4 and 5 need. Records `SCREEN_NOT_EVIDENCE`. |
| 2 | `WALK_FORWARD` | walkforward | yes | Sweeps on train, **selects**, evaluates *that selection* on test. Also runs V1's fixed-parameter procedure as a labelled contrast. |
| 3 | `PURGED_CV` | out-of-sample | yes | Combinatorial purged CV with the selection repeated inside every split → a *distribution* of path Sharpes. |
| 4 | `ROBUSTNESS` | backtest | yes | Cost, slippage, delay, deletion and window stresses; parameter plateau versus spike. |
| 5 | `DEFLATION` | out-of-sample | yes | Effective N by clustering, DSR, PBO/CSCV, SPA, StepM membership. |
| 6 | `HOLDOUT` | holdout | yes | The one look. Consumption recorded **before** the evaluation runs. |

A rejection is a research result. Every rung records *why* it rejected, in numbers, and the
report keeps rejected candidates — a rejection tells the next search where not to go, and
throwing it away is how V1 kept rediscovering the same dead ends.

### Rung 0 — SANITY

One evaluation at `candidate.default_params` over the research window. Rejects on: any
degenerate metric ("a statistic computed from these numbers would be decoration"), fewer than
`min_trades` (400), or non-positive expectancy. Cheapest rung, runs first.

### Rung 1 — IN-SAMPLE SCREEN, and why it is not evidence

Its job is to produce the family of trials — every parameterisation the document declares —
because that family is what rungs 4 and 5 operate on. It carries `is_evidence = False` and its
metrics dict contains a literal `SCREEN_NOT_EVIDENCE` entry stating that in-sample results
select but do not demonstrate. It stores the cross-trial Sharpe variance, which is the `V` term
the deflation rung needs and which V1 computed and discarded.

It also builds the `T × N` trials matrix, and **refuses** to build one when the columns are not
aligned. Trade-basis returns are not aligned — two parameterisations take different trades at
different times — so `_trials_matrix` returns `None` with a stated reason rather than padding,
truncating or inventing a correspondence. PBO, SPA and correlation clustering then fail closed
at rung 5.

### Rung 2 — walk-forward that actually fits

V1's "walk-forward" evaluated one fixed parameter set on a train window and then on a test
window. Nothing was selected, so nothing about selection was measured — the procedure could not
detect overfitting because it never overfitted anything.

Here each fold sweeps the domains the document declares on the train window, selects a
parameterisation, and evaluates **that parameterisation** on the test window. That transfer is
the entire point of walk-forward.
`tests/unit/test_walk_forward_transfers.py` constructs a strategy that passes the V1 procedure
and fails this one.

Schemes: `anchored` (train window grows from the start) or `rolling` (fixed-length train),
5 folds by default. Both are worth running; disagreement between them is itself a finding.

The fixed-parameter procedure is still computed and recorded, under keys prefixed
`CONTRAST_`, with a note: a candidate whose contrast figures are healthy while its true
walk-forward figures are not was being selected on noise.

**Walk-forward efficiency** is `100 × mean(OOS rate) / mean(IS rate)`, where the rate is **log
growth per day**, `ln((E0 + net) / E0) / days`, for every evaluation that records its opening
equity `E0` (every `EngineEvaluator` run does, in `meta["opening_equity"]`). It was money profit
per day until `engine_v3_realism`, which a compounding fixed-fractional sizer biases: an anchored
train window one to five slices long has compounded longer than its one-slice test window, so its
money rate is inflated and WFE was biased DOWN for every profitable strategy (audit P2-11; a
strategy growing 10% per 100 days everywhere scored 86% instead of 100%). An evaluator that
records no opening equity (a synthetic one, which does not compound) falls back to money per day
for the whole fold; bases are never mixed within a fold, and the report lists the basis used
(`walk_forward_efficiency_basis`). A non-positive in-sample rate returns `nan` rather than a
flattering number, and `nan` fails the gate.

The modal selected parameterisation across folds is what rung 6 eventually evaluates; ties break
towards the earliest fold so the answer does not depend on dict ordering. `_param_agreement`
records how much the folds agreed, with the note that `n_distinct_selections == n_folds` means
every fold chose differently — the surface has no stable optimum and the selection step is
fitting noise.

### Rung 3 — purged, embargoed, combinatorial CV

Plain k-fold leaks. A trade opened Monday and closed Friday has a label spanning the week; with
Monday in train and Wednesday in test, the training label already contains the test period's
outcome. V1's walk-forward had no purge and no embargo, so every reported out-of-sample number
was contaminated at the fold boundaries by construction.

`stats/cv.py` implements López de Prado's AFML ch. 7 and 12 estimators.
`CombinatorialPurgedCV(n_groups, k)` tests on every combination of `k` of `N` contiguous groups,
purging training samples whose label span overlaps the test span and embargoing samples
immediately after. At the defaults `N = 6, k = 2` that is `C(6,2) = 15` splits reassembling into
`n_backtest_paths(6,2) = C(5,1) = 5` complete backtest paths — verified in this session.

Label spans are trade spans (entry → exit) when the evaluation supplies exactly one trade per
row, because a trade's label is genuinely determined over its whole holding period and purging
must know that; otherwise rows are periods and spans are unit-length. Which basis was used is
recorded in the report, so nobody has to guess.

**The selection is repeated inside every split**, and this is the part that is easy to get wrong
and worthless to get wrong: if a single fixed return series is simply re-sliced, every path
reassembles the identical sample and the "distribution" is one number repeated `n_paths` times.
So for each split the best column is chosen using the **train rows only**, and that column's
test rows become the path's out-of-sample segment. Different splits choose differently — which
is exactly the variation being measured.

A single out-of-sample number is one draw. What matters is the spread across reassembled paths,
and the variance of that spread feeds rung 5. `min_path_positive_fraction` (default 0.5) is a
**ladder config check, not one of the versioned gates**, and the report says so.

### Rung 4 — robustness

Two independent questions.

*Does the edge survive the assumptions moving?* `stats/stress.py` perturbs one assumption at a
time and returns the whole curve, because the shape is the finding: a strategy whose profit
halves between 1× and 1.5× spread is a spread artefact; one that degrades linearly to 5× is a
strategy. The suite runs spread multipliers `(1.0, 1.5, 2.0, 3.0)`, slippage, execution delay in
both `capture` and `adverse` readings, random deletion, and start-date and end-date truncation.
Each curve carries a `breaking_level`.

The `capture`/`adverse` pair is deliberate. `capture` scales gross P&L by `1 − level`, so a delay
costs a share of the move symmetrically and losers shrink too — the right reading for a signal
firing mid-move. `adverse` charges `level × |gross|` as pure cost, never a benefit — the right
reading when the entry edge is a *level* (a Fibonacci retracement, a cloud edge) where being late
means the level is gone. Run both: a strategy that only survives `capture` has an edge that
depends on being early.

`end_date_stress` is particularly sharp for research that was iterated until it worked, because
the most recent period is the one the researcher saw most often.

*Is the selected point a plateau or a spike?* `stats/stability.py` scores each grid point by its
**neighbourhood** — a Chebyshev ball in grid-index space, so it is agnostic to linear, log or
irregular spacing but does assume each parameter's values are meaningfully ordered (a categorical
parameter must not be passed as an axis). Taking the argmax of a noisy surface selects the point
where the noise was most favourable, which is usually surrounded by cells that perform far worse
— and those cells are what live trading delivers. An isolated peak fails the rung outright.

The plateau gate compares `point_plateau_ratio = (s + c) / (m + c)`, with `s` the selected point's
score, `m` the mean of its neighbours **excluding the point itself**, and the additive floor
`c = |s|` (`stats/stability.plateau_ratio`, definition `excluding_point_additive_floor_v2`). For
`s > 0` it equals `2 / (1 + m/s)`, so the unchanged `<= 1.25` threshold means `m >= 0.6 s`: the
neighbours keep at least 60% of the point's score, at any scale. The pre-v3 ratio `s / mean(including
s)` let the point dilute its own test and exploded as the mean approached zero (a score of 0.12
against neighbours at 0.09 failed at 1.33; it now passes at 1.14); it is still reported as
`point_plateau_ratio_inclusive`. A neighbourhood so negative that `m + c <= 0` gives `inf`, which the
gate set treats as not finite and therefore blocking.

The plateau gate is `NOT_APPLICABLE` when the grid is not fully numeric or has fewer than three
points, and that is recorded as not-applicable rather than as a pass.

### Rung 5 — deflation

**How many independent bets was this search?** `effective_trials_by_clustering` clusters trial
return series on the correlation distance `d = √(0.5(1 − ρ))` — a proper distance on [0,1] —
with hierarchical average linkage, cut so trials correlated above `corr_threshold` (0.7) land in
one cluster. The cluster count is the effective trial count.

The naive adjustment `N_eff = N / (1 + (M−1)·ρ̄)` is deliberately **not** used, and the module
says why: it is the effective *sample size* for the variance of a mean of M equicorrelated
variables, not the number of independent extremes, which is what a maximum-of-N correction needs.
At M = 23,040 with a modest ρ̄ = 0.3 it gives `N_eff = 3`; even ρ̄ = 0.05 gives 20. The expected
maximum Sharpe at N = 20 is about 0.94 against 2.03 at N = 23,040, so the naive adjustment would
halve the false-discovery threshold and wave through precisely the results this library exists to
stop. It also assumes a single equicorrelation, which a search spanning sixty instruments and
seven timeframes violates by construction: EURUSD-H1 and EURUSD-H4 are near-duplicates while
EURUSD-H1 and BTCUSD-D1 are not, and one average cannot represent both. Clustering makes no
equicorrelation assumption and degrades gracefully: perfectly independent trials return N,
`g` duplicated families return `g`.

`LadderConfig.external_trial_count` is **added** to the clustered count. The ladder can see this
candidate's own parameter sweep; it cannot see the other eleven strategies over sixty instruments
that the same campaign searched — and that search is the one that produced V1's 23,040-cell
leaderboard. Leaving it at zero deflates against the sweep alone, which is a floor, not the truth.
The report records the value used.

**Deflated Sharpe Ratio.** `DSR = PSR(SR₀)` where `SR₀ = expected_max_sharpe(N_eff, V)` and

```
PSR(SR*) = Φ[ (SR̂ − SR*)·√(T − 1) / √(1 − γ₃·SR̂ + (γ₄ − 1)/4 · SR̂²) ]
```

`SR̂` is non-annualised at the observation frequency (passing an annualised Sharpe with a per-bar
T inflates PSR massively — `sharpe_moments` exists to derive a consistent set). `γ₄` is
**non-excess** kurtosis (3.0 for a Gaussian); passing excess kurtosis trips the moment-inequality
check `γ₄ ≥ 1 + γ₃²`, which raises rather than returning a number.

The variance term `V` is the **larger** of the rung-3 path-Sharpe variance and the cross-trial
Sharpe variance, and the report records which won. Bailey & López de Prado define V as the
variance of trial Sharpes across the search; the path distribution answers a narrower question
(how much this one selected strategy moves between reassembled paths) and is usually smaller,
because paths share a return-generating process while trials do not. Taking the maximum means the
path distribution can only make deflation **more** demanding, never less — feeding it in cannot
accidentally wave something through.

**PBO via CSCV.** `stats/pbo.py` implements Bailey, Borwein, López de Prado & Zhu (2017): split
the `T × N` per-period return matrix into `S` disjoint contiguous row blocks, and for each of the
`C(S, S/2)` ways to choose half as in-sample, take the in-sample winner and find its out-of-sample
relative rank `ω`; `λ = ln(ω/(1−ω))`; `PBO = P(λ ≤ 0)`. At the paper's `S = 16` that is
`C(16,8) = 12,870` combinations (verified). The ladder defaults to `cscv_splits = 8` → 70
combinations, capped further by `min(cscv_splits, (n_obs // 4) * 2)`, and fails closed if fewer
than two even splits are possible.

Two properties a reader must know before acting on one number. PBO has real sampling error — on
pure noise with 800 periods and 100 trials the estimate's standard deviation is roughly 0.13, so a
single PBO of 0.35 is not meaningfully different from 0.5. And with an **odd** number of trials
the null expectation is not 0.5 but `(N+1)/(2N)`, because `ω ≤ 0.5` includes the exact median
rank; `pbo_null_expectation` exists to compare against, and the advice is to use an even number of
trials where possible. The logit distribution is the object of interest, not its summary: a
bimodal λ says selection works in some regimes and inverts in others, which a single number hides.

**SPA and StepM.** `stats/spa.py` implements three related procedures on a shared stationary-
bootstrap index draw — shared across strategies because resampling each column independently would
destroy the cross-sectional dependence that makes a maximum statistic behave as it does.

- `reality_check` — White (2000). Recentres *every* strategy at its sample mean, so hopeless
  strategies still contribute to the null distribution of the maximum; adding junk to the search
  therefore inflates the p-value and destroys power. Included as the historical baseline and the
  upper bound.
- `superior_predictive_ability` — Hansen (2005). Studentises and drops strategies far enough below
  the benchmark that the data rules them out, via `√n · d̄ₖ / ωₖ < −(1/4)·n^(1/4)`. Returns
  `p_lower`, `p_consistent` and `p_upper`. **`p_consistent` is the one to gate on**, and it is the
  only one of the three whose rejection probability converges correctly whether or not poor
  strategies are present. Report all three: a wide lower-to-upper gap means the answer is being
  driven by how the also-rans are treated, which is itself a finding.
- `step_m` — Romano & Wolf (2005). SPA answers "is *any* of them real?"; StepM answers "*which* of
  them are real?", which is what a promotion decision needs, while still controlling the
  probability of any false promotion at α. Each step recomputes the critical value from the
  maximum over the not-yet-rejected strategies, so the cut-off falls as obvious winners are removed
  and genuine-but-smaller effects become detectable.

With a single-point grid there is no selection, so PBO, SPA and StepM are marked
`NOT_APPLICABLE` and the report states that the **campaign-level** correction still applies and
must come from `external_trial_count`.

### Rung 6 — the holdout, and the one look

`validation/holdout.py`. Three properties, each enforced rather than documented.

1. **The segment is defined once.** `define` computes the final `holdout_fraction` of a dataset's
   date range and stores it. Defining it again with different facts raises: a holdout that can be
   moved is not a holdout.
2. **A strategy content hash gets exactly one look.** `claim` writes the consumption row **first**
   and returns a token. A second claim for the same `(dataset_version_id, strategy_content_hash)`
   raises `HoldoutAlreadyConsumed` — whether the first evaluation finished, crashed, or produced a
   number anybody liked. Claiming before evaluating is the point: a process that dies mid-evaluation
   must not be able to retry until it gets a number it prefers. `HoldoutRung` calls `claim` before
   `evaluate`, and its own docstring says that if the process dies halfway the look is still spent,
   which is the correct outcome.
3. **Earlier rungs cannot touch it.** `ValidationLadder.run` calls
   `registry.assert_untouched(dataset_version_id, research_window)` before any rung executes,
   raising `HoldoutLeak` on overlap.

Keying on the **content** hash rather than the strategy id means renaming or re-registering cannot
buy a second look.

The rung fails on fewer than `holdout_min_trades` (30) trades — "the look is spent regardless" —
or on non-positive net profit: *"This is the only untouched evidence there is, and it says no."*

## 3. The gate set

`validation/gates.py`. Gates are **data**, not `if` statements. Each is a row: a name, a metric
key, a comparison, a threshold, the rung that produces its input, and a written rationale. The set
carries a `version` and a `fingerprint()`, both stamped onto every `ValidationReport`, so a reader
two years later can tell whether a result cleared today's bar or a softer one.

**`GATE_SET_V2`, version `v2.0.0-audit`:**

| Gate | Metric | Test | Threshold | Rung |
|---|---|---|---|---:|
| `min_trades` | `n_trades` | ≥ | **400** | 0 |
| `walk_forward_efficiency` | `walk_forward_efficiency` | ≥ | **50 %** | 2 |
| `oos_window_hit_rate` | `oos_profitable_fraction` | ≥ | **0.60** | 2 |
| `survives_2x_spread` | `net_profit_at_2x_spread` | > | **0** | 4 |
| `parameter_plateau` | `point_plateau_ratio` | ≤ | **1.25** | 4 |
| `deflated_sharpe` | `deflated_sharpe_ratio` | > | **0.95** | 5 |
| `pbo` | `pbo` | < | **0.20** | 5 |
| `spa_consistent_p` | `spa_p_consistent` | < | **0.05** | 5 |
| `stepm_survivor` | `stepm_member` | ≥ | **1.0** (boolean) | 5 |

Each rationale is carried in the code. In brief: 400 trades is the point at which a 0.1 per-trade
effect is separable from noise at this search scale (see §1). 50% walk-forward efficiency is where
the selection step starts destroying more than it adds. A strategy profitable in aggregate but in
only half its windows is a bet on one regime that happened to be in the sample. An edge that is
exactly the cost assumption is a cost assumption, not an edge. Above a plateau ratio of 1.25 the
result is a spike in the parameter surface, which does not survive contact with a different sample.
DSR is the single number V1 never computed. PBO at 0.5 means selection carries no information at
all. Hansen's consistent p-value corrects for the whole family being searched. And a candidate
outside the StepM survivor set has not been *individually* shown to have an edge.

### Fail-closed by construction

`GateStatus` has four values and **two of them block promotion**:

- `PASS` — the comparison held.
- `FAIL` — it did not.
- `NOT_APPLICABLE` — the gate genuinely does not apply (a strategy with no numeric parameters has
  no plateau to sit on). Does not block.
- `NOT_EVALUATED` — the input was missing or non-finite. **Blocks.**

A metric absent from the values mapping is `NOT_EVALUATED`. A gate can only be marked
not-applicable by name, deliberately, never by a missing number. That is the whole point: a gate
nobody ran is not a gate that passed. V1's equivalent was a scattered `if` that silently skipped.

`binding_constraint` returns the **first** blocking gate in ladder order, not the worst. The ladder
is fail-fast, so the gate that stopped the candidate is the one a researcher must address; ranking
by shortfall would point them at a gate that was never the obstacle.

### Changing a threshold

`with_overrides(version, **thresholds)` returns a **new** gate set and the version argument is
mandatory and positional. Loosening a threshold is legitimate research — a 15-minute strategy will
not produce 400 trades in a year of data — but doing it without minting a new version is not,
because the report would then claim to have cleared a bar it never faced.

`ValidationLadder.__post_init__` refuses to construct if `LadderConfig.min_trades` disagrees with
the `min_trades` gate threshold, because one of them would then be decorative.

## 4. The report

`validation/report.py`. One `ValidationReport` per candidate, **pass or fail**.

What makes it evidence rather than a summary:

- **It is produced for rejected candidates too.** V1 kept only winners, so its population of
  results was conditioned on success and its aggregate statistics were meaningless.
- **It names the binding constraint.** "Rejected" is not actionable. *"Rejected at RUNG 5
  DEFLATION: deflated_sharpe 0.71 against a required > 0.95, short by 0.24, with N = 12 effective
  trials"* is.
- **It carries the trial count and the cross-trial Sharpe variance.** Those two numbers are what
  make a Sharpe interpretable; without them a reader cannot reconstruct the deflation, and a
  report nobody can check is decoration.
- **It carries the identity of everything that produced it:** strategy content hash, dataset
  version id, code version (git sha), engine config fingerprint, broker profile fingerprint, gate
  set version and fingerprint, ladder config, both windows, the evaluation count, and per-metric
  provenance labels.
- **It serialises deterministically.** The same object always produces byte-identical JSON and
  reloading reproduces the object, so a stored report can be re-hashed and compared years later.
  Non-finite floats are encoded as `{"$float": "nan" | "inf" | "-inf"}` rather than emitted as bare
  `NaN` literals, so the output is strict JSON that a frontend, a database or another language can
  read, and it still round-trips exactly.

## 5. Bootstrap and ruin

`stats/bootstrap.py`. Trading returns are serially dependent: a trend day is not 24 independent
hourly draws. An iid bootstrap understates every dispersion it touches — the same direction of
error as every other V1 shortcut. Everything here resamples **blocks**, with the length estimated
from the data rather than guessed: `optimal_block_length` implements Politis & White (2004) with
the Patton, Politis & White (2009) corrigendum, which fixed the circular-block constant
(`D_CB = (4/3)·g(0)²` against `D_SB = 2·g(0)²`). It returns `b = 1` — iid resampling — when no
autocorrelation clears the significance band, rather than manufacturing structure.

The stationary bootstrap (Politis & Romano 1994) uses geometric block lengths and wraps
circularly, so every observation has equal draw probability; the moving-block bootstrap
undersamples its two ends unless `circular=True`. BCa intervals are deliberately **not** offered:
the acceleration term needs a delete-one jackknife, which assumes exchangeable observations —
exactly the assumption block resampling exists to avoid.

**The V1 Monte Carlo defect, corrected and measured.** V1 drew a trade sequence and applied the
*original, frozen* position sizes. Under fixed-fractional sizing that is the wrong experiment:
real stakes track running equity, so risk stays constant in percentage terms while a frozen stake
becomes a shrinking fraction of a growing account. `resample_with_compounding` re-simulates sizing
off running equity; `resample_fixed_size` is retained only as the V1-equivalent baseline, with the
instruction not to use it as a gate; and `compare_sizing_modes` runs both over the *same*
resampled sequences so the difference is attributable purely to the sizing assumption.

The measured consequence, over a grid of win rates, payoffs, seeds and risk fractions in
`tests/unit/test_bootstrap.py`: against a **peak-relative** ruin barrier — which is what a
maximum-drawdown promotion gate actually is — frozen sizing understates ruin probability at every
realistic risk setting (≥ 5% per trade) by 0.08 to 0.35 in probability, and understates the median
maximum drawdown for every positive-expectancy sample.

The mechanism is stated precisely, because the folk version ("compounding is always riskier") is
false. Once equity has grown above its start, a frozen stake is a smaller percentage of the
account, so every subsequent drawdown is shallower in percentage terms than the strategy would
really take. The effect **reverses** for paths that draw down before they grow — there a frozen
stake never de-risks and so ruins faster, since `prod(1−f) > 1−k·f`. So the gap is uniform in the
median and at realistic risk fractions but is *not* a universal ordering: at 1–2% risk with a
marginal edge the sign can flip, and `ruin_basis="initial"` flips it more often still.
**Frozen-size Monte Carlo is not a conservative bound in either direction — it is simply the wrong
experiment.**

`r_multiples` must be per-trade P&L in units of risk (−1.0 for a trade that lost exactly its
planned risk). Feeding raw currency P&L silently changes what `risk_fraction` means.

## 6. Minimum track record length

`minimum_track_record_length` implements Bailey & López de Prado (2012):

```
MinTRL = 1 + [1 − γ₃·SR + (γ₄−1)/4 · SR²] · (Z_α / (SR − SR*))²
```

returned in observations at the frequency of `SR`. Computed in this session at Gaussian moments
and 95% confidence, with annual observations:

- distinguishing an observed Sharpe of **1.0 from zero**: **5.1 years**;
- distinguishing an observed Sharpe of **1.0 from 0.5**: **17.2 years**.

Under plausible non-Gaussian moments those move to roughly 4.4–6.6 and 14.5–23.2 years
respectively. **Discrepancy noted:** the V1 audit quoted 2.8 and 11.2 years for the same
comparison. Those figures do not reproduce against this implementation under any moment set
tested. The implemented values are the more demanding ones, which is the safe direction, but the
discrepancy is recorded rather than smoothed over and should be reconciled before either number is
quoted to an operator.

The structural point survives either way: **distinguishing a good strategy from a mediocre one
takes an order of magnitude more data than distinguishing it from nothing.** The function returns
`inf` when `SR ≤ SR*`, because no amount of data makes that significant.

## 7. Promotion and demotion

### Promotion

A candidate may leave RESEARCH for a paper allocation when, and only when:

1. every rung 0–6 passed, and
2. every gate in `GATE_SET_V2` returned `PASS` or `NOT_APPLICABLE`, and
3. the `ValidationReport` carries a complete provenance set (strategy content hash, dataset
   version, code version, engine and broker fingerprints, gate set version), and
4. `external_trial_count` reflects the honest size of the campaign.

The ladder enforces 1 and 2 mechanically: after a rung passes, `_blocking_gate_for` re-evaluates
every gate belonging to that rung and converts the rung's `PASS` into a `FAIL` naming the gate if
any blocks. Point 3 is enforced by the report's constructor. **Point 4 is not enforced and cannot
be** — it is a researcher obligation, recorded on the report so the omission is visible.

Beyond the ladder, promotion into a mode is separately gated at execution time by
`risk/gateway.py::_check_strategy_lifecycle`: a strategy whose lifecycle is `DISCOVERY`,
`RESEARCH`, `VALIDATING`, `CANDIDATE`, `DEGRADED`, `QUARANTINED` or `RETIRED` may not trade in any
mode; `LIVE` mode requires lifecycle `LIVE`; `DEMO` mode refuses a `PAPER` strategy. An unknown
lifecycle blocks. See `PORTFOLIO_RISK_STANDARD.md`.

### Demotion

`StrategyLifecycle` includes `WATCH`, `DEGRADED`, `QUARANTINED` and `RETIRED`, the gateway blocks
the last three, and `portfolio/construction.py` excludes non-allocatable lifecycles from receiving
any risk budget before any other adjustment runs.

**What does not exist at this snapshot:** there is no demotion *mechanism*. No module owns
lifecycle transitions, no state machine validates them, `StrategyDocument` carries no lifecycle
field, and nothing computes a degradation signal. The audit's three pre-registered stopping rules
— halt when the probabilistic Sharpe against half the backtested Sharpe falls below 0.50; halt at
the 95th percentile of the bootstrap max-drawdown distribution rather than a round number; and a
CUSUM on excess return calibrated to a roughly two-year in-control run length — **are not
implemented**. The statistical primitives they need (`probabilistic_sharpe_ratio`,
`bootstrap_confidence_interval`) exist; the monitors do not. This is recorded in `ROADMAP.md`.

## 8. What is enforced, and what is not

| Property | Enforcement | Status |
|---|---|---|
| Holdout defined once per dataset version | `HoldoutRegistry.define` raises on redefinition | enforced |
| One holdout look per strategy content hash | `claim` writes before evaluating | enforced |
| No rung before 6 sees the holdout | `assert_untouched` at ladder start | enforced |
| Walk-forward transfers selected parameters | `WalkForwardRung` + dedicated test | enforced |
| Selection repeated inside every CV split | `PurgedCVRung._select_column` per split | enforced |
| Missing gate input blocks promotion | `GateStatus.NOT_EVALUATED.blocks_promotion` | enforced |
| Ladder `min_trades` agrees with the gate | `ValidationLadder.__post_init__` | enforced |
| Threshold change mints a new version | `with_overrides` requires `version` | enforced |
| Report produced for rejected candidates | `ValidationLadder.run` always builds one | enforced |
| Trials matrix refuses unaligned columns | `_trials_matrix` returns `None` + reason | enforced |
| Honest `external_trial_count` | recorded on the report | **researcher obligation** in the ladder; the agent `validation_handler` reads `N` from `ExperimentLedger.count_trials` and blocks when it is unknown |
| Demotion / stopping rules | — | **not implemented** |
| Lifecycle transitions | — | **not implemented** |
| Parameter binding (domain value → rule literal) | `StrategyDocument.bind` / `bind_defaults`; `tests/unit/test_strategy_binding.py` | implemented. `tests/integration/test_validation_ladder_real_engine.py` runs the ladder against the real backtest engine |

## 9. The agent validation job's deflated Sharpe (2026-09-29)

`agents/jobs.validation_handler` evaluates the gate set against ONE recorded backtest. Its deflated
Sharpe now follows the same rules as rung 5, with the two inputs it lacks supplied honestly:

* **`N` comes from the experiment ledger**: the larger of the trials recorded for the strategy's
  family (`structure_hash`) and for the campaign its experiment belongs to
  (`ExperimentLedger.count_trials`). The payload's `n_trials_in_search` may only RAISE it (recorded
  as a caveat); it is never the source. With no ledger injected, or nothing recorded for the scope,
  the DSR is `NOT_EVALUATED` and blocks.
* **The null variance of the trial Sharpes** is Lo's (2002) asymptotic
  `(1 - g3·SR + (g4 - 1)/4·SR²) / (T - 1)` of a per-trade Sharpe on `T` trades, because no
  cross-section of trial Sharpes is recorded. The handler used to pass the variance of per-TRADE
  RETURNS (about 0.01² at 1% risk), which put the expected maximum of 1,000 null trials at ~0.033
  instead of ~0.165 and reported a DSR of ~0.91 where the honest figure was ~0.05 (audit P1-2).

Every DSR a `validation_handler` stored before this change is invalid.

## 10. Research sizes through portfolio construction (2026-09-29)

Every validation run (`EngineEvaluator`, `run_validation`, and so every discovery campaign) now
sizes through the paper runtime's portfolio construction
(`validation.engine_evaluator.research_construction_policy`: construction_v2, equal risk,
PROBATIONARY, regime `unknown`, no conviction) instead of a flat 1% `risk_fraction`, which is now a
ceiling on the tier base; the policy and its version are in the engine fingerprint and so in every
cache key and report. Results from before this change are **superseded for sizing-dependent
metrics**: net profit and every P&L figure (their scale), drawdown, bootstrap ruin probabilities
and any gate on them, and Sharpe-type ratios on period returns (roughly scale-free, but the
throttle and budgets vary size trade to trade, so not identical). They are **not** superseded for
hit rate or trade counts, which depend on entries and exits only, except where construction
refused an entry the flat path took (drawdown past 10%, where PROBATIONARY is suspended, or 15%,
PAUSE; or a second concurrent position on one instrument, rho 1.0 at the hard cap); on the five seed documents
the trade counts and signals seen are unchanged, and the book's `max_concurrent` refusals appear as
`allocation_dropped` instead. `construction=None` reproduces the flat path for comparison.
