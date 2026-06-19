---
name: fiboki_worker_observer
description: Distinguish API-process state from the worker's real execution state. Use when the System panel disagrees with reality (e.g. shows "paper only" while the worker is routing to IG), or to confirm worker health, router mode and recent execution.
---

# fiboki_worker_observer

## Purpose
Reveal what the WORKER (the process that actually trades) is doing, separately from the API process — Fiboki's System panel reflects the API env, not the worker.

## When to use
- System "Execution Accounts" panel says paper-only but IG orders are filling.
- Checking worker liveness, router mode, env flags, last signal/fill/rejection.

## Files to read first
`backend/src/fibokei/worker.py`, `execution/router_factory.py`, `core/feature_flags.py`, `api/routes/system.py`, `db/repository.py` (worker heartbeat + execution audit).

## Data to inspect
- API: `GET /system/status` (worker heartbeat: `last_beat_at`, `bots_active`, `loops_completed`, `last_error`). `GET /execution/audit` for last order attempt/fill/rejection.
- Railway: confirm on the **worker** service `FIBOKEI_LIVE_EXECUTION_ENABLED`, `FIBOKEI_EXECUTION_ROUTER_MODE`, IG creds — these differ from the API service.

## Allowed actions
- Report worker vs API divergence with evidence.
- Recommend a worker-side execution-target heartbeat so the System UI shows the worker's true router mode/targets.
- Implement that observability (read-only surfacing); add tests for status serialization.

## Forbidden actions
- Changing execution behaviour, risk, or flags to "make the panel match".
- Restarting/redeploying production without operator action.

## Required verification commands
```bash
cd backend && python -m ruff check src/
python -m pytest -q tests/test_api_system.py tests/test_worker_heartbeat.py
```

## Output format
`Worker state` (heartbeat, router mode, targets, last fill/reject) · `API state` · `Divergence` · `Recommended fix`.

## Stop conditions
- If Railway worker env/logs are unreachable, STOP and give the exact checks the operator must run; do not infer worker env from the API process.
