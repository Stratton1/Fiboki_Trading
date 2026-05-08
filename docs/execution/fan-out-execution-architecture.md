# Fan-out execution architecture

Fiboki's multi-broker execution layer is **controlled fan-out**: one bot
signal becomes one parent record and N independent broker attempts. This
document is the source of truth across Phases 1–5.

## Mental model

```
Bot evaluation (closed candle)
    │
    ▼
NormalisedTradePlan       ← broker-neutral; no size yet
    │
    ▼
ExecutionRouter
    │  (per-bot target lookup in db_targets mode;
    │   static target list otherwise)
    │
    ├─► ResolvedTarget(paper)     ──► PaperExecutionAdapter
    ├─► ResolvedTarget(ig demo)   ──► IGExecutionAdapter
    └─► ResolvedTarget(tradovate) ──► TradovateExecutionAdapter

Each target is sized, gated, dispatched independently → one
ExecutionAttempt row per (signal, target). One signal → N attempts.
```

## Validation gate order

For every enabled target, in order. **Any failure short-circuits this
target only**; sibling targets keep going.

| Gate | Check | Failure → status | Code |
|------|-------|------------------|------|
| 1 | Global kill switch | `skipped` | `KILL_SWITCH` |
| 2 | Target / account environment allowed | `skipped` | `ENV_BLOCKED` |
| 3 | Instrument supported by this broker | `skipped` | `UNSUPPORTED_INSTRUMENT_*` |
| 4 | Per-target size > 0 | `rejected` | `SIZE_ZERO` |
| 5 | Account risk (Phase 4) — daily/weekly stop, max open positions | `rejected` | `DAILY_STOP` / `WEEKLY_STOP` / `MAX_OPEN_POSITIONS` / `ACCOUNT_DISABLED` |
| 6 | Adapter dispatch | `filled` / `rejected` / `failed` | broker-specific |

## Router modes

Set `FIBOKEI_EXECUTION_ROUTER_MODE` to one of:

* **`legacy_single`** (default) — one target. Paper unless
  `FIBOKEI_LIVE_EXECUTION_ENABLED=true` enables IG demo. No fan-out.
* **`env_global_fanout`** — every account enabled by env vars
  (`FIBOKEI_PAPER_ACCOUNT_ENABLED`, `FIBOKEI_IG_ACCOUNT_ENABLED`,
  `FIBOKEI_TRADOVATE_ACCOUNT_ENABLED`) becomes a target. **All** running
  bots fan out to **all** enabled accounts. Stepping stone before Phase 2.
* **`db_targets`** — per-bot targets resolved from
  `bot_execution_targets` joined to `execution_accounts`. Bots with no
  rows fall through to the default Paper account. This is the recommended
  mode for production.

## Tables

| Table | Phase | Purpose |
|-------|-------|---------|
| `execution_accounts` | 2 | Operator-managed broker destinations (broker, env, allocation, risk %, limits, enabled, default, live_allowed) |
| `bot_execution_targets` | 2 | Per-bot link to an account, with optional override of allocation / risk_per_trade_pct |
| `bot_signals` | 3 | One row per bot evaluation that produced a plan |
| `execution_attempts` | 3 | One row per (signal × enabled target) with status, broker ids, latency, slippage, rejection reason |
| `execution_audit` | pre-Phase-1 | Legacy single-row audit. Still written for back-compat. |
| `kill_switch` | pre-Phase-1 | Global emergency stop. Single-row table. |

## Sizing

Per-target sizing uses **only** the operator-set allocation:

```
target_sizing_capital = bot_execution_target.allocation_override
                     or execution_account.allocated_capital
target_risk_pct       = bot_execution_target.risk_per_trade_pct_override
                     or execution_account.risk_per_trade_pct
```

Broker-reported balance is informational only. Each broker has its own
post-step:

* **IG / paper:** float lot/contract size, IG-aligned leverage caps applied.
* **Tradovate:** floor to whole contracts; reject if zero. Never silently
  approximate futures sizing.

## Live trading gates

Three independent flags **all** must be `true` for any live attempt:

1. `execution_account.environment = "live"`
2. `execution_account.live_allowed = true` (column on the row)
3. `FIBOKEI_LIVE_EXECUTION_ENABLED = true` (global master)

The router computes `ResolvedTarget.live_allowed` from these. If any
fails, the target is gated as `ENV_BLOCKED`. IG production URL is also
hard-blocked at `IGClient._ensure_demo_only()`. Tradovate live URL is
hard-blocked at `TradovateClient._ensure_env_allowed()`. Belt and braces.

## Reconciliation status (Phase 5)

`/execution/accounts/{id}/reconcile` returns one of:

* `clean` — all positions match.
* `mismatch` — at least one discrepancy; `mismatches[]` populated.
* `unavailable` — broker reachable but request failed.
* `credentials_missing` — operator hasn't configured creds yet.
* `unsupported` — no reconciler for this broker.

## Audit shape (Phase 3)

* **Parent**: `bot_signals` row with bot id, strategy, instrument,
  direction, signal_timestamp, plan_json.
* **Children**: one `execution_attempts` row per target. Status
  vocabulary: `pending | skipped | rejected | submitted | filled |
  partially_filled | closed | failed`.
* **Derived parent status**: `derive_parent_signal_status()` rolls children
  up to `all_filled / partial_success / all_skipped / all_rejected /
  failed / mixed / empty`.

## Frontend surfaces

* **System page**:
  * Execution accounts table (Phase 2 — `ExecutionAccountsCard`).
  * Execution signals card with expandable parent-child rows
    (Phase 3 — `ExecutionSignalsCard`).
  * Legacy execution audit log (pre-Phase-1).
* **Bot detail**: per-bot targets card (Phase 2 — `BotExecutionTargetsCard`)
  with attach / toggle / detach.

## Phases

| Phase | Status | What it added |
|-------|--------|---------------|
| 1 | ✅ | ExecutionRouter, env-driven targets, Tradovate adapter, Phase-1 interim audit |
| 2 | ✅ | execution_accounts, bot_execution_targets, db_targets mode, accounts/targets API |
| 3 | ✅ | bot_signals + execution_attempts tables, signals API, grouped UI |
| 4 | ✅ | Per-account risk engine: daily/weekly stop, max open positions, sibling-isolated rejections |
| 5 | ✅ | Per-account reconciliation status; final docs + deployment checklist |

## Tradovate stance

Tradovate is **demo-first**. The client refuses to call live URLs unless
`FIBOKEI_TRADOVATE_LIVE_ALLOWED=true` AND `FIBOKEI_LIVE_EXECUTION_ENABLED=true`
AND `FIBOKEI_TRADOVATE_ENV=live`.

Contract mapping is **never guessed**:

* `FIBOKEI_TRADOVATE_SYMBOL_MAP="US500:ES,US100:NQ"` for products.
* `FIBOKEI_TRADOVATE_FRONT_MONTH="M6"` for the active expiry suffix.
* No mapping → `UNSUPPORTED_INSTRUMENT_TRADOVATE` skip in audit log.

FX pairs (EURUSD, GBPUSD, …) are deliberately **not** mapped to CME
currency futures (6E, 6B, …) because the lot economics differ from IG
forex CFDs. Operator must enable each Tradovate product explicitly.
