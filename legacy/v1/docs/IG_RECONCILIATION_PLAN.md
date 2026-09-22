# IG Broker Reconciliation Plan (Wave 1)

**Date:** 2026-06-19
**Rule:** *If IG records a trade, Fiboki must show it.*

## The key finding (from live audit)

The Spot Gold demo fill IS in Fiboki's execution audit (row 359):
`XAUUSD SHORT, status=success, deal_id="DIAAAAXSBQD3EAQ", filled 4188.51, slippage 28.49 pips`.

But your IG platform reference is **`SBQLDCAC`** — a *different* string. That's the crux of reconciliation:

- IG returns **two** identifiers per deal: `dealReference` (what you send / see in the IG UI, e.g. `SBQLDCAC`) and `dealId` (IG's internal id, e.g. `DIAAAAXSBQD3EAQ`).
- Fiboki currently persists `deal_id` (the `dealId`) into the audit, and `deal_reference` separately in the adapter result — but the audit row's `deal_id` column shows the `dealId`, so the operator-visible reference doesn't match what IG shows.
- **Fix:** store and surface BOTH, and reconcile against IG's transaction history (`GET /history/transactions`) and open/closed positions — which key on `dealId`/`reference` and carry the real broker PnL.

This is why "the account says £20,554 but Fiboki can't explain the £554": Fiboki has the *open* fill but not the broker's **closed-trade PnL / transaction record**, and the reference shown doesn't match IG.

## Data model additions (BrokerTrade ledger)

New table `broker_trades` (separate from internal paper trades):
`id, source (paper|ig_demo|ig_live|backtest), broker, environment, deal_reference, deal_id, broker_transaction_id, instrument, epic, direction, size, entry_price, exit_price, open_time, close_time, gross_pnl, net_pnl, currency, fees, bot_id, strategy_id, research_run_id, status (open|closed), reconciled (bool), raw_json`.

## Backend

1. `ig_client`: add `get_transactions(from, to)` and `get_activity()` (IG `/history/transactions`, `/history/activity`).
2. `execution/reconciliation.py` (exists): extend to (a) pull IG open + closed positions + transactions, (b) upsert `broker_trades`, (c) match to internal bot signals via `deal_id`/`deal_reference`/time+instrument, (d) flag unmatched on either side.
3. API: `GET /execution/broker-trades` (filters: source, bot, strategy, instrument, date, outcome) and `POST /execution/reconcile` (operator-triggered sync).

## Frontend (Trade History)

Tabs/filters: **All · IG demo · Paper · Backtest · Live (later)**, plus by bot / strategy / instrument / date / outcome. IG-demo rows show `deal_reference` (`SBQLDCAC`), broker net PnL, and a "reconciled ✓" badge.

## Acceptance
A user can open Trade History, filter to **IG demo**, see the Gold trade with reference `SBQLDCAC` and its broker PnL, and answer "where did the £554 come from?"

## Exact production check required (operator / next session)
Reconciliation needs IG transaction history, which is only on the live Railway worker's IG session. To verify before building the importer, run (read-only):
- API (logged-in Chrome): `GET https://api.fiboki.uk/api/v1/execution/audit?execution_mode=ig_demo` — already confirms the open fill + `dealId`.
- Add/confirm an IG transactions pull on the worker (has IG creds): the importer should call IG `/history/transactions?from=<phase_start>` and log the returned `reference`/`transactionType`/`profitAndLoss` so we can match `SBQLDCAC` → internal bot signal.

## Status
**Foundation planned, not yet built.** No new code shipped for this wave in this pass — flagged as the top continuation item because it's the highest credibility lever and needs the IG transactions endpoint wired on the worker.
