# E-4 amendment 2 (2026-10-19): an engine defect voids the first run; P0 and the main stage are re-run

Applies to `research/preregistration/pooled_universe_e4.json` (FILED 2026-10-10 at 78f1dba) and
amendment 1. The filing is not edited.

## What was found

The first main-stage run finished on the MacBook on 2026-10-18 (172 candidates, 0 cleared rungs 0
to 5, all 172 bound at `min_track_record`). Reading it, every row's design effect had basis "too
few periods at every frequency: DEFF = m_active". A 16-year H4 window had **7 daily periods**.

Root cause: the canonical store reads bars as `datetime64[us, UTC]`. `DatetimeIndex.asi8` returns
integers in the index's OWN unit, and four places read them as nanoseconds. All synthetic fixtures
build nanosecond indexes, so every test passed and only real-data runs were wrong:

1. `validation/pooled.py`: days bucketed as `ts // 86_400e9` on microsecond integers, so DEFF
   fell back to the sleeve count on every real pooled candidate.
2. `backtest/engine.py _median_interval`: a 4-hour bar read as 14.4 seconds, so fills after a
   normal bar gap were flagged STALE and paid `stale_spread_multiple` (2x) the spread. This dates
   from 2026-09-19.
3. `backtest/engine.py` equity curve for volatility targeting: rebuilt from microsecond integers
   read as nanoseconds, giving a realised volatility about 31.6 times too high and positions cut by
   the 0.12 target. This dates from 2026-10-10, the K6 speed-up, 40d471a.
4. `risk/accounting.py _periods_per_year_arrays`: the same mistake for any microsecond curve.

Measured on the Mac with the real stored bars (`donchian_breakout_atr` default, research window,
`unit_probe.py`):

| Series     | Index as stored (us)                  | Index in ns                            |
|------------|---------------------------------------|----------------------------------------|
| EURUSD H4  | 675 trades, net -441.24, spread 126.88 | 675 trades, net -578.33, spread 110.35 |
| USDJPY H4  | 629 trades, net -197.05, spread 150.50 | 629 trades, net -423.26, spread 113.95 |

Under the ns index, mean position size is 75% and 53% larger. The trades are the same; sizes and
costs are not.

## Why the first run is void, and what it still shows

The P0 bootstrap is built from fresh engine runs of the seeds' default bindings (their trades and
cost shares), and the main stage runs the engine on every sleeve. Both ran on the defective
engine, so neither is the filed instrument: falsification F4 ("a sleeve's trades differ from the
single-series engine run") covers a defect in the engine itself, not only a defect between the
two paths. The first run's files are kept, renamed `*.engine_v3` on the Mac, and recorded in the
ledger. They are not read for any decision.

Recorded but not decided on: in the void run, 163 of 172 candidates had a pooled per-trade Sharpe
at or below zero. The best `n / MinTRL_95` with NO dependence correction at all (DEFF = 1) was
0.04 on H4 and 0.45 on D1. Under that engine, no correction could have produced a survivor.

## What changes

- The engine is `engine_v4_time_units`. Every integer view of a timestamp goes through
  nanoseconds. `tests/integration/test_time_units.py` runs the interval, the annualisation, the
  realised volatility, the design effect, a full engine run and a full pooled evaluation on the
  same bars in microseconds and nanoseconds, and requires identical answers. Those tests fail on
  the old code: 9 of 13.
- The engine version is part of every E-4 run's identity (the `source.engine` field). `resume`
  and `merge` therefore refuse to mix shards from the two engines.
- **P0 is re-run for both timeframes, then the main stage**, exactly as filed: same plan, seed,
  replicates, gate set, trial counts and decision rule. Admission is decided again from the new P0
  files. If a timeframe is not admitted, its main stage is not run. Nothing else changes.

## Admissibility

This changes no hypothesis, threshold, gate, candidate, trial count, grid, seed or replicate count.
The re-run's outcome cannot be chosen by the change. The defect was found from a field the
filing's own report prints (`design_effect.by_frequency.n_periods`), not from a verdict. The void
run's verdict is the same as the expected outcome, so there was no unwelcome result to escape.
The holdout was never read.
