# Build Log

Reverse-chronological log of build, deployment, and maintenance actions.

---

## 2026-03-14 — Screenshot Refresh (Phase 18 Complete)

**Action:** Recaptured all frontend screenshots against live deployment (https://fiboki.uk).

**Commands run:**
```bash
cd frontend
FIBOKI_TEST_EMAIL=joe FIBOKI_TEST_PASSWORD=*** npx playwright test --project=auth-prod --reporter=list
# 16 passed (1.1m) — real login, real backend data

BASE_URL=https://fiboki.uk npx playwright test --project=screenshots --reporter=list
# 13 passed (29.8s) — mocked API shells
```

**Changes:**
- Updated `playwright.config.ts` viewport from 1280x800 to 1440x900 for auth-prod and screenshots projects
- Extended `e2e/auth-prod.spec.ts` to cover all 12 dashboard pages + trade detail (was 10, now 15 screenshots + session check)
- Extended `e2e/screenshots.spec.ts` to cover 13 pages (added scenarios, jobs, exposure, alerts)
- Auth-prod spec now supports `FIBOKI_TEST_EMAIL` / `FIBOKI_TEST_PASSWORD` env vars (with fallback to `FIBOKI_E2E_USERNAME` / `FIBOKI_E2E_PASSWORD`)
- Auth-prod spec now also writes to `audit/` directory with matching filenames

**Files refreshed:**
- `frontend/screenshots/auth-prod/` — 15 screenshots (01-login-success through 15-system)
- `audit/` — 14 screenshots overwritten (01-dashboard through 15-system, plus 12b-trade-detail)
- `frontend/screenshots/` — 13 mocked screenshots (login, dashboard, charts, backtests, research, scenarios, jobs, bots, exposure, trades, alerts, settings, system)

**Viewport:** 1440x900 (desktop)
**Target:** https://fiboki.uk → https://api.fiboki.uk
