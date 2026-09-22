# Verification Report — Autonomous Build

**Session start:** 2026-06-12
**Local HEAD:** 63b94fc (== origin/main) — "fix: IG execution hardening — session hygiene, risk_pct routing, diagnostic logging"

## Phase A — Forensic Baseline (evidence)

### Verified facts

| # | Check | Result | Evidence |
|---|-------|--------|----------|
| A1 | Deployed API reachable and healthy | PASS | `GET https://api.fiboki.uk/api/v1/health` → `{"status":"ok","version":"1.0.0"}` (2026-06-12) |
| A2 | API endpoints auth-gated | PASS | `/api/v1/system/status`, `/api/v1/execution/mode` → 401 Not authenticated |
| A3 | Local repo in sync with origin | PASS | `git rev-parse HEAD origin/main` → both 63b94fc |
| A4 | IG production hard block present | PASS | `execution/ig_client.py:18-19, _ensure_demo_only()` — base URL pinned to demo; PRODUCTION_BLOCKED error |
| A5 | IG adapter lifecycle complete in code | PASS | `ig_adapter.py` — auth, market details, sizing, place_order with confirmation retry, get_positions, close_position, partial_close, update_stop_limit |
| A6 | Execution router with kill-switch + risk gates | PASS | `execution/router.py` — kill-switch check assumes active on error; account risk engine in dispatch path |
| A7 | Worker entry point exists | PASS | `fibokei/worker.py` (938 lines), `python -m fibokei.worker` supported; also started as daemon thread inside API lifespan (`api/app.py:299-340`) |
| A8 | Railway config defines API only | CONFIRMED | `backend/railway.json` — single service, Dockerfile CMD = uvicorn. Worker service exists only in `render.yaml` (Render fallback, unused) |
| A9 | Worker runs in-process with API | CONFIRMED | `app.py` lifespan `_start_worker_thread()` — daemon thread, dies with API process, no heartbeat/lease |
| A10 | Realism audits already applied | CONFIRMED | `docs/forensic-ig-realism-audit.md` (Sharpe fix, IG leverage limits, £1k capital), `docs/forensic-bot04-eurusd-audit.md` (sizing fix) |
| A11 | Alembic migrations present | CONFIRMED | `backend/alembic/` — 4 migrations; DB falls back FIBOKEI_DATABASE_URL → DATABASE_URL → SQLite |
| A12 | No TODO/FIXME in execution path | CONFIRMED | grep over execution/, jobs/, risk/ |

### Adapter-selection logic (as-built, differs from deployment.md)

`build_execution_router_from_env()` (`execution/router_factory.py`):

- `FIBOKEI_EXECUTION_ROUTER_MODE=legacy_single` (default): IG demo used **only if** `FIBOKEI_LIVE_EXECUTION_ENABLED=true`, else paper.
- `env_global_fanout`: IG target built **only if** `FIBOKEI_IG_ACCOUNT_ENABLED=true` (default **false**). `FIBOKEI_LIVE_EXECUTION_ENABLED` is NOT consulted for demo in this mode.
- `db_targets`: targets read from `execution_accounts`/`bot_execution_targets` tables; bots with no targets fall back to **default paper account**.

**Documentation drift:** `docs/deployment.md` says IG demo activates with `FIBOKEI_LIVE_EXECUTION_ENABLED=true` + `FIBOKEI_IG_PAPER_MODE=true`. That is only true in legacy_single mode. In fan-out/db modes different flags/rows govern IG.

### Root-cause hypothesis matrix — "trades recorded locally but not at IG demo"

| # | Hypothesis | Mechanism | How to confirm | Status |
|---|-----------|-----------|----------------|--------|
| H1 | Router mode/flag mismatch | Deployed env uses `env_global_fanout` or `db_targets` without `FIBOKEI_IG_ACCOUNT_ENABLED=true` / enabled IG account row → IG target never built; signals go to paper only, silently by design | Read Railway env vars; or check startup log line "ExecutionRouter built: mode=… targets=[…]" | **PRIME SUSPECT — needs Railway access** |
| H2 | db_targets fallback | Bots have no `bot_execution_targets` rows → fall back to default paper account | Query `execution_accounts`/`bot_execution_targets` in prod DB | OPEN |
| H3 | Deployed commit stale | Railway running a commit predating IG hardening (63b94fc, 29b14d1, 7b861c7) | Compare Railway deploy SHA vs origin/main | OPEN |
| H4 | IG auth failing at runtime | Creds present but invalid/expired API key → adapter errors logged, attempts recorded as `error` | Railway logs: "IG auth failed"; or `execution_attempts` table status column | OPEN |
| H5 | Worker thread dead | `_start_worker_thread` exception → API healthy but no evaluation at all | Railway logs: "Failed to start paper worker thread"; absence of eval log lines | OPEN (would also stop paper trades, so less likely) |

All five are discriminated by the same two reads: **Railway startup logs** (router summary line + IG config validation line are logged explicitly at boot) and **the `execution_attempts` table**. Neither is reachable from this session (no Railway connector; API auth-gated; DB not exposed).

### Local test baseline

(see WORK_LEDGER task T-A3 — run in progress)
