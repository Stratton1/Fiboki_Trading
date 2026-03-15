# Build Log

Reverse-chronological log of build, deployment, and maintenance actions.

---

## 2026-03-14 — Phase 19: UX + Correctness Hardening (in progress)

**Slice A — DRY-UP TP-hit negative PnL explanation**
- Created `frontend/src/lib/trade-explain.ts` — shared helper (`isTpHitNegativePnl`, `TP_HIT_NEGATIVE_PNL_EXPLANATION`)
- Created `frontend/src/components/TpHitSpreadTip.tsx` — reusable InfoTip component
- Replaced duplicated condition+copy in 3 locations: `backtests/[id]/page.tsx`, `trades/page.tsx`, `trades/[id]/page.tsx`

**Slice B — Shortlist integration**
- Added "Save to Shortlist" star icon on backtests list page (per-row, with filled/unfilled state)
- Added "From Shortlist" dropdown picker on backtests run form and Paper Bots creation form
- Added "Save to Shortlist" button on backtest detail page

**Slice C — Backtest Assumptions + Diagnostics**
- Added Assumptions panel on backtest detail (capital, spread, slippage, leverage, sizing, compounding, pip conversion)
- Added Diagnostics panel with warnings for extreme Sharpe, suspicious win rates, few trades, inconsistent expectancy
- Fixed TypeScript type errors (`Record<string, unknown>` cast, `Number()` wrapping, `String()` for ReactNode)

**Slice D — tp_hit regression tests**
- Created `backend/tests/test_tp_hit_negative_pnl.py` — 4 tests documenting spread artefact
- Tests: wide spread negative PnL, zero spread positive PnL, unit-level Position verification, SHORT direction

**Slice E — Playwright shortlist workflow**
- Created `frontend/e2e/shortlist-workflow.spec.ts` — E2E test for promote→load flow
- Added `shortlist` project to playwright config

**Slice F — Dev seed endpoint**
- Created `backend/src/fibokei/api/routes/dev.py` — `POST /dev/seed/backtest` gated behind `FIBOKEI_DEV_SEED=1`
- Idempotent seed: 1 backtest run + 5 trades (mix of wins/losses, includes TP-hit negative PnL artefact)
- Registered conditionally in `backend/src/fibokei/api/app.py`
- Created `backend/tests/test_dev_seed.py` — 4 tests (seed creates, idempotent, 5 trades, gated without env var)

**Slice G — Backtest detail chart trust + marker noise controls**
- Added marker toggle controls (entries/exits/connecting lines) with localStorage persistence to `backtests/[id]/page.tsx`
- Updated `TradeMarkerChart.tsx` — conditional overlay rendering based on toggle props, lines OFF by default
- Added amber fallback UI when market data unavailable
- Added trade count hint when >50 trades displayed
- Created `frontend/e2e/chart-trust.spec.ts` — 3 Playwright tests (canvas renders, toggle checkboxes, no-data fallback)
- Added `chart-trust` project to `playwright.config.ts`

**Docs**
- Updated `docs/roadmap.md` — Phase 19 marked COMPLETE, T-19.D2/D3/E2 checked off
- Updated `docs/blueprint.md` — added §17.8 Saved Shortlist, expanded Backtests and Running Bots page specs

**Verification:**
```bash
cd backend && python3 -m pytest -q   # 623 passed
cd frontend && npx next build        # clean build, no type errors
```

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
