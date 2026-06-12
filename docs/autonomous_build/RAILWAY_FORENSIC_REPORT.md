# Railway Forensic Report — IG Demo Execution Gap

**Date:** 2026-06-12 (browser-verified via authenticated Railway dashboard session)
**Auditor:** Claude (autonomous build)

## Service map

| Item | Value |
|------|-------|
| Railway project | `ravishing-benevolence` (env: production) |
| API service | `Fiboki_Trading` — Online, domain api.fiboki.uk, US West, 1 replica, volume `fiboki_trading-volume` |
| Worker service | **NONE** — worker runs as in-process daemon thread inside the API service |
| Database | `Postgres` — Online, volume `postgres-volume` |
| Deployed commit | `ad79c302` build of git 63b94fc "fix: IG execution hardening" (== origin/main HEAD), deployed 14 May 2026 |
| Deployment status | ACTIVE, "Deployment successful"; one FAILED historical deploy (worker live-monitoring commit, later redeployed) |
| API health | `GET /api/v1/health` → ok (verified 2026-06-12) |
| Worker liveness | Confirmed alive via continuous `fibokei.worker` log lines on 2026-06-12 |

## Environment variables (presence only — values never read)

Present (15): `DATABASE_URL`, `FIBOKEI_CORS_ORIGINS`, `FIBOKEI_DATA_DIR`, `FIBOKEI_IG_ACCOUNT_ID`, `FIBOKEI_IG_API_KEY`, `FIBOKEI_IG_PAPER_MODE`, `FIBOKEI_IG_PASSWORD`, `FIBOKEI_IG_USERNAME`, `FIBOKEI_JWT_SECRET`, `FIBOKEI_LIVE_EXECUTION_ENABLED`, `FIBOKEI_TELEGRAM_BOT_TOKEN`, `FIBOKEI_TELEGRAM_CHAT_ID`, `FIBOKEI_USER_JOE_PASSWORD`, `FIBOKEI_USER_TOM_PASSWORD`, `FIBOKEI_VISIBLE_STRATEGIES` (+11 Railway-injected).

Absent (notable): `FIBOKEI_EXECUTION_ROUTER_MODE` (→ defaults `legacy_single`), `FIBOKEI_IG_ACCOUNT_ENABLED`, `FIBOKEI_WORKER_EXTERNAL`, Sentry DSN, production-IG variables (correct — must stay absent).

## Decisive log evidence (Deploy Logs, deployment ad79c302)

1. **IG auth and dispatch WORK.** `IG place_order:` lines appear many times daily (FX: GBPUSD/USDCHF/AUDCHF CFDs; indices: DAX/CAC/ASX/HANGSENG/NIKKEI/SPTRD; metals: gold/silver). The earlier belief that "no trades reach IG" is stale — the router selects the IG adapter and submits orders. H1/H2/H3/H5 from VERIFICATION_REPORT are **ruled out**; the gap is per-instrument execution failures (refined H4).

2. **Root cause 1 — prices API version mismatch (all instruments).** Every `GET /prices/{epic}/{resolution}/200` → **404 Not Found**, e.g. `GET https://demo-api.ig.com/gateway/deal/prices/CS.D.GBPUSD.CFD.IP/HOUR/200 → 404`, followed by "IG live feed failed … falling back to yfinance". Code: `ig_client.get_prices` sent `VERSION: 3` on the **v2 path-style** endpoint. IG's v3 prices endpoint is `/prices/{epic}` with query params. → Worker monitoring silently ran on yfinance data, never IG.

3. **Root cause 2 — spread-bet EPICs on a CFD-only API key (gold/silver).** Every gold/silver order → `POST /positions/otc → 403`. Persisted execution_attempts show the broker reason: `'unauthorised access, apiUser has no access to the relevant exchange. Epic=CS.D.USCGC.TODAY.IP exchangeId=FX_BET_ALL'`. `core/instruments.py` hardcodes `CS.D.USCGC.TODAY.IP` / `CS.D.USCSI.TODAY.IP` — spread-bet epics — while the account's exchange access is CFD.

4. **Root cause 3 — naked size-capped orders.** Several bots submit `stop=0.0 limit=0.0 size=20.00` (the FX hard cap) — e.g. bots e01595a5, d62888c1, 49f9b893. Orders without stops at maximum size reached the broker; sizing degraded to the cap because risk-sizing is undefined with stop=0.

5. **No confirmation-retry failures** logged — orders that POSTed (FX/indices) obtained confirmations; their accepted/rejected status is persisted in `execution_attempts` (DB check pending authenticated access).

## Remediation implemented (commit pending push)

| Fix | Change | Tests |
|-----|--------|-------|
| Prices 404 | `get_prices` VERSION 3→2 with explanatory comment | `test_get_prices_uses_version_2` |
| Epic mismatch | New `IGClient.search_markets()`; adapter `_resolve_epic_for_account()` — on 403 "no access to the relevant exchange" (no deal created → duplicate-safe), resolve a tradeable account-type-correct epic at runtime, cache, log remap, retry once | 3 tests incl. no-retry-loop |
| Naked orders | `FIBOKEI_IG_REQUIRE_STOP` (default **true**): reject pre-submission with `MISSING_STOP` | 3 tests |
| Worker extraction | `FIBOKEI_WORKER_EXTERNAL` flag + runbook (previous commit 6b54ff8) | 3 tests |

All 80 execution-layer tests pass locally.

## Remaining actions

1. **Push commits to origin/main** (sandbox has no GitHub credentials) → Railway auto-deploys.
2. After deploy, verify in logs: prices 200s replacing 404s; gold/silver remap warnings then accepted orders; MISSING_STOP rejections for stopless strategies.
3. Query `execution_attempts` for FX/index confirmation statuses (were orders ACCEPTED or REJECTED at IG?) via authenticated API or DB.
4. Decide whether stopless strategies (e01595a5, d62888c1, 49f9b893) should gain stops or be IG-disabled.
5. Create dedicated Railway worker service per DEPLOYMENT_RUNBOOK.md, then set `FIBOKEI_WORKER_EXTERNAL=true` on the API.
6. Correct static gold/silver epics in `core/instruments.py` once the runtime-resolved CFD epics are observed in logs (evidence-based, not guessed).

## Safety notes

- No secret values were viewed or transcribed; variable names only.
- No Railway configuration was changed in this audit.
- Production IG remains hard-blocked; no funded-account surface exists in this deployment (no production variables present).
