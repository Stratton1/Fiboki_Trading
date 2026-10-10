# E-4: pooled-universe candidates

Filing: `research/preregistration/pooled_universe_e4.json` (FILED 2026-10-10). Code:
`src/fiboki/validation/pooled.py` (the pooled evaluator, the effective trade count, the composite
holdout) and `scripts/pooled_universe_study.py` (plan, P0, main stage, the one look, merge).
Ledger: `research/reports/RESEARCH_LEDGER.md`, "E-4 filed".

Nothing in this directory is a result until the filing's decision rule has been applied to it in
the ledger. P0 is read first; the main stage runs only for the timeframes P0 admits.

## What E-4 is, in one paragraph

One strategy document evaluated on every series of its universe (K5's sixteen OANDA MID series) as
ONE candidate: each series is an equal-capital sleeve run by the existing engine path with its own
costs, spread, FX and calendar, and the sleeves' P&L is summed. Because trades on correlated series
are not independent, every count-based gate (rung-0 floor and MinTRL, rung-2 OOS-fold floor, rung-6
holdout floor) reads the effective count `N_eff = N / DEFF`, with `DEFF = 1'S1 / trace(S)` from the
sleeves' daily and weekly P&L covariance (the larger, floored at 1). Gates are `v2.1.0-calibrated`,
unchanged. 172 pooled candidates, 1,064 trials, true N 21,968.

## Runbook (the MacBook, `~/fiboki` runtime checkout)

Preconditions: the branch carrying E-4 is merged into the runtime checkout; the market data is the
marked data root `FIBOKI_DATA_ROOT` (default `var/datastore`, which holds the OANDA practice MID
H4 and D1 bars K5 read); the paper service keeps running. Every stage runs as shards under
`nice -n 10` (so the paper service always gets the CPU first) and `caffeinate -i` (no idle sleep;
keep the Mac on mains). Re-running a stage resumes every shard from its checkpoint, so a sleep, a
reboot or a crash costs at most a minute of work (a main-stage shard re-runs the one candidate it
was on).

```bash
cd ~/fiboki
export FIBOKI_DATA_ROOT="${FIBOKI_DATA_ROOT:-var/datastore}"

# 0. The plan equals the filing (no bars read; exits 3 if it differs).
research/reports/e4_pooled/run_e4_mac.sh plan

# 1. Process P0 FIRST, both timeframes (4 shards each; WORKERS=3 if memory is tight).
research/reports/e4_pooled/run_e4_mac.sh p0 H4
research/reports/e4_pooled/run_e4_mac.sh status          # until every p0_H4 shard says done
research/reports/e4_pooled/run_e4_mac.sh merge-p0 H4     # -> research/reports/e4_pooled/e4_p0_H4.json
research/reports/e4_pooled/run_e4_mac.sh p0 D1
research/reports/e4_pooled/run_e4_mac.sh merge-p0 D1     # -> e4_p0_D1.json

# 2. STOP. P0 is read and recorded in the ledger under the filed rule (size <= 0.05 and
#    z_variance <= 1.25 per timeframe) before the main stage starts.

# 3. The main stage (4 interleaved shards; refuses to start without both merged P0 files, and
#    skips, as not_admitted, every candidate of a timeframe P0 did not admit).
research/reports/e4_pooled/run_e4_mac.sh main
research/reports/e4_pooled/run_e4_mac.sh status
research/reports/e4_pooled/run_e4_mac.sh merge-main      # -> e4_main.json, reports/ beside it
```

Equivalent single commands, for one shard by hand:

```bash
nice -n 10 caffeinate -i .venv/bin/python scripts/pooled_universe_study.py p0 --timeframe H4 \
    --data-root "$FIBOKI_DATA_ROOT" --replicates 400 --replicate-range 0 100 \
    --out research/reports/e4_pooled/shards/p0_H4.0-100.json          # add --resume to continue
nice -n 10 caffeinate -i .venv/bin/python scripts/pooled_universe_study.py main \
    --data-root "$FIBOKI_DATA_ROOT" --shard 0 4 \
    --p0 research/reports/e4_pooled/e4_p0_H4.json research/reports/e4_pooled/e4_p0_D1.json \
    --reports-dir research/reports/e4_pooled/reports \
    --out research/reports/e4_pooled/shards/main.0-of-4.json
.venv/bin/python scripts/pooled_universe_study.py merge research/reports/e4_pooled/shards/p0_H4.*.json \
    --out research/reports/e4_pooled/e4_p0_H4.json
```

Shards must tile the range exactly: `merge` refuses a gap, an overlap, a partial checkpoint or a
shard from a different run (configuration, seed, plan or bars differ). A merged file holds the rows
one uninterrupted run would have written.

### Expected duration and disk on the M1 (8 GB)

| Stage | Work | Wall time, 4 shards |
|---|---|---|
| P0 H4 | 66 source backtests per shard, then 400 replicates x 5 edges of the measuring ladder (about 4 to 6 s per full pooled run on an M1 core) | about 45 to 75 min |
| P0 D1 | 58 source backtests per shard, fewer trades per run | about 20 to 30 min |
| Main, guaranteed part | 11 seed candidates at full depth (at most about 7,200 backtests) and rung 0 of the 161 generated (1,831 backtests) | about 3 to 4 h |
| Main, the rest | about 0.36 h for each generated H4 candidate that passes rung 0 under the MinTRL gate with `N_eff` | expected under 24 h in total; worst case (every candidate to full depth) about 44 h |

The basis is measured, not assumed: E-1 process 2 ran about 26,000 H4 backtests in 14.7 h on this
Mac with four shards (about 1,770 per hour) and K5 ran 2,947 mixed backtests in 7,923 s. A run over
24 hours is reported as such and is never truncated; its result does not depend on wall time.

Memory: under 1 GB per shard. Disk: about 20 to 40 MB per merged P0 file, a few MB per main-stage
shard, up to about 100 MB of main-stage `reports/` (one full ValidationReport per candidate, pass or
fail). `shards/` is git-ignored; the merged files and the reports are the record.

### The one look at the holdout

Not part of the runbook above. Only if the merged main-stage file has a SEED candidate that cleared
rungs 0 to 5 in a timeframe P0 admitted. The command chooses the candidate itself by the filed rule
(highest deflated Sharpe), refuses otherwise, checks the K3 to K5 registries, and spends the look on
every constituent series in the platform's registry:

```bash
.venv/bin/python scripts/pooled_universe_study.py holdout --data-root "$FIBOKI_DATA_ROOT" \
    --main research/reports/e4_pooled/e4_main.json --p0 research/reports/e4_pooled/e4_p0_H4.json \
    --registry var/holdout.sqlite \
    --prior-registry research/reports/campaign_k3_multi_instrument/holdout.sqlite \
                     research/reports/campaign_k4_tsmom/holdout.sqlite \
                     research/reports/campaign_k5/holdout.sqlite \
    --out research/reports/e4_pooled/e4_holdout.json
```

(`--p0` is the merged P0 file of the chosen candidate's timeframe; `--registry` is
`resolve_paths().holdout_db` under `FIBOKI_STATE_DIR`.) A generated survivor never gets the look here:
it is a hypothesis to be re-authored as a seed, as K5 ruled.

## Known approximations (also in the filing)

- Sleeves are independent equal-capital accounts: no shared margin, no cross-instrument
  construction, no book-level concurrency cap.
- The design effect is cross-sectional: serial dependence within a sleeve is not corrected (P0's
  `z_variance` measures the combined effect); it is computed on compounding money P&L.
- P0 is one process (the joint calendar-day bootstrap of real sleeve trades, linear mark-to-market
  over the holding period, E-1 process 1's synthetic 8-point plateau); the engine-path analogue was
  not built (about 40 h on the M1).
- The pooled research window is the constituents' common one, so a series that starts later
  (XAUUSD, XAGUSD, about eleven weeks after the trim) shortens every sleeve's window slightly.
