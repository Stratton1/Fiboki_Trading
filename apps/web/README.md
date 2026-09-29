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
npx tsc --noEmit   # types
npx eslint .       # includes the `?? 0` ban
npm run build
npm run size       # per-route first-load JS budgets (size-limit, after build)
npm run contrast   # WCAG contrast over every token pair in app/globals.css
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
| A poll never blanks a view; a failed refresh keeps the last good data, marked STALE | `useApi` resets only on a path change; `AsyncBoundary`/`ModeBanner`; `refresh.spec.ts` |
| Promotion caveats are ticked one by one; consequences are server-computed | `ConfirmDialog` acknowledgements; `.../promote/preflight`; `promote.spec.ts` |
| A chart's provenance is derived from its rows (MIXED, or "unlabelled source"), never a fallback | `lib/provenance.ts`; `chart-provenance.spec.ts`; `source-rules.spec.ts` |
| Every time is labelled UTC; `as_of` is shown | `lib/format.ts`; `FigureValue`; `SourceBadge`; `time-labels.spec.ts` |
| No heavyweight charting library; every runtime dependency reviewed; per-route byte budgets | `source-rules.spec.ts` (Plotly/mapbox ban incl. the lockfile, an explicit allow-list, first-load gzip ≤ 180 KB shell, ≤ 230 KB other routes); `.size-limit.mjs` |

## Charts

`components/charts.tsx` provides line, bar, heatmap and distribution charts as
hand-rolled inline SVG, in ~360 lines with no runtime dependency. V1 shipped the
full Plotly distribution (roughly 4.5 MB including `mapbox-gl`) to draw line
charts. First-load JS is measured per route by `npm run size:report`.
