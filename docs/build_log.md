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


## 2026-06-19 — Wave 0 trust reset + Wave 2 registry-health

**Verification baseline (Python 3.11.15, fresh .venv):**
- `ruff check src/` clean.
- Core offline subset: 104 passed (indicators, metrics, execution router/signals, fleet/risk).
- Full suite hangs offline on network-coupled (yfinance/IG) + heavy-compute tests
  lacking markers/mocks/timeout → triaged as environment, not code. See
  `docs/TEST_HEALTH_2026-06-19.md`.

**Wave 0 — test hygiene (makes suite completable):**
- `pyproject.toml`: added `pytest-timeout` dev dep; pytest `addopts =
  "--timeout=120 --timeout-method=signal"`; registered `network` / `slow` markers.
  Infinite network hangs now fail fast instead of stalling the whole run.

**Wave 2 — strategy registry truth (productised):**
- `strategies/registry.py`: added `CANONICAL_STRATEGY_IDS` (12), `classify_strategy()`,
  `registry_health()`; `list_available()` now carries a `tier` field.
- `api/routes/strategies.py`: `GET /strategies/registry-health` (placed before the
  `{strategy_id}` route); `tier` added to list + detail responses.
- `tests/test_registry_health.py`: locks 12 canonical, ≥12 registered, registry/disk
  parity, tier classification, `tier` present in list. 9 passed (with API tests).

**Evidence docs added:** CURRENT_STATUS.md, TEST_HEALTH_2026-06-19.md,
STRATEGY_REGISTRY_AUDIT.md, IG_GATE_STATUS.md, NEXT_PHASES.md.

**Still blocked (not done):** IG demo rejection root-cause (Wave 1) needs the live
`error_code` from Railway (`GET /execution/audit?execution_mode=ig_demo` or worker
logs); frontend `npm run build` + screenshots; full-suite green number after the
network tests are marked/mocked.

**Committed to branch `wave0-2-hardening` (NOT main) — pending operator review/push.**


## 2026-06-19 — Wave 3 (lifecycle ledger) + Wave 4 (agent skills spec)

**Wave 3 — append-only agent/lifecycle/lineage ledger (the agent-autonomy foundation):**
- `db/models.py`: `AgentRunModel`, `BotLifecycleEventModel`, `StrategyLineageModel`
  (write-once; full provenance: prompt/code-diff hashes, dataset version, result IDs,
  actor, approval status, reason).
- `db/ledger_repository.py`: create + read functions only — **no update/delete by
  design**. Validates lifecycle event vocabulary (19 types), agent lanes, actors.
- `api/routes/agent_ledger.py`: `GET/POST /agent-runs`, `/bot-lifecycle`,
  `/strategy-lineage` (append-only; wired into `app.py`). Tables auto-created via
  `Base.metadata.create_all` on startup (Alembic migration recommended as follow-up
  for prod history).
- `tests/test_agent_ledger.py`: round-trip + provenance + vocabulary validation +
  **immutability assertion** (repo exposes no update/delete) + accumulate-not-overwrite.

**Wave 4 — agent skills + MCP setup:** `docs/AGENT_SKILLS_SPEC.md` — 8 bounded skills
across 4 lanes (Builder / Quant Auditor / Operator / Safety Governor) with
allowed/forbidden actions, required reads, verification commands, stop conditions;
plus MCP connector plan and non-negotiable permission boundaries.

**Verification:** `ruff check src/` clean; `pytest tests/test_agent_ledger.py
tests/test_registry_health.py tests/test_api_strategies.py` → 16 passed (full app
builds with the new router).

**Gated / not done (honest):** Wave 1 IG rejection fix (needs the live error_code from
Railway); Wave 5 Strategy Lab + Wave 6 bot factory (large multi-session builds, to be
delivered on top of this ledger); Waves 7–8 demo/live fleets (require running system,
market hours, and human sign-off per the safety doctrine).
