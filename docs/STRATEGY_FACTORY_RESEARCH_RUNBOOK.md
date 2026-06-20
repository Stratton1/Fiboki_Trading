# Strategy Factory — Research Runbook

**Purpose:** how to run controlled research over the Strategy Factory families
(traditional_gen1 + hybrid_gen1) and read the results honestly. Research-only —
nothing here promotes a strategy to paper/demo/live (see
`STRATEGY_FACTORY_PROMOTION_RULES.md` for that gate).

## Principles (carried from RULES)

- Rank by a **composite robustness score**, never raw PnL.
- A combination only **qualifies** when it has **≥ 80 trades** (`--min-trades 80`).
  Fewer trades → not enough sample, excluded from the trusted leaderboard.
- Deterministic: identical data + identical specs → identical ranking.
- Closed-candle signals only; no look-ahead (enforced by the factory + tests).
- Honest underperformance is preferred over inflated numbers. Untuned Gen-1
  defaults are expected to look mediocre — that is the point of research.

## Composite score (research/scorer.py)

Weighted blend, normalised 0–1:

| Component | Weight | Meaning |
|-----------|--------|---------|
| Risk-adjusted (Sharpe / 3.0) | 0.25 | reward / volatility |
| Profit factor (/ 5.0) | 0.20 | gross win / gross loss |
| Return (net / capital, capped 100%) | 0.20 | absolute edge |
| Drawdown control (1 − DD/30%) | 0.15 | capital preservation |
| Sample sufficiency (trades / 80) | 0.10 | statistical weight |
| Equity-curve stability (R²) | 0.10 | smoothness of growth |

The min-trades filter is applied **separately** from the score — a combo can
score on partial sample but is only listed as *qualified* at ≥ 80 trades.

## Data

Research loads via `load_canonical(symbol, tf)`: canonical store →
starter → fixtures. The canonical store lives in `data/canonical/<provider>/<symbol>/<symbol>_<tf>.parquet`.

**Current coverage (2026-06-20):** 7 FX majors (EURUSD, GBPUSD, USDCHF, USDJPY,
USDCAD, AUDUSD, NZDUSD), **H1 native** + **H4 resampled** from H1, ~2 years
(2023–2024; USDCAD ~1 year). FX has **no real volume** (volume = 0), so VWAP/OBV
families are `research_limited` and must not be trusted here.

Populate / refresh canonical from the bundled starter set (offline, deterministic):

```python
from fibokei.data.paths import get_canonical_dir, get_starter_dir
from fibokei.data.providers.resampler import resample_ohlcv
import pandas as pd
for p in ["eurusd","gbpusd","usdchf","usdjpy","usdcad","audusd","nzdusd"]:
    h1 = pd.read_parquet(get_starter_dir()/"histdata"/p/f"{p}_h1.parquet")
    out = get_canonical_dir()/"histdata"/p; out.mkdir(parents=True, exist_ok=True)
    h1.to_parquet(out/f"{p}_h1.parquet")
    resample_ohlcv(h1,"H4").to_parquet(out/f"{p}_h4.parquet")
```

**Not yet available locally:** metals, indices, energy, and finer timeframes
(M5–M30). These require provider downloads (`python -m fibokei download-data`
/ `ingest-data` for histdata/dukascopy) and are the next coverage expansion.
Until then "full universe" = the 7 FX majors above.

## How to run

```bash
cd backend && source ../.venv/bin/activate
# Full Gen-1 + canonical sweep over the available FX universe:
python scripts/run_gen1_research.py \
  --strategies all \
  --instruments EURUSD,GBPUSD,USDCHF,USDJPY,USDCAD,AUDUSD,NZDUSD \
  --timeframes H1,H4 --min-trades 80 --out-dir results/phase7
```

Outputs in `--out-dir`:
- `gen1_research_results.csv` — every combination with metrics + tier + qualified flag.
- `gen1_research_summary.json` — run metadata + top-40 qualified leaderboard.
- `run.log` — per-combination progress.

Restrict `--strategies` to a comma-separated id list (e.g. only
`factory_trad_*` / `factory_hyb_*`) to sweep a single tier.

## Staged expansion (widen only after each stage is clean)

1. **Smoke** — FX majors × H1/H4 × Gen-1 (this run).
2. **Medium** — add Gold + M30 once metals data is ingested.
3. **Indices** — add the index instruments once their data is ingested.
4. **Full canonical** — all instruments × all timeframes, then layer the deeper
   validation: out-of-sample / walk-forward (`research/oos.py`,
   `walk_forward.py`) → Monte Carlo (`monte_carlo.py`) → scenario sandbox
   (`scenario.py`) → shortlist.

## Reading results — cautions

- **H1 over-trades.** The bias/filter families fire whenever their condition
  holds, so on H1 they rack up 300–400 trades with large drawdowns. H4 gives
  more selective, more interpretable samples. Treat raw H1 counts with care.
- A high score on one instrument/timeframe is **not** a promotion signal — it
  must survive OOS, walk-forward, Monte Carlo and scenario stress first.
- Known approximations still apply: USD→GBP conversion, static spreads, zero
  slippage by default, no overnight financing. Numbers are comparative, not
  live-accurate.
