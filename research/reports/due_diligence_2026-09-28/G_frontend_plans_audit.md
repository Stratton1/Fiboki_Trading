# G: Fresh-eyes audit of the workstation, the two V2 plans and the operator documentation

**Date:** 2026-09-29. **Repository:** `/home/claude/fiboki-mac` at `a10d428` (branch `v2/integration`), working tree as found (only `apps/web/tsconfig.tsbuildinfo` and a `.hypothesis` cache modified; `_agent_briefs/` untracked). **Mode:** read-only audit; no file other than this report was edited.
**Scope:** (1) every file under `apps/web` except `node_modules` and `.next`; (2) `docs/v2/AGENTIC_INTEGRATION_PLAN.md`, `docs/v2/FRONTEND_OVERHAUL_PLAN.md` and the five reports in `research/reports/due_diligence_2026-09-28/`; (3) `USER_ACTIONS.md`, `docs/v2/OPERATIONS.md`, `docs/v2/DEPLOYMENT.md`, `docs/v2/ROADMAP.md`, `README.md`, `AGENTS.md` (there is no root `CLAUDE.md`; `apps/web/CLAUDE.md` is a one-line include of `apps/web/AGENTS.md`). Web research accessed 2026-09-29; sources in §6.3.

**Commands run in this session (all in `apps/web` unless stated):**

| Command | Result |
|---|---|
| `npm run typecheck` | clean, exit 0, 8.3 s (note: `tsconfig.json:44` excludes `tests/e2e`, so the specs are not type-checked) |
| `npm run lint` | clean, exit 0, 12.6 s |
| `npm run size` | every route within budget. Measured against the existing `.next` build (built 2026-09-29 00:51; no source file under `app/`, `components/`, `lib/` is newer than it) |
| `node scripts/first-load.mjs` | `/` 154.9 KiB of 180; other routes 150.4 to 153.6 KiB of 230; `/system/legend` 170.0 KiB; CSS 12.5 KiB gzip everywhere (unbudgeted) |
| `npm run contrast` (read-only script) | 214 of 214 token pairs pass, 4 exempt |
| `NEXT_PUBLIC_FIBOKI_API="" node --experimental-strip-types -e "import('./next.config.ts')...headers()"` | **throws** `NEXT_PUBLIC_FIBOKI_API is not a URL:` (the value `scripts/dev-up.sh:82` sets) |
| Playwright | **not run** (outside the permitted commands). `test-results/.last-run.json` says `passed`, and `USER_ACTIONS.md:5` claims "318 Playwright passed"; neither was verified here |

---

## 0. Executive summary

**The workstation's honesty contract is real and well engineered; the gating around it, the launcher that starts it, and the documents that describe it have not kept up.** In order of consequence:

1. **The desktop launcher is very probably broken since Wave 1.** `scripts/dev-up.sh:82` exports `NEXT_PUBLIC_FIBOKI_API=""` (for the same-origin rewrite), and Wave 1's CSP builder in `next.config.ts:6-13` throws on an empty value. Verified by evaluating the config with that environment; the effect on `next dev` start-up was not executed. The launcher also runs `next dev` (`dev-up.sh:85`), not the production build that every budget, CSP and Playwright test validates.
2. **The launcher's local-model wiring cannot work.** `Start Fiboki.command:45-54` detects llama.cpp on `:8080`, exports `FIBOKI_LLM_URL` (read by nothing in `src/`) and forces `FIBOKI_AGENT_CYCLES=true` without `FIBOKI_AGENT_LOCAL_MODEL` or `FIBOKI_AGENT_CYCLE_TARGET`, both of which `workers/research_runtime.py:590,665` require, so the worker refuses to start whenever a model server is running. Separately, `LocalHTTPProvider` speaks only Ollama's `/api/chat` and fingerprints through `/api/tags` (`agents/providers.py:382`); the stated target, llama.cpp's OpenAI-compatible `/v1`, is not supported at all.
3. **Wave 1's exit criteria are functionally met but not gated.** `.github/workflows/ci.yml` has no frontend job: no typecheck, lint, build, size-limit, axe or Playwright runs in CI. The plan's "size-limit and axe in CI" is therefore not met, and the ADRs Wave 1 promised (report E §8) do not exist.
4. **Five trust defects survive Wave 0/1** (each would let a stale or wrong state look fine): the status-bar worker heartbeat is green at any age (`StatusBar.tsx:80`); the status-bar "data as of" is the maximum over all payloads including `/api/health`'s own `checked_at`, so it always reads "now" (`lib/as-of.ts:28-34`); views that do not poll never become stale however old they are (`AsyncBoundary.tsx:34-49`, e.g. Overview's portfolio and risk); a validation verdict of `reject` is drawn with the green "ok" badge because the colour comes from `available`, not the verdict (`research/validation/page.tsx:15`, `research/page.tsx:50`); and after arming the kill switch the panel and the mode banner disagree for up to 30 s (`KillSwitch.tsx:82` reloads only its own fetch).
5. **Kill-switch friction is inverted.** In LIVE, *pausing* requires an 8-character reason and typing `REAL MONEY` (`ConfirmDialogLayer.tsx:75`, asserted by `mode-frame.spec.ts:124-134`), while `KillSwitch.tsx:28` claims FLATTEN needs a typed `FLATTEN` that is not implemented or tested. Safety-increasing actions should be the fastest in the product; risk-increasing ones (disarm, promote) the slowest.
6. **The colour grammar leaks.** `components/charts.tsx:21-30` still uses hard-coded hex: LIVE is loss-red `#ff4d4d`, PAPER is a green, breaches are red, the correlation heatmap is red/green; the CVD preset's profit colour is byte-identical to `--accent` (`globals.css:46` and `:180`); walk-forward and out-of-sample chips share one shape (`globals.css:995-996`); health `--critical` (hue 20) sits beside loss red (hue 27). "Green and red mean P&L and nothing else" (Legend copy) is not yet true.
7. **The frontend plan is sound in direction but under-specified where the operator value is**: the chart workstation (replay, drawings, event markers, "why did this trade happen"), an agent desk, a notification centre, a phone kill-switch view, and a production run mode for the Mac are missing or one line each. Re-estimated with those in scope: **about 150 to 170 frontend days and 22 to 28 backend days**, not 112 to 125 and 10 to 11 (§2.1).
8. **The agentic plan's architecture is right and its safety posture is exemplary, but it is written for Ollama, cost in USD and a nightly cycle.** Joe's intent is continuous agents on llama.cpp. The plan needs a runtime chapter (llama-server under launchd, slots, grammar-constrained JSON, KV-prefix discipline, speculative decoding, token/time/energy budgets instead of USD), a model-routing policy with a bake-off, an agent-authored strategy pipeline with honest trial accounting, and an explicit **tier ladder** for widening agent influence with named tests (§3.7).
9. **The operator documents contradict each other and the code.** `README.md:39` and `ROADMAP.md` §3 say `apps/web` is empty; `USER_ACTIONS` C1 and C3 ask Joe to do things commits `d587e6c` and `b43d2f2` already did; "five seeds" vs "twelve strategies"; "400-trade minimum" vs the project's "80"; `O3` says a local LLM is optional while the plan makes it central; every snapshot line quotes 2,683 tests from 2026-09-19. There is no runbook for the web app, llama.cpp, the news recorder, sleep/power on a desktop that must run continuously, upgrades, or agent incidents (§4).
10. **Backlog:** 58 prioritised items in §5. The first ten are small (0.25 to 2 days each), close every trust defect above, and should precede Wave 2.

---

## 1. `apps/web` audit after Wave 0 and Wave 1

### 1.1 What is good (keep all of it)

- **The honesty contract is structural, not stylistic.** `Figure` with mandatory `provenance` (`lib/types.ts:35-43`); `formatFigure` returns `null` for `null` so every caller must handle absence (`lib/format.ts:10-18`); `?? 0` banned twice (ESLint `eslint.config.mjs:19-41` and a source grep `tests/e2e/source-rules.spec.ts:43-57`) with a self-test that the regexes still match (`:154-163`).
- **`useApi` refresh semantics are correct and well explained**: state resets only on path change, adjusted during render to avoid one frame of the previous instrument's numbers (`lib/api.ts:144-222`). Wave 0's blanking defect is genuinely fixed.
- **Aggregates derive provenance from their rows** (`lib/provenance.ts:24-45`) and render MIXED with counts, SOURCE, or "unlabelled source" (`ProvenanceChip.tsx:68-136`). The fallback and literal bans (`source-rules.spec.ts:105-141`) are the right tests.
- **Mode is a whole-shell property**: frame, favicon, tab title, banner, status bar (`components/shell/Mode.tsx`, `ModeBanner.tsx`). "MODE UNKNOWN" disables every mutation via one flag (`platform.tsx:70`).
- **The confirm dialog is a proper audited-act surface**: server-computed consequences, radio choices with no default, per-caveat acknowledgement with the ticked codes sent (`ConfirmDialogLayer.tsx`; `candidates/page.tsx:79-104`), reason reset on every open, Escape ignored while busy, no backdrop dismissal.
- **Design tokens are disciplined**: OKLCH, the Tailwind default palette removed so `text-green-500` cannot smuggle meaning (`globals.css` `@theme inline`, `--color-*: initial`), three densities, reduced motion zeroes durations, tabular figures with slashed zero on `.num/.figure__value/.mono`, contrast script over every pair (214/214).
- **Base UI popups load on first use and prefetch when idle** (`components/ui/layers.ts`, `Shell.tsx:66-79`), which is why the shell sits at 155 KiB.
- **CSP with no inline styles and no eval** (`next.config.ts:33-52`), with Base UI's inline style disabled via `CSPProvider`; `csp.spec.ts` fails on any violation.
- **Tests encode the V1 lessons**: 16 Playwright specs, axe in both themes, CSP, keyboard focus return, responsive at 360 px, visual baselines, live-API smoke behind a flag.

### 1.2 Defects and inconsistencies, by severity

Severity: **S1** misleads an operator about state or safety; **S2** functional or accessibility defect; **S3** hygiene, dead code, drift.

| ID | Sev | Where | Finding | Fix |
|---|---|---|---|---|
| W-01 | S1 | `scripts/dev-up.sh:82`, `next.config.ts:6-13` | Launcher sets `NEXT_PUBLIC_FIBOKI_API=""`; `apiOrigin()` throws on it (verified by evaluating `headers()`). Not verified end to end against `next dev`. | Treat empty as "same origin" (`connect-src 'self'` only). Add a Playwright or node test that evaluates the config with the launcher's environment. |
| W-02 | S1 | `dev-up.sh:85` | The launcher serves `next dev`: dev CSP adds `'unsafe-eval'`, the dev overlay and HMR websockets; budgets and CSP tests never apply to what Joe actually uses. | `next build && next start` under launchd; `dev` only behind an explicit flag. |
| W-03 | S1 | `components/shell/StatusBar.tsx:74-83` | Worker heartbeat tone is `ok` whenever `/api/health` is fresh, regardless of the heartbeat age itself: a 3-hour-old heartbeat reads green "worker hb 3h ago". This is the V1 3 am failure the plan names. | Tone from server-supplied state (`ok/stale/down` with the 120 s and 300 s thresholds, as `/api/system/workers` already computes); never a client threshold. |
| W-04 | S1 | `lib/as-of.ts:13-34`, `lib/api.ts:188` | "data as of" is the newest timestamp seen on any payload, and `/api/health`'s `checked_at` (polled every 20 s) is one of them, so it always reads roughly now and masks stale datasets. | Show the *oldest* as-of among views on screen, or per-topic as-ofs in a popover; exclude probe timestamps. |
| W-05 | S1 | `AsyncBoundary.tsx:34-49`; `app/page.tsx:37-38`, `trading/portfolio`, `trading/risk`, `KillSwitch.tsx:37` | A view with no `refreshMs` is only stale after a failed refresh, which never happens. Overview's balance, risk and kill-switch panel can be hours old with no badge. | Every view declares a max age (server-supplied per topic in Wave 2); until then, mark any payload older than N minutes stale and show "loaded 2h ago". |
| W-06 | S1 | `research/validation/page.tsx:15`, `research/page.tsx:50`, `markets/data-quality/page.tsx:15` | Badge colour from `available`, not the value: verdict `reject` (a value of `validation/report.py` `Verdict`) and quality `pending` render with the green OK badge. | Map verdict and quality to tone server-side (or in one typed client map with exhaustive cases), never from `available`. |
| W-07 | S1 | `KillSwitch.tsx:66-92`, `platform.tsx:52` | After arm/disarm the panel reloads, the banner does not: "KILL SWITCH ARMED" panel beside "Kill switch disarmed" banner for up to 30 s. | Reload the shared mode handle on any kill-switch mutation now; in Wave 2 keep the dialog busy until the stream echoes (plan §5). |
| W-08 | S1 | `ConfirmDialogLayer.tsx:75`; `mode-frame.spec.ts:124-134` | LIVE PAUSE requires `REAL MONEY` typed plus an 8-character reason. Friction on the emergency brake. | Asymmetric friction: PAUSE one click plus optional reason (reason may be added after); FLATTEN a typed `FLATTEN`; disarm and promote the full ceremony. Record the change in `SECURITY_MODEL`. |
| W-09 | S1 | `KillSwitch.tsx:28` | Comment claims FLATTEN requires typing `FLATTEN`; no code or test does this. | Implement per W-08 or delete the claim. |
| W-10 | S2 | `components/charts.tsx:21-30,226,236,279-284,386` | Hard-coded hex palette: `broker_live #ff4d4d` (loss red), `paper #3fbf7f` (green), breach red, red/green correlation, `#ff6b6b` negative bins. Violates D-F and is invisible to `contrast.mjs`. | Tokens only (`var(--series-n)`, provenance stroke grammar); diverging blue/orange for correlation; a source rule banning hex in components. |
| W-11 | S2 | `charts.tsx:99-128` | `LineChart` drops `null` points and joins across them, drawing a gap as data; x is index, not time, so irregular timestamps are evenly spaced; no x-axis or time labels. | Break the path at `null` and hatch the gap; time-scaled x with UTC ticks. uPlot does both natively (Wave 3/4c). |
| W-12 | S2 | `charts.tsx` all | No keyboard-reachable "view data" table; heatmap values only in SVG `<title>` (tooltip-only information, which D-F forbids). | `<figure>` + `<details>` data table per chart; move into the Wave 3 SVG refresh. |
| W-13 | S2 | `globals.css:46` vs `:180`; `:52` vs `:59`; `:995-996` | CVD `--pnl-up` equals `--accent`; loss red and `--critical` differ by 7 hue degrees; WF and OOS chips are both solid (shape does not distinguish them, contrary to the stylesheet's own comment). | Distinct CVD hue for P&L up (e.g. 235 with higher chroma, or a teal) and a shape for OOS (e.g. solid with a corner notch or a dotted inner rule); CVD simulation screenshots in `visual.spec`. |
| W-14 | S2 | `ModeBanner.tsx:81,112` | The banner is `role="alert"` when danger or stale and contains a ticking age ("last good 40s ago"); screen readers re-announce each change. In LIVE the whole banner is permanently an alert. | Banner `role="status"`/no live role; a separate, once-only `aria-live="assertive"` announcer for *transitions* (mode change, stale onset, kill switch armed). |
| W-15 | S2 | `ui/Button.tsx:10`; `candidates/page.tsx:179-186,168-173` | Disabled Promote's reason lives only in `title`, and `disabled:pointer-events-none` means the tooltip never appears; not keyboard reachable either. Eligibility badge likewise. | `aria-disabled` + focusable button + visible reason line (or popover), per the plan's "no tooltip-only information". |
| W-16 | S2 | `Rail.tsx:48-60`, `globals.css` drawer block | Under 1024 px the closed drawer is only translated off-screen: its links stay in the tab order; no Escape, no focus move or return, no `inert`. | `inert` when closed; focus first link on open, return to toggle on close; Escape closes. Or reuse Base UI Dialog as the drawer. |
| W-17 | S2 | `globals.css` (no `scroll-padding`) | Sticky banner (44 px) and sticky status bar (28 px) can cover a focused element: WCAG 2.2 SC 2.4.11 Focus Not Obscured risk. | `html { scroll-padding-block: var(--banner-h) var(--statusbar-h) }`; a keyboard test that tabs to the last row. |
| W-18 | S2 | `trading/execution/page.tsx:59-78,57,83` | The provenance filter is rendered inside the success branch: changing it changes the path, the view goes to loading, the select unmounts and focus is lost; on error the filter disappears. The R distribution is drawn over the first 200 rows but labelled as the distribution. | Filters live outside `AsyncBoundary` (URL state via nuqs in Wave 2); aggregates come from the backend over the whole filtered set, or are labelled "first 200 of N". |
| W-19 | S2 | `ListPage.tsx:94-96`; `system/logs/page.tsx:67`, `execution` `limit=200` | "Showing N of total" with no way to see the rest. | Cursor pagination now; virtualised grid in Wave 3. |
| W-20 | S2 | `app/page.tsx:124-128` | `provenance_mix` keys cast `as never` into `ProvenanceChip`; an unknown key renders an empty chip; counts are raw numbers. | Validate against `PROVENANCES`; render unknown keys as "unlabelled"; counts as `Figure(count)`. |
| W-21 | S2 | `lib/types.ts:130-135,424-429,178-183` | Raw numbers outside `Figure`: `KillSwitchView.open_positions`, `CorrelationView.matrix`, `QueueRow.depth`, heartbeat ages. The correlation page has a TODO for this (`markets/correlations/page.tsx:26-31`). | Backend: wrap in `Figure` with provenance and `as_of`; correlation window and dataset version. |
| W-22 | S2 | `lib/format.ts:71-74,51-52` | Prices use a generic 5-decimal format with trailing zeros stripped (1.1 vs 1.08425), so columns do not align and JPY/gold precision is wrong; `-0.001%` renders `−0.00%`, asserting a loss that rounds to nothing. | Price precision from the instrument (`pip_size`) supplied by the API; sign after rounding. |
| W-23 | S2 | `alerts/page.tsx:18` | Alerts re-polls `/api/health` at 15 s instead of the shared 20 s handle (`platform.tsx` exists to prevent exactly this). | Use `useHealth()`. |
| W-24 | S2 | `lib/api.ts:213-218` | Polling continues in hidden tabs and every hook instance polls independently. | Pause on `visibilitychange` now; TanStack Query dedupe in Wave 2. |
| W-25 | S2 | `tests/e2e/fixtures.ts:254-333`; `a11y.spec.ts:53-61` | Only about nine endpoints are mocked, so the axe sweep audits the *error* state of most routes; populated tables, tiles and charts on 20 routes are not audited. Fixtures are hand-written, so type drift with the API is invisible unless `FIBOKI_LIVE_API=1`. | Generate fixtures from backend pydantic examples (or record from a seeded API) and mock every route; contract test in Python that every `lib/types.ts` interface matches the OpenAPI schema until Wave 2 generates the types. |
| W-26 | S3 | `tsconfig.json:44` | `tests/e2e` excluded: specs are transpiled by Playwright, never type-checked. | `tsconfig.e2e.json` and a `typecheck:e2e` script. |
| W-27 | S3 | `package.json:22-24` | `react`/`react-dom` pinned at 19.0.0; the plan and Next 16 docs lean on 19.2 (`<Activity>`, `useEffectEvent`). | Pin 19.2.x deliberately, with the visual and a11y suites. |
| W-28 | S3 | `source-rules.spec.ts:168` | Bans `echarts` although D-F6 permits a lazy ECharts on Research Lab. | Replace the ban list with the allow-list plus per-route budgets (already present) and a "no frontend indicator engine" rule. |
| W-29 | S3 | dead code | `DialogRoot`, `DialogClose` (`ui/Dialog.tsx:11-12`), `ALL_ROUTES` (`sections.ts:126`), `PageActions` (`PageHeader.tsx:25`, so every page header action slot is empty), `QueueRow`, `TelemetryRow` (no page uses them). `StatusPill` is used only by the Legend while 23 call sites hand-roll `badge badge--*` with per-page tone maps (services and data-health duplicate the same `kind` mapping). | Delete or adopt; migrate pages to `StatusPill`/`Badge` in Wave 3. |
| W-30 | S3 | stale comments | `eslint.config.mjs:9` cites `tests/e2e/no-zero-coalesce.spec.ts` (it is `source-rules.spec.ts`); `scripts/first-load.mjs:11` cites `.size-limit.cjs` (it is `.mjs`); `next.config.ts:62-63` says lint and typecheck are "wired into the Playwright pre-flight" (they are not, `playwright.config.ts`); `charts.tsx:10` "~250 lines", `README.md:81` "~360 lines" (399). | Fix; add a docs-citation check (AGENTS.md §0 already warns citations rot). |
| W-31 | S3 | `playwright.config.ts:18-21` | The "mobile" project is desktop Chrome at 390 px with no touch or mobile emulation, so touch paths (tooltip tap, drawer) are not exercised as touch. | Add an `iPhone 15`/`iPad` device project for a small smoke set. |
| W-32 | S3 | `next.config.ts:77-89` | Headers lack `Permissions-Policy` and `Cross-Origin-Opener-Policy`; `script-src 'unsafe-inline'` is documented as deferred to Wave 2. | Add the two headers; move to nonces when pages become per-request. |
| W-33 | S3 | Hotkeys `Hotkeys.tsx:18` | ⌘⇧D collides with a browser shortcut in some browsers (bookmark all tabs); ⌘⇧L is Firefox's library on some platforms. | Keep, but document; offer remapping once the palette exists. |

### 1.3 Accessibility summary

Good: focus rings everywhere, skip link, dialogs trap and return focus, tooltips mirror accessible names, popovers for caveats and MIXED counts, axe gates in two themes, reduced motion honoured. Gaps: W-12, W-14, W-15, W-16, W-17, W-25, W-31; no forced-colours (Windows High Contrast) rules, where hatches, fills and tinted frames vanish (chip borders survive, which is the right fallback, but the demo hatch and mode frame colours need `@media (forced-colors: active)` rules); no `lang` switches needed; no screen-reader pass recorded (planned Wave 6). The WAI-ARIA APG grid pattern should be adopted only for the interactive `DataGrid` (roving tabindex); static tables should stay `<table>` (Roselli's "grid as anti-pattern" critique applies to read-only data).

### 1.4 Performance summary

- First-load JS: the shell and Overview are at 154.9 KiB against 180 KiB; a trivial list route costs 150.5 KiB, so **the framework floor is about 150 KiB and the real headroom for Wave 2 and 3 is roughly 25 KiB on Overview.** TanStack Query, Zustand, nuqs, cmdk (17.4 KB measured in report E), TanStack Table and Virtual (Virtual 7.8 KB measured) will not all fit on Overview without route-level lazy loading (assumption: Query about 13 KB, Table about 15 KB gzip; not measured here).
- CSS is 12.5 KiB gzip on every route and unbudgeted.
- No INP, LCP or long-task measurement exists yet (planned Wave 6). The status-bar clock re-renders once a second via `useSyncExternalStore` (fine), and `useNow` ticks per stale panel (fine at today's scale; move to one shared ticker when grids go live).

### 1.5 Did Wave 1 achieve its exit criteria?

Plan §7 lists scope; report E §8 lists the exit criteria ("Shell renders all existing pages unchanged inside the new chrome. axe: 0 serious. Budgets enforced.") and the scope items (ADRs, tokens, fonts, primitives, shell, theme/density, CSP, size-limit and axe in CI, source rule amended).

| Criterion | Evidence | Verdict |
|---|---|---|
| OKLCH tokens, dark, light, CVD | `globals.css` lines 1 to 260; contrast 214/214 | **Met** (with W-13 collisions) |
| Fonts self-hosted | `layout.tsx:2-3`, `@fontsource-variable/*` | **Met** |
| Owned `ui/` primitives on Base UI | `components/ui/*` (Dialog, Popover, Tooltip, Menu, Tabs, Select, Toast, Badge, Kbd, Segmented, Sheet, SplitPane) | **Met** |
| Shell: mode frame, banner v2, rail, page header, status bar, inspector, splits | `components/shell/*`; `SplitPane` used only on the Legend; page-header entity switcher absent; `PageActions` unused | **Mostly met** |
| Theme and density switch | `DisplayPanel`, `Hotkeys`, `lib/ui-prefs.ts`, pre-paint script | **Met** |
| CSP | `next.config.ts`, `csp.spec.ts` | **Met** in production build; **breaks** under the launcher (W-01) |
| size-limit and axe **in CI** | `ci.yml` has no web job | **Not met** |
| Byte budgets replace the dependency ban | allow-list and budgets in `source-rules.spec.ts:183-230`, `.size-limit.mjs`; 300/420 KiB route budgets not yet encoded; old ban partly retained (W-28) | **Partly met** |
| ADRs (stack, charts, SSE) | none in the tree | **Not met** |
| axe 0 serious | spec exists; not run this session; most routes audited in error state (W-25) | **Unverified here; coverage partial** |
| Shell renders existing pages unchanged | 29 routes present in `sections.ts` and budgets | **Met** |

**Overall:** Wave 1 delivered the foundation. It did not deliver the gates that keep it true, and the launcher regression shows why that matters: nothing ran the configuration the operator actually uses.

---

## 2. Frontend plan critique (`FRONTEND_OVERHAUL_PLAN.md`)

### 2.1 Cross-cutting improvements

1. **Rename the waves.** Both plans use "Wave 2"; the agentic plan starts at Wave 2 and the frontend at Wave 0. Prefix them (`F2`, `A2`) everywhere, including commit messages and `BUILD_LOG.md`.
2. **Update the status block.** The plan header says "Wave 0 shipped (`c40c886`)"; Wave 1 shipped at `666f33e`. Add a "Wave 1 close-out" wave (§2.2) rather than letting its gaps leak into Wave 2.
3. **State the deployment target for the web app.** The plan (D-F1, §9) assumes a same-origin rewrite but does not say how the workstation runs on the Mac. Decide: `next build` + `next start` (or a static export behind the API) under launchd, one origin via the rewrite, dev mode never. This decides W-01/W-02 and the SSE buffering question in §9.
4. **Make freshness a server contract, not a client guess.** Every stream topic and REST envelope should carry `as_of`, `max_age_s` and `source.kind`; the client only compares. This removes W-03 to W-05 as a class and aligns with "the UI must not rank or decide".
5. **Provenance for everything with a number**, including counts, matrices, queue depths and ages (W-21). Add a backend test that walks every response model and fails on a bare `float`/`int` field outside an allow-list (ids, sequence numbers).
6. **Asymmetric friction as a design rule** (W-08): add it to §3's design language and to `SECURITY_MODEL.md`. Rank every mutation: safety-increasing (pause, acknowledge), neutral (annotate, note), risk-increasing (disarm, promote, enable a channel). Friction scales with the rank; nothing safety-increasing is ever blocked by `mutationsAllowed` except for the unknown-mode rule, and even there PAUSE should be permitted because it can only reduce risk (the backend already accepts it in every mode).
7. **Estimates.** Report E's 112 to 125 days excludes several items the operator intent now requires. Revised (one experienced engineer, ±25%): Wave 1 close-out +4; Wave 2 13 → 17; Wave 3 14 → 16; 4a 20 → 22; 4b 10 → 11; 4c 16 → 28 (full chart workstation, §2.4); 4d 10 → 11; new 4e Agent Desk 10; new 4f notifications and phone ops view 6; Wave 5 6; Wave 6 6 → 8. **Total about 150 to 170 frontend days; backend 22 to 28** (SSE 5, attention 1.5, overlays 5, replay/as-of endpoint 4, drawings store 2, incidents 2, agent desk read models 4, notifications and web push 2, OpenAPI hygiene and response-model provenance 2).
8. **Upgrade FastAPI rather than add `sse-starlette`** if the pin review allows: native SSE landed in FastAPI 0.135.0 with keep-alive pings every 15 s, `Cache-Control: no-cache` and `X-Accel-Buffering: no` by default, and `Last-Event-ID` support (FastAPI docs). One dependency fewer; decide with a full test run (D-F10 already says so).
9. **WebTransport**: now Baseline (Safari 26.4, March 2026, per webrtc.ventures) but needs HTTP/3 and QUIC; for a one-origin loopback workstation SSE remains the right choice. Record the decision in an ADR with a revisit trigger (for example, sub-second tick streaming).
10. **React 19.2 `<Activity>` and Next 16 Cache Components**: Next preserves up to three routes in hidden `Activity` (bundled Next docs, `preserving-ui-state.md`). Valuable for the chart workstation (keep zoom, drawings in progress), but a preserved route is *by definition* showing data from when it was hidden. Rule: on reveal, every view re-evaluates freshness before it is allowed to look current, and hidden routes stop streaming (effects unmount in hidden mode).
11. **Accessibility targets**: add SC 2.4.11 (focus not obscured), 2.5.8 (target size 24 px, relevant in compact density: 24 px rows need 24 px hit targets), and forced-colours to the Wave 6 audit list.
12. **Observability of the frontend**: web-vitals (INP, LCP) and client errors posted to a local API endpoint with correlation ids, so "the chart froze at 14:02" is an incident with evidence.

### 2.2 Per wave

**F0 (done).** Keep. Add the W-06 badge-tone mapping and W-18 filter placement, which are Wave-0-class trust defects missed then.

**F1 close-out (new, about 4 days; before F2).** W-01 to W-09, W-13, W-23, the CI web job (typecheck, lint, build, size, axe subset, CSP, source rules), ADRs for charts, SSE, deployment and friction, `tsconfig.e2e.json`. Exit: the launcher boots the production build; CI red on any web gate.

**F2 data layer (13 → 17 days).**
- Improvements: generate types with `openapi-typescript` and call through `openapi-fetch` (keeps `apiFetch`'s CSRF by middleware), so hand-written `lib/types.ts` goes away and drift becomes a compile error. Drive TanStack Query caches from the stream with `setQueryData` keyed by entity id; do not use `experimental_streamedQuery` for the long-lived multiplexed stream (it accumulates chunks as an array and ties stream lifetime to query state, per its docs), though it suits a single agent run's token stream in the Agent Desk.
- Leader election: Web Locks for leadership plus `BroadcastChannel` for fan-out (the pattern the plan names; open-source `sse-coordinator` and `shared-event-source` implement it and can be read for edge cases, licences to be checked before any copying). Handle leader death (lock release) and sleep/wake (Mac lid, `visibilitychange`, `pageshow`).
- Ordering change: **login and session first** (the API already enforces sessions; the UI has no login screen and the launcher prints a shared default password, see §4). Then OpenAPI types, then Query, then SSE.
- Missing: an explicit **clock-skew check** (server time in every heartbeat; warn if the browser clock is more than 2 s off, because every "age" is computed client-side today); **per-topic `absent` state** rendered as "not wired" rather than hidden.
- Risks: SSE through the Next rewrite buffering (plan §9); verify in F1 close-out with a 10-line test endpoint rather than discovering it in F2.

**F3 core components (14 → 16).**
- `DataGrid`: add row-level provenance filter chips, frozen first column, column-level "unit and provenance" header affordance, copy-as-TSV with provenance column always included, and a "diff since last view" highlight (rows changed since the operator last looked, a TT/IBKR blotter convention). Live updates must not move rows under the pointer (hold sort while hovered or keyboard-focused, show "3 updates pending" bar).
- Command palette: entity search across strategies, symbols, trade ids, incident ids, agent run ids and **evidence ids**; recent items; actions that only *open* dialogs.
- SVG chart refresh: fix W-10 to W-12 here rather than in 4c.
- Missing: a `Timestamp` component (UTC, relative age on hover and focus, exchange-session label), a `Money` component (currency code, account currency, conversion caveat), and a `Delta` component for "since last view".

**F4a operate (20 → 22).** Command, Risk & Exposure, Fleet & Positions, System & Incidents. Ordering change: **Risk & Exposure first** (the kill switch is the one control that must be perfect), then Command, then System & Incidents (so incidents exist for Command's attention queue), then Fleet.

**F4b decide (10 → 11).** Strategy Lifecycle and Journal. Add a demotion flow (the plan lists promote/demote but only promote exists in the API), and the "evidence rank" chips should show the gate set version and trade count against the **gate set's** minimum (the plan and report E say 80; `VALIDATION_STANDARD` and `DEPLOYMENT.md` §10 say 400; the UI must print the server's number, never a constant).

**F4c charts and markets (16 → 28).** The single largest under-estimate. See §2.4 for the feature list. Split into 4c-1 (chart core, overlays, sync; 12), 4c-2 (replay and "why" inspector; 8, depends on the backend as-of endpoint), 4c-3 (drawings, event markers, Market Intelligence and Data Quality screens; 8).

**F4d Research Lab (10 → 11).** Add an agent-authored candidate lane (§3.5) and a "trial budget" meter (honest trial count so far for the campaign, which drives deflation).

**F4e Agent Desk (new, 10).** §3.6.

**F4f notifications and phone ops view (new, 6).** A notification centre (in-app list, acknowledged state, server-ranked severity, deep links; Web Push to Joe's and Tom's phones optional and local-network aware), and a **phone kill-switch view**: a single route at 390 px with mode, kill-switch state, heartbeat, open risk and the PAUSE control, reachable over Tailscale or the LAN with the same session auth (no new auth path). This is the one mobile screen worth building; everything else can remain desktop-first.

**F5 polish (6).** Keep. Add a copy style check (UK English, no em dashes in UI copy to match the docs rule, operator voice).

**F6 audit (6 → 8).** Add forced-colours, SC 2.4.11 and 2.5.8, VoiceOver on macOS Safari (the actual target), INP under 50 events/s with the chart open, and a 24-hour soak test (memory growth of a tab left open overnight, which is how Joe will use it).

### 2.3 Per screen

| Screen | Improvements and missing features |
|---|---|
| **Command** | Server-ranked attention queue with *why ranked* (rule id); "what changed since you last looked" digest (trades closed, incidents, agent claims, promotions); next scheduled high-impact events from the official calendar with countdown; worker/recorder/model-server strip; a "morning brief" card produced by the agent layer but labelled commentary with evidence links; kill-switch card identical to Risk's. |
| **Fleet & Positions** | Per-bot last signal *and* last evaluated bar (a bot that has not evaluated the last closed bar is stale even if the worker is alive); managed-exit exposure per position (the `fiboki_managed_exit_exposure` gauge from `USER_ACTIONS` D2); intent/order timeline in the inspector (PENDING, UNKNOWN, FILLED with client ref and broker ref); per-position mini chart; "close position" with server consequences. |
| **Strategy Lifecycle** | Kanban ladder with gate-set version; binding constraint per card; demotion monitors (PSR against half backtest Sharpe, CUSUM, bootstrap drawdown, once built) as sparklines; backtest-versus-paper divergence overlay; agent-authored badge and parent lineage; caveat checklist unchanged. |
| **Research Lab** | Experiment ledger virtualised; research-memory search *before* the queue-experiment button is enabled (the charter's "ask memory first", enforced by UI order); trial-budget meter; holdout status per strategy (untouched, claimed, spent); parameter plateau heatmap (SVG first). |
| **Market Intelligence** | The chart workstation (§2.4); regime timeline per instrument (Gantt of regime states with dwell); correlation network view (nodes instruments, edges above a threshold, with window and dataset version; SVG force layout computed in the backend, not the browser); headline stream with first-seen UTC and source; agent claims about the symbol with evidence links. |
| **Risk & Exposure** | Limit gauges with bands; "what-if" sandbox that posts a hypothetical position to a backend dry-run of the risk gateway and portfolio construction (returns which of the 18 checks would pass or fail and the size the portfolio layer would allot; the frontend computes nothing and nothing is persisted as an order); kill-switch history; currency exposure netting view. |
| **Data Quality** | Gap timeline per instrument; last-bar age per feed with the calendar-aware expectation (a gap over a weekend is not a defect); recorder health (quotes, headlines); dataset lineage graph; licence status per source (Dukascopy paused, FRED notice). |
| **System & Incidents** | Incidents with timeline, acknowledgement and annotation (audited); process table (API, research worker, news recorder, llama-server, web) with pid, uptime, restarts, heartbeat; model server panel (loaded model, digest, slots busy, tokens/s from `/metrics`); audit log virtualised with hash-chain status; disk usage per store with growth rate. |
| **Journal** | Trade detail with the "why did this trade happen" inspector (§2.4); operator notes; tags; R-multiple and MAE/MFE; session heatmap (P&L by weekday x hour, UTC, split by provenance, never mixed); trade tape (chronological fills with provenance); export with provenance. |
| **Agent Desk (new)** | §3.6. |
| **Phone ops (new)** | §2.2 F4f. |

### 2.4 Chart workstation: detailed feature list

**Architecture.** Three layers with a hard boundary:

1. **Price core: Lightweight Charts 5.2.** Panes (price, volume if stored, backend indicator panes), series markers plugin, price lines, pane primitives, data conflation (5.1), hit testing via `hoveredItem`/`hoveredTarget` (5.2), and the accessibility plugin for keyboard navigation, ARIA and announcements (5.2) (release notes).
2. **Owned overlay layer** (`components/chart/overlays/*`): Fiboki primitives that draw only what `/api/markets/overlays/{symbol}` returns: regime bands with a labelled ribbon, session shading, stop-move step series, event markers, trade connectors, drawing objects. No indicator maths, no signal logic; a source rule forbids importing anything from a list of indicator names and bans arithmetic helpers on OHLC arrays in `components/chart/**` beyond coordinate transforms.
3. **Analytics: uPlot** for equity, drawdown, rolling metrics and backtest-versus-paper overlays, with cursor sync via a shared `sync` key across small multiples (uPlot docs), and the provenance stroke grammar.

**Backend contract (extend report E §7.2).** `GET /api/markets/overlays/{symbol}?tf=&from=&to=&as_of=` returns candles (with `dataset_version_id`, price basis and closed/forming flag), indicator series (`indicator_id`, params, pane hint), signals, fills, levels with validity windows, stop history, regimes, sessions, calendar events (official calendar with `scheduled_at`), headlines (first-seen UTC, source, url hash), and drawings. Every numeric carries provenance. `as_of` is the replay key (below).

**Feature list.**

| # | Feature | Detail | Wave |
|---|---|---|---|
| C1 | Symbol and timeframe switcher | Palette entry, `1` to `6` keys; timeframe list from the backend's supported set | 4c-1 |
| C2 | Multi-timeframe sync | Up to four panels (e.g. D1, H4, H1, M15); shared crosshair *time*; a higher-timeframe bar's span is shaded on lower panels; link groups by colour (Koyfin's seven groups; TradingView linked tabs now also sync time ranges) | 4c-1 |
| C3 | Crosshair sync to analytics | Hovering a date on the equity curve moves the price crosshair and highlights the trades open at that moment | 4c-1 |
| C4 | Signal, fill and trade overlays | ▲/▼ markers for signals on the closed bar; ● entry ✕ exit, filled for executed provenances, hollow for simulated; connector coloured by P&L sign, dashed if simulated | 4c-1 |
| C5 | Stops, targets and entry | Price lines for open positions; historical stop moves as a step series; later TP legs shown as pending with "managed by process" label (the managed-exit exposure caveat) | 4c-1 |
| C6 | Regime bands | Full-height tint plus a 3 px ribbon with text; `unknown` hatched, never "range" | 4c-1 |
| C7 | Session shading | Tokyo/London/New York, rollover hour, weekend gaps, all from the backend session calendar (UTC with local label on hover) | 4c-1 |
| C8 | Event markers | Official calendar events as vertical rules with impact glyph and blackout window shading (what the gateway actually used, from the paper summary); headlines as small ticks at first-seen time, clustering at zoom-out; click opens the source row | 4c-3 |
| C9 | Replay ("time machine") | Bar-by-bar stepping where each step requests the backend state *as of* that bar close: candles up to t, indicator values as computed then, signals and decisions made then, regime then, gateway verdicts then. The frontend never recomputes anything; it asks. Controls: step, play at 1, 2, 5 bars/s, jump to next signal, next trade, next event, next veto. A banner "REPLAY as of 2026-03-08 13:00 UTC" replaces the live look entirely so replay can never be mistaken for now | 4c-2 |
| C10 | "Why did this trade happen" inspector | For a trade id: the DSL rule tree with each predicate's value at the signal bar (from the backend evaluator trace), the portfolio construction steps with each scaling factor (13-step pipeline already records them), the 18 gateway checks with pass/fail and values, the order intent timeline, the fill and costs; each item carries provenance and links to the dataset version and strategy content hash. For shadow channels, show "would have been vetoed by EventVetoPolicy vX, annotation id Y" | 4c-2 |
| C11 | Drawing tools, persisted server-side and audited | Horizontal level, trend line, rectangle zone, text note, Fibonacci levels **as drawings only** (the drawing stores anchor prices and times; the ratios are display constants, not signals, and are never fed to strategies). Stored via `POST /api/markets/drawings` with author, created/updated at, symbol, timeframe, anchors and style; every create, edit and delete lands in the operator audit chain; soft delete with history; shared between Joe and Tom with authorship shown | 4c-3 |
| C12 | Forming bar | 50% opacity, "forming" tag, never carries a signal | 4c-1 |
| C13 | Gaps and missing data | Missing bars shown as gaps with a hatch and "no data" tooltip, never interpolated | 4c-1 |
| C14 | Data provenance header | Dataset version, price basis (bid or mid), source kind, as-of | 4c-1 |
| C15 | Keyboard and screen reader | Accessibility plugin; arrow keys move the crosshair bar by bar; `Enter` on a marker opens the inspector; "view data" table of the visible range | 4c-1 |
| C16 | Performance | 5,000 bars and 500 markers with INP < 200 ms (plan); conflation for 20+ years of H1 | 4c-1 |
| C17 | Snapshot | PNG export stamped with symbol, timeframe, as-of, dataset version and provenance of every overlay (for the Journal) | 4c-3 |
| C18 | Compare | Overlay a second instrument normalised to 100 (backend-normalised) | later |

**Tests.** `chart-overlays.spec` (every overlay kind from a mocked payload; a `null` price never draws a line at 0), `replay.spec` (the banner, the as-of parameter on every request, no request without it while in replay), `drawings.spec` (create, edit, delete produce audit rows; two contexts see each other's drawings), a source rule forbidding indicator computation under `components/chart/**`, and visual baselines per overlay in both themes and CVD.

### 2.5 Design system for dense dashboards

- Keep the 24/32/40 px densities (they match Carbon's compact/short/default table rows, per Carbon's data-table style guidance) and add **row-height-aware hit targets** so compact still meets SC 2.5.8 through padding.
- Add a **number rendering spec**: tabular figures (done), fixed decimals per unit from the server, thousands separators, real minus (done), sign-after-rounding (W-22), right alignment including headers (the `ListPage` headers are left-aligned while the cells are right-aligned).
- **Motion**: the plan's 600 ms tick flash should use a background change plus a 1 px outline for users who cannot see the hue change, and must be suppressed for P&L totals (flashing money encourages fixation).
- **Empty, absent, stale, disconnected, loading, error, forming, replay**: eight view states, not five, each with a token and a legend entry.

---

## 3. Agentic plan critique (`AGENTIC_INTEGRATION_PLAN.md`)

### 3.1 Status reconciliation (the plan is behind the code)

| Plan item | Code state (verified by reading the tree) |
|---|---|
| A2: real local provider, weights pin, manifest, evals, forecast record and scorer | Shipped in `936d65b` (`agents/providers.py`, `agents/manifest.py`, `agents/evals/*`, `record_forecast`/`query_forecast_scores` in `agents/tools.py:2313-2831`) |
| A2: research handlers and nightly cycle; calendar into gateway and campaign | Shipped in `145e004` (`workers/research_runtime.py`) |
| A3: headline recorder and six macro providers | Shipped in `892c958` |
| A3: bar-indexed entry locks | Shipped in `f4e1835` |
| A3: `READ_NEWS_SNAPSHOT` and `query_news` | **Not present** (no such capability in `agents/capabilities.py`) |
| A3: ops Telegram bot | **Not present** (Telegram only as an alert channel in `obs/alerts.py`) |
| A4: `event_classifier`, veto, brief, debate, conviction | Not started |
| First real-model run | **Not evidenced**; `USER_ACTIONS` P7 still asks Joe to run the smoke test |

**Improvement:** keep §3 of the plan as a living "shipped" table with commit ids, and move the plan's status line to "A2/A3 largely shipped; A4 not started".

### 3.2 The runtime gap: llama.cpp is the target, the code targets Ollama

- `LocalHTTPProvider` defaults to `http://127.0.0.1:11434` and `path="/api/chat"` (`providers.py:378-382`), maps `json_schema` to Ollama's `format`, and obtains a digest from `/api/tags` or `/api/show`, refusing to run without one. llama-server exposes `/v1/chat/completions` (with `response_format` carrying a JSON schema, or a raw `grammar`), `/props`, `/slots` and Prometheus `/metrics` (llama.cpp server README). It has no digest endpoint.
- **Add `LlamaCppProvider`** (or a `dialect` on the existing class): `/v1/chat/completions`; `response_format: {"type":"json_schema","json_schema":{"schema":...}}`; `cache_prompt: true`; optional `id_slot`; `seed`; `n_probs` off. **Pinning**: sha256 of the GGUF file computed once at start-up from the path `/props` reports (or from launchd configuration), plus the llama.cpp build commit (`/props` and `--version`), the chat template hash, the sampler parameters, `--ctx-size`, and the draft model's sha256 if speculative decoding is on. Refuse to run if the file hash cannot be computed, exactly as the Ollama path refuses without a digest.
- **Fix the launcher contract**: one set of variables (`FIBOKI_AGENT_LOCAL_URL`, `FIBOKI_AGENT_LOCAL_MODEL`, `FIBOKI_AGENT_LOCAL_DIALECT=llamacpp|ollama`), and the launcher must never turn `FIBOKI_AGENT_CYCLES` on implicitly: a detected model server is not an operator decision to run agents.
- **Cost model**: `SessionBudget.max_cost_usd` and `budget_usd=0.25` (`agents/session.py:73,294`) are meaningless at zero marginal cost. Budgets must be **tokens in and out, wall-clock seconds, slot-seconds and an energy estimate** per run and per day, with USD retained only for remote research roles.

### 3.3 Per wave

**A2 (agents on real models, read-only).** Missing: (a) llama.cpp provider (§3.2); (b) a **first-run acceptance protocol**: 20 recorded runs of `run_failure_investigation` and `run_research_cycle`, eval pass rate, schema-refusal rate, median and p95 latency, tokens, all written to a dated report; (c) **model bake-off harness** (§3.4.5) so the model choice is evidence, not a blog table; (d) `ModelRouter` still sorts by cost and locality (`providers.py:957-1000`); it should consume eval and forecast scores per task class as the plan says, with a minimum-evidence rule before a score may change routing.

**A3 (data the agents read).** Missing: `query_news` (the one tool the event classifier needs is absent, deliberately so for the classifier, but the research roles need it); headline **licence metadata per row** (source, terms, redistribution flag) so the UI can refuse to display what the licence forbids; **time integrity**: the value of `first_seen` depends on the Mac's clock, so record NTP offset at each poll and alert on drift above one second; retention and size limits for the headline store; the Telegram bot should be deferred behind the phone ops view (F4f), which needs no third-party chat service and reuses session auth.

**A4 (bounded channels, shadow only).** Improvements: (a) the classifier's schema should be generated from the pydantic model and compiled to a grammar, with an enum for every categorical field, integer severity 0 to 3, and `confidence` as an integer 0 to 100 (llama.cpp's grammar converter supports numeric bounds for integers only, per its grammar README); (b) run the classifier with **two models from different families** and store both; disagreement is itself a feature for the shadow evaluator and a cheap robustness check; (c) record the classifier's input hash and the headline's licence flag in each `EventAnnotation`; (d) add a **deterministic unscheduled-event proxy** baseline (spread spike, realised-volatility jump) as a first-class policy that can ship on its own if it wins; (e) for `ThesisDebate`, fix the evidence pack's size so it fits the smallest routed context window with margin, and put the static part first for KV reuse (§3.4.4); (f) pre-registration artefacts should be hash-stamped into the audit chain so the decision date cannot be moved silently.

**A5 (forward evaluation).** Add interim *safety* monitoring (not interim efficacy peeking): a data-quality stop (classifier outage, schema-refusal rate above a threshold) that pauses shadow collection without unblinding outcomes; state the minimum detectable effect for the pre-registered event count so a null result is interpretable.

**Never list.** Keep verbatim. Add: "an agent choosing which strategies are evaluated on the holdout" and "an agent editing the pre-registration after data collection starts".

### 3.4 Making agents run continuously and usefully on llama.cpp

**3.4.1 Topology.** One `llama-server` per model under its own LaunchAgent (KeepAlive, throttle, logs to `var/llm/*.log`), bound to 127.0.0.1, `--metrics` on, `-np` parallel slots sized to the roles that run concurrently (typically 2 to 4), `--ctx-size` = slots x per-slot context, `-fa auto`, `--jinja` only if tool calling is used (Fiboki's workflows inject evidence and need no tool loop for the classifier). A small **agent scheduler process** (the existing research worker, or a sibling `fiboki worker run agents` with its own lease) owns queues and budgets; the API only reads.

**3.4.2 Scheduling.** Replace "one nightly cycle" with four queues and a budget-aware dispatcher:

| Queue | Trigger | Examples | Priority |
|---|---|---|---|
| Event | new headline, calendar release, alert | `event_classifier`, `failure_investigator` | highest; latency target under 60 s from first-seen |
| Bar-close | H1/H4/D1 close per instrument | regime commentary, brief refresh, debate (A4) | high; must finish before the next close |
| Research | continuous background | research cycle, hypothesis generation, critique | low; pre-emptible |
| Maintenance | daily | eval runs, forecast scoring, memory compaction, drift canaries | low |

Rules: never run research generation in the five minutes before an H1 close if bar-close work is queued; cap GPU memory so the deterministic system never swaps (`workers/scheduler.py` already refuses admission under memory pressure; extend it to read llama-server `/metrics` slot occupancy); back off on thermal pressure and on battery (desktop: on UPS event).

**3.4.3 Context budgets.** A per-role table in code, enforced before the request (the provider already refuses a prompt larger than `num_ctx`):

| Role | Input budget | Output budget | Notes |
|---|---|---|---|
| event_classifier | 1.5k | 200 | one headline plus fixed instructions; no strategy IP |
| thesis_advocate | 8k | 1k | evidence pack plus opponent's claims |
| thesis_arbiter | 10k | 400 | brief plus transcript |
| research roles | 12k | 2k | memory summaries, never raw ledgers |
| failure_investigator | 8k | 1.5k | alert, logs excerpt, backtest summary |

Budgets are versioned in the run manifest, so a changed budget changes the manifest hash (the plan's A2 item already hashes prompts and schemas).

**3.4.4 Structured output and KV reuse.**
- **Grammar-constrained JSON everywhere**: every role's output is a pydantic model with `extra="forbid"`; the schema is sent as `response_format.json_schema`; llama.cpp converts it to GBNF, defaults to no additional properties, and supports `minLength`/`maxLength`, integer bounds, `minItems`/`maxItems`, anchored `pattern`, and local `$ref` (nested and remote refs are broken; `if/then/else`, `not`, `uniqueItems`, `prefixItems` unsupported) (llama.cpp grammar README). The existing code already inlines local `$ref`s for Ollama (`providers.py:359-361`); reuse that. Validate again with pydantic after decoding: the grammar guarantees shape, not meaning.
- **Schema design rules**: enums rather than free strings; ids as anchored patterns (`^ev_[0-9a-f]{12}$`) so a claim can only cite a well-formed evidence id; bounded arrays; avoid long chains of optional fields (the README warns of very slow sampling for `x? x? x?` patterns; prefer `x{0,N}`).
- **KV cache reuse**: keep the static prefix identical and first (system prompt, role remit, schema description, fixed instrument metadata), append variable evidence last; pin a role to a slot with `id_slot` rather than relying on the similarity heuristic (the llama.cpp KV-reuse tutorial recommends explicit slot management and pre-warming); use `--slot-save-path` to persist warmed slots across restarts. Known issue: slot save/restore does not persist the draft model context under speculative decoding (llama.cpp issue #28619), so measure before combining the two.

**3.4.5 Model routing by task and model choice.**
- Route by task class (the enum exists) with measured scores. Candidate families to bake off, **not recommendations until measured on Fiboki's eval set**: a 4B to 12B instruct model for classification; a 20B to 35B model (dense or MoE) for research and debate; a different family for the critic and the arbiter so correlated errors are less likely. Report D §5.4 lists RAM tiers (16 GB: gpt-oss-20b in MXFP4 or 9B to 12B models; 32 GB: 27B to 31B; 64 to 128 GB: gpt-oss-120b at Q4 to Q6) from a secondary source; a measured June 2026 benchmark on an M5 Max 128 GB reports Qwen3.6-35B-A3B at 93 tok/s without and 105 tok/s with MTP speculative decoding in llama.cpp, and the dense Qwen3.6-27B at 18 rising to 32 tok/s with MTP, with llama.cpp 10 to 24% faster than MLX on those models (stared/benching-local-llms-on-apple-silicon).
- **Implication**: speculative decoding (MTP or a draft model via `-md`) is worth up to +75% on dense models and little on MoE; MoE models are the throughput choice for continuous agents; the Metal wired-memory ceiling defaults to roughly 78% of RAM and can be raised with `sysctl iogpu.wired_limit_mb`, which resets on reboot (ModelPiper) and therefore belongs in the llama.cpp runbook, not in a permanent daemon.
- **Function-calling reliability** matters less than it seems because Fiboki's workflows inject evidence and require JSON only; where tools are used, evaluate with a BFCL-style harness (Berkeley Function Calling Leaderboard v4 methodology) on Fiboki's 25 tool schemas, and record tool-call validity per model and quantisation (report D notes quantisation degrades tool-call JSON reliability).
- **Bake-off harness**: fixed prompts from recorded runs, three seeds, per model and quantisation: schema validity, eval pass rate, claim-citation validity, latency p50/p95, tokens/s, memory, and energy (from `powermetrics` sampling, optional). Output a dated report and a routing table proposal; the router changes only when a human accepts it.

**3.4.6 Memory.** Three kinds, each owned by deterministic code: (a) institutional research memory (exists: exact, structural and similarity keys); (b) **episodic run summaries**: after each run a schema-bound summary (claims, evidence ids, outcome) is stored and retrievable by instrument, role and date; agents never read other agents' free text, only these structured summaries (the plan's "no free-text hand-offs" rule, made operational); (c) **forecast track record** per role and model, fed back into prompts only as numbers ("your last 50 forecasts: Brier 0.21"), never as prose from past runs, to limit self-reinforcing narratives.

**3.4.7 Evaluation loops.**
- Offline evals on every run (exist: citations, no trade instructions, provenance stamped, weights pinned, budget respected). Add: evidence-id validity (every cited id resolves and its content hash matches), numeric fidelity (every number in prose equals a number in the cited evidence within rounding), refusal correctness (a prompt that asks for an order must be refused), and injection canaries (headlines containing instructions must be stored as data).
- **Online**: forecast scoring after `horizon_end_at` (exists), calibration curves per role and model, and drift canaries (a fixed daily prompt whose output is compared across days; a change without a model change is an alarm).
- **Human-in-the-loop labels**: a weekly sample of 20 outputs rated by Joe or Tom in the Agent Desk (two-click rubric) to calibrate any LLM-as-judge; Bloomberg's CTO describes evaluations with domain specialists and human assessors as "the make-or-break" of ASKB (Fortune, 2026-04-28).
- External reference for forecasting ability: ForecastBench runs contamination-free forecasting rounds every two weeks (questions resolve after model cutoffs); use its methodology, not its leaderboard, as the template for Fiboki's forecast record.

**3.4.8 Guardrails.** The plan's tool-less classifier is the **context-minimisation** and **dual-LLM** patterns from "Design Patterns for Securing LLM Agents against Prompt Injections" (Beurer-Kellner et al., 2025): make that explicit and apply the **map-reduce** pattern to headline batches (each headline classified in isolation, only enums aggregated) and **plan-then-execute** to research cycles (the tool plan is fixed before any untrusted text is read). Keep: no execution capability, AST tests, audit chain, schema-only outputs. Add: per-role allow-list of *which* evidence stores it may read (strategy IP never reaches a role that reads untrusted text), and an output **sanitiser** that strips URLs and markdown links from any prose shown in the UI (links come only from evidence ids).

### 3.5 Agent-authored strategy candidates: the pipeline

Goal: agents may propose, the deterministic pipeline decides, the operator approves. Every stage writes an artefact with a content hash; every candidate, including rejected ones, increments the honest trial count.

| Stage | Owner | Gate (all deterministic unless stated) |
|---|---|---|
| S0 Brief | `research_director` (LLM) | Must cite research-memory query ids showing the question is not already answered |
| S1 Hypothesis | `quant_researcher` (LLM) | Mandatory mechanism, **evidence against**, falsifier, expected sign and rough magnitude; schema-bound |
| S2 DSL draft | strategy author role (LLM, grammar-constrained to the `StrategyDocument` JSON schema) | Parses with `extra="forbid"`; no free-text code anywhere (the DSL is data) |
| S3 Compile and static checks | `strategy/compiler` | Compiles; warmup declared; mandatory stop; complexity at or below a cap; parameter count at or below a cap; `is_reparameterisation` against the registry returns false; structural-hash and AST-similarity novelty above a threshold (the AlphaAgent originality and complexity regularisers, applied to Fiboki's DSL); hypothesis-to-rule alignment checked by a *different-family* critic model (advisory flag only) |
| S4 Smoke backtest | `backtest_handler` | Development slice only (never validation or holdout windows); fixed small bar budget; must produce at least a minimum trade count on the slice or it is rejected as untestable, not as bad |
| S5 Trial accounting | `validation` | Candidate registered with campaign id; `external_trial_count` updated from the campaign ledger automatically (closing the charter's "nothing can enforce this" gap for agent candidates); per-campaign quota (for example 20 candidates per week) so the deflation bar does not run away |
| S6 Ladder | validation ladder | Unchanged rungs and gates; holdout claim before evaluation |
| S7 Critique | `critic` (LLM, different family) | Must cite the validation report id (eval exists); advisory |
| S8 Human review | Joe or Tom in the Agent Desk | Diff against parent document, lineage, trial budget consumed, binding constraint, critic notes; approve to "candidate" lifecycle or reject with reason (audited) |
| S9 Lifecycle | existing promotion flow | Same caveat checklist as any strategy; "agent-authored" provenance on the document and every downstream figure |

Kill criteria: a campaign whose agent candidates' pass rate at rung 1 falls below the base rate for human candidates over a pre-registered window is paused; three consecutive S3 novelty failures from one role trigger a prompt review. UI: an "agent-authored" badge that survives into lifecycle, portfolio and journal views.

### 3.6 The Agent Desk (operator UI)

One screen, five tabs, all read models server-side; nothing on it can change trading state.

1. **Runs.** Timeline of runs by queue and role; status, model id and digest, manifest hash, tokens in/out, wall time, slot, queue wait, energy estimate, eval verdicts (PASS, FAIL, NOT_EVALUABLE, never blank). A run detail shows each step's schema-bound output and the tool calls with arguments and result hashes; `experimental_streamedQuery` can stream a live run's steps into the view.
2. **Claims.** Every claim as a row: text, role, run, confidence, and **evidence links** that resolve to the tool call id, its arguments, the dataset version and the content hash, with "re-run this query" (ASKB's pattern of citing the BQL formula for structured data and the highlighted excerpt for documents, per Hedge Fund Alpha, 2026-09-09, applied to Fiboki's tool calls and headline store). Claims with an unresolved or mismatched evidence id are shown struck through with the eval finding.
3. **Forecasts.** Open forecasts with horizon countdown; resolved forecasts with outcome; Brier score and calibration plot per role and model (uPlot); comparison against a naive base rate. Never mixed with trading provenance.
4. **Channels (shadow ledger).** For event veto and conviction dampener: every would-have-vetoed or would-have-dampened decision, the counterfactual P&L once the trade closes, running totals against each pre-registered baseline, the pre-registration hash and decision date, and a "days until decision" meter. Clearly labelled SHADOW; figures carry `Provenance.SHADOW`.
5. **Candidates and models.** The S8 review queue (§3.5); the model registry (loaded models, digests, quantisation, context, slots, bake-off results); budgets used today versus limits; drift canary status.

Controls allowed: pause or resume the *agent scheduler* (audited; this cannot touch trading), acknowledge a run failure, rate an output for evaluation, approve or reject a candidate at S8 (audited with reason). Controls forbidden: anything that changes a policy constant, a channel's `enabled` flag or a tier (those are deploy-time acts, §3.7).

### 3.7 Cardinal-rule tiering: widening agent influence consciously

Principle: agent influence is a **tier** stored as a versioned, signed deploy-time record (like the live-execution authorisation record), never an API toggle. Each tier adds one bounded capability, requires a pre-registered test to pass before promotion, and demotes automatically on named failures. Test names below are **proposed**; the first two already have equivalents in the tree (the capability enum guard and the layering/AST tests).

| Tier | Agent influence | Consumer and bound | Entry requirement | Named tests (proposed) | Automatic demotion |
|---|---|---|---|---|---|
| **T0 Observe** (today) | Research artefacts, notes, forecasts | None on trading | Default | `test_agents_import_nothing_from_execution_layers`, `test_capability_enum_has_no_execution_noun`, `test_no_job_type_can_express_execution` | n/a |
| **T1 Annotate (shadow)** | `EventAnnotation`, `ConvictionReading` written | Shadow evaluator only; policies `enabled=False` | Pre-registration filed and hashed | `test_annotation_contract_has_no_price_size_or_free_text`, `test_policies_default_disabled`, `test_shadow_writes_only_shadow_provenance` | classifier outage or schema-refusal rate above threshold pauses collection |
| **T2 Veto new entries** | Block new entries during classified severe unscheduled events | `EventVetoPolicy`, veto-only, missing annotation = no veto plus alert | Pre-registered test beats the deterministic spread/volatility proxy; operator signature | `test_event_veto_only_blocks_new_entries_never_exits_or_sizes` (AST plus property), `test_missing_annotation_never_vetoes`, `test_veto_stamped_on_every_risk_decision` | veto rate above a ceiling per day, or realised benefit below baseline over a rolling window, returns to T1 |
| **T3 Dampen size** | Reduce size when debate disagrees | `ConvictionPolicy`, factor in [floor, 1.0], stale = 1.0 | As T2, plus a floor no lower than 0.5 | `test_conviction_factor_never_exceeds_one` (property), `test_conviction_consumed_only_in_step_conviction` (AST), `test_stale_conviction_is_neutral` | same pattern |
| **T4 Author candidates to paper** | Agent-authored strategies may reach paper through §3.5 | Same ladder, same gates, human approval at S8 | Campaign trial accounting automated; novelty and complexity gates live | `test_agent_candidate_increments_trial_count`, `test_agent_candidate_cannot_touch_holdout_before_claim`, `test_agent_authored_provenance_propagates` | pass-rate kill criterion (§3.5) |
| **T5 Protective suggestions** (optional, later) | Suggest tighter stops or an earlier exit on an open position | **Human-confirmed only**, one click in the phone ops view; deterministic check that the suggestion strictly reduces risk | Separate pre-registration; T2 or T3 has been stable for a pre-set period | `test_protective_suggestion_can_only_reduce_risk`, `test_suggestion_requires_operator_action` | any suggestion that would increase risk is a critical incident and demotes to T4 |
| **Never** | Order origination, upsizing, risk-limit changes, kill-switch disarm, execution-mode changes, holdout selection | n/a | n/a | the existing structural mechanisms plus `test_tier_record_cannot_express_never_items` | n/a |

Mechanics: `AgentInfluenceTier` enum in `core` (so `agents/` can read but not write it); tier record in `var/` signed by the operator (same pattern as the live authorisation record, with timestamps and name); the tier is stamped on every allocation and risk decision alongside the policy versions; `fiboki agents tier status` prints it; the Agent Desk shows it read-only; raising a tier requires a commit that changes a reviewed constant **and** the signed record, lowering it requires only the record (safety-asymmetric, like the kill switch).

### 3.8 Other missing pieces

- An **agent incident runbook** (model server down, schema refusals spike, injection canary tripped, drift canary tripped, disk full from logs).
- **Prompt and schema registry** with versions and diff view (the run manifest hashes them; nothing shows them).
- **Red-team corpus** of adversarial headlines and research prompts, run in CI against the offline provider and weekly against the real model.
- **Model update policy**: a new model or quantisation is a new router candidate that must pass the bake-off; forecasts and shadow ledgers are stratified by model digest so a mid-period model change cannot contaminate a pre-registered evaluation (the pre-registration pins the model; enforce it).
- **Data licensing in the UI**: FRED's required notice wherever FRED data is shown (USER_ACTIONS P6), and vendor flags respected.
- **Energy and heat**: continuous local inference on a desktop is a real running cost and a thermal load; report energy per day in the Agent Desk.

---

## 4. Operator documentation critique

### 4.1 Contradictions and stale statements

| Doc and line | Statement | Reality (verified) |
|---|---|---|
| `README.md:9`, `OPERATIONS.md:3-5`, `DEPLOYMENT.md:3-4`, `ROADMAP.md:3-10` | "2683 passed, 2 skipped" (2026-09-19) | `USER_ACTIONS.md:5` says 3,923 backend and 318 Playwright; plan §3 says 3,604. None re-verified here. Snapshot lines should carry a date and commit and be regenerated, not hand-edited |
| `README.md:39` | "`apps/web/` is empty" | 29 routes, 16 specs, Wave 1 shipped |
| `ROADMAP.md` §3 "Frontend" row | "exists and is empty; V2 has no operator UI" | as above |
| `ROADMAP.md` §2 "Research job handlers" | "wiring that registers them is not written" | `workers/research_runtime.py` registers them when `FIBOKI_AGENT_CYCLES` is on (`OPERATIONS.md:99-103` says so) |
| `ROADMAP.md` §4 last row | "`marketstate/calendar.py` ships NO dated events" | 339 dated official events since `c06e284`, wired into gateway and campaigns since `145e004` |
| `ROADMAP.md` §5 item 4, §6 | "157 paths uncommitted ... Commit the tree" | committed |
| `ROADMAP.md` §1 | "41 registered instruments" | project brief says 60 canonical / 67 registered; reconcile against `core/instruments` |
| `USER_ACTIONS.md` C1, C3 | Merge V2 into `Fiboki_Trading`; remove the committed live flag | done in `d3235e8`..`bac4a01` and `b43d2f2` |
| `USER_ACTIONS.md` D2 vs O4, `AGENTS.md:108-111`, `ROADMAP.md` §6 | "each of the twelve strategies" vs "V2 ships five seeds" | pick one; the project brief says 12 bots |
| `USER_ACTIONS.md` F3, `DEPLOYMENT.md` §10, `VALIDATION_STANDARD` vs project instructions and report E §5.2 | 400-trade minimum vs 80-trade minimum | the UI must print the gate set's number; the docs must say which applies to ranking and which to promotion |
| `USER_ACTIONS.md` O3 vs plan D-A6 and operator intent | "Local LLM entirely optional, nothing depends on it" | the plan and Joe's intent make continuous local agents central |
| `USER_ACTIONS.md` P7 | filed under FUTURE with a P (paper) number; Ollama only; `qwen2.5:7b-instruct` | the target is llama.cpp on :8080; the model is a 2024 model; should be a numbered setup step with the bake-off |
| `OPERATIONS.md:28-31` | "nothing here has served a request outside a test client and no deployment exists" | the launcher serves the API to a browser daily |
| `OPERATIONS.md` §2 table, `README.md:72` | seven command groups | `calendar` and `news` groups exist (`USER_ACTIONS` P3, P6) |
| `OPERATIONS.md` §12 | "No market data in this repository"; "no scheduled reconciliation"; nothing on startup reconcile | `0a0e2ef` populated measurements; `8abd4bf` added a fail-closed startup reconcile |
| `OPERATIONS.md` §9 backup table | lists stores as of 2026-09-19 | missing: headline store, macro vintages, forecast record, `agents/schedule.json`, entry-lock state, drawings (future), model files and their hashes, `var/paper`, `var/alerts.jsonl`, `var/incidents.jsonl` |
| `DEPLOYMENT.md` §2 | LaunchAgent "stops at logout ... drains the battery in a bag" (laptop framing) | the target is now a desktop that must run continuously; sleep, power and auto-restart policy are undocumented |
| `DEPLOYMENT.md` §5 | environment table | lacks `FIBOKI_OPERATORS`, `FIBOKI_AGENT_*`, `FIBOKI_PAPER_ROOT`, `FIBOKI_INCIDENT_LOG`, news and FRED keys, `NEXT_PUBLIC_FIBOKI_API`, `FIBOKI_API_PROXY_TARGET` |
| `DEPLOYMENT.md` §7 | CI jobs | no frontend job exists; the table should say so until one does |
| `AGENTS.md:150` | venv at `/home/claude/v2/.venv` | the repo is `fiboki-mac` with `.venv` at the root; on the Mac it is `~/Documents/Claude/Projects/Fiboki/.venv` |
| `README.md:16`, `SECURITY_MODEL` (not read in full) vs `apps/web/README.md:16`, `dev-up.sh:8,20` | operator passwords as unsalted sha256 in `FIBOKI_OPERATORS`; the launcher gives Joe and Tom the same default password `fiboki-dev` and prints it | use a salted KDF (scrypt or argon2) and per-operator secrets from the macOS Keychain; never print passwords |
| `apps/web/AGENTS.md` | Next's generated agent rules only | no pointer to the workstation's own rules (`apps/web/README.md` "The rules this app exists to keep") |
| `BUILD_LOG.md` headings | "(uncommitted working tree)" for entries since committed; no F0/F1 entries | update headings; add frontend entries |

### 4.2 Missing runbooks

1. **Mac desktop install** (from a clean machine): Homebrew, Python 3.11 venv with constraints, Node 22, `npm ci`, data store migration (`scripts/migrate-store.sh`), research ledger build, operators' credentials in Keychain, launch agents for API, web (production build), research worker, news recorder, llama-server; `pmset` settings (no sleep on AC, restart after power failure, wake for network access), FileVault and auto-login trade-off, firewall (loopback only), Time Machine exclusions for model files and caches.
2. **llama.cpp setup**: install (Homebrew or build at a pinned commit), model download with sha256 recorded, `llama-server` flags per role (§3.4.1), `iogpu.wired_limit_mb` guidance and its reset at boot, slots, `--metrics`, smoke test via the Fiboki provider, bake-off, how to change models without contaminating a pre-registered evaluation.
3. **Backup and restore**, rewritten for the current stores (§4.1), with a `fiboki backup` command (the roadmap gap) using `sqlite3 .backup`, a restore rehearsal checklist, and hash-chain verification; offsite copy; retention.
4. **Incident response** keyed by alert event: `WORKER_DOWN`, `HEARTBEAT_STALE`, `RECONCILIATION_DIVERGENCE`, recorder stopped, calendar expiry approaching (the fixture ends 2026-12-04), model server down, agent eval failures, disk nearly full, clock drift, audit chain broken, UI shows MODE UNKNOWN. Each with: what it means, first three commands, when to PAUSE, who to tell, how to close.
5. **Upgrade**: `git pull`, constraints install, lockfile verify, golden tests, full suite, `npm ci`, production build, size and a11y gates, restart order, rollback; model upgrade as its own procedure.
6. **Kill-switch drill** (USER_ACTIONS L4 asks for one; no procedure exists): paper and demo, from desktop and phone, timed, results recorded.
7. **Operator onboarding for Tom**: role, what he can and cannot do, how to read provenance and mode, the drill.
8. **Calendar refresh** (quarterly, before the declared end date).

### 4.3 Proposed document map

| Audience | Document | Status |
|---|---|---|
| Everyone | `README.md`: what Fiboki is today, one screen, links | rewrite; generated status block |
| Operator (daily) | `docs/ops/RUNBOOK.md`: start, stop, health, routine, drill | new, absorbs `OPERATIONS.md` §§1-5, 10 |
| Operator (setup) | `docs/ops/INSTALL_MAC.md`, `docs/ops/LLAMA_CPP.md` | new |
| Operator (bad day) | `docs/ops/INCIDENTS.md` keyed by alert | new, absorbs `OPERATIONS.md` §11 |
| Operator (data) | `docs/ops/BACKUP_RESTORE.md`, `docs/ops/UPGRADE.md` | new |
| Operator (decisions) | `USER_ACTIONS.md`: open items only, closed items moved to an appendix with commit ids | prune |
| Operator (screens) | `docs/ops/WORKSTATION_GUIDE.md`: modes, provenance, states, keyboard, what each screen answers; mirrors `/system/legend` | new |
| Engineers | `AGENTS.md`, `docs/v2/*` standards, `docs/adr/*` | add ADRs; fix paths |
| Status | `docs/v2/ROADMAP.md` regenerated from a script that runs the suite and reads a YAML of items with evidence commands | automate |
| Plans | `docs/v2/AGENTIC_INTEGRATION_PLAN.md`, `docs/v2/FRONTEND_OVERHAUL_PLAN.md` with prefixed waves and living "shipped" tables | update |

Add a docs CI check: every file path and test name cited in `docs/` and `README.md` must exist (the charter's "citations rot" warning made mechanical), and any "Verified" snapshot line must include a commit hash that is an ancestor of HEAD.

---

## 5. Prioritised backlog

Value and risk: H, M, L. Effort in engineer-days (± 25%). Wave: F = frontend plan, A = agentic plan, D = docs/ops, B = backend support. Ordered by priority.

| # | Item | Value | Effort | Risk if not done | Prerequisite | Wave |
|---:|---|:---:|---:|:---:|---|---|
| 1 | Fix launcher CSP crash: treat empty `NEXT_PUBLIC_FIBOKI_API` as same-origin; config test with launcher env | H | 0.25 | H | none | F1c |
| 2 | Launcher runs production build (`next build && next start`) under launchd; dev behind a flag | H | 1 | H | 1 | F1c/D |
| 3 | Launcher never enables agent cycles implicitly; unify LLM env vars | H | 0.5 | H | none | A2/D |
| 4 | Status-bar worker tone from server heartbeat state (120 s/300 s) | H | 0.5 | H | none | F1c |
| 5 | Status-bar as-of: oldest on-screen as-of, probes excluded | H | 0.5 | H | none | F1c |
| 6 | Max-age staleness for non-polled views | H | 1 | H | none | F1c |
| 7 | Verdict and quality tones from values, exhaustive map | H | 0.5 | H | none | F1c |
| 8 | Kill-switch mutation reloads shared mode handle | H | 0.25 | M | none | F1c |
| 9 | Asymmetric friction: one-click PAUSE, typed FLATTEN, full ceremony for disarm/promote; update spec and security model | H | 1.5 | H | decision by Joe | F1c |
| 10 | CI web job: typecheck (incl. e2e), lint, build, size, CSP, source rules, axe subset | H | 1.5 | H | none | F1c |
| 11 | `LlamaCppProvider` with GGUF sha256, build commit, template hash, sampler params pinned | H | 3 | H | none | A2 |
| 12 | First real-model acceptance protocol (20 runs, eval pass rate, latency, tokens) with dated report | H | 2 | H | 11 | A2 |
| 13 | Replace USD budgets with token, wall-time, slot and energy budgets for local roles | M | 1.5 | M | 11 | A2 |
| 14 | Chart palette to tokens; ban hex in components; diverging blue/orange heatmap | M | 1 | M | none | F1c |
| 15 | CVD P&L hue distinct from accent; OOS chip shape; CVD simulation snapshots | M | 1 | M | none | F1c |
| 16 | Banner live-region fix (status role; one-shot assertive announcer for transitions) | M | 0.5 | M | none | F1c |
| 17 | Drawer: inert when closed, Escape, focus management | M | 0.5 | M | none | F1c |
| 18 | `scroll-padding` for sticky chrome; SC 2.4.11 test | M | 0.25 | M | none | F1c |
| 19 | Disabled-with-reason pattern (`aria-disabled`, visible reason) | M | 0.5 | M | none | F1c |
| 20 | Execution page: filter outside boundary; server aggregates or "first 200 of N" | M | 0.5 | M | none | F1c |
| 21 | Mock every route in Playwright fixtures; axe on populated states | M | 2 | M | none | F1c |
| 22 | ADRs: charts, SSE, deployment, friction, WebTransport deferral | M | 1 | L | none | F1c |
| 23 | Docs corrections (§4.1 table) and docs citation CI check | H | 2 | M | none | D |
| 24 | Operator passwords: salted KDF, Keychain, per-operator secrets; stop printing | H | 1.5 | H | none | B/D |
| 25 | Login, logout, session page, role gating in the shell | H | 3 | H | 24 | F2 |
| 26 | OpenAPI-generated types via `openapi-typescript`/`openapi-fetch`; delete hand-written types | H | 2 | M | none | F2 |
| 27 | Response-model provenance test (no bare numbers) and fixes (open positions, correlation matrix, queue depth, ages) | M | 2 | M | none | B |
| 28 | SSE endpoint (FastAPI 0.135 native or sse-starlette), envelopes with `as_of`/`max_age_s`, ring buffer | H | 5 | M | pin decision | B/F2 |
| 29 | SSE client: Web Locks leader, BroadcastChannel fan-out, seq-gap resync, sleep/wake handling, clock-skew check | H | 5 | M | 28 | F2 |
| 30 | Mac install, llama.cpp, backup/restore, incidents, upgrade runbooks (§4.2) | H | 4 | H | 2, 11 | D |
| 31 | `fiboki backup` command and a rehearsed restore | H | 2 | H | none | B/D |
| 32 | Model bake-off harness and routing proposal report | H | 3 | M | 11, 12 | A2 |
| 33 | Router consumes eval and forecast scores with minimum-evidence rule; human-approved changes only | M | 2 | M | 32 | A2 |
| 34 | Agent scheduler with four queues, bar-close awareness, memory and slot admission | H | 4 | M | 11 | A2 |
| 35 | Per-role context budgets in code, hashed in manifest | M | 1 | L | 34 | A2 |
| 36 | Schema design rules and grammar compatibility test for every role output (llama.cpp subset) | M | 1.5 | M | 11 | A2 |
| 37 | KV-prefix ordering, `id_slot` pinning, slot persistence; measure with and without speculative decoding | M | 2 | L | 11 | A2 |
| 38 | Episodic structured run summaries as the only agent-to-agent memory | M | 2 | M | none | A3 |
| 39 | New evals: evidence-id validity, numeric fidelity, refusal correctness, injection canaries, drift canaries | H | 3 | M | none | A3 |
| 40 | `query_news` tool with point-in-time filter and licence flags | M | 2 | L | headline store | A3 |
| 41 | NTP drift recording for first-seen timestamps; alert above 1 s | M | 0.5 | M | none | A3 |
| 42 | Agent-authored candidate pipeline S0 to S8 with automatic trial accounting and quotas | H | 8 | H | 39 | A3/A4 |
| 43 | `AgentInfluenceTier` record, stamping, CLI and the named tests for T0/T1 | H | 3 | H | none | A4 |
| 44 | Event classifier with two model families, grammar schema, map-reduce isolation | M | 4 | M | 11, 43 | A4 |
| 45 | Deterministic unscheduled-event proxy policy (spread/vol spike) as baseline and standalone | H | 3 | M | none | A4 |
| 46 | Pre-registration hashed into the audit chain; A5 safety stops | M | 1 | M | 43 | A4/A5 |
| 47 | Agent Desk screen (runs, claims with evidence links, forecasts, shadow ledger, candidates and models) | H | 10 | M | 26, 38, 39 | F4e |
| 48 | Backend read models for the Agent Desk | H | 4 | M | 38 | B |
| 49 | Risk & Exposure screen first in F4a, kill switch v2 and history | H | 6 | M | 25, 29 | F4a |
| 50 | Server-ranked attention queue with rule ids; Command screen | H | 6 | M | 28 | F4a/B |
| 51 | Incidents read model, ack and annotate (audited); System & Incidents screen incl. model server panel | M | 7 | M | 28 | F4a/B |
| 52 | Chart core: LWC wrapper, overlay layer, sync, sessions, regimes, gaps, forming bar, a11y plugin | H | 12 | M | overlays endpoint | F4c-1 |
| 53 | Overlays endpoint incl. sessions, events, headlines, stop history | H | 5 | M | none | B |
| 54 | Replay (as-of endpoint and UI) and "why did this trade happen" inspector | H | 12 | M | 52, 53 | F4c-2/B |
| 55 | Drawings persisted server-side with audit and authorship | M | 4 | L | 52 | F4c-3/B |
| 56 | What-if sandbox calling a backend risk-gateway dry-run | M | 5 | M | 49 | F4a/B |
| 57 | Notification centre and phone ops (kill-switch) view over LAN/Tailscale with session auth | H | 6 | M | 25, 29 | F4f |
| 58 | Frontend web-vitals and error beacons to the API; 24-hour soak test | M | 2 | L | 10 | F6 |

---

## 6. Facts, assumptions and sources

### 6.1 Facts (verified in this session)

- Typecheck and lint clean; all routes within first-load budgets (shell 154.9 of 180 KiB); contrast 214/214; figures and commands as in the header table.
- `next.config.ts` `headers()` throws with `NEXT_PUBLIC_FIBOKI_API=""`, the value `scripts/dev-up.sh:82` sets; the launcher runs `npm run dev` (`dev-up.sh:85`).
- `FIBOKI_LLM_URL` is exported by `scripts/desktop/Start Fiboki.command:45-54` and read nowhere under `src/`; `research_runtime.py` requires `FIBOKI_AGENT_LOCAL_MODEL` and `FIBOKI_AGENT_CYCLE_TARGET` when cycles are on; `LocalHTTPProvider` uses `/api/chat` and Ollama digest endpoints.
- `.github/workflows/ci.yml` contains no frontend job.
- All W-xx findings cite the file and line read in this session; the verdict enum value `reject` exists in `validation/report.py:256-258`.
- Operator passwords are configured as unsalted sha256 (`api/settings.py:150`); the launcher uses one default password for both operators (`dev-up.sh:8,20`).
- Agentic plan items shipped or absent as in §3.1 (commit log and file contents).

### 6.2 Assumptions and unverified items

- That the `headers()` exception prevents `next dev` from serving pages (strongly expected because Next loads custom routes at start-up; not executed).
- Playwright results (`318 passed`) and backend counts quoted in the docs were not re-run.
- Library sizes for TanStack Query and Table (about 13 KB and 15 KB gzip) are estimates; only cmdk (17.4 KB) and TanStack Virtual (7.8 KB) come from report E's measurements.
- The re-estimates in §2.1 and §5 are judgement, ±25%, one experienced engineer.
- Model throughput figures are from one third-party benchmark on an M5 Max 128 GB; Joe's desktop specification is not known here. Model-family suggestions are candidates for the bake-off, not recommendations.
- Proposed test names in §3.7 are proposals except where noted as existing.
- The WebTransport Baseline date is from a secondary source (webrtc.ventures); caniuse was not fetched.
- OpenBB citation features were seen only as sidebar items in the developer docs; the Copilot basics page describes visible reasoning steps and widget context, not citations.

### 6.3 Sources (all accessed 2026-09-29)

- llama.cpp server README (flags for JSON schema, grammar, `--jinja`, `-md`, `--cache-reuse`, `--slot-save-path`, `-np`, `--metrics`): https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- llama.cpp grammars README (JSON-schema-to-GBNF support and limits, `additionalProperties` default, `x{0,N}` advice, `response_format` forms): https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md
- llama.cpp speculative decoding docs: https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md
- llama.cpp KV cache reuse tutorial (slots, `id_slot`, prefix stability): https://github.com/ggml-org/llama.cpp/discussions/13606
- llama.cpp issue #28619 (slot save/restore does not persist draft context): https://github.com/ggml-org/llama.cpp/issues/28619
- Local LLM benchmark on Apple M5 Max 128 GB, 2026-06-14 (Qwen3.6, MTP, llama.cpp vs MLX): https://github.com/stared/benching-local-llms-on-apple-silicon
- Metal wired-memory ceiling and `iogpu.wired_limit_mb`: https://modelpiper.com/blog/iogpu-wired-limit-mb-mac
- Berkeley Function Calling Leaderboard v4: https://gorilla.cs.berkeley.edu/leaderboard.html
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- Beurer-Kellner et al., "Design Patterns for Securing LLM Agents against Prompt Injections" (2025): https://arxiv.org/abs/2506.08837 and summary https://simonwillison.net/2025/Jun/13/prompt-injection-design-patterns/
- AlphaAgent (originality, hypothesis alignment, complexity regularisation): https://arxiv.org/abs/2502.16789
- QuantaAlpha (evolutionary LLM alpha mining, 2026): https://arxiv.org/abs/2602.07085
- ForecastBench (contamination-free forecasting benchmark): https://www.forecastbench.org/about/
- Fortune on Bloomberg ASKB (evaluations, multi-model routing), 2026-04-28: https://fortune.com/2026/04/28/bloomberg-askb-ai-agents-lessons-from-bloomberg-cto-shawn-edwards-eye-on-ai/
- Hedge Fund Alpha on ASKB citations (BQL formula and highlighted excerpts), 2026-09-09: https://hedgefundalpha.com/profiles/bloomberg-askb-agentic-ai/
- Bloomberg ASKB announcement (not readable, HTTP 403): https://www.bloomberg.com/company/stories/meet-askb-bloomberg-introduces-agentic-ai-to-the-bloomberg-terminal/
- OpenBB Copilot basics: https://docs.openbb.co/workspace/analysts/ai-features/copilot-basics
- TradingView updates April to August 2026 (linked tabs sync time ranges, Fibonacci drawing alerts, notification schedules, AI chart copilot beta): https://chartwisehub.com/tradingview-updates-april-august-2026/
- TradingView Bar Replay 1-second interval: https://www.tradingview.com/blog/en/1-second-update-interval-in-bar-replay-53338/
- Koyfin dashboards (seven colour groups linking widgets): https://www.koyfin.com/help/mydashboards-myd/
- Lightweight Charts release notes (5.0 panes and primitives, 5.1 conflation, 5.2 hit testing and accessibility plugin): https://tradingview.github.io/lightweight-charts/docs/release-notes
- uPlot (cursor sync): https://github.com/leeoniya/uPlot
- FastAPI Server-Sent Events (0.135.0, keep-alive, headers, Last-Event-ID): https://fastapi.tiangolo.com/tutorial/server-sent-events/
- TanStack Query `experimental_streamedQuery`: https://tanstack.com/query/latest/docs/reference/streamedQuery
- WebTransport Baseline (Safari 26.4, March 2026): https://webrtc.ventures/2026/04/webtransport-is-now-baseline-what-it-means-for-real-time-media/
- React 19.2 (Activity, useEffectEvent, Performance Tracks): https://www.react.dev/blog/2025/10/01/react-19-2
- Next.js 16 bundled docs, "Preserving UI state" (Activity keeps up to three routes): `apps/web/node_modules/next/dist/docs/01-app/02-guides/preserving-ui-state.md`
- Sharing one SSE connection across tabs (leader election): https://www.server-sent-events.com/frontend-consumption-client-patterns/sharing-one-sse-connection-across-tabs/ and https://github.com/Pareo-AI/sse-coordinator
- openapi-typescript / openapi-fetch: https://openapi-ts.dev/
- WCAG 2.2 SC 2.4.11 Focus Not Obscured (Minimum): https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html
- WAI-ARIA APG data grid examples: https://www.w3.org/WAI/ARIA/apg/patterns/grid/examples/data-grids/ and Roselli, "ARIA Grid As an Anti-Pattern": https://adrianroselli.com/2020/07/aria-grid-as-an-anti-pattern.html
- Carbon data table style (row densities): https://carbondesignsystem.com/components/data-table/style/
- Internal: `research/reports/due_diligence_2026-09-28/{A,B,C,D,E}*.md` (read for §§2 and 3; section references inline).
