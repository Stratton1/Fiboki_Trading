# Historic Data Coverage — HistData pull complete

**Date:** 2026-06-20
**Source:** HistData.com (free open-source M1 forex/CFD data) via the `histdata`
package, ingested through `data/providers/histdata.py` → canonical store.
**Result:** **all 60 HistData-mapped instruments**, **6 timeframes each**
(M1, M5, M15, M30, H1, H4), **352 parquet datasets, ~7.2 GB**.

## What was pulled

| Asset class | Instruments |
|-------------|-------------|
| FX majors (7) | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, NZDUSD |
| FX G10 crosses (22) | EURJPY, GBPJPY, EURGBP, AUDJPY, EURAUD, AUDCAD, AUDCHF, AUDNZD, CADCHF, CADJPY, CHFJPY, EURCAD, EURCHF, EURNZD, GBPAUD, GBPCAD, GBPCHF, GBPNZD, NZDCAD, NZDCHF, NZDJPY, SGDJPY |
| FX Scandinavian (4) | USDNOK, USDSEK, EURNOK, EURSEK |
| FX EM (9) | USDSGD, USDHKD, USDTRY, USDMXN, USDZAR, USDPLN, USDCZK, USDHUF, ZARJPY |
| FX EUR-EM (5) | EURTRY, EURPLN, EURCZK, EURHUF, EURDKK |
| Metals (2) | XAUUSD (gold), XAGUSD (silver) |
| Energy (2) | BCOUSD (Brent), WTIUSD (WTI) |
| Indices (9) | US500, US100, UK100, DE40, JP225, CAC40, AU200, HK50, DXY |

Each instrument resampled to M1/M5/M15/M30/H1/H4 with provenance tracked.

## History depth (representative, H1)

- **Deepest (2000–2025, ~156k H1 bars):** EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF.
- **Crosses from 2002–2008** (~110k–147k H1 bars) depending on pair.
- **EM / exotics & indices from 2010–2011** (~50k–93k H1 bars) — HistData's
  start date for those symbols.
- **Metals from 2009** (~99k H1 bars).
- **WTIUSD ends 2023-12** — HistData has no later WTI; everything else runs to 2025-12.

Full per-symbol inventory: `backend/results/datapull/coverage.json`.

## How it was acquired (and a fix worth noting)

`python -m fibokei download-data --years 2000..2026` drove the provider's
downloader + ingest. Two operational issues were found and fixed:

1. **Hang on a missing exotic.** The histdata downloader *blocks* (rather than
   erroring) when a pair/year isn't available, stalling the whole run. Fix:
   `scripts/resume_pull.py` sets a global socket timeout so a hanging call
   raises and is skipped — the per-year `try/except` then continues. The 861
   already-downloaded zips were preserved.
2. **Two symbols left stale.** USDCHF and USDJPY still held the old 2-year
   Phase-7 starter data (H1/H4 only) because they already existed in canonical
   and the resume treated "present" as "done". Force-repulled both — now full
   2000–2025 history across all 6 timeframes.

## Caveats

- HistData FX **volume is synthetic/zero**, so VWAP/OBV strategies remain
  `research_limited` and must only be judged on instruments with real volume.
- Index/commodity CFDs are HistData's proxy series, not exchange feeds — fine for
  research ranking, not for tick-accurate execution modelling.
- The canonical parquet store is gitignored (reproducible); `coverage.json` is
  committed as the record.
