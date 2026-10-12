# K6: 100,000 generated documents, pooled (runbook)

**Status: DRAFT, not filed.** `research/preregistration/campaign_k6.json` is filed only after
E-4's process P0 has been run on the Mac and read for both timeframes, and on the operator's
approval. Until then `scripts/campaign_k6.py` refuses every evidence run (`--data-root`); only the
harness (`--starter`) runs. Nothing in this directory is a result.

## What K6 is, in one paragraph

The operator asked for "100,000 more" strategies. K6 makes that one pre-registered campaign:
100,000 structurally distinct, cost-aware documents generated from a fixed grammar and seed
(`research/generate_families_k6.py`, cells in `research/generated_k6/`), each evaluated as ONE
pooled candidate per admitted timeframe over a named universe (E-4's pooling, `N_eff = N / DEFF`),
screened by rung 0 run alone, then laddered (rungs 0 to 5, holdout deferred) on the screen's
survivors. **Every one of the 100,000 counts in the trial count**, the trial count is fixed now
(495,290 including both declared universes and E-4's 21,968), and it is never lowered. A
survivor is a hypothesis to re-author as a seed under its own filing; K6 reads no holdout.

## What 100,000 trials costs, in plain words

The deflation bar grows like `sqrt(2 ln N)`, not like N. The expected maximum of N null z-scores
is 4.05 at E-4's 21,968 trials and 4.73 at K6's 495,290: about 17% higher,
not twenty times. What it still demands is the problem. With T effective pooled trades a deflated
Sharpe of 0.95 needs roughly a per-trade Sharpe of `(E[max Z] + 1.645) / sqrt(T)`:

| effective trades T | 1,000 | 2,000 | 4,000 | 8,000 |
|---|---|---|---|---|
| certifiable per-trade Sharpe at N = 21,968 | 0.180 | 0.127 | 0.090 | 0.064 |
| certifiable per-trade Sharpe at N = 495,290 | 0.202 | 0.142 | 0.101 | 0.071 |

A 0.08 per-trade edge needs about 6,344 effective trades at K6's N (5,066 at E-4's);
a 0.12 edge about 2,820. E-4 expects `N_eff` of roughly 1,300 to 3,500 on H4, so **K6 can only
certify per-trade Sharpes of about 0.11 to 0.18 or more on H4, and nothing below about 0.2 on D1.**
A null result says "no edge that large in this grammar"; it says nothing about 0.03 to 0.08 edges.
(Gaussian approximation, V = 1/T; the ladder's own DSR uses per-period returns and the trials'
observed variance and moments. `campaign_k6.py plan` prints the full table.)

## Order (each step waits for the one before)

```bash
# 0. Calibrate the time estimate on THIS Mac (10 to 20 min; nothing filed depends on it):
research/reports/campaign_k6/run_k6_mac.sh bench
#    -> research/reports/campaign_k6/bench_<host>.json: seconds per document-series and per
#       screened document (16 synthetic sleeves). Put the numbers in the filing's compute section.

# 1. The generator and the plan reproduce the filing byte for byte (about an hour, one core):
research/reports/campaign_k6/run_k6_mac.sh check

# 2. FILE campaign_k6.json (operator), only after E-4's P0 files exist and admit:
#      research/reports/e4_pooled/e4_p0_H4.json, e4_p0_D1.json

# 3. Screen every document on H4 (4 interleaved shards, nice'd, caffeinated, resumable):
research/reports/campaign_k6/run_k6_mac.sh screen H4
research/reports/campaign_k6/run_k6_mac.sh status screen H4      # rows, s/doc, hours left, peak RSS
research/reports/campaign_k6/run_k6_mac.sh merge screen H4       # refuses gaps/overlaps/strangers

# 4. Ladder the H4 survivors (at most 5,000, in generation-index order):
research/reports/campaign_k6/run_k6_mac.sh ladder H4
research/reports/campaign_k6/run_k6_mac.sh merge ladder H4

# 5. D1, only if E-4's P0 admitted D1: steps 3 and 4 with D1.
```

Single shard by hand (the script does exactly this):

```bash
nice -n 10 caffeinate -i .venv/bin/python scripts/campaign_k6.py screen --universe fx_pooled_k5 \
    --timeframe H4 --data-root "$FIBOKI_DATA_ROOT" --p0 research/reports/e4_pooled/e4_p0_H4.json \
    --shard 0 4 --max-rss-mb 1500 --out research/reports/campaign_k6/shards/fx_pooled_k5/screen_H4.0-of-4.jsonl
#   add --resume to continue; exit code 75 means it stopped on the memory guard (resume it)
```

Harness check without real data (any machine; NOT evidence):

```bash
.venv/bin/python scripts/campaign_k6.py screen --starter --timeframe H4 --limit 3 \
    --out /tmp/k6_harness.jsonl
```

## Resume, shards, memory

* Shards are JSON lines: a header line, then one row per document, flushed per row, fsynced at
  most once a minute. A crash, sleep or `kill` loses at most the document in progress; a torn
  last line is dropped and that document redone. `--resume` refuses a file from a different run.
* Shards are interleaved (`k, k+N, ...`): a stopped run is a random subsample, not a family block.
* Each screen shard holds one timeframe's 16 series plus one document's prepared frames and
  evaluation cache (released per document): 278 MB peak measured here; a ladder shard holds a
  candidate's whole cache (0.5 to 1.5 GB estimated, as E-4). **Not measured on the Mac.** The
  runner stops itself (exit 75, resumable) above `MAX_RSS_MB` (1,500). Check `status` after the
  first hundred documents; if four shards together exceed about 5 GB or Memory Pressure turns
  yellow, stop them and re-run with `WORKERS=3` (delete the old shard files first: a different
  shard count is a different set of shards, and `merge` refuses an overlap).

## Time on the M1 (8 GB), honestly

Measured here (cloud sandbox, 2 shared CPUs, synthetic H4 bars, `scripts/k6_engine_benchmark.py`),
the same 8 documents before and after the engine change, identical ledgers on all 16 pairs:

| bars | before (mean s per document-series) | after | speed-up |
|---|---|---|---|
| 6,000 | 5.67 | 1.15 | 4.9x |
| 15,600 | 15.76 | 2.49 | 6.3x |
| 31,200 | not run (quadratic before; extrapolated about 34) | 5.11 | about 7x |

The screen itself, measured here (`--k6-screen 4 --sleeves 16 --bars 26000`): **25.5 s per
document** (30 s when it reaches S0b, 6.5 s when S0a stops it), peak resident 278 MB per process.

The Mac is not this machine. Two calibrations, both stated:

* E-1 measured about 8.1 s per (pre-change) H4 backtest per shard-core on the M1; the same work
  is about 27 s here, so an M1 core is about 3.3x faster: about **8 s per screened H4 document**.
* Conservatively, 8.1 s / 6.5x = 1.2 s per sleeve run, x 16 sleeves: about 19 s per document.

| stage | work | 4 shards, estimate |
|---|---|---|
| H4 screen | 100,000 documents x 16 sleeves (1.6 million sleeve backtests) | about 2.3 to 5.5 days |
| D1 screen | 66,661 documents x 16 sleeves (a D1 run is about a sixth of an H4 run) | about 0.4 to 1 day |
| ladder, per timeframe | 1,000 to 5,000 survivors x about 0.06 h (E-4's 0.36 h / 6) | about 0.6 to 3 days |
| **total** | | **about 4 to 12 days** of continuous running |

Step 0 replaces this with the Mac's own figure before anything is filed. The filed stop rules:
a stage whose projected wall time after its first 2,000 documents exceeds 1.5x its budget (H4
screen 14 days, D1 screen 5, each ladder 5) stops, and is reported as partial: unrun documents
are named, nothing is concluded about them, and **the declared N is not lowered**.

## What not to do

* Do not run K6 on evidence data before the filing; the runner refuses, do not work around it.
* Do not filter, re-order or "skip the boring families" by hand. The generation order and the
  cap rule are fixed by the seed, not by results.
* Do not read any survivor as a strategy, paper-trade it or quote its numbers. It is a hypothesis.
* Do not lower the declared N because a stage did not run.

## Files

| file | what |
|---|---|
| `research/generate_families_k6.py` | the grammar, generator, dedupe and manifest |
| `research/generated_k6/cells.csv.gz`, `MANIFEST.json` | the 100,000 cell codes and their hashes |
| `scripts/campaign_k6.py` | plan / screen / ladder / merge / status |
| `scripts/k6_engine_benchmark.py` | timing (and the before/after evidence for the engine change) |
| `research/preregistration/campaign_k6.json` | the DRAFT filing |
| `research/reports/campaign_k6/run_k6_mac.sh` | the Mac wrapper |
