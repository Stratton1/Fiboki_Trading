# Fiboki V2: Frontend Overhaul Plan (operator workstation)

**Status:** approved plan; Wave 0 shipped (`c40c886`). **Date:** 2026-09-28. **Owner:** Joe.
**Input:** research/reports/E_frontend_landscape.md (current-state audit with file references, landscape survey with 34 cited sources, measured bundle sizes).

## 1. Executive summary

`apps/web` gets the hard part right and the rest wrong. It encodes the V1 lessons faithfully: every number is a `Figure` with provenance, "absence is not zero" is enforced by lint and by a grep test, four async states, a mode banner in the root layout, PAUSE distinct from FLATTEN. But it is 28 static polled tables with no price chart, no login screen, no keyboard model, no sortable or virtualised grid, colour meanings that collide (the PAPER chip was the same green as profit, LIVE the same red as loss), and, until this wave, a banner that blinked out of LIVE every 30 seconds.

The overhaul keeps the honesty contract unchanged and rebuilds everything around it as a high-end, dark-first, keyboard-driven terminal: one real-time stream, a design system in OKLCH, a proper grid, a price chart with backend-computed overlays, nine screens each with one primary action, and a visual grammar in which *execution mode* (where an order would go) and *provenance* (where a number came from) can never be confused.

Total: about 112 to 125 frontend engineer-days plus 10 to 11 backend days, in seven waves. Wave 0 (3 days) is done.

## 2. Decisions

| # | Decision | Note |
|---|---|---|
| D-F1 | Stay on Next.js 16 / React 19 / TypeScript. Client components; same-origin `/api` via the existing rewrite. | No SEO, cookie auth, external API: server components add nothing. |
| D-F2 | **Tailwind CSS 4 with OKLCH tokens**; an owned `components/ui/*` layer on **Base UI** (shadcn pattern, no visual dependency). | Base UI is shadcn's default since July 2026; MIT. |
| D-F3 | **TanStack Query** for server state, one multiplexed **SSE** stream (native `EventSource`, leader tab via Web Locks) feeding a **Zustand** live store; **nuqs** for URL state. | Fixes the poll-blanking and no-shared-cache defects structurally. WebSockets rejected: every mutation is already an audited REST POST with CSRF. |
| D-F4 | **TanStack Table 9 + Virtual 3** in one owned `DataGrid`; **cmdk** palette; `g`-chord hotkeys. | Row-keyed live updates, no remount on tick. |
| D-F5 | **Price charts: TradingView Lightweight Charts 5.2**, not KLineChart. **This reverses the project instruction.** Reason: KLineChart's value is its in-browser indicator engine, which violates "indicators are computed only in backend `indicators/`" and invites chart/backtest mismatch; Lightweight Charts has no indicator engine, so the rule holds by construction. 58 KB gzipped measured. If Joe overrules, a source-rule test must forbid `registerIndicator`/built-in indicator names. | Joe to confirm. |
| D-F6 | **Analytics: uPlot (23 KB) plus the existing SVG components; ECharts tree-shaken and lazy-loaded on the Research Lab only if a heatmap needs interaction. Plotly rejected** (about 1.4 MB gzipped; the V1 mistake). **This also reverses the project instruction.** | Joe to confirm. |
| D-F7 | Replace the "exactly three dependencies / no chart library" test with an allow-list plus **per-route byte budgets** enforced by size-limit in CI (shell ≤ 180 KB, chart workstation ≤ 300 KB, Research Lab ≤ 420 KB). | Keeps the V1 protection without blocking a 58 KB chart. |
| D-F8 | Fixed workstation shell with resizable splits, saved column sets and density modes; **no free-form docking**. | Two operators; IBKR's TWS-vs-Desktop experience shows configurability matters, but docking is state and test surface without benefit here. |
| D-F9 | Fonts: Inter Variable (`tnum`, `zero`) for UI and figures; JetBrains Mono for ids and hashes. Both OFL, self-hosted. | |
| D-F10 | Backend SSE via `sse-starlette` at the current `fastapi==0.115.6` pin (native SSE needs ≥ 0.135). | Upgrade is a separate decision with its own test run. |

## 3. Design language (summary; token values in report E §4)

- **Quiet chrome, loud state.** Near-monochrome surfaces (neutral hue 255, low chroma). Colour is reserved for four meanings: execution mode, provenance, P&L direction, health.
- **Green and red are exclusively P&L**, always with a sign and a ▲/▼ glyph; a colour-blind preset swaps to blue/orange; zero renders only when the API returned 0, null renders as "no data".
- **Execution mode changes the whole shell**: a viewport frame (none for backtest, 1 px cyan paper, 2 px dashed violet shadow, 3 px hatched amber demo, **4 px magenta live with "REAL MONEY" and the operator's name**), the favicon and the tab title. Live is magenta, never loss-red. "MODE UNKNOWN" appears only when the stream and REST have both failed, never on a tick.
- **Provenance is a chip shape**: hollow for simulated (dashed = backtest, solid = walk-forward, double = holdout), filled for executed (paper, shadow, demo, live). Readable in greyscale. Mixed data shows MIXED with counts; nothing is ever labelled from the first row.
- **Freshness is visible everywhere**: five view states (loading, error, success, **stale**, disconnected); a status bar with stream lag, worker heartbeat age, API health, latest `as_of`, operator and a UTC clock. A connected stream with a dead worker must look stale (the V1 3 am lesson).
- **Density** is a setting (24 / 32 / 40 px rows), not a compromise. Numerics right-aligned with tabular figures and a real minus sign.
- **Motion confirms, never decorates**: 80 to 240 ms, exits shorter than entries, no animated number counting, a 600 ms background flash on a live tick, all off under reduced motion.
- **Accessibility is a gate**: WCAG 2.2 AA on audited flows, 3:1 on every state indicator, focus rings on everything, no tooltip-only information, axe in CI at zero serious violations.

## 4. Information architecture

Nine screens, each answering one question with one primary action (detail and widgets per screen in report E §5.2):

| Screen | Question | Primary action |
|---|---|---|
| Command | Is anything wrong, and what needs me now? | Triage the top item of a **server-ranked** attention queue |
| Fleet & Positions | What is every bot doing and what is open? | Pause or close one bot or position |
| Strategy Lifecycle | What should be promoted, demoted or retired? | Promote or demote, with the caveat checklist |
| Research Lab | What have we tried and what does the evidence say? | Queue an experiment |
| Market Intelligence | What is the market doing, and what did the agents conclude? | Open a symbol in the chart workstation |
| Risk & Exposure | How close are we to any limit? | Arm or disarm the kill switch |
| Data Quality | Can we trust the inputs? | Open the affected instrument or dataset |
| System & Incidents | Is every process up, and what happened? | Acknowledge or annotate an incident |
| Journal | What did we trade, why, and what did we learn? | Add a note to a trade |

Entity routes (`/fleet/positions/[id]`, `/lifecycle/[hash]`, `/markets/[symbol]`, `/system/incidents/[id]`, `/journal/[tradeId]`) with an inspector sheet that opens the same view inline. Keyboard: `⌘K` palette, `g x` chords per screen, `j/k` row navigation, `⇧K` **opens** the kill-switch dialog (never arms). No single keystroke executes a mutation.

## 5. Real-time architecture

`GET /api/stream?topics=…` (SSE): snapshot on subscribe, then deltas keyed by entity id, tombstones, per-topic sequence numbers with a server ring buffer for `Last-Event-ID` replay, a 5 s heartbeat carrying `worker_heartbeat_age_s`, mode and kill-switch state. Client: one leader connection per browser (6-connection HTTP/1.1 limit), LRU dedupe, seq-gap → REST resync, backoff 1→15 s with jitter, "DISCONNECTED" with manual reconnect after five failures, no optimistic UI for kill switch or promote (the dialog stays busy until the stream echoes the change, so Tom's arm is visible to Joe within one heartbeat). Every number inside an event is still a `Figure` with provenance.

## 6. Chart strategy

Price: Lightweight Charts with multi-pane (price, backend indicator panes), `createSeriesMarkers` for signals and fills (shape encodes direction, fill encodes executed vs simulated), `createPriceLine` for entry/stop/target, a pane primitive for regime bands with a labelled ribbon, historical stop moves as a step series, the forming bar at 50% opacity. Everything comes from a new `/api/markets/overlays/{symbol}`; the frontend computes nothing. Analytics: uPlot with the provenance stroke grammar (dashed backtest, dotted walk-forward, solid executed), a mixed-provenance series is split or refused, drawdown as its own pane, every chart with a keyboard-reachable "view data" table.

## 7. Waves and estimates

| Wave | Scope | Days | Status |
|---|---|---:|---|
| 0 | Poll keeps last-known-good; caveat checklist; MIXED provenance; UTC labels; stable keys; 25 regression specs | 3 | **Done** (`c40c886`) |
| 1 | Tokens (OKLCH, dark/light/CVD), fonts, owned `ui/` primitives on Base UI, shell (mode frame, banner v2, rail, status bar, inspector, splits), CSP, size-limit and axe in CI, byte budgets replace the dependency ban | 14 | Next |
| 2 | TanStack Query + `ViewState` with freshness, OpenAPI-generated types, **login/session/role gating**, nuqs, SSE client with leader election and mock harness, Zustand live store | 13 | Backend SSE endpoint is a prerequisite (4 to 5 days) |
| 3 | `DataGrid`, `FigureValue`/`ProvenanceChip`/`Stat`/`StaleBadge`/`CaveatPopover` v2, `ConfirmDialog` v2 (focus trap, radio choices), palette, hotkeys, SVG chart refresh | 14 | |
| 4a | Command, Risk & Exposure, Fleet & Positions, System & Incidents | 20 | Needs `/api/command/attention` and an incidents read model |
| 4b | Strategy Lifecycle, Journal | 10 | |
| 4c | Chart workstation, Market Intelligence, Data Quality | 16 | Needs `/api/markets/overlays` |
| 4d | Research Lab | 10 | |
| 5 | Motion, copy, light theme, print styles | 6 | |
| 6 | Keyboard, screen reader, contrast/CVD audit, INP under a 50 events/s stream, React Compiler comparison | 6 | |
| | Contingency ~10%; **total ≈ 112 to 125**; backend ≈ 10 to 11 | | |

Playwright gates per wave (report E §8.1): freshness, promote-caveats, provenance-mixed, auth, stream (two browser contexts), mode-frame, grid, keyboard, axe, visual, chart-overlays, perf.

## 8. Backend asks (small, separate)

SSE endpoint with envelopes and ring buffer; server-computed consequences (done for promote and disarm); `/api/command/attention` ranked server-side; `/api/markets/overlays/{symbol}` with indicator series from `indicators/`; volume on `/api/markets/bars` if stored; incident read model and audited acknowledge; OpenAPI published for type generation.

## 9. Not verified yet

OKLCH values need a contrast check and CVD simulation before sign-off; SSE through the Next rewrite must be shown not to buffer (fallback: one reverse proxy in front of both, or EventSource direct to the API origin with CORS credentials); stream topics beyond mode, kill switch and health depend on the composition root that does not yet exist (ARCHITECTURE §12), so until then those topics are labelled `absent`, exactly as REST does today.

---

## 10. Revision 1.1 (2026-09-29): status and additions from the audit

**Shipped:** Wave 0 (`c40c886`), Wave 1 (`666f33e`), Wave 2 (`b5dcfdf`), Wave 3 plus the trust defects from report G §1.2 and the CI web job (this commit). Playwright 478 passed; contrast 268/268; every route within budget (shell 176 KB of 180).

**Decisions confirmed by Joe:** best available charts, own overlay layer. Lightweight Charts for price, uPlot and owned SVG/canvas for analytics (D-F5/D-F6 stand). Kill-switch friction is asymmetric: PAUSE reason-only, FLATTEN typed, disarm and promote full ceremony.

**Additions adopted from report G:**
- Chart workstation feature list C1–C18 (G §2.4): multi-timeframe sync with link groups, crosshair sync to analytics, signal/fill/level overlays with the provenance grammar, regime bands, session shading, calendar and headline markers, **replay ("time machine") where every step asks the backend for state as-of that bar**, a "why did this trade happen" inspector reading the allocation ledger (now recorded per trade in both engine and paper) and the gateway attempt row, server-side audited drawings (Fibonacci levels as drawings only), forming-bar and gap rendering, PNG snapshot for the Journal. Backend: `/api/markets/overlays` exists; add `as_of` replay and `/api/markets/drawings`.
- Eight view states (empty, absent, stale, disconnected, loading, error, forming, replay) with tokens (done) and a number-rendering spec (done).
- Agent Desk screen (Wave 4e) and a phone/tablet ops view for the kill switch over LAN (Wave 4f).
- Revised estimate: 150–170 frontend days and 22–28 backend days for the full scope; the next two waves are 4c-1 (chart core, 12 days) and 4a Risk & Exposure v2 (6 days).

**Verify on the Mac:** SSE through the Next rewrite does not buffer; `uk.fiboki.web` serves the production build; visual baselines are Linux-only.
