# Operating Phases & Reset Semantics

**Date:** 2026-06-19

## Model (exists)
`EvaluationPhaseModel` (`evaluation_phases`) with `phase_label`, `is_active`,
`started_at`, `archived_at`, `initial_balance`, `normalized_baseline`,
`broker_balance_at_start`, `total_trades`, `net_pnl`, `final_balance`. Bots and
trades carry a `phase_id`. Repository: `get_active_phase`, `archive_current_phase`,
`create_new_phase`, `transition_to_new_phase`. API: `GET /paper/phases`,
`GET /paper/phases/active`, `POST /paper/phases/transition`.

**Principle:** Reset = *archive the current phase and start a new one*. History is
preserved (archived phases remain queryable + exportable to Excel). Never delete.

## Fix shipped this session
Starting a new phase now **always zeroes the period PnL counters** (daily/weekly)
on the paper account, and the **worker detects the phase change and reloads the
account** so the reset is not overwritten by the worker's in-memory copy.
Previously Phase B's −£193.46 daily PnL bled into Phase C. Balance rebase remains
opt-in via `reset_account`. Tests: `tests/test_worker_phase_reset.py`.

## Remaining (Wave 3 continuation)
- Dashboard metric toggle: **current phase / lifetime / since-reset / IG-demo /
  paper / backtest**. Each card reads phase-scoped aggregates.
- Phase association for backtests/research/alerts where practical.
- A single fleet-summary source so the bots page and dashboard agree on
  active vs stale counts (the "44 vs less" mismatch).

## Operator note
After a phase transition, the worker applies the reset on its **next poll cycle**
(≤60s). If you want it immediate, a worker restart also reloads the account from
the (already-reset) DB.
