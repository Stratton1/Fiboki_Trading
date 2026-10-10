# E-4 amendment 1 (2026-10-10, before any E-4 result exists)

Applies to `research/preregistration/pooled_universe_e4.json` (FILED 2026-10-10 at 78f1dba). The
filing is not edited. This amendment records what an adversarial review of the filed instrument
changed before the first replicate was run on evidence data, and why each change is admissible.

**Admissibility.** No P0 replicate and no main-stage candidate has been run on the evidence data
(the OANDA MID bars on the Mac) at the time of this amendment; no E-4 number exists. Every change
below either (a) makes the instrument REFUSE a run the filing never allowed, or (b) resolves a
reading of the filing in the direction that cannot favour a result. No hypothesis, decision
parameter, threshold, candidate, trial count, grid, seed or replicate count changes.

## 1. The admission gate reads the whole filed run, and only the filed run (enforcement)

`scripts/pooled_universe_study.py p0_admits` decided a timeframe's admission from a P0 file's
`partial` flag, `evidence` flag, `config.replicates`, size and z-variance. A COMPLETED SHARD (100 of
400 replicates, `partial: false`, `config.replicates: 400`) therefore admitted the main stage on a
quarter of the filed replicates, and so would a run with a different `--sr` grid, a different
`--seed`, a different gate set or a different trial count, since none of those was checked. Now
refused unless the file covers every replicate (`shard` tiles `range(replicates)` and the rows
match), the edge grid equals `p0.injected_sharpe_grid`, the seed is the filed 20261010, the gate-set
fingerprint is the filed production fingerprint, and the trial accounting is the filed 21,968 /
21,960. Two P0 files for one timeframe passed to `main --p0` are refused rather than the first
taken. Pinned by `tests/unit/test_pooled_universe.py::test_p0_admits_the_filed_run_and_only_the_filed_run`.

## 2. An evidence run may use no setting the filing does not fix (enforcement)

`--instruments`, `--bars-from`, `--folds`, `--spa-bootstraps`, `--stress-samples`, `--seed`, `--sr`
and `--replicates` exist for the harness checks on starter data. An evidence run (`--data-root`)
that moved any of them would have written `evidence: true` into a file the admission and holdout
rules read. `require_filed_configuration` now refuses such a run before any bar is read; starter
runs are untouched. The `holdout` command additionally refuses a main-stage file that is not an
evidence file, was judged under another gate set, or carries another trial count. Pinned by
`test_an_evidence_run_may_use_no_setting_the_filing_does_not_fix`.

## 3. The replicate rule: which file decides (clarification)

The filing's `p0.replicate_rule` says: "if the size point estimate lies within 2 Monte Carlo SE of
0.05, double that timeframe's replicates once and report both". It does not say which of the two
files the admission (decision rule 2) reads. Reading: the 800-replicate file decides, the 400 file
is kept and both are recorded in the ledger. The 800 run is a fresh set of shards from the same
seed (the first 400 replicates of it are byte for byte the 400 run's, since replicate `r` depends
only on the base seed, the timeframe and `r`), so this is more of the same procedure, not a second
chance at a different one; the admission cannot be re-tried on the 400 file once the 800 exists.
`run_e4_mac.sh` names P0 shards and merged files by their replicate count (`.r400.`, `.r800.`) so the
two runs cannot be merged into each other, and `main` reads the one the operator copies to
`e4_p0_<TF>.json` after the ledger names it.

## 4. The memory estimate in `compute.memory_and_disk` is an estimate (correction of a claim)

"Under 1 GB per shard" was not measured on the Mac. Measured here: one cached full-window H4
evaluation (24,000 per-bar returns, 330 trades) serialises to 0.98 MB; a full ladder run holds
about 30 full-window-equivalents per sleeve in the candidate's in-memory cache, and a seed
candidate has up to 16 sleeves, so a main-stage shard on a seed candidate plausibly peaks between
0.5 and 1.5 GB, and the four interleaved shards are all on seed candidates at the start. The runbook
now says to check resident size after the first seed candidate and to re-run with fewer shards if
the four together approach 5 GB. No result depends on the sharding.

## 5. Review notes that change nothing (recorded so a reader knows they were considered)

* `DEFF = 1'S1 / trace(S)` on per-period sleeve P&L is Kish's design effect for the variance of
  the pooled total; since the sum of all trade P&L equals the sum over periods of the pooled
  per-period P&L, `Var(mean per trade) = sigma^2 * DEFF / N` holds whenever the per-period sleeve
  covariance is representative of the covariance of the sleeves' totals, which is the
  cross-sectional-only approximation the filing states. The variance-weighted `rho_bar` is the
  correct weighting for a sum, not an approximation. Heterogeneous sleeves (different trade counts
  or holding periods) are handled by the ratio form without further assumption. Taking the larger
  of daily and weekly and flooring at 1 can only lower `N_eff`.
* Its known blind spot is serial dependence (within a sleeve across periods, and cross-sleeve
  lead-lag beyond a week). P0's `z_variance` tests this only to the extent the stationary bootstrap
  preserves it (dependence within blocks of the Politis-White length on a sparse entry-day series,
  likely a few days). A pass of H_calibration is therefore evidence about the cross-sectional
  correction, weaker evidence about serial dependence. Stated as a limitation of P0, not a defect.
* The `z_variance_max` of 1.25 at 400 replicates (Monte Carlo SE of a variance about 0.07) tolerates
  an `N_eff` overstated by up to about 25% before the check fails; this is the filed resolution.
* `N_eff` does not enter the deflated Sharpe, PBO, SPA, StepM or purged-CV paths (computed on the
  pooled per-period series, whose variance already carries the cross-sleeve covariance), and the
  trial count is charged in full. Verified against `validation/ladder.py`: the only count reads are
  the three filed sites; `n_trades_effective == n_trades` for every non-pooled evaluation, so no
  single-series verdict or gate value changes (existing ladder, gate, holdout and measuring suites
  pass unchanged).
* The composite holdout's research window ends at the EARLIEST constituent holdout start and the
  leakage check is made per constituent against the real registry, so no rung before 6 can read any
  constituent's holdout; a claim is checked on every constituent before any is written.
* Equal-capital sleeves mean the pooled book risks up to `m x 1%` of `m x GBP 10,000` at once; the
  pooled per-bar Sharpe and every gate the ladder reads are scale-free, but a pooled `net_profit` is
  not comparable in money to a single-series one. Stated so nobody compares them.
