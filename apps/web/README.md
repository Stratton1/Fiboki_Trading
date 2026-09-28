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
npx playwright test
FIBOKI_LIVE_API=1 npx playwright test live-api   # needs the API running
```

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
| Responsive from 360px, drawer under lg, no x-scroll | `globals.css`; `responsive.spec.ts` |
| Realism caveats are server-computed, never page copy | `CaveatList` renders payload only; `source-rules.spec.ts` |
| A poll never blanks a view; a failed refresh keeps the last good data, marked STALE | `useApi` resets only on a path change; `AsyncBoundary`/`ModeBanner`; `refresh.spec.ts` |
| Promotion caveats are ticked one by one; consequences are server-computed | `ConfirmDialog` acknowledgements; `.../promote/preflight`; `promote.spec.ts` |
| A chart's provenance is derived from its rows (MIXED, or "unlabelled source"), never a fallback | `lib/provenance.ts`; `chart-provenance.spec.ts`; `source-rules.spec.ts` |
| Every time is labelled UTC; `as_of` is shown | `lib/format.ts`; `FigureValue`; `SourceBadge`; `time-labels.spec.ts` |
| No heavyweight charting library | `components/charts.tsx` is inline SVG; `source-rules.spec.ts` pins `dependencies` to next/react/react-dom |

## Charts

`components/charts.tsx` provides line, bar, heatmap and distribution charts as
hand-rolled inline SVG, in ~360 lines with no runtime dependency. V1 shipped the
full Plotly distribution — roughly 4.5 MB including `mapbox-gl` — to draw line
charts. Total client JS here is ~212 KB gzipped for the whole application.
