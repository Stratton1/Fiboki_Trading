# E — Frontend landscape and overhaul brief: Fiboki V2 operator workstation

**Author:** research brief (principal product design / frontend architecture)
**Date:** 2026-09-28 · **Web sources accessed:** 2026-09-28 unless stated · **Language:** UK English
**Scope:** `/home/claude/v2/apps/web` (current state), reference repos under `/home/claude/research/repos`, public landscape.

---

## 0. Executive summary

- **The current workstation gets honesty right and nearly everything else wrong.** `apps/web` is small: 28 routes, 10 components, about 4.5k lines of source and 1.1k lines of Playwright. It encodes V1's lessons well: every number is a `Figure` with provenance, "absence is not zero" is enforced by lint and by a grep test, there are four distinct async states, a sticky mode banner and a PAUSE≠FLATTEN kill switch. But it is a set of static, polled, client-rendered tables. It has no real-time layer, no price chart, no login screen, no keyboard model, no sortable or virtualised grids, and no design system beyond one 500-line stylesheet.
- **Three defects affect operator trust and should be fixed before any redesign:**
  1. **Polling blanks the view.** `useApi` resets to `loading` on every poll tick. The mode banner flips to a grey "MODE …" every 30 s and the health panel turns into a skeleton every 20 s (`lib/api.ts:127-130`, `:162-167`).
  2. **The promote dialog defeats the backend's caveat gate.** It hard-codes `acknowledge_caveats: true` (`app/trading/candidates/page.tsx:48`). The backend requires this flag specifically so the operator makes an explicit acknowledgement (`src/fiboki/api/routers/trading.py`, promote handler).
  3. **Several charts invent a provenance.** Examples: `?? "backtest"` in `app/trading/execution/page.tsx:82`, `?? "paper"` in `exposure/page.tsx:52`, and a hard-coded `provenance="backtest"` in `market-pulse/page.tsx:27` and `markets/correlations/page.tsx:26`.
- **Recommended stack:**
  - Stay on Next.js 16.3 / React 19 / TypeScript.
  - Styling: Tailwind CSS 4.3 with OKLCH design tokens. Components: an owned layer (shadcn pattern) built on **Base UI 1.8**, not a component library dependency.
  - Data: **TanStack Query 5** plus one multiplexed **SSE** stream feeding a small **Zustand** store. Grids: **TanStack Table 9 + TanStack Virtual 3**. Command palette: **cmdk**.
  - Charts: **TradingView Lightweight Charts 5.2** for price charts. For analytics, **uPlot** plus the existing hand-rolled SVG. **ECharts 6** (tree-shaken, lazy-loaded) only where a heatmap or surface genuinely needs it.
  - **Do not adopt Plotly**: it measured about 1.4 MB gzipped, which is the V1 mistake.
- **Two decisions for Joe that override the current project instructions:**
  1. **Lightweight Charts instead of KLineChart.** KLineChart's built-in indicator engine computes indicators in the browser, which breaks the "indicators centralised in backend `indicators/`" rule. Lightweight Charts has no indicator engine, so it can only draw backend series.
  2. **uPlot/SVG instead of Plotly** for analytics.
- **Execution mode and provenance are different things and need different visual languages.** Mode (where orders go) should change the whole shell: frame, banner, favicon and title prefix. Provenance (where a number came from) should be a chip shape: hollow means simulated, filled means executed. Green and red should be reserved for P&L direction only; today "paper" is the same green as profit and "live" is the same red as loss.
- **Size:** about 112–125 engineer-days of frontend work in seven waves (a 3-day "fix-now" wave 0 is worth doing on its own), plus about 10–11 days of backend work for an SSE endpoint and a small number of new read models. The backend's FastAPI pin (0.115.6) predates native SSE (0.135.0), so `sse-starlette` or an upgrade is needed.

---

## 1. Current-state audit of `apps/web`

### 1.1 Inventory (verified from the tree)

| Item | Finding | Evidence |
|---|---|---|
| Framework | Next.js 16.3.5, React 19.0.0, TypeScript 5.7.2 (strict, `noUncheckedIndexedAccess`) | `package.json`, `tsconfig.json` |
| Runtime dependencies | **Exactly three**: `next`, `react`, `react-dom`. A test pins this. | `tests/e2e/source-rules.spec.ts:106-113` |
| Routes | 28 pages + root layout. No dynamic routes (`[id]`), no `loading.tsx` / `error.tsx`, no route handlers, no login route. | `find app -name page.tsx` |
| Sections in nav | 6 sections, 28 links: Command, Research, Markets, Trading, Intelligence, System. The brief lists "alerts" and "market-pulse" separately; they sit under *Command*. | `components/Nav.tsx:12-81` |
| Components | 10 files, 1,412 LOC: `AsyncBoundary`, `ConfirmDialog`, `FigureValue`, `KillSwitch`, `ListPage`, `ModeBanner`, `Nav`, `ProvenanceChip`, `charts`, `primitives` | `components/` |
| Library code | `lib/api.ts` (171), `lib/format.ts` (98), `lib/types.ts` (453, hand-mirrored pydantic models) | `lib/` |
| Styles | One global stylesheet, 502 lines, hex tokens on `:root`, dark by default, light via `prefers-color-scheme` only, system fonts | `app/globals.css` |
| Source LOC | about 4,520 (pages about 1,885; components 1,412; lib 722; CSS 502) | `wc -l` |
| Tests | 8 Playwright specs, 56 tests, run in two projects (desktop 1280 and "mobile" 390×844 Chrome). API is mocked by route interception. | `tests/e2e/*.spec.ts`, `playwright.config.ts` |
| Data fetching | Home-grown `useApi` hook with a discriminated union. **Not SWR** (the brief says "SWR-style"). No cache, no dedupe, polling via a nonce. | `lib/api.ts:108-171` |
| Charts | Hand-rolled inline SVG: line, horizontal bar, heatmap, histogram. **Neither KLineChart nor Plotly is present.** A test bans `plotly.js`, `echarts`, `d3` and `chart.js`. | `components/charts.tsx`, `source-rules.spec.ts:103-113` |
| API endpoints used | 29 distinct paths. Backend endpoints **not used**: `/api/auth/login|logout|me`, `/api/markets/bars/{symbol}`, `/api/trading/lifecycle/*` (3), `/api/system/kill-switch/history`, `/api/system/queues`, `/api/trading/execution-telemetry`, `/api/research/strategies/{id}`, `POST /api/research/experiments`, `POST /api/intelligence/research-memory/notes` | `grep` in `apps/web` vs `src/fiboki/api/routers/*.py` |
| Bundle | All static JS in `.next/static`: **290 KB gzipped** (measured). The README says "~212 KB". The difference is probably per-route vs total; either way it is small. | `.next/static`, `README.md` |
| Security headers | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`. No CSP. | `next.config.ts` |

### 1.2 Quality score per area (1 = poor, 5 = excellent)

| Area | Score | Rationale |
|---|---:|---|
| Data honesty / provenance contract | **4.5** | `Figure`/`FigureValue`/`ProvenanceChip` have no unlabelled path. The null-state contract is enforced twice (ESLint `no-restricted-syntax` and a source grep). Points lost for the chart-level provenance fallbacks (W4 below). |
| Safety controls (mode banner, kill switch, confirm) | **3.5** | Good semantics: no default choice, mandatory reason, typed confirm phrase, server-computed consequences for arm. Points lost for the hard-coded caveat acknowledgement, hard-coded promote/disarm consequences, and banner flicker. |
| Data layer / freshness | **1.5** | No cache, no dedupe, no stale-while-revalidate, no streaming, no staleness indicator. The poll resets to loading. `as_of` is carried on every `Figure` and `SourceNote` but never rendered. |
| Information architecture | **2.5** | "One job per section" is a sound principle. But there are no drill-downs, no entity pages (strategy, instrument, position, incident), no cross-links and no URL state. |
| Visual design / brand | **1.5** | Competent default dark theme, but no type scale, no elevation model, no iconography, no density modes, system fonts, hex colours, and colour meanings that collide. |
| Tables | **1.5** | Plain `<table>`: no sort, filter, column control, virtualisation, sticky first column, or right-aligned numerics. A 200-row cap on trades. |
| Charts | **2.0** | Honest empty states and provenance in the legend. But fixed 720×240 SVG, no x-axis, no crosshair or tooltips (only `<title>`), no zoom, **no price chart** at all. |
| Keyboard / power use | **0.5** | No shortcuts, no command palette, no focus management beyond `focus()` on dialog open. |
| Accessibility | **2.5** | Semantic landmarks, `role="alert"`/`status`, `aria-current`, reduced-motion on the skeleton. But information is only available through `title` tooltips, there is no focus trap, no designed focus ring, no `aria-live` for data changes, and state is shown by colour alone in the heatmap and P&L. |
| Performance | **4.0** | Tiny dependency surface. Everything is `"use client"`, which is acceptable for a cookie-authenticated client of an external API. |
| Auth / session UX | **0.5** | No login page. A 401 renders as a generic error panel. The principal and role are never shown and buttons are not role-gated (`PrincipalView` is defined in `lib/types.ts:446-453` and unused). |
| Test discipline | **3.5** | Good behavioural specs for the safety rules. No visual regression, no axe checks, no keyboard specs, and no test that the poll does not flicker. |

### 1.3 Top 10 concrete weaknesses

| # | Weakness | Why it matters to an operator | Evidence |
|---|---|---|---|
| **W1** | **Poll tick resets state to `loading`.** Each `refreshMs` tick changes `nonce`, which runs the "reset during render" branch. The mode banner turns grey ("MODE …", `data-mode="loading"`) every 30 s. Overview health turns into a skeleton every 20 s and Alerts every 15 s. Last-known-good data is thrown away. | The one control that must never waver, the mode banner, blinks out of LIVE/DEMO twice a minute. An operator glancing at it can catch it in the "unknown" state. | `lib/api.ts:123-130`, `:162-167`; `components/ModeBanner.tsx:19-37`; `app/page.tsx:35`; `app/alerts/page.tsx:17` |
| **W2** | **No shared cache and no push.** `/api/system/execution-mode` is fetched independently by the banner, by every `KillSwitchPanel` and by Candidates. The kill-switch panel never refreshes. | If Tom arms the switch, Joe's panel still says DISARMED until he reloads. The banner catches up after up to 30 s. With two admins this is a real coordination hazard. | `components/KillSwitch.tsx:25-26`; `app/trading/candidates/page.tsx:202-203` |
| **W3** | **Caveat acknowledgement is automated away.** Promote always sends `acknowledge_caveats: true`. The backend returns `caveats_not_acknowledged` precisely to force a human act. The promote and disarm consequence lists are hard-coded in page code, although `ConfirmDialog` documents consequences as "Server-computed … Never written into this component". | The UI silently signs a safety attestation on the operator's behalf. The consequence copy can drift from the backend's real behaviour, which is the V1 failure mode. | `app/trading/candidates/page.tsx:40-50`, `:354-380`; `components/KillSwitch.tsx:155-166`; `components/ConfirmDialog.tsx:9`; `src/fiboki/api/routers/trading.py` promote (`if not body.acknowledge_caveats`) |
| **W4** | **Chart-level provenance is invented.** A distribution over a *mixed* trade list is labelled with the first row's provenance, falling back to `"backtest"`. Exposure falls back to `"paper"`. Spread and correlation charts are hard-coded `"backtest"`, although instrument specs and correlations are not backtest results. | This breaks the app's own first rule: the chip claims an origin the data did not assert. | `app/trading/execution/page.tsx:80-84`; `app/trading/exposure/page.tsx:52`; `app/market-pulse/page.tsx:27`; `app/markets/correlations/page.tsx:26` |
| **W5** | **Colour meanings collide.** `--prov-paper` `#3fbf7f` is the same colour as `--pos`, and `--prov-broker_live` `#ff4d4d` is the same as `--danger` and close to `--neg`. The correlation heatmap and histogram use red against green only. Four research provenances are near-identical blues. | A green "PAPER" chip next to a green profit reads as "good". Red/green-only encoding fails for roughly 8% of men with colour-vision deficiency (see §2.4). | `app/globals.css:399-412`; `components/charts.tsx:17-26`, `:264-269`, `:371` |
| **W6** | **No authentication or identity UX.** There is no `/login` and `/api/auth/me` is never called, so the shell cannot say *who* is operating. Admin-only actions are not disabled for viewers (`can_promote` and `can_arm_kill_switch` are unused). | With two named admins, "who armed it" and "am I the one who can disarm it" should be visible in the shell. | `lib/types.ts:446-453`; `src/fiboki/api/routers/auth.py:86,151,165` |
| **W7** | **Tables are not workstation-grade.** Numbers are left-aligned (all `th`/`td` are `text-align:left`, with no numeric alignment), so decimals do not line up despite tabular numerals. There is no sort, filter, column pinning or virtualisation, and trades are capped at 200 rows. There are no row drill-downs and no entity routes. | Scanning 80+ trade samples or 67 instruments by eye is slow and error-prone. There is nowhere to "open" a strategy or position. | `app/globals.css:674-694`; `app/trading/execution/page.tsx:551`; `app/research/page.tsx:27` (every strategy links to the list page) |
| **W8** | **No price chart and no trade context.** `/api/markets/bars/{symbol}` exists and returns OHLC with a `dataset_version_id`, but nothing renders it. Signals, entries, stops, targets and regimes cannot be seen against price. | An operator cannot visually audit a paper trade, which is the most basic trust check. | `src/fiboki/api/routers/markets.py:137-191`; `components/charts.tsx` (no OHLC) |
| **W9** | **Keyboard and assistive-technology gaps.** `ConfirmDialog` has no focus trap, does not return focus, and leaves the background tabbable. Escape can cancel while `busy`. The choice buttons have no radio semantics. Caveats, "est" and provenance help are only in `title` attributes, which do not work with touch or keyboard. There is no visible focus design and no `aria-live` for data changes. | A11y matters for dense tools too. Operators work keyboard-first, and tooltip-only caveats are invisible on an iPad. | `components/ConfirmDialog.tsx:76-101`, `:111-125`; `components/FigureValue.tsx:42-58`; `components/ProvenanceChip.tsx:19-31` |
| **W10** | **Time and state are not addressable.** Timestamps are formatted in UTC but not labelled as UTC. `Figure.as_of` and `SourceNote.as_of` are never shown. Filters live in `useState`, so the provenance filter is lost on reload and cannot be shared as a link. `Math.random()` is used as a React key for agent runs. | "When was this true?" is the second question after "where did it come from?", and the UI cannot answer it. | `lib/format.ts:74-83`; `app/trading/execution/page.tsx:20`; `app/intelligence/runs/page.tsx:22` |

### 1.4 What to keep: the V2 contract to carry forward unchanged

1. **`Figure` → `FigureValue` → `ProvenanceChip` with no unlabelled path** (`components/FigureValue.tsx`, `components/ProvenanceChip.tsx`). Restyle it; do not weaken it.
2. **The "absence is not zero" double gate**: the ESLint rule in `eslint.config.mjs` and the grep test `source-rules.spec.ts:43-58`.
3. **Four async states as a discriminated union** (`AsyncBoundary`). Extend it with a fifth, *stale* (last-known-good shown with an age), rather than dropping it.
4. **Mode banner rendered once in the root layout** (`app/layout.tsx`). "Mode unknown" is an explicit state.
5. **ConfirmDialog semantics**: no default choice, mandatory reason (≥8 characters, audited), typed confirmation phrase for the worst acts, and the current mode stated in the dialog.
6. **Kill switch: PAUSE ≠ FLATTEN, armable in every mode**, with server-computed consequences.
7. **Source rules**: no native `confirm()`, no hard-coded mode claims, no hard-coded realism caveats. Keep them, but **replace the "no chart library" name-ban with a byte budget per route** (see §3.7) so that it keeps protecting against the V1 Plotly mistake without blocking a 58 KB price chart.
8. **Playwright by API interception** (`tests/e2e/fixtures.ts`). This proves the UI holds no trading logic.
9. **CSRF double-submit and `credentials: "include"`** in `apiFetch`.

---

## 2. Landscape survey

### 2.1 Reference repositories (local)

**FinceptTerminal** (`/home/claude/research/repos/FinceptTerminal`): native C++20/Qt6 desktop terminal, AGPL-3.0 (`LICENSE`). **Borrow ideas, not code.**

| Idea worth borrowing | Where | How it maps to Fiboki |
|---|---|---|
| **Single token struct for all colours**. "No hardcoded hex anywhere else". Tokens for bg base/surface/raised/hover, three border strengths, four text levels, accent, positive/negative plus their `_dim` and `_bg` tints, `row_alt`, and a 6-colour chart palette. | `fincept-qt/src/ui/theme/ThemeTokens.h:1-68`; preset `ThemeManager.cpp:11-43` ("Obsidian": `#080808` base, amber `#d97706` accent, Consolas) | The layer structure is right. Fiboki should express it in OKLCH and add a *provenance* and *mode* token family, which Fincept lacks. |
| **DataHub pub/sub with topic policies**: `ttl_ms`, `min_interval_ms`, `refresh_timeout_ms`, `push_only`, `coalesce_within_ms` (latest value wins inside a window), `pause_when_inactive`, `drop_on_idle`. **Errors do not overwrite last-known-good.** `TopicStats` exposes `last_publish_ms`, `total_errors` and `in_flight`. | `src/datahub/TopicPolicy.h:1-60`, `src/datahub/DataHub.h:20-120` | This is almost exactly the right client model for the SSE layer in §6: per-topic cadence, coalescing, last-known-good kept on error, and inspectable freshness. |
| **LiveTableModel**: rows keyed and updated in place, `dataChanged` only for cells that changed, so selection, scroll and sort survive every tick. The header comment describes the anti-pattern of rebuilding the table each tick. | `src/ui/tables/LiveTableModel.h:1-50` | The same principle applies to React: stable row ids, cell-level memoisation, no remount on update, and fine-grained subscriptions (TanStack Table v9's store model supports this). |
| **Ctrl+K command palette**, centred on the active frame, Esc to dismiss. **Command bar** plus an **F-key bar**. | `src/ui/command/CommandPalette.h`, `src/ui/navigation/CommandBar*.cpp`, `FKeyBar.h` | Use cmdk for the palette. F-keys are not reliable in browsers; use `g`-chords instead (§5.3). |
| **Saved workspaces and persona templates**, a layout catalogue, a status bar outside the dock area. | `src/core/layout/LayoutTemplates.h`, `LayoutCatalog.*`, `ui/navigation/DockStatusBar.h` | A persistent **status bar** (connection, stream lag, worker heartbeat, operator, clock in UTC) is a strong pattern for Fiboki. A full docking system is not needed for two operators (§3.5). |
| **KLineChart in a web view**, with a `set_position_line(price, label, colour)` API for entry lines, and an overlay manager for horizontal levels that expands the y-range to include them. | `src/ui/charts/KLineChartWidget.h:17-40`, `ChartOverlayManager.h` | Confirms the overlay needs (entry, stop, target lines that must remain in view). Fiboki should own an equivalent `ChartOverlayModel` driven by backend data. |

**Vibe-Trading** (`/home/claude/research/repos/Vibe-Trading`), MIT:

- `desktop/electron/`: an Electron shell that owns a loopback FastAPI child process. It uses a per-process 256-bit secret, waits for `/health` before loading the UI, and has a watchdog (`desktop/electron/README.md`, `src/backend-manager.ts`, `src/backend-watchdog.ts`). **Relevant to local-first Fiboki** as a possible later packaging option. It is not needed now.
- `frontend/src/hooks/useSSE.ts`: EventSource with exponential backoff (1 s → 30 s), **LRU de-duplication of event ids**, **Last-Event-ID resume**, a generation counter against stale reconnects, and a whitelist of known event types. **This is the best local template for §6.**
- `frontend/src/components/layout/ConnectionBanner.tsx`: "reconnecting (attempt n)" state, then a terminal "disconnected + reload" state after 5 attempts, with `role="status"`.
- `frontend/src/lib/pnl-colors.ts` and `lib/chart-theme.ts`: separates **P&L colours** (always profit/loss) from **candle direction colours**, which flip under the Chinese red-up convention. Chart colours are read from CSS variables so charts follow the theme. This is a useful discipline to copy.
- `frontend/src/lib/echarts.ts`: tree-shaken ECharts registration and `echarts.connect()` for synchronised crosshairs. `hooks/useChartLifecycle.ts` handles init, ResizeObserver with rAF, and dispose.
- Stack: React 19, Vite 8, Tailwind 3, Zustand 5, ECharts 6, sonner, lucide, Inter and JetBrains Mono via `@fontsource` (`frontend/package.json`).

**freqtrade** (`/home/claude/research/repos/freqtrade`), GPL-3.0:

- The web UI is **FreqUI, a separate repository** (`github.com/freqtrade/frequi`). The core repo installs its release with `freqtrade install-ui` (`freqtrade/commands/deploy_ui.py:60`) and serves it from `rpc/api_server/web_ui.py`.
- FreqUI's current dependencies (read from its `package.json` on 2026-09-28): Vue 3.5, Nuxt UI 4 / reka-ui, Pinia 4, **ECharts 6 via vue-echarts**, **TanStack Vue Table 8**, `@noction/vue-draggable-grid` (drag and resize dashboard). Licence: not confirmed via the API (see §9).
- Real-time uses **WebSockets with typed message schemas** and subscriptions (`freqtrade/rpc/api_server/ws/channel.py`, `ws_schemas.py`; the `RPCMessageType` enum includes `entry`, `entry_fill`, `exit_fill`, `protection_trigger`, `new_candle`, `analyzed_df`). Per-channel send throttling and a subscription list are good patterns. **For Fiboki, SSE is preferable** because all client→server actions are already audited REST POSTs with CSRF (§6).

### 2.2 Professional terminals and platforms

| Product | What is worth borrowing | Source |
|---|---|---|
| **Bloomberg Terminal** | *Launchpad* combines many functions in one personal view. Chromium-based rendering let HCI specialists define workflows. The 2026 **ASKB** agentic layer (beta, about one third of 375k users as of April 2026) returns answers "with transparent attribution" and **exposes the underlying BQL query** so a user can check and extend it. This is a direct parallel to Fiboki's provenance rule: every agent claim should link to the run and dataset behind it. No ground-up visual redesign of the Terminal in 2024–26 was found; the change is the AI layer. | [Innovating a modern icon](https://www.bloomberg.com/company/stories/innovating-a-modern-icon-how-bloomberg-keeps-the-terminal-cutting-edge/); [Relaunching Launchpad (2017)](https://www.bloomberg.com/ux/2017/11/10/relaunching-launchpad-disguising-ux-revolution-within-evolution/); [Markets Media, 23 Feb 2026](https://www.marketsmedia.com/bloomberg-introduces-agentic-ai-to-the-terminal/); [Slashdot, 28 Apr 2026](https://news.slashdot.org/story/26/04/28/1832202/the-bloomberg-terminal-is-getting-an-ai-makeover). Bloomberg's colour-accessibility article ([link](https://www.bloomberg.com/company/stories/designing-the-terminal-for-color-accessibility/)) returned 403 / robots-blocked and was **not read**. |
| **TradingView** | Multi-chart layouts that sync **symbol, interval, crosshair, time/date range, and drawings** (drawings only for the same symbol). **Selective sync groups** via markers on individual charts. A dense hotkey set. | [How to sync the charts of my layout](https://www.tradingview.com/support/solutions/43000629992-how-to-sync-the-charts-of-my-layout/); [Hotkeys](https://www.tradingview.com/support/shortcuts/) |
| **Koyfin** | Function codes (e.g. `MYD` for My Dashboards). Resizable drag-and-drop widgets. **Seven colour "groups" link widgets** so selecting a security in one updates the others. Saved column templates and views. | [My Dashboards](https://www.koyfin.com/help/mydashboards-myd/); [Dashboard groups](https://www.koyfin.com/help/my-dashboards-groups/); [My Views](https://www.koyfin.com/help/my-views/) |
| **OpenBB Workspace** | A widget grid that renders data from your own backend ("no proprietary connectors"). Copilot runs against your own data. Self-hosted or VPC deployment with entitlement and audit. Fiboki's architecture is similar (its own API, its own agents, its own audit), so OpenBB is the closest product analogue. | [OpenBB Workspace](https://openbb.co/products/workspace/); [Copilot docs](https://docs.openbb.co/workspace/openbb-copilot); [Generative UI](https://docs.openbb.co/workspace/analysts/ai-features/generative-ui) |
| **Sierra Chart** | "Chartbooks" are workspace files holding charts, DOM, Time & Sales and spreadsheets. Many can be loaded, one visible at a time, switched with F7/F8 or tabs. Plain, dense and fast. | [Chartbooks](https://www.sierrachart.com/index.php?page=doc%2FChartbooks.html) |
| **MotiveWave** | A workspace, charting and analysis platform often compared with Sierra Chart. Only secondary sources were reviewed, so no specific pattern is claimed. | [Modest Money comparison](https://www.modestmoney.com/motivewave-vs-sierra-chart/) (secondary) |
| **IBKR: TWS vs IBKR Desktop** | TWS offers deep customisation and complex/algorithmic orders and suits pros, but its UI is described as "outdated". IBKR Desktop has a "modern user interface" with fewer layout options, and adds Rapid Order Entry, chart orders, **MultiSort screeners** and multi-chart mode. **The lesson: a modern UI that removes configurability loses professionals.** Fiboki should be opinionated and fixed-layout by default, with a small number of well-chosen view options (density, columns, saved views) rather than free-form docking. | [BrokerChooser: TWS vs IBKR Desktop](https://brokerchooser.com/broker-reviews/interactive-brokers-review/tws-vs-ibkr-desktop); [IBKR Desktop](https://www.interactivebrokers.com/en/trading/ibkr-desktop.php) |
| **Trading Technologies (TT)** | Workspaces made of a parent window and child windows across monitors. Each window holds widgets. **Connection status is always in the title bar.** Saved layouts. | [Workspaces in TT Desktop](https://library.tradingtechnologies.com/trade/ttd-workspaces-in-tt-desktop.html); [MD Trader overview](https://library.tradingtechnologies.com/trade/mdt-overview.html) |
| **Portara (CQG)** | **Correction to the brief:** Portara is primarily CQG's **historical futures data and research** product, not an operator UI. It is relevant to the data-quality screen (continuous-contract and back-adjustment metadata), not to UI patterns. | [CQG: Portara](https://www.cqg.com/products/historical-data/portara); [portaracqg.com](https://portaracqg.com/) |

### 2.3 Premium dark design language (Linear, Vercel, Raycast)

- **Linear** moved its theme system from HSL to **LCH** because it is perceptually uniform. Themes are generated from **three inputs: base colour, accent colour, contrast** (contrast 30–100), which gives high-contrast variants automatically. Inter Display is used for headings and Inter for body text. The stated goals were "reduce visual noise, maintain visual alignment, and increase the hierarchy and density of navigation" ([How we redesigned the Linear UI](https://linear.app/now/how-we-redesigned-the-linear-ui); a further refresh is listed on [Linear changelog 2026-03-12](https://linear.app/changelog/2026-03-12-ui-refresh), not read in detail). **Adopt the "few inputs → generated scale" approach in OKLCH.**
- **Vercel/Geist**: Geist Sans and Geist Mono, OFL, nine weights, Swiss-influenced, first-class in `next/font` ([vercel.com/font](https://vercel.com/font)).
- **Raycast**: the command palette is the primary navigation, with a monochrome surface and one accent. Only third-party write-ups of its tokens were found, so it is treated as inspiration only.
- **OKLCH** is supported natively in all modern browsers. It has predictable lightness across hues (so contrast can be set by L), P3 gamut and formulaic palette generation ([Evil Martians](https://evilmartians.com/chronicles/oklch-in-css-why-quit-rgb-hsl)). shadcn/ui's Tailwind v4 theme already uses OKLCH variables ([shadcn Tailwind v4](https://ui.shadcn.com/docs/tailwind-v4)).

### 2.4 Dense-data design systems, typography, colour, motion, accessibility

- **IBM Carbon data table** row heights: **xs 24, sm 32, md 40, lg 48, xl 64 px** ([Carbon data table style](https://carbondesignsystem.com/components/data-table/style/)). Use xs/sm/md as Fiboki's three density modes.
- **Adobe Spectrum** tables offer `density` compact/spacious, sizes s–xl, a "quiet" variant (transparent, no side borders) and an "emphasized" variant ([Spectrum Web Components table](https://opensource.adobe.com/spectrum-web-components/components/table/)). The quiet variant is the right default look for workstation grids.
- **Atlassian**: token-first foundations with 8px-based spacing ([Spacing](https://atlassian.design/foundations/spacing), [Tokens](https://atlassian.design/foundations/tokens)).
- **Typography:** **Inter** supports `tnum` (tabular figures), `zero` (slashed zero), `case`, `ss01` and `frac`, under OFL ([rsms.me/inter](https://rsms.me/inter/)). JetBrains Mono, IBM Plex Mono and Geist Mono are all OFL and available as `@fontsource` packages (npm, 2026-09-28).
  - Recommendation: Inter Variable with `tnum` + `zero` for all figures, and a mono only for identifiers such as hashes, order ids, correlation ids and codes, where 0/O and 1/l/I must be unambiguous.
- **Colour-vision deficiency and contrast:** WCAG 2.2 SC 1.4.11 requires **3:1** for graphical objects needed to understand content, including chart lines, state indicators and focus indicators ([W3C Understanding 1.4.11](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)). Colour must never be the only carrier of meaning (SC 1.4.1). CVD-safe categorical palettes (Okabe–Ito, Wong, IBM) are collected at [Coloring for Colorblindness](https://davidmathlogic.com/colorblind/) (the page loaded only as metadata, so the palette values used here are from general knowledge; see §9).
- **Motion:** NN/g recommends about 100 ms for simple feedback, 200–300 ms for moderate changes such as modals, 100–500 ms overall, and says animations over 500 ms "feel like a real drag". Ease-out for entering elements; exits slightly faster than entries ([NN/g animation duration](https://www.nngroup.com/articles/animation-duration/)).
- **Performance targets:** LCP ≤ 2.5 s, **INP ≤ 200 ms**, CLS ≤ 0.1, measured at the 75th percentile ([web.dev Web Vitals](https://web.dev/articles/vitals)).

### 2.5 Libraries: versions, licences, measured size, maintenance (npm registry, 2026-09-28)

Sizes marked **(measured)** come from esbuild `--bundle --minify` of the named imports, gzipped at level 9, with React external. Other sizes are bundlephobia whole-package figures.

| Library | Latest | Last publish | Licence | Size (gzip) | Notes |
|---|---|---|---|---|---|
| lightweight-charts | 5.2.1 | 2026-08-12 | Apache-2.0 (attribution: `attributionLogo` default on) | **58.0 KB measured** (createChart + Candlestick + Line + Histogram + createSeriesMarkers). TradingView claims about 35 KB base. | v5 has multi-pane, plugins, pane primitives and a series-markers plugin. 5.1 adds data conflation; 5.2 adds series hit-testing and an **accessibility plugin** ([release notes](https://tradingview.github.io/lightweight-charts/docs/release-notes)). |
| klinecharts | 10.0.3 | 2026-08-27 | Apache-2.0 | **61.7 KB measured** | **10.0.0 stable only since 2026-07-10**, after about 18 months of alpha and beta (registry timeline). Built-in indicators and drawing overlays, multi-y-axis, custom hotkeys ([releases](https://github.com/klinecharts/KLineChart/releases), [v9→v10](https://klinecharts.com/en-US/guide/v9-to-v10)). |
| uplot | 1.6.32 | 2025-03-14 | MIT | **23.0 KB measured** | 166,650 points in 25 ms cold. 3,600-point stream at 60 fps using about 10% CPU. Cursor sync. **Deliberately excludes** animations, stacking and aggregation ([README](https://github.com/leeoniya/uPlot/blob/master/README.md)). Stable but slow-moving. |
| echarts | 6.1.0 | 2026-05-19 | Apache-2.0 | **209 KB measured** (core + line/bar/heatmap + grid/tooltip/visualMap/dataZoom + canvas); 368 KB full | Very capable. Heavy even when tree-shaken. |
| plotly.js-dist-min | 4.1.1 | 2026-09-14 | MIT | **about 1,399 KB** | Rejected (V1 lesson, `README.md` "Charts"). |
| highcharts (+Stock) | 13.1.1 | 2026-09-20 | **Commercial** (`highcharts.com/license`) | about 102 KB core | Excellent stock charts, but needs a paid licence for commercial use. Not justified for two operators. |
| @tanstack/react-query | 5.104.0 | 2026-09-26 | MIT | about 13.8 KB | `experimental_streamedQuery` exists ([docs](https://tanstack.com/query/v5/docs/reference/streamedQuery)) but is still experimental. |
| swr | 2.5.1 | 2026-08-12 | MIT | about 5.7 KB | Fine for simple use, but weaker mutation and invalidation semantics. |
| zustand | 5.0.15 | — | MIT | <1 KB | For high-frequency live slices. |
| jotai | 3.0.0 | — | MIT | — | Not needed if Zustand is used. |
| @tanstack/react-table | 9.2.4 | v9 announced 2026-08-04 | MIT | about 31.8 KB (tree-shakable in v9) | v9 uses TanStack Store fine-grained state and "up to 86% less retained heap" than v8 ([announcement](https://tanstack.com/blog/announcing-tanstack-table-v9)). |
| @tanstack/react-virtual | 3.14.13 | 2026-09-14 | MIT | **7.8 KB measured** | |
| cmdk | 1.1.1 | 2025-03-14 | MIT | **17.4 KB measured** (includes Radix Dialog) | Unstyled, auto-filtering ([repo](https://github.com/pacocoursey/cmdk)). Slow release cadence but stable. |
| @base-ui/react | 1.8.0 | 2026-09-04 | MIT | per-component | 1.0 released 2026-02-06 by MUI, built by the Radix and Floating UI authors ([InfoQ](https://www.infoq.com/news/2026/02/baseui-v1-accessible/)). **shadcn/ui made Base UI the default in July 2026**, with Radix still supported ([changelog](https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default)). |
| radix-ui | 1.6.7 | — | MIT | per-component | Supported, but momentum has moved to Base UI ([OpenReplay](https://blog.openreplay.com/shadcn-ui-radix-base-ui-switch/)). |
| react-aria-components | 1.21.1 | — | Apache-2.0 | larger | The strongest accessibility story for grids (virtualised table with keyboard navigation, [Virtualizer](https://react-aria.adobe.com/Virtualizer)). More verbose. |
| @ark-ui/react | 5.39.2 | — | MIT | — | Zag.js state machines. A good alternative. |
| @mantine/core | 9.6.3 | — | MIT | large | Batteries included, but has a recognisable "Mantine look". A poor fit for a bespoke premium identity. |
| tailwindcss | 4.3.3 | — | MIT | build-time | CSS-first `@theme` tokens, OKLCH-native palette. |
| motion (formerly Framer Motion) | 13.4.4 | — | MIT | about 47.7 KB full; much less with `LazyMotion` + `m` | Import from `motion/react`. Web Animations API with JS fallback. Honours reduced motion ([motion.dev](https://motion.dev/docs/react)). |
| react-resizable-panels | 4.14.1 | 2026-09-27 | MIT | about 19 KB | Split panes. |
| dockview | 8.3.1 | 2026-09-10 | MIT | about 85 KB | Full docking. Deferred (§3.5). |
| sonner | 2.0.8 | — | MIT | about 9.4 KB | Toasts. |
| nuqs | 2.10.1 | — | MIT | small | Type-safe URL query state. |
| @microsoft/fetch-event-source | 2.0.1 | **2021-04-25** | MIT | small | Unmaintained. Avoid; native `EventSource` supports `withCredentials` ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/EventSource/EventSource)). |
| @axe-core/playwright | 4.13.0 | — | MPL-2.0 | dev only | a11y assertions in Playwright. |
| size-limit | 14.1.0 | 2026-09-27 | MIT | dev only | Per-route byte budgets in CI. |

**Server side** (PyPI, 2026-09-28):

- FastAPI latest is 0.141.1. **Native SSE arrived in 0.135.0 (2026-03-01)** as `fastapi.sse.EventSourceResponse` / `ServerSentEvent`. It sends automatic 15 s keep-alive comments, `Cache-Control: no-cache` and `X-Accel-Buffering: no`, and supports `Last-Event-ID` via a header parameter ([FastAPI SSE tutorial](https://fastapi.tiangolo.com/tutorial/server-sent-events/)).
- **Fiboki pins `fastapi==0.115.6` and `uvicorn==0.34.0`** (`pyproject.toml:22-23`). The options are to upgrade, or to use `sse-starlette` 3.5.0 at the current pin.
- **MDN warns that without HTTP/2 the browser allows only 6 concurrent SSE connections per browser and domain, across all tabs** ([MDN EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)). Uvicorn serves HTTP/1.1, which shapes the design in §6.

---

## 3. Recommended stack for the overhaul

Stay on **Next.js 16.3.x, React 19.2, TypeScript 5.7+**. Next 16 gives stable Turbopack, stable React Compiler support (opt-in), React 19.2 `<Activity>` and View Transitions, and `proxy.ts` ([Next.js 16](https://nextjs.org/blog/next-16)).

### 3.1 Summary table

| Concern | Choice (version) | Licence | Why | Rejected |
|---|---|---|---|---|
| Styling and tokens | **Tailwind CSS 4.3** with CSS-first `@theme` mapped to OKLCH custom properties (§4) | MIT | Zero runtime. Tokens become utilities. Good support in AI-assisted tooling (Cursor, Claude). | CSS-in-JS (runtime cost), plain global CSS (does not scale to a design system) |
| Primitives | **Base UI 1.8** (`@base-ui/react`) inside an **owned** `components/ui/*` layer (shadcn copy-in pattern: no upstream visual dependency) | MIT | Unstyled, accessible (focus trap, dismiss, portal, `inert`), actively maintained by MUI, now shadcn's default | Mantine (opinionated look); Radix (still fine; use it if the team prefers it); React Aria (use its patterns as the a11y reference for the grid, below) |
| Icons | lucide-react 1.x | ISC | Consistent 1.5px stroke, tree-shakable | — |
| Fonts | **Inter Variable** (UI and figures, `tnum`/`zero`), **JetBrains Mono Variable** (ids, hashes, code), self-hosted via `next/font/local` or `@fontsource-variable/*` | OFL-1.1 | Tabular figures with good legibility at 11–13 px; mono with distinct 0/O, 1/l | Geist is a valid alternative. Inter's `tnum`/`zero` support is the deciding factor. |
| Server state | **TanStack Query 5.10x** | MIT | Stale-while-revalidate (fixes W1), dedupe (fixes W2), `placeholderData: keepPreviousData`, mutation lifecycle, `setQueryData` for stream deltas, devtools | SWR 2.5 (fewer invalidation and mutation primitives); keeping `useApi` (reinventing a cache) |
| Live state | **Zustand 5** store fed by one SSE connection, rAF-batched | MIT | For high-frequency topics (marks, heartbeats, stream health) outside React Query | Jotai (fine, but a second paradigm is unnecessary) |
| Streaming transport | **Native `EventSource`** (`withCredentials: true`), one multiplexed stream per tab, cross-tab leader via Web Locks + BroadcastChannel | — | Server→client only. Mutations stay audited REST POSTs with CSRF. | WebSockets (bidirectional is not needed; auth and CSRF are harder); `fetch-event-source` (unmaintained since 2021) |
| URL state | **nuqs 2** | MIT | Filters, selected entity, timeframe and tab become shareable links (fixes W10) | — |
| Grids | **TanStack Table 9** + **TanStack Virtual 3** inside one owned `<DataGrid>` | MIT | Headless, tree-shakable, low memory, row-keyed updates (the LiveTableModel lesson) | AG Grid (enterprise licence for key features, and a heavy visual system) |
| Price charts | **Lightweight Charts 5.2** | Apache-2.0 + attribution | Multi-pane, markers plugin, price lines, **pane primitives for regime shading**, accessibility plugin, 58 KB measured. **No built-in indicator engine, which fits the "indicators live in the backend" rule by construction.** | KLineChart 10 (see §7.1); Highcharts Stock (commercial) |
| Analytics charts | **uPlot 1.6** for time-series analytics (equity, drawdown, rolling metrics, exposure over time); **the existing SVG components upgraded** for bars, histograms and small multiples; **ECharts 6 (tree-shaken, `next/dynamic`, Research Lab only)** for large heatmaps or parameter surfaces if SVG proves insufficient | MIT / Apache-2.0 | uPlot: 23 KB and extremely fast. SVG: zero cost and already written. ECharts: capability on demand. | **Plotly (about 1.4 MB gzipped)** |
| Command palette | **cmdk 1.1** | MIT | Unstyled, composable, 17 KB | — |
| Hotkeys | **tinykeys 4** (about 0.6 KB) or `@tanstack/react-hotkeys` (0.x, too new) | MIT | Chord support (`g o`), scoping | react-hotkeys-hook is acceptable too |
| Motion | **CSS transitions by default**; **Motion 13** (`motion/react` with `LazyMotion` + `m`) only for layout and shared-element transitions (drawers, panel reflow, list reordering) | MIT | Most micro-interactions need no JS. Motion is loaded only where layout animation earns it. | Animating numeric ticks |
| Split panes | **react-resizable-panels 4** | MIT | Chart/blotter/inspector splits with persisted sizes | dockview (defer; §3.5) |
| Toasts | **sonner 2** | MIT | Stackable, accessible | — |
| Dates | `Intl.DateTimeFormat` with `timeZone: "UTC"` and an explicit "UTC" suffix; optional `@internationalized/date` | — | Timestamps are UTC by rule | moment |
| API types | Generate from FastAPI **OpenAPI** with `openapi-typescript` and replace the hand-mirrored `lib/types.ts` | MIT | Removes drift between pydantic and TypeScript | — |
| Tests | Playwright 1.56 + **@axe-core/playwright 4.13** + Playwright screenshot comparison; **size-limit 14** per-route budgets; `web-vitals` 6 for INP in e2e | — | See §8 | — |

### 3.2 Rendering model

- Keep pages as **client components**. The API is a separate origin (in development it is a different port on 127.0.0.1) with cookie auth and CSRF, and there is no SEO requirement. Server components add cookie-forwarding complexity with no benefit.
- **Add a same-origin API path.** Use `next.config.ts` `rewrites()` to map `/api/:path*` to `http://127.0.0.1:8000/api/:path*` (or put a reverse proxy in front of both). This makes cookies first-party (`SameSite=Strict` becomes possible), simplifies CORS, and puts EventSource on the same origin.
  - Caveat: streaming SSE through Next's dev/prod rewrite proxy must be **verified not to buffer**. FastAPI sets `X-Accel-Buffering: no`, but the Next proxy is not nginx. **Assumption; see §9.**
- Use **`<Activity>`** (React 19.2) to keep heavy screens such as the chart workstation mounted but hidden when switching tabs, so charts do not re-initialise.
- Turn on the **React Compiler** (`reactCompiler: true`) after wave 2, once the grid is stable. Measure build time and INP before and after.

### 3.3 Data-layer contract (replaces `useApi`)

```ts
// lib/query.ts — one shape for every server read
type Freshness = "live" | "fresh" | "lagging" | "stale" | "disconnected";
type ViewState<T> =
  | { status: "loading" }                                          // first load only
  | { status: "error"; error: ApiError }                           // no data ever received
  | { status: "success"; data: T; freshness: Freshness; asOf: string;
      refreshError?: ApiError };                                   // last-known-good + why it may be old
```

- A background refetch **never** returns to `loading`. It keeps `data` and changes `freshness`. A failed refetch keeps `data`, sets `refreshError`, and the UI shows "STALE · last good 42 s ago · retrying" (the DataHub rule from Fincept `TopicPolicy.h`).
- `AsyncBoundary` gains that fifth visual state. The "absence is not zero" rule is unchanged.

### 3.4 Component layer (owned)

`components/ui/` contains Button, IconButton, Kbd, Tooltip (also opens on focus and tap, which fixes W9), Popover, Dialog, **ConfirmDialog v2**, Tabs, Segmented, Select, Combobox, Menu, Toast, Badge/StatusPill, **ProvenanceChip v2**, **ModeFrame**, **FigureValue v2**, **Stat**, Sparkline (SVG), **DataGrid**, EmptyState, **StaleBadge**, Skeleton, SplitPane, Sheet (side inspector), CommandPalette, ShortcutSheet.

**ConfirmDialog v2** is built on Base UI Dialog:

- focus trap, `inert` background, focus returned to the trigger, Escape disabled while `busy`;
- choices are a radio group;
- an **explicit caveat-acknowledgement checkbox listing each caveat code**, which is the only way `acknowledge_caveats: true` can be sent (fixes W3);
- consequences come from the API only (new backend field needed for promote and disarm; §6.6).

### 3.5 Layout: fixed workstation shell, not free docking

Two operators, one product and nine jobs do not need TT- or Sierra-style free docking. It adds state, test surface and support burden, and IBKR's experience shows that removing configurability hurts professionals. Offer **opinionated fixed layouts with resizable splits, saved column sets, density, and one "pin to Overview" mechanism**. Revisit dockview (MIT, 85 KB) in a later phase only if both operators ask for it.

### 3.6 Security posture

- Add a **Content-Security-Policy**: `default-src 'self'`; `connect-src` limited to the API origin; no `unsafe-eval`; `script-src` using a nonce via `proxy.ts`.
- Lightweight Charts and uPlot need no eval.
- Keep `X-Frame-Options: DENY`. No secrets in `NEXT_PUBLIC_*`.

### 3.7 Replace the chart-library ban with budgets

Amend `tests/e2e/source-rules.spec.ts:103-113`:

- keep an explicit ban on `plotly.js`, `react-plotly.js` and `mapbox-gl`;
- replace the "exactly three dependencies" assertion with an **allow-list** plus a **size-limit** CI gate with these first-load gzip budgets:

| Route | First-load JS budget (gzip) |
|---|---|
| Shell + Overview | ≤ 180 KB |
| Fleet & Positions | ≤ 230 KB |
| Chart workstation | ≤ 300 KB |
| Research Lab (with lazy ECharts) | ≤ 420 KB |

---

## 4. Design language proposal

### 4.1 Principles

1. **Provenance before polish.** No aesthetic choice may hide where a number came from or how old it is.
2. **Quiet chrome, loud state.** Surfaces are near-monochrome. Colour is reserved for meaning: mode, provenance, P&L direction, and health.
3. **Every meaning has a non-colour carrier**: text label, glyph, shape or pattern (WCAG 1.4.1).
4. **Density is a setting, not a compromise.** Three density modes share one type ramp.
5. **Motion confirms, never decorates.** 80–240 ms, no animated number counting, no motion on live price ticks beyond a brief background flash.

### 4.2 Colour tokens (OKLCH, dark-first)

The palette is generated from three inputs, following the Linear approach: **neutral hue 255, accent hue 250, contrast level**. Values below are the dark theme and are starting points to be tuned with a contrast checker (see §9).

```css
:root, :root[data-theme="dark"] {
  /* Neutrals: hue 255, very low chroma (cool graphite) */
  --bg-canvas:    oklch(0.145 0.008 255);  /* app background */
  --bg-sunken:    oklch(0.125 0.008 255);  /* nav rail, table header */
  --bg-surface:   oklch(0.175 0.009 255);  /* panels, cards */
  --bg-raised:    oklch(0.205 0.010 255);  /* popovers, dialogs, hover rows */
  --bg-overlay:   oklch(0.235 0.011 255);  /* menus, command palette */
  --border-subtle:oklch(0.260 0.010 255);
  --border:       oklch(0.320 0.012 255);
  --border-strong:oklch(0.420 0.014 255);
  --fg:           oklch(0.955 0.004 255);  /* primary text  */
  --fg-muted:     oklch(0.780 0.010 255);  /* labels        */
  --fg-subtle:    oklch(0.640 0.012 255);  /* captions (>=4.5:1 on canvas; verify) */
  --fg-disabled:  oklch(0.480 0.010 255);

  /* Accent: selection, focus, links only (never state) */
  --accent:       oklch(0.720 0.140 250);
  --accent-bg:    oklch(0.720 0.140 250 / 0.14);
  --focus-ring:   oklch(0.800 0.150 250);  /* 2px ring + 2px offset, >=3:1 */

  /* P&L direction: the only green/red in the system */
  --pnl-up:       oklch(0.780 0.170 152);  /* profit */
  --pnl-down:     oklch(0.680 0.200  27);  /* loss   */
  --pnl-flat:     var(--fg-muted);
  --pnl-up-bg:    oklch(0.780 0.170 152 / 0.12);
  --pnl-down-bg:  oklch(0.680 0.200  27 / 0.12);

  /* Health / severity (always with an icon) */
  --ok:           oklch(0.760 0.120 175);  /* teal-green, distinct from pnl-up */
  --warn:         oklch(0.820 0.150  85);
  --critical:     oklch(0.650 0.220  20);
  --unknown:      oklch(0.600 0.020 255);

  /* Execution mode: whole-shell frame colours (see 4.4) */
  --mode-backtest:oklch(0.620 0.030 255);  /* slate */
  --mode-paper:   oklch(0.760 0.110 210);  /* cyan  */
  --mode-shadow:  oklch(0.720 0.140 300);  /* violet */
  --mode-demo:    oklch(0.800 0.160  70);  /* amber */
  --mode-live:    oklch(0.650 0.250 355);  /* hot magenta, never used elsewhere */

  /* Provenance: hue by evidence family, shape by simulated vs executed (4.4) */
  --prov-sim:     oklch(0.700 0.050 255);  /* backtest/walkforward/OOS/holdout */
  --prov-paper:   var(--mode-paper);
  --prov-shadow:  var(--mode-shadow);
  --prov-demo:    var(--mode-demo);
  --prov-live:    var(--mode-live);

  /* Charts: CVD-aware categorical (Okabe–Ito derived, tuned for dark) */
  --series-1: oklch(0.780 0.130 235); --series-2: oklch(0.800 0.150  70);
  --series-3: oklch(0.720 0.130 165); --series-4: oklch(0.700 0.160 330);
  --series-5: oklch(0.850 0.150 100); --series-6: oklch(0.680 0.170  45);
  --regime-trend: oklch(0.720 0.130 235 / 0.08);
  --regime-range: oklch(0.800 0.150  70 / 0.08);
  --regime-stress:oklch(0.650 0.220  20 / 0.10);
}
@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) { /* light values */ } }
:root[data-theme="light"] {
  --bg-canvas: oklch(0.985 0.003 255); --bg-surface: oklch(1 0 0);
  --bg-sunken: oklch(0.965 0.004 255); --bg-raised: oklch(1 0 0);
  --border: oklch(0.880 0.006 255);    --fg: oklch(0.200 0.010 255);
  --fg-muted: oklch(0.420 0.012 255);  --fg-subtle: oklch(0.520 0.012 255);
  --pnl-up: oklch(0.550 0.150 152);    --pnl-down: oklch(0.550 0.200 27);
  /* modes/provenance: same hue, L lowered ~0.2 for 4.5:1 on white */
}
```

**CVD-safe preset** (a user setting, `data-pnl="cvd"`): `--pnl-up: oklch(0.72 0.14 250)` (blue) and `--pnl-down: oklch(0.75 0.16 55)` (orange). The glyphs (§4.5) remain regardless.

**Why these hues:**

- Green and red are **exclusively** P&L.
- Health "ok" is teal (175°), not P&L green (152°), and always has a ✓ icon.
- **Live is magenta (355°)**, not loss red (27°). "Live" must never be read as "losing", and loss must never be read as "live". Live also always carries a filled chip and the text "REAL MONEY".
- The research provenances share one neutral hue and differ by **shape** (§4.4). The current four near-identical blues are removed.

### 4.3 Type, spacing, radii, elevation, motion

| Token group | Values |
|---|---|
| Families | `--font-ui: "Inter Variable", system-ui, sans-serif` (`font-feature-settings: "cv11","ss01"`); `--font-num: var(--font-ui)` with `"tnum","zero"`; `--font-mono: "JetBrains Mono Variable", ui-monospace, monospace` |
| Type scale (px / line-height) | `2xs 10.5/14` (chip labels, caps +0.06em) · `xs 11.5/16` (grid compact, captions) · `sm 12.5/18` (grid regular, labels) · `base 13.5/20` (body) · `md 15/22` (panel titles) · `lg 18/26` (page titles) · `xl 24/30` (stat values) · `2xl 32/38` (hero figure on Overview only). Weights 400 / 500 / 600. No 700 except the mode label. |
| Numerics | Right-aligned in grids. Same decimals per column (the unit formatter decides). Negative values use a real minus `−` (U+2212), not a hyphen. Units are shown in the column header, not repeated per cell. |
| Spacing (4px base) | `0, 2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64` |
| Radii | `--r-xs 2px` (chips) · `--r-sm 4px` (inputs, buttons) · `--r-md 6px` (panels) · `--r-lg 10px` (dialogs, palette) · `--r-full` |
| Elevation (dark) | Expressed as surface lightness plus border, not shadow: e0 canvas, e1 surface + `--border-subtle`, e2 raised + `--border` + `0 1px 0 oklch(1 0 0 / .04) inset`, e3 overlay + `--border-strong` + `0 12px 32px oklch(0 0 0 / .45)`. In light mode, shadows carry more of the effect. |
| Motion durations | `--dur-instant 0ms` (reduced motion) · `--dur-xs 80ms` (hover, press) · `--dur-sm 120ms` (tooltip, toggle) · `--dur-md 160ms` (popover, menu, tab indicator) · `--dur-lg 200ms` (dialog in, sheet) · `--dur-xl 240ms` (panel reflow). **Exits are about 25% shorter than entries.** |
| Easing | `--ease-standard cubic-bezier(.2,0,0,1)` · `--ease-enter cubic-bezier(0,0,.2,1)` (ease-out) · `--ease-exit cubic-bezier(.4,0,1,1)` · `--ease-emph cubic-bezier(.3,0,0,1)` |
| Live-tick flash | Background tint of `--pnl-up-bg` or `--pnl-down-bg`, fading over 600 ms. **No position or size motion.** Disabled under `prefers-reduced-motion` and in compact density. |
| Focus | 2px `--focus-ring` with a 2px offset on every interactive element (≥3:1, WCAG 2.4.11/1.4.11) |

### 4.4 Execution-mode and provenance visual grammar

**Two axes and two languages. Never mix them.**

**A. Execution mode**: *where would an order go right now?* This is an environment property shown by the **whole shell**, read from `/api/system/execution-mode` (pushed on the stream after wave 2).

| Mode | Frame (2px inset border around the viewport) | Banner | Favicon / title | Extra |
|---|---|---|---|---|
| backtest | none | slate, compact: `BACKTEST · nothing can place an order` | grey dot / `[BT] Fiboki` | — |
| paper | 1px `--mode-paper` | cyan: `PAPER · simulated fills` | cyan dot / `[PAPER]` | — |
| shadow | 2px dashed `--mode-shadow` | violet: `SHADOW · mirrored, not sent` | violet / `[SHADOW]` | — |
| demo | 3px `--mode-demo` with **diagonal hatch** on the banner | amber: `DEMO · real orders to a demo venue` | amber / `[DEMO]` | Confirm dialogs show a DEMO stamp |
| **live** | **4px solid `--mode-live`** + banner with inverted text | **magenta, `REAL MONEY` in caps, operator name, kill-switch button always visible** | **magenta favicon, `● LIVE` title prefix** | Mutating dialogs require the typed phrase. A 250 ms banner pulse **once** on mode change only. |
| unknown / loading | 2px `--unknown` striped | `MODE UNKNOWN` (only when the stream **and** REST have failed, never on a poll tick) | grey `?` | All mutating controls disabled |

The **kill-switch state lives in the banner** in every mode (`ARMED · PAUSE · by tom 14:02Z`). The status bar mirrors the mode in the bottom-left so it is visible even when the banner is scrolled into a split.

**B. Provenance**: *where did this number come from?* This is a per-figure property shown by the **chip beside the number** and the **stroke of a chart series**.

| Provenance | Chip | Chart series | Evidence rank |
|---|---|---|---|
| backtest | **dashed outline**, `--prov-sim`, label `BT` | dashed line | 1 (weakest) |
| walkforward | solid outline, `--prov-sim`, `WF` | dotted line | 2 |
| out_of_sample | solid outline, `--prov-sim`, `OOS` | solid thin line | 3 |
| holdout | **double outline**, `--prov-sim`, `HOLD` | solid line | 4 |
| paper | **filled** `--prov-paper`, dark text, `PAPER` | solid, paper hue | executed (simulated fills) |
| shadow | filled `--prov-shadow`, `SHADOW` | solid, violet | executed (not sent) |
| broker_demo | filled `--prov-demo` + hatch, `DEMO` | solid, amber | executed |
| broker_live | filled `--prov-live`, white text, `LIVE`, plus a `£` glyph | solid thick, magenta | executed, real money |

Rules:

1. **Hollow = simulated, filled = executed.** This is readable in greyscale.
2. Chip labels are always text. The colour is secondary.
3. A column or chart with **mixed** provenance shows a `MIXED` chip whose popover lists the counts per provenance (from the API's `mixed_provenance_result` caveat). **It never shows the first row's provenance** (fixes W4).
4. **Stale** overlays the chip with a clock glyph and age.
5. **Estimated** is a small `est` superscript with a focusable popover.
6. **Caveats** show as a `⚠n` badge whose popover renders `CaveatList` in full, and that popover is keyboard and touch accessible.

### 4.5 P&L and risk semantics

- **P&L sign:** colour (`--pnl-up`/`--pnl-down`) **plus** an explicit sign (`+£1,240.50` / `−£233.25`) **plus** a ▲/▼ glyph in stat tiles. Zero is shown as `£0.00` in `--pnl-flat`, and only when the API returned 0. Null is shown as `no data` in italic `--fg-subtle`, never as 0 (the contract is kept).
- **Risk utilisation** (exposure against limit, daily loss against limit, drawdown against limit) uses **fixed bands, not a hue gradient**:

| Band | Utilisation | Treatment |
|---|---|---|
| Normal | < 70% | neutral bar |
| Elevated | 70–90% | amber `--warn` + ◆ |
| Breach | ≥ 90% (or `breached: true` from the API) | `--critical` + ▲! |

  Bars show a **limit tick** and a numeric label. The API decides "breached"; the UI never computes breach status. It only positions the bar using `utilisation_pct` from the payload.
- **Health:** ✓ ok (teal) · ◐ degraded (amber) · ✕ down (critical) · ? unknown (grey). The worst component wins, as the backend already enforces.
- **Correlation heatmap:** a diverging scale **blue (−1) → neutral (0) → orange (+1)** with a numeric overlay in cells when there is room, replacing today's red/green (`charts.tsx:264-269`).

### 4.6 Density modes

| Mode | Grid row | Grid text | Panel padding | Default for |
|---|---|---|---|---|
| **Compact** | 24 px (Carbon xs) | xs 11.5 | 8 | Fleet blotter, trades, audit log on ≥1440 px |
| **Regular** | 32 px (Carbon sm) | sm 12.5 | 12 | Default |
| **Comfortable** | 40 px (Carbon md) | base 13.5 | 16 | Tablet, and screens below 1024 px automatically |

Density is set with `data-density` on `<html>`, stored per operator in `localStorage` (a convenience only), and toggled with `⌘⇧D`.

---

## 5. Information architecture

### 5.1 Shell

```
┌ Mode banner (sticky; full-width; mode colour; kill-switch state; operator) ───────────────┐
├ Rail (56px icons; expands 232px) ┬ Page header: title · entity switcher · view tabs · actions ┤
│ ⌂ Command                        │                                                         │
│ ◎ Fleet & Positions              │   Screen body (fixed layout, resizable splits)          │
│ ⇅ Strategy Lifecycle             │                                          ┌ Inspector ┐  │
│ ⚗ Research Lab                   │                                          │ (Sheet,    │  │
│ ◈ Market Intelligence            │                                          │  selected  │  │
│ ⚠ Risk & Exposure                │                                          │  entity)   │  │
│ ▦ Data Quality                   │                                          └───────────┘  │
│ ⚙ System & Incidents             │                                                         │
│ ✎ Journal                        │                                                         │
├──────────────────────────────────┴─────────────────────────────────────────────────────────┤
│ Status bar: ● stream live 0.4s · worker hb 12s · API ok · data as-of 14:02:10Z · joe(admin) · 14:02:31 UTC │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Entity routes** (new, fixing W7): `/fleet/positions/[id]`, `/lifecycle/[contentHash]`, `/research/strategies/[id]`, `/research/experiments/[id]`, `/markets/[symbol]`, `/system/incidents/[id]`, `/journal/[tradeId]`. The inspector sheet opens the same view inline; `↗` opens the full route.
- **Migration map:**

| Current page(s) | New home |
|---|---|
| `/`, `/alerts`, `/market-pulse` | Command |
| `/trading/portfolio`, `/trading/execution` | Fleet & Positions and Journal |
| `/trading/candidates` | Strategy Lifecycle |
| `/trading/exposure`, `/trading/risk` | Risk & Exposure |
| `/research/*` | Research Lab |
| `/markets`, `/markets/regimes`, `/markets/correlations` | Market Intelligence |
| `/markets/data-quality`, `/system/data-health` | Data Quality |
| `/intelligence/*` | Market Intelligence › Agents, and System › Audit |
| `/system/*` | System & Incidents |

### 5.2 Screens

For every screen: every figure is a `FigureValue v2`, every panel has a freshness badge, and every mutating action goes through ConfirmDialog v2.

| Screen | Question it answers | Key widgets | **Primary action** | Data (existing → new) |
|---|---|---|---|---|
| **Command (Overview)** | "Is anything wrong, and what needs me now?" | ① **Attention queue**: ranked list of open incidents, breaches, stale workers, strategies awaiting review, and unacknowledged caveats, each with a deep link. ② Platform health strip (worst-wins, per-check pills). ③ Book summary stats: equity, day P&L, open risk, drawdown against limit, all with provenance. ④ Fleet heat strip: one cell per strategy bot, coloured by lifecycle health, with a glyph. ⑤ Mode and kill-switch card. ⑥ Equity sparkline (uPlot). | **Triage the top attention item** (`Enter` opens it) | `/api/health`, `/api/trading/portfolio`, `/api/trading/risk`, `/api/system/kill-switch` → **new `/api/command/attention`** (server-ranked; the UI must not rank) |
| **Fleet & Positions** | "What is every bot doing and what is open?" | Bot grid: strategy, lifecycle state, mode, last signal, last heartbeat, open risk, day P&L, all live. Positions blotter: instrument, side, size, entry, mark, stop, target, distance to stop, unrealised, age. Selected row opens an inspector with a **mini price chart** (entry, stop, target lines) and the order/intent timeline. | **Pause or close a single bot or position** (ConfirmDialog; server consequences) | `/api/trading/positions`, `/api/trading/lifecycle/strategies` → **new `/api/fleet/bots`** and stream topics `fleet`, `positions`, `marks` |
| **Strategy Lifecycle** | "What should be promoted, demoted or retired?" | Kanban-like **ladder**: Candidate → Paper → Shadow → Demo → Live (gated), plus Retired. Each card shows evidence rank chips, trade count against the 80-trade gate, and blocking reasons. Candidate grid (today's page, sortable). Evaluation detail: last lifecycle tick, degradation flags, backtest-vs-paper divergence (uPlot overlay). | **Promote / demote** (one action, with a caveat checklist) | `/api/trading/candidates`, `POST …/promote`, `/api/trading/lifecycle/strategies/{hash}` and `/evaluation` (currently unused) |
| **Research Lab** | "What have we tried and what does the evidence say?" | Strategy registry (grid, with an entity page). Experiments ledger (virtualised). Validation ladder view (rungs passed, binding constraint). Parameter Lab: search-space size shown prominently with the deflation warning, and a sensitivity heatmap (lazy ECharts or SVG). Hypotheses. Datasets with version ids. | **Queue an experiment** (`POST /api/research/experiments`, currently unused) | existing `/api/research/*` |
| **Market Intelligence** | "What is the market doing, and what did the agents conclude?" | **Chart workstation**: symbol and timeframe switcher, backend-computed indicator panes, signal, trade and stop overlays, regime shading (§7). Regime table. Correlation matrix (blue/orange). Agent runs and research memory, **each claim linked to its run and dataset** (the ASKB-style attribution pattern). | **Open a symbol in the chart** (`/` or `⌘K` then symbol) | `/api/markets/bars/{symbol}` (unused today), `/api/markets/regimes`, `/correlations`, `/api/intelligence/*` → **new** `/api/markets/overlays/{symbol}` (signals, trades, stops, regimes, indicator series; backend-computed) |
| **Risk & Exposure** | "How close are we to any limit?" | Limit gauges (daily loss, drawdown, margin) with bands. Exposure by instrument, currency and strategy (bar-with-limit tick, sortable table). Gateway permits (new risk, closing). Limit-set version. **Kill switch panel** (full). Kill-switch history (currently unused endpoint). | **Arm or disarm the kill switch** | `/api/trading/risk`, `/api/trading/exposure`, `/api/system/kill-switch*` |
| **Data Quality** | "Can we trust the inputs?" | Per-instrument quality grid (bars, gaps, stale runs, last bar age). Dataset versions. A gap timeline (SVG strip per instrument). Feed and recorder health. | **Open the affected instrument or dataset** | `/api/markets/data-quality`, `/api/system/data-health`, `/api/research/datasets` |
| **System & Incidents** | "Is every process up, and what happened?" | Services, workers (heartbeat age against the 120 s stale / 300 s down thresholds), queues, broker-health controls, settings (read-only). **Incidents**: derived alerts with a timeline. **Audit log** (virtualised, hash-chain integrity banner). Execution telemetry (slippage and latency distributions). | **Acknowledge / annotate an incident** (new, audited) | `/api/system/*`, `/api/intelligence/audit*`, `/api/trading/execution-telemetry` (currently unused) → **new** incidents read model and ack endpoint |
| **Journal** | "What did we trade, why, and what did we learn?" | Trade list (all provenances, provenance as a column and filter, URL-persisted). Trade detail: chart snapshot with entry, exit, stop and signal; R-multiple; costs; caveats. Operator notes (`POST research-memory/notes`, or a new trade-note endpoint). | **Add a note to a trade** | `/api/trading/trades` → **new** `/api/trading/trades/{id}` with notes |

### 5.3 Keyboard model

Shortcuts are shown in tooltips and in the palette, and discoverable with `?`. **No single keystroke ever executes a mutation.** Keys only open dialogs.

| Keys | Action |
|---|---|
| `⌘K` / `Ctrl K` | Command palette: navigate, open entity (strategy, symbol, trade id, incident), run view actions, toggle theme or density |
| `g c` / `g f` / `g l` / `g r` / `g m` / `g k` / `g d` / `g s` / `g j` | Go to Command / Fleet / Lifecycle / Research / Markets / Risk (**k** for kill/risk) / Data / System / Journal |
| `/` | Focus the screen filter |
| `j` / `k`, `↑` / `↓` | Move the grid row selection (roving tabindex) |
| `Enter` / `o` | Open the selected row in the inspector; `⇧Enter` opens its full route |
| `Esc` | Close inspector, popover or dialog (disabled while a dialog is `busy`) |
| `[` / `]` | Previous / next view tab |
| `1`…`6` (chart focused) | Timeframe M5 / M15 / H1 / H4 / D1 / W1 |
| `+` / `-` / `0` (chart focused) | Zoom in / zoom out / reset |
| `⇧K` | **Open** the kill-switch dialog (never arms directly) |
| `⌘⇧D` | Cycle density |
| `⌘⇧L` | Toggle light/dark |
| `?` | Shortcut sheet |

---

## 6. Real-time architecture (SSE from FastAPI)

### 6.1 Why SSE

- The data flow is server→client. Every client→server act is already an audited REST POST with CSRF (`lib/api.ts:55-94`, `src/fiboki/api/audit_trail.py`).
- SSE reconnects automatically, supports `Last-Event-ID` resume, travels over plain HTTP with the existing session cookie (`withCredentials`), and FastAPI ≥0.135 supports it natively.
- WebSockets would add a second auth path and a second place where mutations could hide.

### 6.2 Backend (new; SSE part about 4–5 engineer-days, and about 10–11 including §6.6)

- **Endpoint:** `GET /api/stream?topics=mode,killswitch,health,fleet,positions,marks,incidents,risk` returning `text/event-stream`.
  - Implementation: `sse-starlette` 3.5 at the current `fastapi==0.115.6` pin, **or** upgrade to FastAPI ≥0.135 and use `fastapi.sse.EventSourceResponse`.
  - Auth: same session cookie, the RBAC dependency, and an origin check. The stream must not start any worker (the topology rule).
- **Event envelope** (the Figure contract is unchanged inside `data`):

  ```
  id: <topic>:<seq>                event: <topic>.<kind>
  data: {"topic":"positions","seq":1843,"kind":"snapshot|delta|tombstone|heartbeat",
         "as_of":"2026-09-28T14:02:10.412Z","source":{...SourceNote},
         "data":{...}}          # every number still a Figure with provenance
  ```

  - **Snapshot on subscribe**, then deltas keyed by entity id; tombstones for closed positions.
  - **Per-topic sequence numbers.** The server keeps a small **ring buffer per topic** (for example the last 500 events). On reconnect with `Last-Event-ID` it replays the gap, or sends a fresh `snapshot` if the id has aged out. **The client never has to guess.**
- **Heartbeat event every 5 s** (in addition to FastAPI's 15 s comment ping):

  ```json
  {"server_time": "...", "worker_heartbeat_age_s": 12.1, "mode": "paper",
   "kill_switch": {"active": false}, "topics": {"positions": {"seq": 1843, "as_of": "..."}}}
  ```

  This lets the UI compute **server-side staleness**, not just connection liveness. A connected stream with a dead worker must look stale, which is the V1 3 am lesson (`OBSERVABILITY_STANDARD.md` §1, §10 rule 1).
- **Sources:** mode and kill switch come from the kill-switch journal and settings; health from the existing checks (re-evaluated on a timer, not per request); positions, fleet and risk from the paper/live engine's state store; incidents from alert events.
  - **Gap:** the architecture doc says no process currently composes the workers or starts the watchdog (`ARCHITECTURE.md` §6, §12). Until that wiring exists the stream publishes what the API can measure (health, mode, kill switch) and **labels the other topics `absent`**, exactly as REST does today.
- **Coalescing:** marks and prices coalesce to at most 4 Hz per instrument on the server (Fincept's `coalesce_within_ms`). Fleet and positions send on change. Risk sends on change or every 5 s.
- **Proxying:** send `X-Accel-Buffering: no`. If served behind the Next rewrite, verify there is no buffering (§9).

### 6.3 Client

```
EventSource(/api/stream, {withCredentials:true})      ← one per browser (leader tab)
  │  Web Locks "fiboki-stream" elects a leader; followers receive via BroadcastChannel
  ▼
StreamRouter (dedupe by id [LRU 1,000, per Vibe-Trading useSSE], seq-gap check per topic)
  ├─ low-frequency topics  → queryClient.setQueryData(key, applyDelta)   (TanStack Query cache)
  │    mode, killswitch, health, risk, fleet, positions, incidents
  └─ high-frequency topics → zustand liveStore (rAF-batched writes, ≤60 Hz, per-symbol slices)
       marks, stream health, heartbeat
```

- **Why a single leader connection:** on HTTP/1.1 (uvicorn, loopback), browsers allow **6 SSE connections per browser and domain across all tabs** (MDN). An operator with seven tabs open would otherwise starve both the stream and ordinary fetches.
- **Reconnection:** native EventSource retry, honouring the server's `retry:` hint, plus an application-level backoff of 1 s → 2 → 4 → 8 → 15 s with a cap and ±20% jitter.
  - After 5 failed attempts the banner shows `DISCONNECTED` with a manual *Reconnect* button (the Vibe-Trading `ConnectionBanner` pattern) and REST polling fallback begins at 10 s.
  - A **generation counter** prevents a stale socket from writing after a reconnect (as in `useSSE.ts`).
- **Gap handling:** if `seq` jumps for a topic, mark that topic `resyncing` and invalidate its query, which fetches a REST snapshot. The stream then resumes deltas.
- **Mutations:** REST POST with CSRF. **No optimistic UI** for kill switch, promote or pause. The dialog stays `busy` until the POST returns **and** the corresponding stream event arrives (or 5 s passes, after which the result is shown with a "confirming…" badge). Tom's arm is visible to Joe within about one heartbeat.
- **Rendering:** grids subscribe per row or cell (TanStack Table v9 store and Zustand selectors), so a mark update re-renders one cell, not the grid (Fincept's `LiveTableModel` lesson). No layout change on tick: fixed column widths and tabular numerals.

### 6.4 Freshness and staleness indicators

Each topic declares an expected cadence, for example positions 5 s, health 20 s, marks 1 s in market hours.

| State | Rule | UI |
|---|---|---|
| **live** | stream connected, topic `as_of` age < 1.5× cadence, worker heartbeat < 120 s | green-teal dot in the status bar; no badge on panels |
| **lagging** | age 1.5–3× cadence | amber dot; panel badge `LAG 9s` |
| **stale** | age > 3× cadence **or** worker heartbeat ≥ 120 s (backend `stale_after_seconds`) | amber hatch on panel header; figures dimmed to `--fg-muted`; badge `STALE · as of 14:01:22Z` |
| **disconnected** | stream down and REST failing | red banner strip; figures keep **last-known-good** with a strike-through age tag. **Never zero, never blank.** Mutations disabled. |
| **absent** | the backend says the source does not exist in this deployment (`SourceNote.kind = "absent"`) | explicit `ABSENT` empty state, as today |

The status bar always shows: stream state and lag, worker heartbeat age, API health, the most recent `as_of`, the operator, and the UTC clock.

### 6.5 Tests for the stream

- Playwright routes `/api/stream` to a scripted SSE body (`route.fulfill` with `text/event-stream` and chunked lines).
- Assert:
  - snapshot then delta renders;
  - a sequence gap triggers a REST resync;
  - a dropped stream shows `STALE` then `DISCONNECTED`, and **the numbers remain**;
  - a kill-switch event from "another operator" updates the banner and panel within 1 s;
  - no view ever shows `loading` after first data (the regression test for W1).

### 6.6 Other backend asks (small)

1. Server-computed `consequences` for **promote** (per target lifecycle) and **disarm**, in the same shape as kill-switch arm.
2. `/api/command/attention`: a ranked attention list. Ranking is logic and belongs in the backend.
3. `/api/markets/overlays/{symbol}?timeframe=…&from=…&to=…`: signals, entries, exits, stops and targets over time, regime segments, and **indicator series computed in `indicators/`**, each item with provenance and `dataset_version_id`.
4. Add volume to `/api/markets/bars` if the store has it (currently `o/h/l/c` only; `markets.py:179-188`).
5. Incident read model and an audited acknowledge endpoint.
6. OpenAPI schema published for `openapi-typescript`.

---

## 7. Chart strategy

### 7.1 Engine choice

| Use | Engine | Why |
|---|---|---|
| **Price charts** (candles, indicator panes, overlays) | **Lightweight Charts 5.2** | Multi-pane (price, volume and backend indicator panes). `createSeriesMarkers` for signals and fills. `createPriceLine` for entry, stop, target and limit levels. **Pane primitives** to paint regime bands behind candles. Series hit-testing (5.2) for trade tooltips. The accessibility plugin (5.2). Data conflation (5.1) for long histories. Apache-2.0 with an attribution logo to keep on or credit elsewhere. **58 KB measured.** |
| **Analytics time series** (equity, drawdown, rolling Sharpe/expectancy, exposure over time, backtest-vs-paper overlay) | **uPlot 1.6** | 23 KB. Handles 10⁵+ points trivially. Synchronised cursors across small multiples. Series stroke styles (dash patterns) map directly onto the provenance grammar. |
| **Small, static analytics** (bars with a limit tick, R-histograms, sparklines, gap strips, validation ladder) | **Existing inline SVG** (`components/charts.tsx`), restyled with tokens, responsive width, x-axes, and focusable data points | Zero cost, already provenance-aware, already honest about empty data |
| **Heatmaps and surfaces** (parameter sensitivity, large correlation matrices) | SVG first; **ECharts 6 tree-shaken and `next/dynamic` on Research Lab only** if interaction (zoom, visualMap brushing) is required | 209 KB measured is acceptable only when lazy-loaded on one screen |

**Why not KLineChart (the current intention in the project brief):**

- KLineChart's value lies in its **built-in indicator and drawing engine**, and using that engine means computing indicators in the browser. That breaks a non-negotiable rule ("indicator calculations must be centralised in `indicators/`"; "frontend must not calculate indicators"). It also risks a backtest/chart mismatch, the parity concern in the project instructions.
- KLineChart can be driven with pre-computed series, but at that point it offers little over Lightweight Charts.
- Its 10.x line only left beta in July 2026 (npm timeline), whereas Lightweight Charts has shipped stable v5 minors steadily since 2025.
- Lightweight Charts has **no indicator library at all**, so the rule is enforced by construction.
- **This is a recommendation for Joe to confirm.** If KLineChart is kept, add a source-rule test that forbids `registerIndicator` calc functions and `createIndicator` with built-in names.

**Why not Plotly for analytics (also the current intention in the project brief):**

- About 1.4 MB gzipped (bundlephobia). The V2 README records V1 shipping about 4.5 MB of Plotly and mapbox-gl.
- uPlot plus SVG covers every analytics view in the IA at under 3% of that weight.

### 7.2 Overlay model for price charts

```ts
// Built only from /api/markets/overlays — the frontend computes nothing.
type ChartOverlay =
  | { kind: "signal";  t: string; side: "long"|"short"; strategy_id: string; provenance: Provenance; reason: string }
  | { kind: "fill";    t: string; price: Figure; side: "buy"|"sell"; trade_id: string; provenance: Provenance }
  | { kind: "level";   role: "entry"|"stop"|"target"|"limit"; price: Figure; from: string; to: string|null; position_id: string }
  | { kind: "regime";  from: string; to: string; label: "trend"|"range"|"stress"|"unknown" }
  | { kind: "series";  pane: string; name: string; points: SeriesPoint[]; provenance: Provenance; indicator_id: string };
```

| Overlay | Rendering |
|---|---|
| **Signals** | Series markers. Long = ▲ below the bar, short = ▼ above. The shape encodes direction, so colour is not needed. Colour comes from the strategy's categorical series token. Marker text is the strategy short code. Hit-test tooltip: strategy, reason, provenance chip. |
| **Fills and trades** | Markers: ● entry, ✕ exit. Provenance by **fill**: filled marker = executed (paper/demo/live), hollow = simulated (backtest). A thin connector from entry to exit is drawn by a series primitive, coloured by P&L sign (`--pnl-up`/`--pnl-down`), dashed if the trade was simulated. |
| **Stops, targets, entry** | `createPriceLine` for *current* open positions. Stop is a dashed `--critical` line labelled `SL 1.0842`. Target is dashed `--ok`, `TP`. Entry is solid `--fg-muted`, `EN`. **Historical stop moves** are a step series in a primitive, so trailing-stop behaviour is visible. The y-range is expanded to include all active levels (Fincept `ChartOverlayManager::overlay_price_range`). |
| **Regime shading** | Pane primitive drawing full-height bands with `--regime-*` at 8–10% alpha, **plus a 3 px ribbon at the bottom of the pane with a text label on hover**, so the regime is not conveyed by background tint alone. `unknown` is drawn with diagonal hatching, never as "range". |
| **Indicators** | Only the `series` overlay from the backend (for example Ichimoku components, Fibonacci levels), in panes chosen by the backend's `pane` hint. The legend shows `indicator_id`, the parameters and the dataset version. |
| **Provenance of the chart itself** | The chart header shows `dataset_version_id` and a SourceNote chip. Candles are market data, not a provenance class; overlays carry their own provenance. |
| **Closed-candle rule** | The last (forming) bar is drawn at 50% opacity with a "forming" tag if the backend sends it at all. Signals can only attach to closed bars, which the backend already guarantees. |
| **Sync** | Within the Market Intelligence workstation, up to four panels share a crosshair and time range (the TradingView sync set), with an opt-in "sync symbol" per panel group, in the style of Koyfin's colour groups. |

### 7.3 Analytics chart conventions (uPlot/SVG)

- The provenance stroke grammar from §4.4 (dashed = backtest, dotted = walk-forward, solid = OOS/holdout/executed).
- The chart legend always lists provenance chips, sample size and `as_of`.
- A mixed-provenance series is **never** drawn as one line. It is split per provenance or refused with an explanatory empty state.
- Equity curves show drawdown as a separate lower pane, not as an area under the curve.
- Every chart has a keyboard-reachable "View data" toggle that renders the underlying table, for screen-reader parity and auditability.

---

## 8. Build plan (waves)

Estimates are **engineer-days for one experienced frontend engineer**, including tests, and assume the current API contract. With AI-assisted implementation calendar time can compress, but review and test effort does not shrink proportionally. Backend asks (§6.2, §6.6) are listed separately.

| Wave | Scope | Est. (d) | Exit criteria |
|---|---|---:|---|
| **0 · Fix-now** (before the redesign; valuable on its own) | W1: stop the poll → loading reset (keep last-known-good). W3: caveat-ack checkbox, remove the hard-coded `true`. W4: remove provenance fallbacks and show `MIXED`. W10: UTC suffix. Stable keys. Regression tests for each. | **3** | New Playwright specs green. Banner never shows "MODE …" after first load. |
| **1 · Foundation** | ADRs (stack, charts, SSE). Tailwind 4 + OKLCH tokens (dark, light, CVD preset). Fonts. Owned `ui/` primitives on Base UI (Button, Dialog, Popover, Tooltip, Menu, Tabs, Select, Toast, Badge, Kbd). **Shell**: mode banner v2 + ModeFrame, rail nav, page header, status bar, inspector sheet, split panes. Theme and density switch. CSP. size-limit and axe wired into CI. Source-rules test amended to budgets. | **14** | Shell renders all existing pages unchanged inside the new chrome. axe: 0 serious. Budgets enforced. |
| **2 · Data layer** | TanStack Query client + `ViewState` with freshness. `apiFetch` kept (CSRF). OpenAPI-generated types. **Login / logout / session page, principal in shell, role gating.** nuqs URL state. **SSE client** (leader election, router, dedupe, gap resync, backoff), Zustand live store, freshness engine, status bar wiring. **Mock SSE harness for Playwright.** REST-poll fallback. | **13** | Stream tests (§6.5) green against the mock. Tom/Joe two-context test: arm in A appears in B within 1 s (against a stub stream). |
| **3 · Core components** | `DataGrid` (TanStack Table 9 + Virtual: sort, filter, column visibility and pinning, saved column sets, keyboard row nav, right-aligned numerics, row-keyed live updates, CSV export). `FigureValue v2` / `ProvenanceChip v2` / `Stat` / `StaleBadge` / `CaveatPopover`. **ConfirmDialog v2** (focus trap, radio choices, caveat checklist, server consequences). Command palette + hotkeys + shortcut sheet. SVG chart refresh (responsive, axes, focusable points, "view data"). | **14** | Grid handles 10k rows at 60 fps scroll and INP < 100 ms on a 2020 MacBook-class CPU. Dialog a11y spec green. |
| **4a · Screens: operate** (priority order) | **Command** (attention queue once the backend exists; interim client-side *display* of server alerts only) · **Risk & Exposure** (gauges, bands, kill switch v2, history) · **Fleet & Positions** (bot grid, blotter, inspector) · **System & Incidents** (services, workers, queues, broker, audit, telemetry) | **20** | Each screen: the four/five states, freshness, keyboard path, and its primary action tested end to end. |
| **4b · Screens: decide** | **Strategy Lifecycle** (ladder, candidates, evaluation, promote/demote) · **Journal** (trade list, detail, notes) | **10** | Promote flow cannot proceed without ticking each caveat. Lifecycle endpoints wired. |
| **4c · Charts and markets** | Lightweight Charts wrapper + overlay model (§7.2) + regime primitive + sync. uPlot wrappers (equity, drawdown, rolling). **Market Intelligence** (chart workstation, regimes, correlations, agents with run/dataset attribution). **Data Quality**. | **16** | Visual regression snapshots for overlays. The chart renders 5,000 bars + 500 markers with INP < 200 ms. |
| **4d · Research Lab** | Registry, experiments (virtualised), validation ladder, Parameter Lab (+ lazy ECharts heatmap if needed), hypotheses, datasets, queue-experiment action | **10** | Route budget ≤ 420 KB including the lazy chunk. |
| **5 · Polish and motion** | Motion tokens applied; Motion `LazyMotion` for sheet, panel and list layout transitions; live-tick flash; empty-state illustrations (monochrome line); copy pass (UK English, operator voice); light-theme tuning; print styles for the Journal | **6** | Design review sign-off by Joe and Tom |
| **6 · A11y and performance audit** | Keyboard-only walkthrough of every primary action. Screen-reader passes (VoiceOver/Safari, NVDA/Firefox). Contrast audit of every token pair (APCA/WCAG). CVD simulation screenshots (protan, deutan, tritan). INP profiling under a synthetic stream (50 events/s). Bundle audit. React Compiler on/off comparison. | **6** | WCAG 2.2 AA on audited flows. INP p75 < 200 ms (target < 100 ms) under stream load. LCP < 1.5 s locally. |
| **Contingency** | Integration friction and backend contract churn | **~10%** | |
| **Total frontend** | | **≈ 112–125** | |
| **Backend (separate)** | SSE endpoint + envelopes + ring buffer (4). Server consequences for promote and disarm (0.5). Attention list (1). Overlays endpoint (2–3, depending on the indicator API). Incidents read model and ack (1.5). OpenAPI hygiene (0.5). FastAPI upgrade or sse-starlette (0.5). | **≈ 10–11** | `pytest` coverage per the project testing rules |

### 8.1 Playwright coverage plan

The existing 8 specs are retained and migrated. New suites (◆ = gate for the wave):

| Suite | Asserts | Wave |
|---|---|---|
| `freshness.spec` ◆ | No return to loading after first data. Stale and disconnected keep last-known-good. `as_of` is visible. | 0/2 |
| `promote-caveats.spec` ◆ | Promote is disabled until every caveat is ticked. The request body carries `acknowledge_caveats: true` only then. Server consequences are rendered verbatim. | 0/4b |
| `provenance-mixed.spec` ◆ | A mixed dataset shows `MIXED` with counts. No chart labels a mixed series with a single provenance. | 0/3 |
| `auth.spec` ◆ | Login, logout, 401 redirect. A viewer sees disabled admin actions with reasons. The principal is shown in the shell. | 2 |
| `stream.spec` ◆ | Snapshot then delta. Seq-gap resync. Dedupe. Backoff. Two browser contexts: arm in one appears in the other. Leader election with 2 tabs → 1 stream connection. | 2 |
| `mode-frame.spec` | Frame, favicon and title per mode. Live shows REAL MONEY. Unknown disables mutations. | 1 |
| `grid.spec` | Sort, filter, column persistence in the URL, keyboard nav, right alignment of numerics, 10k-row virtual scroll, live cell update without losing selection or scroll. | 3 |
| `keyboard.spec` | Every `g`-chord. Palette open, search, open entity. `⇧K` opens (never arms) the kill switch. Focus return after dialogs. | 3 |
| `a11y.spec` (axe) ◆ | 0 serious or critical violations on every route in dark, light, compact and comfortable. | 1→6 |
| `visual.spec` | Screenshot baselines for tokens, chips, mode frames, overlay rendering, CVD preset. | 1→5 |
| `chart-overlays.spec` | Markers, price lines and regime bands appear exactly for the mocked overlay payload. A null price never draws a line at 0. | 4c |
| `perf.spec` | `web-vitals` INP collected during scripted interactions under a 50 events/s mock stream. Asserts p75 < 200 ms. | 6 |
| `responsive.spec` (existing, extended) | 390 / 768 / 1280 / 1920 widths. Comfortable density auto-selected below 1024 px. | 1 |
| `live-api.spec` (existing) | Smoke tests against the real API when `FIBOKI_LIVE_API=1` | all |

---

## 9. Facts vs assumptions

### 9.1 Verified facts (from the code, registries or cited pages on 2026-09-28)

- `apps/web` inventory, LOC, routes, dependencies, unused endpoints, and the defects W1–W10 with line references (§1). The measured 290 KB gzipped of static JS.
- The backend requires `acknowledge_caveats` and the frontend hard-codes it to `true` (`trading.py` promote; `candidates/page.tsx:48`).
- The backend pins `fastapi==0.115.6` and `uvicorn==0.34.0`. FastAPI native SSE arrived in 0.135.0 (2026-03-01). sse-starlette 3.5.0 exists.
- V2 is documented as local-first on a Mac (`docs/v2/DEPLOYMENT.md`, `ARCHITECTURE.md` §1). **This conflicts with the project instructions' "Vercel + Railway/Render"**; the V2 docs describe that split as a deliberate reversal of V1. The recommendations here work either way, but the same-origin rewrite (§3.2) and HTTP/1.1 connection limit (§6.3) assume the local-first layout.
- Package versions, publish dates and licences are from the npm and PyPI registries. The **KLineChart 10.0.0 stable date of 2026-07-10** comes from the npm `time` field. (One fetched summary of GitHub releases gave 2024 dates, which conflicts with the registry; the registry is taken as authoritative.)
- Measured tree-shaken gzip sizes: Lightweight Charts 58.0 KB, KLineChart 61.7 KB, uPlot 23.0 KB, ECharts subset 209 KB, TanStack Virtual 7.8 KB, cmdk 17.4 KB. Plotly (about 1.4 MB) and Highcharts (about 102 KB) are bundlephobia figures.
- Landscape claims cited inline:
  - Linear LCH/3-variable theming; Carbon row heights; Spectrum density options;
  - WCAG 1.4.11 3:1; Core Web Vitals thresholds; NN/g durations;
  - MDN 6-connection SSE limit and `withCredentials`;
  - TanStack Table v9 (2026-08-04); Base UI 1.0 (2026-02-06); shadcn Base UI default (July 2026); Next.js 16 features;
  - Bloomberg ASKB (Feb/Apr 2026 reporting); TradingView sync set; Koyfin 7 colour groups; TT workspace structure; Sierra Chart chartbooks; IBKR Desktop vs TWS trade-offs.

### 9.2 Assumptions and unverified items (to confirm before or during wave 1)

1. **OKLCH token values are proposals.** Every text/background and graphic/background pair must be run through a contrast checker (WCAG 2 ratio, with APCA as a second view), and the palette through protan, deutan and tritan simulation before sign-off.
2. **The Okabe–Ito-derived categorical palette** is based on general knowledge of that palette. The cited page (davidmathlogic.com) returned only metadata to the fetch tool.
3. **Bloomberg's colour-accessibility approach was not read** (403 / robots). Nothing in this brief depends on it.
4. **FreqUI's licence** was not confirmed (the GitHub API returned none). It is assumed GPL-3.0 like freqtrade. Either way only ideas are borrowed.
5. **MotiveWave**: only secondary comparison sources were reviewed. No specific pattern from it is relied on.
6. **SSE through Next.js rewrites** is assumed to stream without buffering. This must be verified in wave 2; if it buffers, the fallback is to put the API and web behind one reverse proxy (Caddy or nginx), or to connect EventSource directly to the API origin with CORS credentials.
7. **The CPU and frame-rate targets** (60 fps grid scroll, INP < 100 ms target) assume a recent Mac, which is the documented primary target.
8. **Stream topics beyond mode, kill switch and health depend on backend wiring that does not exist yet** (`ARCHITECTURE.md` §12: "No application wiring"). Until the paper worker, state store and watchdog run as processes, positions, fleet and risk topics will be `absent` or seed-labelled.
9. **The effort estimates** assume one experienced engineer, the current API shapes, and no redesign of backend read models beyond §6.6. They exclude project management and design exploration time, and are ±25%.
10. **Two instruction-level deviations need Joe's decision**: Lightweight Charts instead of KLineChart, and uPlot/SVG (plus optional lazy ECharts) instead of Plotly. The reasoning is in §7.1. If either is rejected, keep the byte-budget and "no frontend indicator computation" tests anyway.
11. **The "Portara" entry in the brief** is taken to mean CQG's historical-data product. If a different Portara UI was intended, that section needs revisiting.
