# IG Demo Rejection — Root Cause (from activity history Z5ZAV)

**Date:** 2026-06-19
**Evidence:** IG Activity History export `ActivityHistory-Z5ZAV-(16-06-2026)-(19-06-2026).csv`.

## Root cause (confirmed)
Every FX/index order rejects with IG reason:

> **`Rejected: Failed to retrieve price information for this currency`** (Currency column = `#.`, Level = 0)

…while **Spot Gold** (epic `CS.D.CFPGOLD.CFP.IP`) **opened and closed cleanly**
(`Position opened: SBQD3EAQ` → `Position/s closed: SBQD3EAQ`, ref `SBQLDCAC`, +£10).

This is the **same class of failure the metals epics already hit** (previously a
403 "no access to the relevant exchange", fixed by remapping XAU/XAG to `.CFP`
epics). The FX/index epics in `core/instruments.py` are still the standard
`CS.D.<PAIR>.CFD.IP` / `IX.D.<IDX>.IFD.IP` forms, which **are not priceable on
this demo account (Z5ZAV)** — so IG can't retrieve a price and rejects.

Why we previously saw "UNKNOWN": IG's *deal-confirmation* `reason` returns
`UNKNOWN` for these, even though the *activity log* carries the real text. The
adapter read the confirmation only.

## Fixes shipped (branch `wave0-2-hardening`)
1. **Surface the real reason** — adapter now logs + persists the full IG
   confirmation to the audit (`fix(ig)` earlier), so the reason stops being
   swallowed as UNKNOWN.
2. **Generalised epic re-resolution retry** — the runtime "find a tradeable epic
   for this account and retry once" logic (which fixed metals via the 403 path)
   now ALSO fires on price/market/currency/UNKNOWN reject reasons
   (`_is_epic_resolution_reject`). Duplicate-safe: no deal was opened on a
   rejected confirmation. Tested against the exact Z5ZAV reason string.

## Honest caveat / what may still be needed
The retry only helps **if a priceable FX/index epic exists for this account**.
If account Z5ZAV genuinely cannot price standard FX CFDs (e.g. it's a limited /
spread-bet-style demo product enabling only `.CFP`-type instruments), the remap
search must find the enabled variant — and if none exists, the operator must
either enable those markets on the IG demo account or the catalogue must be
repointed to the account's supported epics.

**Recommended verification (needs IG creds — worker/prod):** run an IG markets
search per failing instrument on Z5ZAV and record the first TRADEABLE epic whose
`get_market` succeeds, then bake the verified epics into `core/instruments.py`.
A `fiboki ig-universe`/epic-audit command (planned in `NEXT_BUILD_STREAMS.md`)
would automate this. Until verified epics are confirmed, the generalised retry is
the safe automatic mitigation.

## Reconciliation note
The activity also confirms the broker-ledger design: the Gold deal carries
`DealId DIAAAAXSBQLDCAC` / `DIAAAAXSBQD3EAQ` and reference `SBQLDCAC` — both now
captured by the Wave-1 ledger.
