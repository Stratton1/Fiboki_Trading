# V1 -> V2 data-store migration

The V1 HistData canonical store (`data/canonical/histdata`, 60 instruments x 6
timeframes, ~7.2 GB) migrated into a V2 `DataStore` at `var/datastore`, which is
what `FIBOKI_DATA_ROOT` now points at.

Run it with `scripts/migrate-store.sh` (defaults to `H4 H1`). The per-instrument
table is `PER_INSTRUMENT.md`, regenerated from the manifests by
`scripts/render_migration_report.py`. The manifests themselves are
`h4/ingest_manifest.json` and `h1/ingest_manifest.json`.

## What was migrated

| Timeframe | Series | Bars stored | Dataset versions | Excluded | Rows removed by repair |
|---|---:|---:|---:|---:|---:|
| H4 | 60 / 60 | 1,724,484 | 120 (60 raw + 60 canonical) | 0 | 1 |
| H1 | 60 / 60 | 6,538,378 | 120 (60 raw + 60 canonical) | 0 | 1 |
| **total** | **120** | **8,262,862** | **240** | **0** | **2** |

M1, M5, M15 and M30 were **not** migrated. They are the bulk of the 7.2 GB and
nothing in the platform reads them yet; H4 is the timeframe every K1/K2 result
was produced on and H1 is the next one down. Running
`scripts/migrate-store.sh M30` adds one.

Every series came out `quality=validated` and readable. Dataset version ids are
**not** stable across ingests: a `DatasetVersion` id hashes the content checksum
together with its lineage, so a fresh ingest of identical bytes is a fresh
lineage node with a new id. This store's XAUUSD H4 is
`ds_af8fc1b3143b3530c3f24ded` over the same 26,837 bars that K1 recorded as
`ds_3c1473cbf51e5a641ef41cbf` and K2 as `ds_817053e6d9976bdc8f40bca9`. The bars
are the same bars; the ids are not, and no report has been edited to pretend
otherwise.

## The four V1 defects, declared rather than absorbed

1. **Timestamps were EST-without-DST stamped as UTC.**
   `HistDataParquetProvider` applies the +5h correction and records it as a
   declared adjustment in the dataset metadata, so a later reader does not have
   to know the folklore. It is visible as
   `misaligned_bar_start` on H4 (every bar of a 4-hour series lands on an odd
   hour once corrected -- 26,837 of 26,837 for XAUUSD), and that is a true
   statement about the data rather than a fault in it.
2. **Prices are BID, not mid.** Recorded as `price_basis=bid` on every dataset.
   Every P&L computed from this store is therefore a bid-referenced P&L.
3. **Volume is identically zero.** Reported as a `volume_always_zero` defect on
   every one of the 120 series, at its full row count. Nothing imputes volume.
4. **EURUSD carries a negative-price sentinel bar** (OHLC all -0.0001) at
   `2001-09-12T01:00:00+00:00`. Integrity classifies it CRITICAL and correctly
   rejects the dataset. It is present in **both** EURUSD H1 and EURUSD H4 and
   was dropped in each under one explicit, versioned
   `RepairPlan(actions=(DROP_NON_POSITIVE,), reason=..., actor=...)` -- the same
   single authorised repair the K2 campaign used. The removed timestamp is in the
   manifest and in the canonical dataset's transformation record, and the
   repaired dataset's lineage points back at the unrepaired RAW bytes, which are
   still resolvable.

Nothing else was repaired. Gaps (`unexpected_gap`, `expected_gap`), off-session
bars, stale runs and return outliers are reported and left alone, because they
are properties of the market and the calendar rather than corruption. Two
examples worth reading before quoting a gap count as a defect: the index and
commodity series (AU200, CAC40, DE40, DXY, BCOUSD) show 3,000-4,300
`unexpected_gap` findings each, which is what a daily-session instrument looks
like against a continuous-hours expectation; and `off_session_bar` counts in the
hundreds-to-thousands are HistData's habit of carrying bars slightly outside the
session calendar V2 models.

## Provenance, in one sentence per layer

* `raw/<INSTRUMENT>/<TF>/<version_id>/` -- immutable, checksummed, read-only on
  disk, with the timezone correction and the basis declaration as adjustments.
* `canonical/<INSTRUMENT>/<TF>/<version_id>/` -- derived from the RAW version by
  a recorded `TransformationStep` (`integrity_validated`, or
  `integrity_repaired` for the two EURUSD datasets), carrying the integrity
  verdict as its quality and the integrity report as `_integrity.json`.
* `catalogue.db` -- every version with its lineage. `catalogue.sqlite` is a
  symlink to it, because `/api/research/datasets` opens the `.sqlite` name; see
  the gap noted in `scripts/migrate-store.sh`.
