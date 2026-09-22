# Brokers

Fiboki is a multi-broker execution platform. Strategies are broker-neutral;
broker-specific behaviour is isolated behind `ExecutionAdapter` instances
and dispatched by the `ExecutionRouter`.

## Supported brokers

| Broker | Status | Default env | Live status |
| --- | --- | --- | --- |
| Paper | Always available | n/a | Simulated only |
| IG | Demo only | `demo-api.ig.com` | Production hard-blocked at `IGClient._ensure_demo_only` |
| Tradovate | Phase 1 — demo scaffold | `demo.tradovateapi.com` | Hard-blocked unless three explicit flags align |

## Architecture

A bot generates one `NormalisedTradePlan`. The router fans it out to every
enabled `ResolvedTarget`. Each target is a fully resolved execution
destination (broker × environment × allocated capital × risk %). Each
fan-out produces one `ExecutionAttempt` per target, recorded as its own
audit row tagged with a shared `parent_signal_id`.

Critical invariants:

- **Strategies never import broker code.** They produce broker-neutral
  signals and trade plans only.
- **Sizing is per-target.** Each target uses its own
  `allocated_capital × risk_per_trade_pct`. One bot fanning to two brokers
  yields two independent sizes.
- **Failures are sibling-isolated.** If IG rejects and Tradovate fills,
  Tradovate stays open. The router never rolls back peers.
- **Live execution is off by default.** Multiple gates (per-broker URL
  block, router `live_allowed` flag, global `FIBOKEI_LIVE_EXECUTION_ENABLED`,
  kill switch) must all align before any real-money order can flow.

## Phase status

* **Phase 1 (this release):** ExecutionRouter, env-driven targets, Tradovate
  scaffold. See `tradovate-integration-design.md`.
* **Phase 2 (next):** `execution_accounts` and `bot_execution_targets`
  tables — operators configure accounts and per-bot targets via the UI
  rather than env vars.
* **Phase 3:** First-class `bot_signals` and `execution_attempts` tables
  for parent-child audit display.
* **Phase 4:** Per-account risk engine.
* **Phase 5:** Operational polish — per-account reconciliation, slippage
  analytics by broker, broker-aware Telegram alerts.

## Per-broker docs

* [Tradovate integration design](./tradovate-integration-design.md)
* IG: see `docs/forensic-ig-realism-audit.md` (existing)
