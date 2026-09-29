# Fiboki V2 Operator Workstation

A Next.js + TypeScript client for the Fiboki V2 API. It holds **no trading
logic**: it computes no indicator, evaluates no signal and decides no size.
Every number on screen arrived from `src/fiboki/api` inside a `Figure`, carrying
the provenance of the run that produced it.

## Running

```bash
# 1. the API
cd ../..
FIBOKI_STATE_DIR=var \
FIBOKI_ALLOWED_ORIGINS=http://127.0.0.1:3000 \
FIBOKI_SESSION_SECRET="$(openssl rand -hex 32)" \
FIBOKI_OPERATORS="joe:admin:$(printf 'yourpassword' | sha256sum | cut -d' ' -f1)" \
.venv/bin/python -m uvicorn --factory fiboki.api.app:asgi_factory --port 8000

# 2. the workstation
cd apps/web
npm install
npm run dev
```

## Gates

```bash
npm run gen:api    # lib/generated/openapi.ts from openapi.json (or the snapshot)
npx tsc --noEmit   # types, including lib/api-contract.ts drift checks
npx eslint .       # includes the `?? 0` ban
npm run build
npm run size       # per-route first-load JS budgets (size-limit, after build)
npm run contrast   # WCAG contrast over every token pair, and accent/P&L distinguishability
npx playwright test
FIBOKI_LIVE_API=1 npx playwright test live-api   # needs the API running
```

Visual baselines (`tests/e2e/visual.spec.ts-snapshots/`) are committed for
Linux Chromium, desktop project, and skipped on other platforms. Regenerate with
`npx playwright test visual --project=desktop --update-snapshots`.

## Design system (Wave 1)

Tailwind CSS 4 on OKLCH tokens (`app/globals.css`, dark by default, light and a
colour-blind P&L preset), owned primitives on Base UI in `components/ui/`, and
the shell in `components/shell/`: mode banner and viewport frame, favicon and
tab title per mode, a 56px rail expanding to 232px over nine sections, a page
header with the section's views, a status bar and an inspector sheet. Base UI
popups load on first use (`components/ui/layers.ts`) so the shell stays inside
its 180 KB first-load budget. `/system/legend` shows what every colour, shape
and frame means.

## Data layer, session and live stream (Wave 2)

Server state is TanStack Query (`lib/query.ts`): every read is keyed by its
path, shared across views, and exposed as one `ViewState` (loading on first
load only; success carries `freshness`: live, fresh, lagging, stale or
disconnected, and `asOf`, and never returns to loading). `apiFetch` in
`lib/api.ts` is still the only way to the API (credentials, CSRF, typed
errors); a 401 sends the operator to `/login?next=<path>`.

One SSE connection per browser (`lib/stream/client.ts`, loaded after first
paint): the tab holding the Web Lock `fiboki-stream` owns the `EventSource`
and fans frames out over a `BroadcastChannel`. `lib/stream/router.ts` speaks
the backend's entity model (`src/fiboki/api/routers/stream.py`: snapshot
`{entities, count}`, delta `{id, entity}`, tombstone `{id}`), dedupes on
(event, id) in an LRU of 1,000, checks per-topic sequence numbers (a gap is
re-read by REST, never guessed), writes only REST-shaped fields into the query
cache (anything else triggers a REST re-read), and puts marks and heartbeats in
the Zustand live store (`lib/live-store.ts`, rAF-batched). Backoff 1, 2, 4, 8, 15 s with jitter; DISCONNECTED after five
failures, with Reconnect and REST polling every 10 s. The freshness rules are
in `lib/freshness.ts`. Mutations show no optimistic state: the dialog waits for
the platform to echo the change (`lib/echo.ts`).

URL state (filters, selection, tabs) is nuqs, mounted per page
(`components/UrlState.tsx`). Tests drive the stream with the scripted harness
in `tests/e2e/sse.ts`.

## Core components (Wave 3)

- **DataGrid** (`components/grid/`, loaded on first use): TanStack Table 9 and
  Virtual 3. Sort (no data always last), text filter, column visibility and
  pinning, saved column sets in localStorage, a roving-tabindex row model
  (↓/j, ↑/k, Home, End, PageUp/Down, Enter), selection kept by row key through
  live updates, the unit in the header derived from the rows' own Figures,
  CSV export with a unit and provenance column per figure. `?row=<key>`
  selects a row. The trades, candidates, instruments and audit screens use it.
- **Stat** (`components/Stat.tsx`): the stat tile, ▲/▼ glyph and sign decided
  after rounding. **CaveatPopover**: caveats and reasons behind a focusable
  button, never a `title`.
- **Keyboard** (`components/shell/Hotkeys.tsx`, `ShellCommands.tsx`): ⌘K
  palette (cmdk, lazy; go to, open by id, actions that only open dialogs),
  `g` chords per section, `?` shortcut sheet, ⇧K OPENS the kill-switch dialog.
- **Charts** (`components/charts.tsx`): tokens only (a source rule bans hex),
  responsive width, a UTC time axis, gaps drawn as hatched gaps, arrow-key
  readout and a "View data" table on every chart.
- **Numbers** (`lib/format.ts`): fixed decimals per unit, thousands
  separators, real minus, sign after rounding; price precision from the
  instrument's pip size when the API supplies it.
- **View states**: eight (loading, empty, absent, stale, disconnected, error,
  forming, replay), each a `--state-*` token, glyph and outline
  (`components/ui/ViewStateTag.tsx`, `/system/legend`).
- **Kill-switch friction is asymmetric**: PAUSE needs a reason only, in every
  mode including LIVE; FLATTEN needs the typed word FLATTEN in every mode;
  disarm needs RE-ARM (`components/KillSwitchControl.tsx`).

## The rules this app exists to keep

Each has a test. They are not style preferences; each one closes a specific way
the V1 frontend misled an operator.

| Rule | Enforced by |
|---|---|
| A `ProvenanceChip` beside every number, from the data | `FigureValue` has no unlabelled path; `tests/e2e/provenance.spec.ts` |
| Provenance is a column, never a page title or a tab | `trading/execution/page.tsx`; `provenance.spec.ts` |
| A sticky execution-mode banner reading the real mode | `app/layout.tsx` renders it once; `mode-banner.spec.ts` |
| `data?.x ?? 0` is banned | `eslint.config.mjs` **and** `source-rules.spec.ts` (greps the tree) |
| Loading, empty, error and success are four distinct states | `AsyncBoundary`'s discriminated union; `error-states.spec.ts` |
| One shared confirm dialog, enumerating consequences | `ConfirmDialog`; `kill-switch.spec.ts` asserts no native `confirm()` |
| Kill switch reachable in every mode, PAUSE ≠ FLATTEN | `KillSwitch`; `kill-switch.spec.ts` |
| Execution mode changes the whole shell (frame, favicon, title); unknown mode disables every mutation | `components/shell/Mode.tsx`; `mode-frame.spec.ts` |
| Dialogs trap focus, hide the page, return focus; Escape is ignored while busy | `components/ui/ConfirmDialogLayer.tsx`; `keyboard.spec.ts` |
| Zero serious or critical axe violations on every route, dark and light | `a11y.spec.ts` |
| A strict CSP with no inline styles and no eval, and no violation on any route | `next.config.ts`; `csp.spec.ts` |
| Responsive from 360px, drawer under lg, comfortable density under 1024px, no x-scroll | `globals.css`; `responsive.spec.ts` |
| Realism caveats are server-computed, never page copy | `CaveatList` renders payload only; `source-rules.spec.ts` |
| A poll or stream event never blanks a view; a failed refresh keeps the last good data, marked STALE | `useApi`'s `ViewState` (`lib/query.ts`); `AsyncBoundary`/`ModeBanner`; `refresh.spec.ts`, `freshness.spec.ts` |
| A connected stream with a dead worker looks stale; a gap is re-read, never guessed | `lib/freshness.ts`, `lib/stream/router.ts`; `freshness.spec.ts`, `stream.spec.ts`, `stream-router.spec.ts` |
| One stream per browser, whatever the number of tabs | Web Locks leader in `lib/stream/client.ts`; `stream.spec.ts` |
| No optimistic UI for the kill switch or an incident acknowledgement | `lib/echo.ts`; `stream.spec.ts` |
| Sign-in never puts a credential in a URL; a 401 returns the operator to where they were; roles disable what they cannot do, with the reason | `app/login`, `lib/api.ts`, `lib/auth.ts`; `auth.spec.ts` |
| The attention queue is the server's ranking, never re-sorted | `components/command/AttentionPanel.tsx`; `command.spec.ts`, `source-rules.spec.ts` |
| Promotion caveats are ticked one by one; consequences are server-computed | `ConfirmDialog` acknowledgements; `.../promote/preflight`; `promote.spec.ts` |
| A chart's provenance is derived from its rows (MIXED, or "unlabelled source"), never a fallback | `lib/provenance.ts`; `chart-provenance.spec.ts`; `source-rules.spec.ts` |
| Every time is labelled UTC; `as_of` is shown | `lib/format.ts`; `FigureValue`; `SourceBadge`; `time-labels.spec.ts` |
| The status bar's worker tone is the platform's verdict; "data as of" is the oldest view on screen, probes excluded | `lib/freshness.ts` `workerStatus`, `lib/as-of.ts`; `trust.spec.ts` |
| A view that neither polls nor streams goes stale after five minutes | `lib/freshness.ts`; `trust.spec.ts` |
| A badge's tone comes from its value (a REJECT is never green) | `lib/tones.ts`; `trust.spec.ts` |
| No hex colour in a component; charts draw in tokens | `source-rules.spec.ts`; `trust.spec.ts` |
| A grid keeps its selection through live updates; 10,000 rows stay a small DOM | `components/grid/`; `grid.spec.ts` |
| No keystroke mutates: ⇧K and the palette only open dialogs | `components/shell/Hotkeys.tsx`; `keyboard.spec.ts` |
| No heavyweight charting library; every runtime dependency reviewed; per-route byte budgets | `source-rules.spec.ts` (Plotly/mapbox ban incl. the lockfile, an explicit allow-list, first-load gzip ≤ 180 KB shell, ≤ 230 KB other routes); `.size-limit.mjs` |

## Charts

`components/charts.tsx` provides line, bar, heatmap and distribution charts as
hand-rolled inline SVG with no runtime dependency. V1 shipped the
full Plotly distribution (roughly 4.5 MB including `mapbox-gl`) to draw line
charts. First-load JS is measured per route by `npm run size:report`.
