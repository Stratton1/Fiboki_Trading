# Fiboki Frontend Architecture

## 1. Technology Stack

| Dependency | Purpose |
|-----------|---------|
| Next.js (App Router) | Framework, routing, middleware |
| TypeScript | Type safety |
| Tailwind CSS | Styling |
| shadcn/ui (@radix-ui) | UI primitives |
| KLineChart (`klinecharts`) | Financial candlestick charts |
| Plotly.js (`react-plotly.js`) | Analytics and research charts |
| SWR | Server state management |
| lucide-react | Icons |
| clsx + tailwind-merge | Class utilities |

## 2. Directory Structure

```
frontend/src/
  app/
    layout.tsx                        Root layout (AuthProvider)
    login/page.tsx                    Login page (public)
    (dashboard)/
      layout.tsx                      Sidebar + header (protected)
      page.tsx                        Dashboard (KPI cards, Plotly sparklines)
      charts/page.tsx                 KLineChart workspace
      backtests/
        page.tsx                      Run + list backtests
        [id]/page.tsx                 Backtest detail (Plotly + KLineChart)
      research/page.tsx               Research matrix + rankings (Plotly)
      bots/page.tsx                   Paper bot controls
      trades/
        page.tsx                      Trade history table
        [id]/page.tsx                 Trade detail (KLineChart + metrics)
      settings/page.tsx               Configuration
      system/page.tsx                 System diagnostics
  middleware.ts                       Route protection (cookie check)

  components/
    ui/                               shadcn/ui primitives
    charts/
      core/
        TradingChart.tsx              Main KLineChart component
        ChartProvider.tsx             Chart instance context
      overlays/
        IchimokuOverlay.ts            Tenkan, kijun, senkou, chikou rendering
        FibonacciOverlay.ts           Retracement/extension levels
      annotations/
        TradeMarkers.ts              Entry/exit arrows, SL/TP lines
        StrategyLabels.ts            Cloud interaction, swing labels
        StopTargetLines.ts           SL/TP/trailing lines
      panels/
        ChartToolbar.tsx             Instrument/timeframe selectors
        OverlayControls.tsx          Toggle checkboxes for indicators
        MarkerControls.tsx           Toggle trade marker visibility
        TradeInspector.tsx           Selected trade detail panel
    analytics/
      EquityCurve.tsx                Plotly equity curve
      DrawdownChart.tsx              Plotly drawdown
      Heatmap.tsx                    Plotly heatmaps
      Distribution.tsx               Plotly PnL/duration distributions
      MiniSummary.tsx                Dashboard sparklines
      ComparisonChart.tsx            Multi-strategy/multi-run

  lib/
    api.ts                           Typed fetch client (credentials: "include")
    auth.tsx                         AuthProvider context
    hooks/
      use-bots.ts                    SWR hook for paper bots
      use-trades.ts                  SWR hook for trade history
      use-backtests.ts               SWR hook for backtests
      use-research.ts                SWR hook for research rankings
      use-market-data.ts             SWR hook for OHLCV + Ichimoku
    chart-mappers/
      candle-mapper.ts               Contract -> KLineChart format
      ichimoku-mapper.ts             Contract -> overlay data
      fibonacci-mapper.ts            Contract -> Fibonacci levels
      annotation-mapper.ts           Contract -> trade markers
    analytics-mappers/
      equity-mapper.ts               Contract -> Plotly traces
      heatmap-mapper.ts              Contract -> Plotly heatmap
      distribution-mapper.ts         Contract -> Plotly distribution

  types/
    contracts/
      chart.ts                       CandleBar, IchimokuPoint, FibLevel, MarketDataResponse
      analytics.ts                   EquityCurvePoint, BacktestSummary, BacktestDetail
      trades.ts                      Trade, TradeMarker, TradeListResponse, ChartAnnotationsResponse
      research.ts                    ResearchResult, ResearchRunSummary
```

## 3. Routing

The app uses Next.js App Router with a route group for the dashboard.

| Route | Page | Auth Required |
|-------|------|--------------|
| `/login` | Login form | No |
| `/` | Dashboard | Yes |
| `/charts` | KLineChart workspace | Yes |
| `/backtests` | Backtest list + run form | Yes |
| `/backtests/[id]` | Backtest detail | Yes |
| `/research` | Research matrix + rankings | Yes |
| `/bots` | Paper bot controls | Yes |
| `/trades` | Trade history table | Yes |
| `/trades/[id]` | Trade detail view | Yes |
| `/settings` | Configuration | Yes |
| `/system` | System diagnostics | Yes |

Route protection is handled by `middleware.ts` which checks for the `fibokei_token` cookie. Unauthenticated requests are redirected to `/login`. Authenticated users on `/login` are redirected to `/`.

## 4. State Management

| Concern | Tool | Scope |
|---------|------|-------|
| Auth state | React Context (`AuthProvider`) | Login, logout, user identity, isLoading |
| Theme/UI prefs | React Context | Selected instrument, timeframe, sidebar state |
| Server data | SWR hooks | Bots, trades, backtests, research, market data |
| Chart state | Local component state | Zoom, pan, selected trade, overlay toggles |

**Rules:**
- Context is never used for server data.
- SWR handles caching, revalidation, and polling for all API-fetched state.
- Chart state is ephemeral and local to the chart component tree.

## 5. API Client Pattern

The API client at `lib/api.ts` provides a typed interface to the backend.

**Key behaviors:**
- All requests use `credentials: "include"` to send the `fibokei_token` cookie.
- Base URL from `NEXT_PUBLIC_API_URL` (default: `http://localhost:8000`).
- All paths prefixed with `/api/v1`.
- 401 responses trigger redirect to `/login` (except auth endpoints).
- Login uses `application/x-www-form-urlencoded` (OAuth2 form).
- All other endpoints use `application/json`.

```typescript
// Usage pattern in SWR hooks
const { data } = useSWR("/backtests", () => api.listBacktests());
```

## 6. Contract Types

Frontend types in `types/contracts/` mirror backend Pydantic schemas exactly. These types are the shared language between frontend and backend. See [docs/api_contracts.md](../api_contracts.md) for full shapes.

| File | Types |
|------|-------|
| `chart.ts` | `CandleBar`, `IchimokuPoint`, `FibLevel`, `MarketDataResponse` |
| `analytics.ts` | `EquityCurvePoint`, `BacktestSummary`, `BacktestDetail` |
| `trades.ts` | `Trade`, `TradeMarker`, `PricePoint`, `TradeListResponse`, `ChartAnnotationsResponse` |
| `research.ts` | `ResearchResult`, `ResearchRunSummary` |

## 7. Chart Component Separation

KLineChart and Plotly components are strictly separated. See [docs/charting_spec.md](../charting_spec.md) for the full specification.

| Layer | Path | Engine |
|-------|------|--------|
| Chart core | `components/charts/core/` | KLineChart |
| Chart overlays | `components/charts/overlays/` | KLineChart |
| Chart annotations | `components/charts/annotations/` | KLineChart |
| Chart panels | `components/charts/panels/` | React (controls for KLineChart) |
| Analytics | `components/analytics/` | Plotly.js |

**Rules:**
- KLineChart logic and Plotly logic never share the same component layer.
- Research visualizations never go into chart components.
- Dashboard uses lightweight Plotly components (sparklines), not KLineChart.

## 8. Data Mapper Pattern

Raw API contract types are transformed before rendering:

```
API Response (contract type)
  -> chart-mapper or analytics-mapper
    -> Engine-specific format (KLineChart data / Plotly traces)
      -> Component renders
```

- `chart-mappers/` convert contracts to KLineChart internal formats.
- `analytics-mappers/` convert contracts to Plotly trace objects.
- Mappers are pure functions with no side effects.

## 9. Theme

| Token | Value | Usage |
|-------|-------|-------|
| Background layers | `#FAFAF9`, `#F5F5F4`, `#FFFFFF` | Off-white layered backgrounds |
| Text | `#1C1917` | Near-black for readability |
| Primary green | `#16A34A` | Main accent |
| Green dark | `#15803D` | Hover / active states |
| Green light | `#22C55E` | Highlights |
| Green accent | `#86EFAC` | Subtle accents |
| Font | Inter / system sans-serif | All text |
| Charts | Dark candles on light bg | Green/red for bullish/bearish |
