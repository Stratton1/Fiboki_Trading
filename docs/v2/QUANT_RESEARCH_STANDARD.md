# Quantitative Research Standard

**Snapshot:** 2026-09-19T05:05Z. Test suite verified in this session:
**2683 passed, 2 skipped** in 287 s.

This document states how research is conducted in Fiboki V2 and what makes a result
admissible. It is the standard `AGENTS.md` binds agents to, and the standard a human
researcher is held to as well. Where a requirement is enforced by code, the enforcement is
named; where it is currently convention, that is said in those words.

---

## 1. The claim this standard exists to make possible

The only claim worth making about a strategy is: *this result was produced by this exact
code, on these exact bytes, under these exact cost assumptions, after this many trials, and
here is why the number survives being deflated by that trial count.*

V1 could make none of those. It could not say which data produced a result, because parquet
was rewritten in place. It could not say what code produced it, because dependencies were
unpinned. It could not say how many trials preceded it, because the trial count was never
recorded. And it could not say what the number meant, because the annualisation was a
constant and the selection window was also the validation window.

Everything below is the minimum arrangement under which that claim becomes checkable.

## 2. Headline profit is never the ranking criterion

This is the first rule because every other rule is downstream of it.

A backtest's net profit is the single most over-fitted number in the artefact. It is the
quantity the search maximised, it is the quantity most sensitive to the two or three luckiest
trades, and it is the quantity that moves most when a cost assumption moves. Ranking on it
selects for the combination whose noise was most favourable.

Fiboki V2 therefore ranks and gates on properties of the *procedure that produced* a result,
not on the result's size:

- the probability the Sharpe exceeds the expected maximum of a search of this size
  (`deflated_sharpe_ratio`, gated `> 0.95`);
- the probability the in-sample winner ranks below the out-of-sample median (`pbo`, gated
  `< 0.20`);
- whether the family beats the benchmark under a correction for the whole search
  (`spa_p_consistent < 0.05`) and whether this specific candidate is in the FWER-controlled
  survivor set (`stepm_member`);
- whether out-of-sample profit is at least half the in-sample rate of the *same selected
  parameters* (`walk_forward_efficiency >= 50%`);
- whether the candidate was profitable in at least 60% of out-of-sample windows;
- whether it still makes money at twice the modelled spread;
- whether it sits on a plateau rather than a spike (`point_plateau_ratio <= 1.25`).

The full gate set with thresholds, rationales and formulas is in `VALIDATION_STANDARD.md`.
Net profit appears in exactly one gate — `survives_2x_spread` — and there only as a sign
test.

Operationally, this means a table sorted by return is not a shortlist, and a research output
that leads with a return figure is not a research output. The `ValidationReport` is the unit
of research communication; it is produced for rejected candidates as well as accepted ones,
and it names the binding constraint rather than reporting a verdict.

## 3. Reproducibility

### Exact pins

`pyproject.toml` pins every dependency that can change a number to a single version, with the
reason written above the block:

```
numpy==2.2.6      pandas==2.2.3     scipy==1.14.1     pyarrow==18.1.0
pydantic==2.10.4  sqlalchemy==2.0.36 alembic==1.14.0  fastapi==0.115.6
uvicorn==0.34.0   httpx==0.28.1     python-dateutil==2.9.0.post0
orjson==3.10.13   typer==0.15.1     rich==13.9.4
```

Python is constrained to `>=3.11,<3.13`. The build backend is pinned too
(`hatchling==1.27.0`). Widening any of these requires re-running the golden tests and
re-stamping stored results; the comment in `pyproject.toml` says so.

Two further layers close what `pyproject.toml` cannot. `deploy/constraints.txt` pins the
transitive dependencies, each with the incident that produced it written above the pin — the
documented example being `click >= 8.2`, which breaks every `--help` in the CLI because
`typer 0.15.1` calls `Parameter.make_metavar()` with the pre-8.2 signature. And
`deploy/requirements.lock` records every installed distribution, with `make lock-check`, the CI
`lockfile` job and `fiboki system doctor` all failing on drift. **Drift in numpy, pandas, scipy
or pyarrow is always an error, never a warning**: a result computed against a different numpy is
not comparable with one computed against the locked version.

### Dataset versioning and content hashes

`data/versioning.py` gives every dataset a deterministic identity:

```
version_id = H( content_checksum || canonical(lineage) )
```

`content_checksum` (`data/schema.content_checksum`) is built from the raw little-endian bytes
of each column in a fixed order, with the index encoded as int64 nanoseconds, and deliberately
excludes any timestamp of when it was taken. Identical content therefore hashes identically on
any machine in any process at any time. The lineage half is the canonicalised chain of
`TransformationStep` records, whose `parameters` must be JSON-serialisable and must fully
determine the step's behaviour — an undeclared parameter is an invisible fork in the lineage.

Consequences: identical content arrived at the same way always gets the same id; the same
content arrived at differently gets a different id; an experiment stores one short string and
can always re-resolve the exact bytes; and nothing about *when* a version was created enters
its identity. Re-registering an id with materially different facts raises `VersionConflict`
rather than overwriting.

A strategy has two hashes, and the distinction matters. `StrategyDocument.content_hash`
(`strategy/dsl.py`) covers semantic content only — renaming a strategy does not change it,
because it is the same strategy. `research/structure.structure_hash` covers the same canonical
view with every number elided, so two documents that AND the same conditions over the same
indicators with the same exit shapes share it however their thresholds differ. The content hash
is what the holdout registry keys on; the structure hash is what research memory keys on, so
that a rediscovered idea with a period of 21 instead of 14 is recognised as the idea it is.

`BacktestResult.data_fingerprint()` records, per instrument, the bar count, first and last
timestamps, and a SHA-256 over the OHLC bytes and index. `BacktestConfig.fingerprint()` and
`ExecutionProfile.fingerprint()` record the full friction assumption set. All three are
stamped onto the `ValidationReport`.

### Determinism tests

- `tests/unit/test_engine_determinism.py` pins the engine's determinism, including the
  property that adding an instrument does not change another instrument's fills (the
  counter-based `rng_for(seed, bar_index, sequence)` derivation in `sim/profiles.py`).
- `BacktestResult.ledger_sha256()` hashes a canonical tab-separated form built from `repr` of
  each float, over `LEDGER_COLUMNS` — which excludes UUIDs on purpose, because a UUID is random
  by construction and would defeat the test it is most often mistaken for evidence of.
- `tests/golden/` holds hand-calculated constants: `test_indicator_values.py` pins indicator
  outputs on a 20-bar series chosen so most intermediate quantities are exact binary fractions,
  and `test_golden_pnl.py` writes out the arithmetic for every expected P&L figure in the test's
  own docstring, using round conversion rates (USD→GBP 0.80, JPY→GBP 0.0055) so a conversion
  error cannot hide inside a plausible number. These carry the `golden` marker, declared in
  `pyproject.toml` as *"hand-calculated financial correctness tests. Never weaken to make them
  pass."*
- `tests/unit/test_no_lookahead.py` and `tests/unit/test_compiler_causality.py` corrupt future
  bars and demand a bit-identical past. `tests/unit/test_indicator_causality.py` parametrises
  over `indicators/registry.causality_suite()`, so an indicator added without a causality proof
  is collected automatically rather than silently untested.

What is **not** yet pinned: there are no stored backtest regression fixtures — no committed
ledger hash for a named strategy on a named dataset version that a future change would have to
break deliberately. The golden P&L tests cover the arithmetic; a regression pin over a realistic
run does not exist. It is on the roadmap.

## 4. Experiment lineage

`research/experiment.py` is an append-only SQLite ledger. Every piece of research writes a row:
who asked for it (`ActorKind` distinguishes agents from humans on purpose), why, what was tried,
against which dataset version, with which code version, and what came back.

Append-only is enforced twice. At the API, `ExperimentLedger` exposes `create`, `get` and `list`
and nothing else. At the database, SQLite triggers raise on `UPDATE` and `DELETE`. The second is
the one that matters: an API without a delete method is a convention, a trigger is a property of
the artefact. `is_append_only_violation` exists so a caller can tell that specific refusal apart
from an unrelated SQL failure.

**You do not amend a record.** You append a new experiment whose `parent_experiment_id` points at
the one being corrected and whose `reason` states what was wrong with it. The history then shows
both the error and the correction. A research record that can be quietly tidied is worth nothing,
because its entire value is that it contains the results nobody liked.

`research/lineage.py` assembles the full chain on demand: experiment → its parent experiments
(why it was tried at all) → the strategy document and its mutation ancestry → the dataset version
it was validated on → that version's transformation lineage → the raw source bytes and their
checksum. A missing link is reported as a gap, never skipped, because a chain that quietly omits
one looks complete.

`research/memory.py` closes the loop the other way, and it is meant to be consulted *before* work
is done, not after it is repeated. It answers on three keys: exact (same content hash),
structural (same structure hash — the same rules over the same indicators, differing only in
numbers), and related (weighted structural similarity plus free-text recall over the reasons
people wrote down). The answer is never yes/no: it returns the prior experiments, their outcomes
and the rungs they died at, because "it failed at the deflation rung with a DSR of 0.41" and "it
failed at rung 0 because the data was wrong" call for opposite decisions.

## 5. Selection and validation never share data

This is the defect that made every V1 out-of-sample number meaningless, and it is closed
structurally rather than by discipline.

**The holdout is owned by a registry, not by a convention.** `validation/holdout.py` defines one
segment per dataset version — the final `holdout_fraction` of the date range — and stores it.
Defining it again with different facts raises: a holdout that can be moved is not a holdout.

**A strategy content hash gets exactly one look.** `HoldoutRegistry.claim` writes the consumption
row *first* and returns a token. A second claim for the same `(dataset_version_id,
strategy_content_hash)` raises `HoldoutAlreadyConsumed`, and it raises whether the first
evaluation finished, crashed, or produced a number anybody liked. Claiming before evaluating is
the whole point: a process that dies mid-evaluation must not be able to retry until it gets a
number it prefers. Keying on the content hash rather than the strategy id means renaming or
re-registering cannot buy a second look.

**Earlier rungs cannot touch it.** `HoldoutRegistry.assert_untouched` is called by
`ValidationLadder.run` before any rung executes, and refuses any research window that overlaps
the reserved segment. The leak is a raised exception, not a silent number.

Within the research window, the separation is maintained by construction too. Rung 1 is named
`IN_SAMPLE_SCREEN` and carries `is_evidence = False`; its metrics dict literally contains a
`SCREEN_NOT_EVIDENCE` key stating that in-sample results select but do not demonstrate. Rung 2's
walk-forward sweeps the declared domains on the train window, selects, and evaluates *that
selection* on the test window — `tests/unit/test_walk_forward_transfers.py` constructs a strategy
that passes V1's fixed-parameter procedure and fails this one. Rung 3's combinatorial purged CV
repeats the selection inside every split, because re-slicing one fixed return series would
reassemble the identical sample on every path and produce a "distribution" that is one number
repeated.

`DateWindow` is half-open `[start, end)` on purpose: consecutive windows tile the parent exactly,
with no bar in two of them. An inclusive end would put the boundary bar in both train and test —
a small leak that compounds across folds.

## 6. The standard of evidence

A result is admissible when all of the following hold. Each is checkable.

**Provenance is complete.** The `ValidationReport` carries the strategy content hash, the dataset
version id, the code version (git sha), the engine config fingerprint, the broker profile
fingerprint, the gate set version and its fingerprint, and the ladder config. A report missing
any of these is not evidence; it is a summary.

**The trial count is honest.** Deflation is only meaningful against the real size of the search.
The ladder can see how many parameterisations of *this* strategy were tried; it cannot see that
the same campaign also searched eleven other strategies over sixty instruments and seven
timeframes. `LadderConfig.external_trial_count` exists for exactly that, it is **added** to the
clustered effective count before deflation, and leaving it at zero deflates against the parameter
sweep alone — a floor, not the truth. The report records the value used, so a reader can see
whether the campaign was declared or ignored. **This is the single easiest place in the system to
produce a flattering number honestly-looking, and it is the researcher's obligation, not the
code's.**

**Correlated trials are counted as one.** `stats/multiple_testing.effective_trials_by_clustering`
clusters trial return series on the correlation distance `d = sqrt(0.5 * (1 - rho))` with
hierarchical average linkage, cut at `corr_threshold` (0.7 by default), and uses the cluster
count. The naive adjustment `N_eff = N / (1 + (M-1) * rho_bar)` is deliberately not used, and the
module says why: at M = 23,040 with `rho_bar = 0.3` it gives `N_eff = 3`, and even
`rho_bar = 0.05` gives 20 — which would drop the expected-maximum-Sharpe threshold from 2.03 to
about 0.94 and wave through precisely the results the library exists to stop.

**A metric that cannot be honestly computed is not a number.** `backtest/metrics.py` raises
`DegenerateMetricError` rather than substituting a value: a profit factor with no losing trades
is infinite, not excellent; a Sortino with no returns below the MAR is undefined, not perfect; a
Calmar with zero drawdown is undefined, not infinite; a CAGR on a wiped-out account is undefined,
and the account being wiped out is the thing to report. `strict=True` is the default, and the
module states that ranking code treating `None` as "worst" is correct while code treating it as
"best" is the V1 bug returning. Correspondingly, a gate whose input is missing is
`GateStatus.NOT_EVALUATED`, which **blocks promotion** — a gate nobody ran is not a gate that
passed.

**Rejections are kept.** Every `ValidationReport` is produced and stored, pass or fail. V1 kept
only winners, so its population of results was conditioned on success and its aggregate
statistics were meaningless. A rejection tells the next search where not to go.

**Approximations are stated, not absorbed.** The known ones at this snapshot are listed in
`DATA_ARCHITECTURE.md` and `EXECUTION_ARCHITECTURE.md`, and several are stated in the code that
owns them: static financing rates over a sample in which real rates went from ~0 to ~5.5%;
financing charged on business-day rollovers with a per-asset-class triple day at a fixed UTC hour
and with no holiday calendar (`backtest/position.financing_nights`); a single-lag Hurst estimator published as an indicator of tendency rather
than a measurement; execution-delay stress that rescales the captured move rather than re-walking
bars; and an economic calendar that ships event *types* but no dated instances, so every blackout
query currently returns "not in blackout" — the dangerous default, which is why
`EconomicCalendar.assert_populated` exists.

**Every figure carries where it came from.** `core/enums.Provenance` has eight members ordered by
how much a reader should trust them — backtest, walkforward, out-of-sample, holdout, paper,
shadow, broker demo, broker live — and `api/provenance.Figure` cannot be constructed without one.
The ladder labels each gate input with the provenance of the rung that produced it.

## 7. The economic-story requirement

A strategy document's `hypothesis` field has a minimum length enforced at the schema level and is
required to state the evidence *against* the idea. This is not decoration. The five seed documents
in `research/strategies/` each do it; the Ichimoku one states plainly that Deng, Sakurai and Ueda
(2021) found no significant Ichimoku profitability in FX after accounting for data snooping, that
the rule family did not survive Step-SPA correction in Coakley, Marzano and Nankervis (2016), and
that the honest prior is therefore "expected edge approximately zero before costs, negative after
spread" — and that it is included as a *baseline*: if the pipeline cannot show this
underperforming, the pipeline is broken.

That is the correct use of a negative prior. A strategy with a stated economic story can be
falsified; a rule set with none is a curve fit waiting to be discovered.

`STRATEGY_STANDARD.md` documents the full requirement.

## 8. What a researcher must do, in order

1. Ask `research/memory.py` whether this has been tried, structurally as well as exactly, and read
   the rungs prior attempts died at.
2. Write the hypothesis and the falsifier *before* running anything. Pre-register the success
   criteria (`agents/research_store.SuccessCriterion`, `ExperimentDesign`).
3. Declare the parameter domains on the document. A sweep is defined by the strategy, not by
   whatever the sweeping script guessed.
4. Run the ladder against a named dataset version, with `external_trial_count` set to the honest
   size of the campaign.
5. Read the `ValidationReport` binding constraint, not the verdict.
6. Write the experiment row, including a rejection. Append a note recording what was learned.
7. If the candidate reached rung 6, the holdout look is spent. There is no second one.

## 9. What is enforced by code and what is not

| Requirement | Enforcement |
|---|---|
| Exact direct dependency pins | `pyproject.toml` |
| Transitive pins | `deploy/constraints.txt` + `deploy/requirements.lock`, verified by `make lock-check` and the CI `lockfile` job |
| Dataset content addressing | `data/versioning.compute_version_id` |
| Experiment ledger immutability | SQLite triggers |
| One holdout look per content hash | `HoldoutRegistry.claim`, claim-before-evaluate |
| No rung before 6 touches the holdout | `HoldoutRegistry.assert_untouched` |
| Walk-forward actually transfers parameters | `WalkForwardRung`, `tests/unit/test_walk_forward_transfers.py` |
| Missing gate input blocks promotion | `GateStatus.NOT_EVALUATED.blocks_promotion` |
| Degenerate metric raises rather than flatters | `backtest/metrics.DegenerateMetricError` |
| Indicator causality | `tests/unit/test_indicator_causality.py` over the whole registry |
| No look-ahead in the engine or compiler | `test_no_lookahead.py`, `test_compiler_causality.py` |
| Hand-calculated indicator and P&L values | `tests/golden/`, `golden` marker |
| Honest `external_trial_count` | **Not enforced in the ladder.** Researcher obligation; recorded on the report. The agent `validation_handler` takes `N` from `ExperimentLedger.count_trials` (family and campaign) and never from its payload; unknown `N` is `NOT_EVALUATED`. |
| Retail leverage caps by the ESMA currency set | `tests/golden/test_golden_retail_leverage.py` (all 123 registered instruments; OANDA marginRate cross-checked against the ESMA class, 12 stricter exceptions listed) |
| BID bars never traded as mid | `backtest/engine._validate_frame`, `tests/unit/test_price_basis_and_utc.py` |
| Resampling statistics are seeded | `tests/unit/test_stats_rng_required.py` (AST) |
| Backtest regression pins over a realistic run | **Not enforced.** Gap. |
| Import-direction layering between packages | **Not enforced.** Gap. |

## 10. Engine generation `engine_v3_realism` (2026-09-29)

The 2026-09 backend audit (`research/reports/F_backend_audit.md` section 2) found eight ways a
stored number could be believed that should not be. Each is corrected in code, pinned by a test and
listed in `backtest/version.ENGINE_V3_REASONS`; every result stamped `engine_v2_exit_vocabulary`
or earlier is superseded (`BUILD_LOG.md`, 2026-09-29).

**Account currency.** Research runs in GBP, the operator's account currency
(`validation.run.RESEARCH_ACCOUNT_CCY`), so research and paper monetary figures are comparable.
Quote-currency conversion uses `core.money.SeriesFxSource` built by
`validation.run.build_research_fx_source` from the store's validated **D1** closes of the GBP
crosses, each close indexed at the bar's close (open + 1 day), one triangulation leg through USD
when no GBP cross is registered (NZD), and a staleness limit of 4 days. A missing cross refuses the
run, naming every instrument to ingest. HKD has no route (no USDHKD is registered), so HK50 cannot
be researched in GBP until one is.

**Price basis.** The engine refuses a frame labelled `bid`, `ask` or `last`. Research converts BID
bars to `synthetic_mid` by adding half the instrument's registered typical spread
(`data.providers.histdata.bid_to_mid`) and records the conversion in the evaluator's engine
fingerprint (`price_basis_lineage`), so it enters every cache key. An unlabelled frame is recorded
as `assumed_mid`.

**Sizing.** `fixed_fractional_v2` is the default: the risk per unit is the stop distance plus the
spread and two fills of expected slippage (`backtest.engine.stop_out_cost_per_unit`), so "1% at
risk" means 1% lost at the stop including costs. The rule and its cost profile are recorded on
every plan (`TradePlan.sizing_basis`) and in every backtest's config fingerprint (`sizing`).

**Sharpe.** `Metrics.sharpe` is computed on equity resampled to trading days ending 17:00 New York
and annualised by the measured trading days per year. `sharpe_lo_adjusted` applies Lo's (2002)
autocorrelation correction; `sharpe_bar_based` is the old figure, kept for comparison.

**Validation statistics.** Walk-forward efficiency is computed on log growth per day where the
evaluation records its opening equity (every `EngineEvaluator` run does), removing the compounding
bias against longer anchored train windows. The plateau ratio excludes the point from its own
neighbourhood and uses the additive floor `c = |score|`, so `<= 1.25` means "the neighbours keep
at least 60% of the point's score" at any scale. Neither gate threshold moved; E-1
(`research/preregistration/gate_calibration_e1.json`) will measure whether they should.

## 11. Generated strategy families (2026-09-30)

**What the grammar is.** `research/generate_families.py` enumerates a closed grammar
FAMILY × ENTRY_EVENT × FILTER × EXIT_MODEL in which every axis is structural: a different rule,
indicator, rule section or exit kind, never a different number. Six families, each with one
hand-written economic story and its evidence against (`trend_pullback`, `breakout_vol`,
`mean_reversion_band`, `momentum_oscillator`, `ichimoku_variants`, `session_breakout`); two or
three entry events per family; filters `none`, a realised-volatility regime gate, an ADX regime
gate, or a session restriction (fixed literals: a filter is a claim about market state, not a knob);
exits `time_exit`, `tp_r`, `trail`, and a swing-structure stop with one R-multiple target. The
grammar has 310 cells; every cell's `structure_hash` is computed and any that equals another
cell's or a seed's is dropped (none did: 310 after dedupe), and a seeded (`rng_seed` 20260930),
stratified sample of exactly 100 is written to `research/generated/` with at least 12 per family.
Each document declares at most three `choice` parameters (at most 18 declared cells, so the
runner's default 8-point grid covers every axis). `MANIFEST.json` records the generator version,
the grammar size before and after dedupe, the seed, every id with its structure and content hash,
and the SHA-256 of the generator source; regeneration is byte-identical
(`tests/unit/test_generated_families.py`).

**All 100 are ONE pre-registered campaign, K5.** They enter a campaign only through
`research/run_discovery_campaign.py --generated-dir research/generated`, which records the count,
the directory and the manifest sha256 in `run.log` and in the report notes. Every document raises
the trial count against which **every** strategy in the project is deflated, the six seeds
included; K5's planned trials must be added to the external prior of any later campaign on the
same bars, exactly as K3's were declared for K4. A campaign that ran the 100 and reported only the
best, or ran them in batches under separate campaign ids, would deflate against a fraction of the
search it actually did.

**A survivor is a hypothesis, not a result.** A generated document shares its family's story with
every other member of the family; nothing about the particular recombination was argued for before
it was tested. A generated document that survives the ladder must be re-authored as a seed in
`research/strategies/` with its own written economic story and evidence against, justified as a
different bet by `is_reparameterisation`, and tested in a NEW pre-registered campaign on data K5
did not select on, before anything about it is trusted.

**Known limitation: the generator cannot invent a mechanism.** It only recombines entry events,
filters and exits under six stories written by hand. Two recombinations are cheap and several
families borrow a seed's mechanism (`ichimoku_variants` re-uses the seed's pieces; `breakout_vol`
re-uses the Donchian break behind a compression setup; `mean_reversion_band` overlaps
`rsi_band_mean_reversion`), so correlated survivors across a family and its seed are closer to one
bet counted several times than to independent confirmation.

Named approximations the grammar adds: the `breakout_vol` compression setup compares 20-bar with
100-bar realised volatility, a proxy for "below its 100-bar median" (there is no rolling
percentile indicator); the realised-volatility regime gate is the seeds' absolute per-bar band
`[0.0015, 0.05]`, which binds much more often on H4 FX than on D1 or on metals; the session filter
restricts documents to H4 because D1 has no intraday hours; and the swing-structure exit is split
into long-only and short-only documents because `StopModel` has one `level` operand for both
sides, so a both-sided swing stop would silently fall back to the minimum-distance floor on one
side.
