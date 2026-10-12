# K6 pre-registration (DRAFT): read this first

`campaign_k6.json` is the filing; this page says what it commits to, in plain words. Runbook:
`research/reports/campaign_k6/README.md`.

**Status.** DRAFT, not filed, no compute on evidence data. It is filed only after E-4's process
P0 (`pooled_universe_e4.json`) has run on the Mac and been read for both timeframes, only if P0
neither found pooling too lax (F1) nor `N_eff` mis-calibrated (F2) on a timeframe K6 would run,
and only on the operator's approval. `scripts/campaign_k6.py` refuses evidence runs until the
status reads FILED.

**What is fixed now, before any result.**

* The documents: 100,000 cells of a seven-axis grammar (`research/generate_families_k6.py`,
  seed 20261011), structurally distinct on three hashes, none with the rules of a seed or a K5
  document (10 such cells were dropped), cost-aware (stops at least 2 ATR, targets at least 2R
  or 3 ATR, time exits at least 20 bars, no H1), at most 8 grid points each. Families:
  momentum 22,205; mean reversion 18,576; breakout 18,392; Ichimoku 13,906; trend pullback
  13,836; trend signal 13,085. The cells file and a hash chain over every document are in
  `research/generated_k6/MANIFEST.json`; `--check` regenerates them byte for byte.
* The universe is a name: `fx_pooled_k5` (E-4's sixteen OANDA MID series) now;
  `crypto_majors_v1` declared, refused until its instruments, costs, long-only rule and its own
  P0 are filed by amendment. Unknown names are refused.
* The stages: S0a (pooled signal count below rung 0's floor: cannot pass rung 0, engine skipped),
  S0b (rung 0 alone, which is exactly the fail-fast ladder's first step), S1 (rungs 0 to 5,
  holdout deferred) for at most 5,000 survivors per timeframe in generation order. Gates:
  `v2.1.0-calibrated`, unchanged, with E-4's `N_eff`.
* The trial count: **495,290** = E-4's 21,968 + 2 universes x 166,661 screen trials + 2 x 2
  timeframes x 5,000 x 7 grid points. Every document counts whether or not it runs; the number
  is never lowered.
* No holdout is read. A survivor is a hypothesis to re-author as a seed under its own filing.

**What that N does to the bar.** The expected maximum of N null z-scores rises from 4.05 at
21,968 trials to 4.73 at 495,290 (it grows like `sqrt(2 ln N)`): 17%, not 20x. With T effective
pooled trades the certifiable per-trade Sharpe is roughly `(E[max Z] + 1.645) / sqrt(T)`: 0.20 at
T = 1,000, 0.14 at 2,000, 0.10 at 4,000. A 0.08 edge needs about 6,300 effective trades; E-4
expects 1,300 to 3,500 on H4. **K6 can certify only per-trade Sharpes of roughly 0.11 to 0.18 or
more on H4, and 0.3 or more on D1.** Its expected outcome is zero survivors, and that null will
say nothing about the 0.03 to 0.08 edges the platform cannot certify on these bars at any N.

**Compute.** Measured here (not on the Mac): the engine change is 5 to 6x faster per
document-series with identical ledgers, and the screen costs 25.5 s per document here (16
sleeves, 26,000 H4 bars). Estimated on the M1 with four shards: about 4 to 12 days for the FX
screen and ladder. The runbook's step 0 measures it on the Mac first; the filed
stop rules end a stage that projects past 1.5x its budget, without lowering N.
