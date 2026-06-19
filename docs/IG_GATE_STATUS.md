# IG Demo Execution & Gate 2 Status — 2026-06-19

## What is confirmed working

- **Bots route to IG demo.** The worker (`worker.py`) builds an `ExecutionRouter`
  from env and hands it to every bot. A mix of fills + rejects (gold filled,
  FX/indices rejected) proves real IG routing — paper would fill everything.
- **XAUUSD bot04 SHORT filled on IG demo** (~2026-06-18 23:49:09) → "all filled".
- **Sizing uses the real demo balance.** `IGExecutionAdapter._calculate_size()` uses
  `(_get_account_balance() × risk%) / (stop_pips × value_per_pip)`. The £20k demo
  balance is already in play; the paper account is irrelevant to IG sizing.
- **Live money is hard-blocked.** IG production is blocked at the `IGClient`/adapter
  layer; `ig_paper_mode` defaults true. We are demo-only.

## The open problem

Most FX/index demo orders return **"all rejected"** (GBPUSD, JP225, EURCAD, AUDCHF,
DE40). The adapter has explicit pre-submission reject codes — the cause is one of:

| Code | Meaning | Likely? |
|------|---------|---------|
| `MARKET_DETAILS_UNAVAILABLE` | IG market spec didn't load (quota 403 / exchange access) | **High** — worker comments cite IG ~10k/week allowance exhaustion |
| `STOP_TOO_TIGHT` | Converted stop below IG market minimum | Medium |
| `MISSING_STOP` | Strategy emitted no stop (gated off by `FIBOKEI_IG_REQUIRE_STOP`) | Medium |
| `size_below_min` | Below IG min deal size | Low (sizes off £20k) |
| broker-side | Market closed / not tradeable / epic access | Medium (instrument-specific) |

## Why we can't confirm the cause from the repo

There is **no local DB and no local `.env`** in the working tree. The execution-audit
rows and worker logs that carry the exact `error_code`/`rejection_reason` exist
**only on Railway**. Per discipline: not guessing, not patching blind.

## Exact retrieval (operator action — pick one)

1. **Production API** (fastest):
   ```
   GET https://api.fiboki.uk/api/v1/execution/audit?execution_mode=ig_demo
   ```
   Send with the `fibokei_token` cookie (logged-in browser, or copy the cookie).
   Look at the newest `place_order` rows → `error_message` + `detail_json`
   (`rejection_reason`, `error_code`). Or in the UI: System page → expand a rejected
   row in **Execution Signals (Phase 3)**.

2. **Worker logs** (Railway → `fibokei-worker` service). Grep for:
   `IG order rejected pre-submission` · `IG order NOT placed` ·
   `MARKET_DETAILS_UNAVAILABLE` · `STOP_TOO_TIGHT` · `MISSING_STOP` ·
   `exceeded allowance` · `403`.

## The one line that determines the fix

> e.g. `GBPUSD rejected: error_code=MARKET_DETAILS_UNAVAILABLE` (→ IG quota/throttle fix)
> or `GBPUSD rejected: error_code=STOP_TOO_TIGHT` (→ min-stop enforcement fix)

## Gate 2 acceptance (not yet met)

No live-money enablement until a demo trade is proven end-to-end during market hours:
**place → deal reference → position sync → close → execution audit → slippage
capture → reconciliation clean.** Current status: **PENDING** (blocked on the
rejection fix + a market-hours run).

## Visibility bug to fix alongside

System page "Execution Accounts" shows the **API** process env (`legacy_single`,
Paper-only) — not the worker's real targets. Add a worker execution-target heartbeat
and label API vs worker state separately so this stops misleading operators.
