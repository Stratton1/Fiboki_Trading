# Phase 5 Design — FIBOKEI Web Platform

Date: 2026-03-07
Status: Approved
Scope: Subphases 5.1 through 5.6

---

## 1. Overview

Phase 5 delivers the FIBOKEI web platform: a Next.js frontend deployed to Vercel, communicating with the existing FastAPI backend (deployed separately to Railway/Render). The platform provides dashboard KPIs, a KLineChart-based trading workspace, Plotly-powered research analytics, paper bot controls, trade history with detail inspection, settings, and system diagnostics.

Subphase 5.6 (execution adapter abstraction) is backend execution-readiness work, not part of the frontend build.

---

## 2. Authentication — Secure HTTP-Only Cookies

- Backend sets access token as an `HttpOnly`, `Secure`, `SameSite=Lax` cookie on successful login via `POST /api/v1/auth/login`.
- Backend clears cookie on `POST /api/v1/auth/logout`.
- Frontend never touches the token directly. All API calls use `credentials: "include"`.
- Token refresh: `POST /api/v1/auth/refresh` called automatically on 401 responses.
- Next.js middleware at `frontend/src/middleware.ts` protects all `/(dashboard)` routes server-side by checking for the auth cookie.
- Seeded admin user bootstrapped via backend CLI command (`python -m fibokei seed-admin`).
- Auth context provides `login()`, `logout()`, `user`, `isAuthenticated` — no token storage in frontend.

---

## 3. Charting Architecture

### 3.1 KLineChart — Primary Financial Chart Engine

KLineChart is the primary financial chart engine. Ichimoku, Fibonacci, trade markers, and strategy overlays will be implemented through our chart layer using KLineChart's extensibility model. We do not assume built-in indicator support.

**Responsibilities:**
- Candlestick rendering (OHLCV)
- Instrument and timeframe switching
- Ichimoku overlay (tenkan, kijun, senkou A/B, chikou) — implemented via our overlay layer
- Fibonacci retracement/extension overlays — implemented via our overlay layer
- Trade entry/exit markers, SL/TP lines, partial exit markers
- Strategy annotations (cloud interaction zones, swing labels, confidence markers)
- Zoom, pan, fit-to-data
- Inspect selected trade (click marker to open detail panel)
- Jump to trade timestamp
- Show/hide individual Ichimoku components
- Show/hide Fibonacci overlays
- Toggle marker visibility (entries, exits, SL/TP)
- Replay-ready architecture (hooks for future step-through mode)

**Used on pages:** Charts workspace, trade detail view, backtest trade inspection, bot review.

### 3.2 Plotly.js — Research and Analytics Engine

Plotly.js handles all research, analytics, and summary visualisations.

**Responsibilities:**
- Equity curves, drawdown curves, cumulative returns
- Monthly return heatmaps
- PnL and duration distributions
- Strategy vs instrument heatmaps
- Ranking comparisons, composite score visualisations
- Sharpe vs profit factor scatter charts
- Dashboard mini-charts (sparklines, summary visuals)
- Multi-run and multi-strategy comparisons

**Used on pages:** Dashboard (mini-charts), backtest results, research matrix, comparison views.

### 3.3 Separation Rules

- KLineChart logic and Plotly logic never share the same component layer.
- Research visualisations never go into chart components.
- Trade annotations never go into Plotly unless explicitly needed for an analytics page.
- Dashboard uses lightweight Plotly components, not KLineChart instances.

---

## 4. Frontend Architecture

### 4.1 Directory Structure

```
frontend/src/
  app/
    login/page.tsx
    middleware.ts                     # Route protection (cookie check)
    (dashboard)/
      layout.tsx                     # Sidebar + header, protected
      page.tsx                       # Dashboard (Plotly mini-charts, KPI cards)
      charts/page.tsx                # KLineChart workspace
      backtests/
        page.tsx                     # Run + list backtests
        [id]/page.tsx                # Backtest detail (Plotly analytics + KLineChart trades)
      research/page.tsx              # Research matrix + rankings (Plotly heatmaps)
      bots/page.tsx                  # Paper bot controls
      trades/
        page.tsx                     # Trade history table
        [id]/page.tsx                # Trade detail (KLineChart + metrics)
      settings/page.tsx              # Config
      system/page.tsx                # Diagnostics
  components/
    ui/                              # shadcn/ui primitives
    charts/
      core/                          # KLineChart container, init, lifecycle
        TradingChart.tsx             # Main chart component
        ChartProvider.tsx            # Chart instance context
      overlays/                      # Indicator drawing logic
        IchimokuOverlay.ts           # Tenkan, kijun, senkou, chikou rendering
        FibonacciOverlay.ts          # Retracement/extension level rendering
      annotations/                   # Trade and strategy markers
        TradeMarkers.ts              # Entry/exit arrows, SL/TP lines
        StrategyLabels.ts            # Cloud interaction, swing labels
        StopTargetLines.ts           # SL/TP/trailing lines
      panels/                        # Chart UI controls
        ChartToolbar.tsx             # Instrument/timeframe selectors
        OverlayControls.tsx          # Toggle checkboxes for indicators
        MarkerControls.tsx           # Toggle trade marker visibility
        TradeInspector.tsx           # Selected trade detail panel
    analytics/                       # Plotly wrappers
      EquityCurve.tsx
      DrawdownChart.tsx
      Heatmap.tsx
      Distribution.tsx
      MiniSummary.tsx                # Dashboard sparklines
      ComparisonChart.tsx            # Multi-strategy/multi-run
  lib/
    api.ts                           # Typed fetch with credentials:"include"
    auth.tsx                         # Auth context (login/logout/user only)
    hooks/                           # SWR hooks
      use-bots.ts
      use-trades.ts
      use-backtests.ts
      use-research.ts
      use-market-data.ts
    chart-mappers/                   # Contract -> KLineChart format
      candle-mapper.ts
      ichimoku-mapper.ts
      fibonacci-mapper.ts
      annotation-mapper.ts
    analytics-mappers/               # Contract -> Plotly traces
      equity-mapper.ts
      heatmap-mapper.ts
      distribution-mapper.ts
  types/
    contracts/                       # Mirrors backend schemas exactly
      chart.ts                       # CandleBar, IchimokuSeries, FibLevel
      analytics.ts                   # EquityCurve, DrawdownSeries, HeatmapData
      trades.ts                      # TradeAnnotation, TradeDetail, TradeMarker
      research.ts                    # ResearchResult, RankingEntry, MatrixCell
```

### 4.2 State Management

| Concern | Tool | Rules |
|---------|------|-------|
| Auth state | React Context | Login/logout/user identity only |
| Theme/UI prefs | React Context | Selected instrument, timeframe, sidebar state |
| Server data | SWR | Bots, trades, backtests, research, market data. Handles caching, revalidation, polling |
| Chart state | Local component state | Zoom, pan, selected trade, overlay toggles — ephemeral |

Context is never used for server data. SWR handles all API-fetched state.

### 4.3 Dependencies

- `next` (14+, App Router)
- `typescript`
- `tailwindcss`
- `@radix-ui/*` (via shadcn/ui)
- `klinecharts` (KLineChart)
- `plotly.js` + `react-plotly.js`
- `swr`
- `lucide-react` (icons)
- `clsx` + `tailwind-merge` (shadcn utility)

---

## 5. Trade Detail View

Each trade opens into a dedicated detail page (`/trades/[id]`) with:

- KLineChart centred on the entry/exit time range (with buffer bars)
- Entry marker, exit marker, SL line, TP line(s), partial exit markers
- Signal rationale text from the strategy
- Indicator snapshot at entry time (Ichimoku state, ATR, regime)
- Outcome metrics: PnL, PnL%, duration, MFE, MAE, exit reason
- Navigation to previous/next trade

This view is also accessible from backtest detail pages and bot inspection.

---

## 6. Backend API Domains

### 6.1 Existing Routes (Retained)

| Route | Purpose |
|-------|---------|
| `POST /api/v1/auth/login` | Login (modified: sets cookie) |
| `GET /api/v1/instruments/` | List instruments |
| `GET /api/v1/strategies/` | List strategies |
| `POST /api/v1/backtests/run` | Run backtest |
| `GET /api/v1/backtests/` | List backtests |
| `GET /api/v1/backtests/{id}` | Backtest detail |
| `POST /api/v1/research/run` | Run research matrix |
| `GET /api/v1/research/rankings` | Research rankings |
| `POST /api/v1/research/compare` | Compare combinations |
| `POST /api/v1/paper/bots` | Create bot |
| `GET /api/v1/paper/bots` | List bots |
| `GET /api/v1/paper/account` | Paper account |

### 6.2 New Routes Required

| Route | Purpose |
|-------|---------|
| `POST /api/v1/auth/logout` | Clear auth cookie |
| `POST /api/v1/auth/refresh` | Refresh token |
| `GET /api/v1/auth/me` | Current user info |
| `GET /api/v1/market-data/{instrument}/{timeframe}` | OHLCV bars + precomputed indicator series |
| `GET /api/v1/charts/annotations/{backtest_id}` | Normalized trade/strategy annotations for a backtest |
| `GET /api/v1/charts/annotations/bot/{bot_id}` | Annotations for a live paper bot |
| `GET /api/v1/trades/` | Trade history list (filterable, paginated) |
| `GET /api/v1/trades/{trade_id}` | Trade detail with indicator snapshot |
| `GET /api/v1/trades/export` | CSV export |
| `GET /api/v1/system/health` | System health check |
| `GET /api/v1/system/status` | Engine status, dataset registry |

### 6.3 Annotation Payloads (Backend-Generated)

The backend returns normalized annotation objects. Frontend renders them, does not invent them.

```json
{
  "trade_markers": [
    {
      "trade_id": "t-001",
      "strategy_id": "bot01_sanyaku",
      "direction": "LONG",
      "entry": {"timestamp": "2025-01-01T00:00:00Z", "price": 1.1000},
      "exit": {"timestamp": "2025-01-01T06:00:00Z", "price": 1.1080},
      "stop_loss": [{"timestamp": "2025-01-01T00:00:00Z", "price": 1.0950}],
      "take_profit": [{"timestamp": "2025-01-01T00:00:00Z", "price": 1.1100}],
      "partial_exits": [],
      "label": "Sanyaku Long",
      "outcome": "take_profit_hit"
    }
  ],
  "strategy_annotations": [
    {"type": "cloud_interaction", "timestamp": "2025-01-01T01:00:00Z", "price": 1.1020, "label": "Cloud breakout"}
  ],
  "indicator_snapshots": {}
}
```

---

## 7. Theme and Styling

Per blueprint S5.3:
- Background: off-white layers (`#FAFAF9`, `#F5F5F4`, `#FFFFFF`)
- Text: near-black (`#1C1917`)
- Primary greens: `#16A34A` (main), `#15803D` (dark), `#22C55E` (light), `#86EFAC` (accent)
- Charts: dark candles on light background, green/red for bullish/bearish
- Font: Inter or system sans-serif
- Professional research-terminal feel
- Clean spacing, readable labels, strong contrast for candles and markers

---

## 8. Subphase 5.6 — Backend Execution Readiness

This is backend-only work, separate from the frontend build.

- `ExecutionAdapter` ABC: `place_order`, `cancel_order`, `modify_order`, `get_positions`, `get_account_info`, `close_position`, `partial_close`
- `PaperExecutionAdapter`: wraps existing paper engine through the adapter interface
- `IGExecutionAdapter`: stub, all methods raise `NotImplementedError("IG live trading not enabled in V1")`
- `FeatureFlags`: `FIBOKEI_LIVE_EXECUTION_ENABLED=False`, `FIBOKEI_IG_PAPER_MODE=True`
- `get_execution_adapter()` returns `PaperExecutionAdapter` by default

---

## 9. Subphase Summary

| Subphase | Scope | Type |
|----------|-------|------|
| 5.1 | Next.js scaffold, cookie auth, API client, login, layout, middleware | Frontend |
| 5.2 | Dashboard KPIs (Plotly), KLineChart workspace with Ichimoku overlay | Frontend |
| 5.3 | Backtest form + results (Plotly), research heatmap (Plotly) | Frontend |
| 5.4 | Paper bot controls, trade history, trade detail view (KLineChart) | Frontend |
| 5.5 | Settings, system diagnostics, Vercel deployment | Frontend |
| 5.6 | ExecutionAdapter ABC, PaperAdapter, IG stub, feature flags | Backend readiness |

---

## 10. Docs to Produce Before Coding

1. `docs/architecture.md` — system overview, component boundaries
2. `docs/frontend_architecture.md` — component tree, state management, routing
3. `docs/charting_spec.md` — KLineChart responsibilities, Plotly responsibilities, interaction requirements
4. `docs/api_contracts.md` — all endpoint schemas, annotation payloads, chart data contracts
5. `docs/auth_spec.md` — cookie-based flow, middleware, refresh, session handling
