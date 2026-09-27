# The research ledger the API serves

`FIBOKI_EXPERIMENT_DB` = `var/experiments.sqlite`. Built by
`scripts/build_research_ledger.py` (idempotent; re-running it adds nothing), and
rebuildable from scratch because the ledger is runtime state under `var/` and is
not committed.

**604 rows.** What each one is, and how much of it is original:

| Rows | What | Provenance |
|---:|---|---|
| 14 | hypothesis rows: 5 pre-registrations + 9 campaign status changes | documents ORIGINAL; ledger rows re-filed |
| 16 | prior XAUUSD H4 ladder runs from `research/reports/xauusd_h4/` | **ORIGINAL** `ValidationReport` attached in full |
| 2 | K1 and K2 campaign summary rows | RECONSTRUCTED from the campaign report |
| 572 | K1 (30) and K2 (542) campaign cells | RECONSTRUCTED from the campaign report |

599 rejected, 5 pending (the pre-registrations). 16 distinct dataset versions.
16 rows carry a complete validation report; 72 carry a real structural
fingerprint.

## Why anything is reconstructed at all

The campaigns wrote their ledgers to `<out>/experiments.sqlite` beside their
reports. Those SQLite files were produced on a container scratch path and are not
on this host: `research/reports/campaign_k1_xauusd_h4/` and
`research/reports/campaign_k2_multi_instrument/` hold the JSON reports, the
markdown and the run logs, and no ledger. So the API's ledger started empty while
a large body of real work sat on disk in JSON.

Original artefacts are filed verbatim. The 16 prior ladder runs are complete
`ValidationReport` objects and go in whole, through the same
`run_discovery_campaign.backfill` the campaigns themselves used -- one
implementation, not a copy. The five hypothesis documents in
`research/hypotheses/` are the originals and are re-filed as written.

## Exactly what a reconstructed row loses

Every reconstructed row is tagged `reconstructed` and carries
`original_experiment_id`, `reconstructed_from` and a `reconstruction_note` in its
outputs. 572 of the 574 carry the id the campaign originally minted, so a row
here can be matched back to the campaign report line for line.

Recovered verbatim from the report: strategy id and content hash, dataset
version, hypothesis id, origin operator, parents, generation, rationale, trial
counts (own, external, and the count used for deflation), evaluations, engine
runs, trade count, verdict, the rung it died at, the binding constraint, the full
gate-value map, the deflation threshold and the cross-trial Sharpe variance.

**Not** recovered, and therefore EMPTY rather than guessed:

* **Mutant strategy documents.** The campaign report records a mutant's content
  hash but never its document, so a mutant cell has no `structure_hash` and no
  structural tokens. That is why only 72 rows have a fingerprint: the seed-named
  cells and the backfilled runs, where the document is on disk. Novelty checks
  keyed on `structure_hash` therefore cannot see the 500 mutant cells; they can
  see them by `strategy_content_hash`.
* **Per-cell validation reports.** The ledger derives `rejected_at_rung` from an
  attached report, so that column is empty on reconstructed rows. The rung is in
  `outputs['died_at_rung']` verbatim and restated in the conclusion. No report
  was synthesised to fill the column -- a fabricated report is worse than a blank
  field.
* **Original timestamps.** `ExperimentLedger.create` stamps `created_at` itself,
  so every reconstructed row is dated the day it was rebuilt. The campaign's own
  `created_at` is preserved on the summary row as
  `outputs['original_created_at']`.

## What was deliberately not filed as an experiment

K1's 35 and K2's 273 **skipped** cells, and the refused mutations. A cell that
was never evaluated is not an experiment, and filing 308 of them would inflate
the ledger's own count of work done. They are kept in full on the campaign
summary rows (`outputs['skipped']`, `outputs['rejected_mutations']`) so the
novelty and mutation refusals stay auditable.

## Validation page

`/api/research/validation` reads `research/reports/<strategy_id>.json`. Those four
paths are symlinks to the **current-engine, production-gate-set** report for each
strategy (`xauusd_h4/<id>__production__ds_3c1473cbf.json`) -- not copies, so they
cannot drift, and not the `__ds_45aeaa0e6` set, which that directory's README
marks superseded and says must not be quoted. `rsi_band_mean_reversion` has no
ladder run and is correctly shown as `not_validated`.
