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


## 2026-06-19 — Post IG-demo proof: fleet recovery, skills, build-stream plan

**Confirmed via live API (Chrome):** IG demo is executing — Gold fill `dealId=
DIAAAAXSBQD3EAQ` (audit row 359). FX/indices were rejecting with `REJECTED:
UNKNOWN`; root cause = IG returns `reason:"UNKNOWN"` and the adapter discarded the
full confirmation. **Fixed** (prior commit): adapter now logs + persists the full
IG confirmation and surfaces the real reason/error_code.

**Shipped this session (branch `wave0-2-hardening`):**
- `fix(phases)`: phase transition zeroes daily/weekly PnL + worker reloads account
  on phase change (was carrying −£193.46 into Phase C). `tests/test_worker_phase_reset.py`.
- `feat(fleet)`: `POST /paper/bots/restore-stale` — restores errored bots
  (state→monitoring, clears error) and classifies monitoring-but-stale bots under
  `needs_attention`. `tests/test_restore_stale_bots.py` (ruff clean, 4 passed).
- `feat(skills)`: 4 priority Claude Code skills as real files under
  `.claude/skills/`: `fiboki_repo_guardian`, `fiboki_ig_execution_debugger`,
  `fiboki_worker_observer`, `fiboki_docs_scribe`.
- `docs`: `IG_RECONCILIATION_PLAN.md` (captures the `SBQLDCAC` dealReference vs
  `dealId` nuance + exact prod check), `OPERATING_PHASES.md`,
  `NEXT_BUILD_STREAMS.md` (8 waves + shortlist-dedup/factory answers),
  `FRIEND_DEMO_READINESS.md`.

**Not built this pass (continuation, by design — preserve working execution):**
Wave 1 reconciliation importer (needs IG `/history/transactions` on worker),
Wave 2 recent-execution panel + clickable cards, Wave 4 chart overlays, Wave 6
short-PnL UI/Telegram audit, Wave 7 research/job UX, worker-side stale auto-heal,
promotion dedupe via ledger. All specced in `NEXT_BUILD_STREAMS.md`.

**Branch only — `main` untouched, nothing pushed/deployed.**


## 2026-06-19 — Wave 1: broker trade ledger (backend foundation)

Makes broker-executed IG trades importable + visible (rule: if IG records a
trade, Fiboki must show it).

- `db/models.py`: `BrokerTradeModel` (`broker_trades`), unique (source, reference)
  for idempotent import; stores IG `reference` (e.g. SBQLDCAC) AND `deal_id`,
  direction/size/levels, broker `pnl`, currency, timestamps, bot/strategy, raw.
- `execution/ig_client.py`: `get_transactions(from, to)` → IG v2
  `/history/transactions` (operator-facing reference + profitAndLoss).
- `execution/broker_ledger.py`: `parse_ig_pnl` (handles £/GBP/()/commas),
  `normalize_ig_transaction` (skips cash/no-ref; direction from size sign),
  `import_ig_transactions` (idempotent upsert, returns counts).
- `db/repository.py`: `upsert_broker_trade` (keyed source+reference),
  `list_broker_trades` (filters: source/bot/strategy/instrument).
- `api/routes/execution.py`: `GET /execution/broker-trades` (filterable),
  `POST /execution/reconcile-trades` (pulls IG history, typed status, never
  raises; defaults from_date = 30d ago).
- Tests: `tests/test_broker_ledger.py` — PnL parsing, SBQLDCAC import, skip
  cash/no-ref, idempotent re-import (no dupes). ruff clean; 7 passed (incl.
  full-app build via api_strategies).

**Operator note / deploy:** `POST /execution/reconcile-trades` needs IG creds on
the service that runs it. Per `render.yaml` only the **worker** has IG creds, so
either (a) add IG **read** creds to the API service (history pull is read-only,
no execution), or (b) call the importer from the worker on a schedule. New
`broker_trades` table auto-creates via `create_all` on deploy.

**Continuation (Wave 1 UI):** Trade-History tabs/filters (All / IG demo / Paper /
Backtest) reading `/execution/broker-trades`; match broker trades to bot signals
(deal_id/time/instrument) to fill bot_id/strategy_id; instrument name→symbol map.

Branch `wave0-2-hardening`; `main` untouched.


## 2026-06-19 — Wave 1 UI: Trade History IG-demo tab

- `frontend/src/lib/api.ts`: `brokerTrades(params)` → `GET /execution/broker-trades`;
  `reconcileTrades(fromDate?)` → `POST /execution/reconcile-trades`.
- `frontend/src/app/(dashboard)/trades/page.tsx`: source tabs **Paper / Backtest**
  vs **IG Demo**. IG Demo tab shows broker-executed trades (closed time,
  instrument, direction, size, open/close level, **broker PnL**, IG reference)
  with a **Sync from IG** button (calls reconcile, shows imported/updated/skipped).
  Direction rendered neutrally; PnL carries the colour (not direction) — aligns
  with the Wave 6 short/sell-PnL concern in the new surface.
- Verified: `tsc --noEmit` clean for changed files (only pre-existing unrelated
  error in `ichimoku-store.test.mts`).

Completes the visible chain for the Gold trade: once IG creds are on the service
running `reconcile-trades`, "Sync from IG" imports `SBQLDCAC` and it appears under
the IG Demo tab with its £554 broker PnL. Branch `wave0-2-hardening`.


## 2026-06-19 — Wave 6 (short PnL colour) + Wave 2 (recent execution)

**Wave 6 — short/sell PnL display.** Direction is no longer coloured as if it
were profit/loss:
- `trades/page.tsx`: direction badge LONG→info / SHORT→neutral (was SHORT→error/red).
- `trades/[id]/page.tsx`: Direction field no longer red for SHORT; PnL field keeps
  the outcome colour.
- Telegram `send_trade_closed` already used `✅ if pnl>0 else ❌` (PnL-based) — left as is.

**Wave 2 — recent execution.** Dashboard now has a **Recent Execution** panel
(last 6 trades: date, instrument, direction, strategy, PnL coloured by PnL), each
row links to the trade detail; "Trade history" link to `/trades`. 30s refresh.

Verified: `tsc --noEmit` clean for all changed files. Branch `wave0-2-hardening`.


## 2026-06-19 — IG reject ROOT CAUSE fix + promotion dedupe + stale auto-heal

**IG rejections — root cause from real activity history (Z5ZAV CSV):** every
FX/index order rejected `Failed to retrieve price information for this currency`;
Gold (`CS.D.CFPGOLD.CFP.IP`) filled. Same class as the old metals 403 — the
FX/index `.CFD.IP`/`.IFD.IP` epics aren't priceable on this account. See
`docs/IG_REJECTION_DIAGNOSIS_2026-06-19.md`.
- `fix(ig)`: `_is_epic_resolution_reject()` + generalised the runtime epic
  re-resolution retry to fire on price/market/currency/UNKNOWN rejects (not just
  403). Duplicate-safe (no deal opened). Tested vs the exact Z5ZAV reason.

**Promotion dedupe (answers "is it already promoted?"):**
- `create_bot` refuses a 2nd active bot for the same strategy+instrument+TF
  (409 `already_promoted` with existing bot ids) unless `allow_duplicate=true`;
  writes a `promoted_to_paper` ledger event.
- `GET /paper/bots/promotion-status` → already_promoted / count / bot_ids.
- Tests: `test_promotion_dedupe.py`.

**Stale auto-heal (Wave 5 deeper):**
- Worker `_auto_heal_stale_bots()` re-warms monitoring bots stale beyond a
  generous per-TF window (skips open positions); runs each cycle.
- Tests: `test_ig_epic_and_heal.py`.

Verified: ruff clean; 37 passed across the new/related suites. Branch
`wave0-2-hardening`.

**Operator caveat:** the epic retry only succeeds if a priceable FX/index epic
exists for Z5ZAV. If the demo account can't price standard FX CFDs, the markets
must be enabled on IG or the catalogue repointed to supported epics (verify via
an IG markets search on the worker — `fiboki ig-universe` planned).
