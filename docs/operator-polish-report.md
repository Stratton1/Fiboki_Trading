# Operator Polish & Workflow Upgrade Report

**Date:** 2026-03-15
**Scope:** Charts, Research, Backtests, Scenarios (primary), Jobs, Bots, Exposure, Trades, Alerts, Settings, System (secondary audit)

---

## 1. Executive Summary

Added human-readable strategy names across all 7 pages that display strategy IDs, exposed chart reset/fit operations via imperative ref, added vertical line drawing tool, expanded instrument list, added "Send to Backtest" workflow link from Research results, and added Playwright tests for all new features. All 661 backend tests pass, frontend builds clean.

## 2. Charts Page Improvements

| # | Improvement | File(s) |
|---|------------|---------|
| 1 | Expanded instruments from 11 to 23 (metals, oil, indices, crypto) | ChartCell.tsx |
| 2 | Added vertical line drawing tool (V-Line, shortcut X) | ChartCell.tsx |
| 3 | Added Reset View button (RotateCcw icon, scrollToRealTime) | ChartCell.tsx, TradingChart.tsx |
| 4 | Added Fit to Data button (Maximize2 icon, scrollToDataIndex) | ChartCell.tsx, TradingChart.tsx |
| 5 | Exposed chart operations via forwardRef + useImperativeHandle | TradingChart.tsx |
| 6 | Added Bots workflow link alongside Backtest and Research | ChartCell.tsx |
| 7 | Added InfoTip tooltips to Ichimoku toggle (component explanations) | ChartCell.tsx |
| 8 | Added InfoTip tooltips to Sessions toggle (session times, best setups) | ChartCell.tsx |

## 3. Research Page Improvements

| # | Improvement | File(s) |
|---|------------|---------|
| 1 | Strategy names in run results table (sub-line under ID) | research/page.tsx |
| 2 | Strategy names in Saved Shortlist table | research/page.tsx |
| 3 | Strategy names in heatmap Y-axis labels | research/page.tsx |
| 4 | Strategy name in Promote to Paper Trading dialog | research/page.tsx |
| 5 | "Send to Backtest" action link per result row | research/page.tsx |

## 4. Backtests Page Improvements

| # | Improvement | File(s) |
|---|------------|---------|
| 1 | Strategy human-readable names in results table rows | backtests/page.tsx |
| 2 | Strategy names in filter dropdown options | backtests/page.tsx |
| 3 | Strategy names in Shortlist picker dropdown | backtests/page.tsx |
| 4 | Strategy name in backtest detail page header | backtests/[id]/page.tsx |

## 5. Scenarios Page Improvements

| # | Improvement | File(s) |
|---|------------|---------|
| 1 | Strategy name tooltips on per-bot result rows | scenarios/page.tsx |
| 2 | Cartesian product breakdown ("3 strategies x 5 instruments x 2 TFs = 30 bots") | scenarios/page.tsx |
| 3 | Pre-run validation messages (missing strategies/instruments/timeframes) | scenarios/page.tsx |
| 4 | Run button title attribute explaining why disabled | scenarios/page.tsx |
| 5 | Fixed `key={i}` anti-pattern in results table (now uses strategy+instrument+TF) | scenarios/page.tsx |
| 6 | Added Sharpe and Max DD tooltips to results table headers | scenarios/page.tsx |
| 7 | Added `tip` prop to SortHeader component | scenarios/page.tsx |

## 6. Cross-Page Strategy Name Infrastructure

Created `frontend/src/lib/strategy-names.ts` — shared utility mapping all 12 bot IDs to human-readable names:

| Bot ID | Human Name |
|--------|-----------|
| bot01 | Pure Sanyaku Confluence |
| bot02 | Kijun-sen Pullback |
| bot03 | Flat Senkou Span B Bounce |
| bot04 | Chikou Open Space Momentum |
| bot05 | MTFA Sanyaku |
| bot06 | N-Wave Structural Targeting |
| bot07 | Kumo Twist Anticipator |
| bot08 | Kihon Suchi Time Cycle Confluence |
| bot09 | Golden Cloud Confluence |
| bot10 | Kijun + 38.2% Shallow Continuation |
| bot11 | Sanyaku + Fib Extension Targets |
| bot12 | Kumo Twist + Fibonacci Time Zone |

Consumed by: Backtests list, Backtests detail, Research results, Research shortlist, Research heatmap, Scenarios results, Bots list (flat + grouped), Trades list.

## 7. Secondary Audit — 7 Other Pages

| Page | Lines | Status | Notes |
|------|-------|--------|-------|
| Jobs | 268 | Good | Real-time 3s refresh, status badges, error expansion, drill-through links |
| Bots | 411 | Good + improved | Strategy names added, fleet summary, grouping, stale detection |
| Exposure | 424 | Good | Risk utilization gauges, direction balance, concentration warnings |
| Trades | 209 | Good + improved | Strategy name tooltips added, pagination, TP-hit spread tip |
| Alerts | Existing | Good | Telegram integration, alert history |
| Settings | Existing | Good | Account config, API credentials |
| System | Existing | Good | Health check, data manifest, system info |

**Secondary audit findings (no critical issues):**
- Jobs page: timestamps could use relative format (minor)
- Bots page: shortlist dropdown lacks outside-click handler (minor)
- Exposure page: feature-complete with risk gauges, no issues
- All pages have proper empty states, loading spinners, and error handling

## 8. TradingChart Architecture Change

Converted `TradingChart` from a plain function component to a `forwardRef` component:

```
Before: export default function TradingChart({ ... }) { ... }
After:  const TradingChart = forwardRef<TradingChartHandle, TradingChartProps>(...)
        export default TradingChart;
```

Exported `TradingChartHandle` interface with `resetView()` and `fitToData()` methods. ChartCell creates a ref with `useRef<TradingChartHandle>(null)` and passes it to TradingChart.

## 8b. Backtests Code Quality Fixes

From audit findings:
- **Silent error swallowing fixed**: `handleDelete`, `handleBulkDelete`, `handlePromote` now surface errors via `setError()` instead of `catch { /* */ }`
- **Magic numbers extracted**: `MIN_TRADES_FOR_RANKING = 80` and `LEGACY_AGE_MS = 30 days` are now named constants

## 9. Files Modified

| File | Change |
|------|--------|
| `frontend/src/lib/strategy-names.ts` | **New**: Strategy ID → name mapping utility |
| `frontend/src/components/charts/core/TradingChart.tsx` | forwardRef + useImperativeHandle for reset/fit |
| `frontend/src/components/charts/core/ChartCell.tsx` | Reset/fit buttons, V-line tool, expanded instruments, Bots link, InfoTips |
| `frontend/src/app/(dashboard)/research/page.tsx` | Strategy names (results, shortlist, heatmap, promote), backtest link |
| `frontend/src/app/(dashboard)/backtests/page.tsx` | Strategy names (table, filter, shortlist picker) |
| `frontend/src/app/(dashboard)/backtests/[id]/page.tsx` | Strategy name in detail header |
| `frontend/src/app/(dashboard)/scenarios/page.tsx` | Strategy name tooltips on result rows |
| `frontend/src/app/(dashboard)/bots/page.tsx` | Strategy names in flat and grouped views |
| `frontend/src/app/(dashboard)/trades/page.tsx` | Strategy name tooltips |
| `frontend/e2e/operator-polish.spec.ts` | **New**: 16 Playwright tests for new features |
| `frontend/playwright.config.ts` | Added operator-polish project |

## 10. Test Coverage

| Suite | Tests | Status |
|-------|-------|--------|
| Backend (pytest) | 661 | All passing |
| Frontend build | — | Clean (no errors, no warnings) |
| Playwright: operator-polish | 16 | New (charts reset/fit, V-line, bots link, instrument count, page loads, navigation) |
| Playwright: existing | 30+ | Unchanged |

## 11. Verification Commands

```bash
# Backend
cd backend && python3 -m pytest -v          # 661 passed

# Frontend build
cd frontend && npx next build               # Compiled successfully

# Playwright (requires running frontend)
cd frontend && npx playwright test --project=operator-polish
```

## 12. What's Not Changed (Deliberate)

- **No backend changes** — all improvements are frontend-only
- **No new API endpoints** — strategy names are a static client-side map
- **No database migrations** — nothing structural changed
- **Win rate column not added to backtests list** — would require backend summary endpoint change
- **No undo/redo for drawings** — would require significant state management work
- **No chart crosshair sync across multi-chart layout** — klinecharts API limitation
