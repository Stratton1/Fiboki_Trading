# Deployment Runbook — Dedicated Worker Migration (Phase C)

## Goal

Move bot orchestration out of the API process into a dedicated Railway worker service, with zero double-execution risk.

## Preconditions

- API service healthy (`GET /api/v1/health` → ok).
- Know the current router mode: check startup logs for `ExecutionRouter built: mode=…`.
- PostgreSQL attached and shared.

## Steps

1. **Create the worker service** in the Railway project:
   - Same repo, same root (`backend/`), same Dockerfile.
   - Override start command: `python -m fibokei.worker --poll-interval 60`
   - No public domain, no health-check path (Railway healthcheck is HTTP-only; worker liveness comes from logs until heartbeat lands — see Phase C2).
   - Restart policy: ON_FAILURE, max retries 5+.
2. **Copy env vars** from the API service to the worker service: `FIBOKEI_DATABASE_URL` (or rely on Railway PG injection), all `FIBOKEI_IG_*`, `FIBOKEI_EXECUTION_ROUTER_MODE`, account-enable flags, `FIBOKEI_TELEGRAM_*`.
3. **Verify worker logs** show: `Recovered N active bots` and `ExecutionRouter built: mode=… targets=[…]` with the expected targets.
4. **Set `FIBOKEI_WORKER_EXTERNAL=true` on the API service** and redeploy it. Verify startup log shows `in-process worker thread disabled`.
5. **Restart drill:** restart the worker service; confirm `recover()` log line, no duplicate trades for already-evaluated bars (worker tracks `last_evaluated_bar` per bot).
6. **Rollback:** delete/stop the worker service and unset `FIBOKEI_WORKER_EXTERNAL` on the API. The in-process thread resumes on next API deploy.

## Order matters

Deploy the worker service FIRST, verify it evaluates, THEN flip `FIBOKEI_WORKER_EXTERNAL` on the API. During the brief overlap, `last_evaluated_bar` tracking prevents duplicate candle processing, but keep the overlap short.

## Known gaps (tracked in WORK_LEDGER)

- No DB heartbeat yet (T-C2) — worker liveness is logs-only until then.
- No leader lease — never run two worker services simultaneously.
