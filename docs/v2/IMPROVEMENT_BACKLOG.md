# Fiboki V2: Improvement Backlog (consolidated)

**Date:** 2026-09-29. **Sources:** research/reports/due_diligence_2026-09-28/F_backend_audit.md (52 items, B-01..B-52, and rule proposals R-1..R-17) and G_frontend_plans_audit.md (58 items). This file is the single tracking list; the reports hold the evidence and file:line citations. Status is updated per commit. Nothing here is "done" unless a test proves it.

Legend: **Done** (commit on `v2/integration`), **Next** (next wave), **Later**, **Decide** (needs Joe).

## 1. Safety and correctness (from F §2)

| Item | Finding | Status |
|---|---|---|
| B-06/07 (P0-1) | Kill switch resolved three ways and cached per process | **Done**: `core/paths.resolve_paths`, `KillSwitch.refresh()`, gateway refuses an in-memory journal outside BACKTEST, cross-process test |
| B-09 (P1-4) | Gateway inputs defaulted to benign values | **Done**: `None` inputs block in DEMO/LIVE; PAPER excuses only with an explicit flag recorded on the attempt row; AST test |
| B-08 (P1-6) | fsync not durable on macOS; torn tails crash loaders | **Done**: `core/durable.py` (F_FULLFSYNC, CRC-framed lines, torn-tail quarantine + CRITICAL alert) for intents, kill switch, alerts, audit |
| B-15/16 (P1-9) | Alerts never left the machine; no watchdog | **Done**: httpx transports, CRITICAL outbox, `fiboki alerts test`, `fiboki watchdog run`; token redacted from httpx logs |
| B-17 (P1-10) | Long jobs outran the lease | **Done**: heartbeat pulse around every job and between jobs |
| B-13 (P1-8) | In-memory "durable" job ledger | **Done**: SQLite `jobs.sqlite`, idempotency UNIQUE, fenced claims, restart test; research worker uses it |
| B-18 (P1-15) | Nothing traded forward | **Done**: `entrypoints/paper_forward.py` from a committed, hashed wiring file; real httpx transport (practice host only, AST-limited to entrypoints/cli); live OANDA spread source; launchd `uk.fiboki.paper` |
| B-10 (P1-1) | FCA/ESMA leverage table wrong for AUDUSD, NZDUSD, XAGUSD, HK50 and ten major crosses | **Done**: currency-set definition, golden test over 41 instruments with citations; `ENGINE_VERSION=engine_v3_realism` |
| B-11 (P1-3) | BID bars traded as mid | **Done**: engine refuses non-mid frames; research converts via `bid_to_mid` and fingerprints it; agent bar sources converted |
| B-12/14 (P1-2) | Agent DSR used the wrong variance and a payload trial count | **Done**: Lo (2002) null variance; trial count from the experiment ledger; NOT_EVALUATED when unknown |
| B-25 (P1-5) | Research in USD, paper in GBP, no FX source | **Done**: research defaults to GBP with `SeriesFxSource` from stored daily closes (4-day staleness); agents' payloads default GBP with an FX source |
| B-26 (P1-16) | Sizing ignored costs | **Done**: `fixed_fractional_v2` (stop + spread + 2× expected slippage) default in both sizers |
| B-24 (P1-11) | Portfolio construction unwired | **Done**: paper sizes through construction (tiers, health, confidence, correlation, caps, vol target ≤ 1, margin, drawdown throttle, regime, correlated open risk, conviction step); engine mirrors it via `portfolio/engine_policy.py`; 3-path byte-identical parity test |
| B-27/34 (P2-1/2) | Blackout window from bar open; margin ignores the plan | **Done**: `limits_v2_*` (pre 30 min or one bar, post 15 min, from decision time); after-trade margin; `default_limit_set(mode)` |
| P2-6/7/9/11/12/13, P3-1/2/3/4/9 | Session DST, financing days, min stop, WFE, plateau, Sharpe, OLS precision, rng, staleness, UTC, end_inclusive | **Done** (see BUILD_LOG) |
| B-35 (P2-14) | SQLite without WAL/busy_timeout | **Done** for SQLAlchemy engines; raw `sqlite3` stores (news, positioning, events, jobs) set their own pragmas; a shared helper is **Next** |
| B-37 (P2-16) | Token in repr; unsalted sha256 passwords; shared default password | **Done**: repr=False, scrypt with legacy verify + rotation warning, doctor flags legacy hashes. **Decide**: rotate Joe's and Tom's passwords (USER_ACTIONS) |
| B-20 (P1-7) | Holdout re-spent via data refresh and reparameterisation | **Partly done**: append-only triggers + outcomes table. Calendar-span segments and per-family look budget: **Next** |
| B-21/22/23 (§3) | Gate thresholds uncalibrated | **Next**: E-1 pre-registration drafted (`research/preregistration/gate_calibration_e1.json`) and `scripts/gate_power_study.py`; run it before any threshold changes; publish `v2.1.0-calibrated` |
| B-28 (P1-12) | Calendar coverage cliff 2026-12-04; four currencies | **Next**: refresh job + doctor warning (doctor warns now); backfill 2010–2023; add RBA/BoC/SNB/RBNZ, US retail sales/PCE/ISM/GDP |
| B-29 | DSL `session_window` tz-aware, decision-time semantics (schema bump) | **Later** (holdout key-version handling first) |
| B-30/31/32 | Measured hour-of-week spread from the recorder; financing from rate history; validate against OANDA profile | **Later** (needs 4 weeks of recorded quotes) |
| B-36 | Catalogue paths relative to data root | **Next** (before the desktop move) |
| B-38 | Remaining AST guards (no worker start in api/, LEDGER_COLUMNS has no UUID, no path literals) | **Partly done** (compiled_in guard, rng guard); rest **Next** |
| B-39..42 | Vectorised rule masks (20–50×), indicator cache, cached AST parse in tests, categorical columns | **Later** (pin ledger hashes first) |
| B-47 | Cross-platform determinism CI (macOS arm64 vs Linux) | **Next** (needs a macOS runner or a recorded-hash job on the Mac) |
| B-48 | Budgets in tokens/seconds; daily GPU-seconds cap | **Partly done** (session token/second caps); scheduler cap **Next** |
| B-49 | Split `cli.py` (2k+ lines) and `agents/tools.py` (3k+) | **Later** |
| B-50/51 | Property and golden test families; new gates (bootstrap MaxDD, book correlation, regime coverage) | **Next** with E-1 |
| B-52 / R-1..R-17 | Rule updates | **Done** in AGENTS.md §8 (this commit) |

## 2. Frontend (from G §5)

| Item | Status |
|---|---|
| 1–10 (launcher CSP, production build under launchd, launcher never enables cycles, heartbeat tone, as-of, max-age staleness, verdict tones, mode reload after arm, asymmetric friction, CI web job) | **Done** (friction: PAUSE reason-only; FLATTEN typed `FLATTEN`; disarm typed `RE-ARM`; promote in LIVE typed `REAL MONEY`). Production build under launchd: `uk.fiboki.web.plist` runs `next start`; **verify on the Mac** |
| 11–13 (llama.cpp provider, first-run acceptance protocol, token budgets) | 11 **Done**; 12 **Next** (needs the Mac and a model); 13 **Partly done** |
| 14–21 (chart tokens, CVD, live region, drawer inert, scroll-padding, disabled-with-reason, execution page filter, mock every route) | 14, 15, 19, 20 **Done**; 16, 17, 18, 21 **Next** |
| 22–23 (ADRs; docs corrections + citation CI check) | **Next** |
| 24–26 (KDF passwords; login/roles; OpenAPI types) | **Done** |
| 27 (response-model provenance test) | **Next** |
| 28–29 (SSE endpoint and client) | **Done** (sleep/wake handling and clock-skew check **Next**) |
| 30–31 (runbooks; backup command and rehearsed restore) | **Done** except a rehearsed restore on the Mac |
| 32–37 (bake-off harness, router by scores, four-queue scheduler, context budgets, grammar compatibility test, KV pinning) | 35 **Done**; 32, 33, 34, 36, 37 **Next** (A2 remainder) |
| 38–41 (episodic summaries, new evals, query_news, NTP drift) | 40 **Done**; 38, 39, 41 **Next** |
| 42 (agent-authored candidate pipeline S0–S9) | **Next** (the T4 tier exists in code as a value; the pipeline is the next agent wave) |
| 43–46 (tier record, classifier with two families, deterministic proxy policy, pre-registration hashed) | 43 **Done**; 45 **Partly done** (baseline in shadow_report); 44, 46 **Next** |
| 47–48 (Agent Desk + read models) | **Next** (frontend Wave 4e) |
| 49–51 (Risk & Exposure, Command, Incidents screens) | Command v1 and Incidents **Done**; Risk & Exposure v2 **Next** |
| 52–55 (chart core, overlays endpoint, replay + trade inspector, drawings) | 53 **Done**; 52 **Next** (Wave 4c-1); 54, 55 **Later** |
| 56–58 (what-if sandbox, phone ops view, web-vitals beacons + soak) | **Later** |

## 3. Decisions for Joe

1. Rotate both operator passwords (legacy sha256 entries still verify but are flagged by `fiboki doctor`).
2. Enable which optional sources: Finnhub key (free), FRED key (free), Marketaux (paid), Trading Economics (paid, point-in-time consensus). ForexFactory feed stays off unless you accept the terms risk; it is comparison-only either way.
3. Approve E-1 (gate calibration study) before any threshold moves.
4. The agent influence tier: T1 (shadow) today. Raising to T2 (veto) or T3 (dampen) requires the pre-registered comparison to pass and a signed record; T4 (agent-authored candidates to paper) requires the S0–S9 pipeline.
