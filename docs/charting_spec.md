# FIBOKEI Charting Specification

## 1. Engine Selection

FIBOKEI uses two charting engines with strict separation.

| Engine | Purpose | Package |
|--------|---------|---------|
| KLineChart | Financial candlestick charts with overlays and trade markers | `klinecharts` |
| Plotly.js | Analytics, research, and summary visualizations | `react-plotly.js` |

**Rule:** KLineChart and Plotly never share the same component layer. A component uses one engine or the other, never both.

## 2. KLineChart -- Financial Charts

### 2.1 Responsibilities

- Candlestick rendering (OHLCV)
- Instrument and timeframe switching
- Ichimoku Cloud overlay (tenkan, kijun, senkou A/B, chikou)
- Fibonacci retracement/extension overlays
- Trade entry/exit markers, SL/TP lines, partial exit markers
- Strategy annotations (cloud interaction zones, swing labels, confidence markers)
- Zoom, pan, fit-to-data
- Click marker to open trade detail panel
- Jump to trade timestamp
- Show/hide individual Ichimoku components
- Show/hide Fibonacci overlays
- Toggle marker visibility (entries, exits, SL/TP)

### 2.2 Component Structure

```
components/charts/
  core/
    TradingChart.tsx          Main chart container, init, lifecycle
    ChartProvider.tsx          Chart instance context (shared ref)
  overlays/
    IchimokuOverlay.ts         registerIndicator() -- tenkan, kijun, senkou, chikou
    FibonacciOverlay.ts        registerIndicator() -- retracement/extension levels
  annotations/
    TradeMarkers.ts            createOverlay() -- entry/exit arrows
    StrategyLabels.ts          createOverlay() -- cloud interaction, swing labels
    StopTargetLines.ts         createOverlay() -- SL/TP/trailing lines
  panels/
    ChartToolbar.tsx           Instrument/timeframe selectors
    OverlayControls.tsx        Toggle checkboxes for indicator visibility
    MarkerControls.tsx         Toggle trade marker visibility
    TradeInspector.tsx         Selected trade detail panel
```

### 2.3 Data Pipeline

```
GET /api/v1/market-data/{instrument}/{timeframe}
  -> MarketDataResponse { candles, ichimoku }
    -> candle-mapper.ts: CandleBar[] -> KLineChart KLineData[]
    -> ichimoku-mapper.ts: IchimokuPoint[] -> overlay data arrays

GET /api/v1/charts/annotations/{backtest_id}
  -> ChartAnnotationsResponse { trade_markers, strategy_annotations }
    -> annotation-mapper.ts: TradeMarker[] -> overlay point objects
```

### 2.4 Ichimoku Overlay

Registered via `registerIndicator()` in KLineChart. The backend precomputes all five Ichimoku components and returns them in the `ichimoku` array of `MarketDataResponse`.

| Line | Color Convention | Source Field |
|------|-----------------|-------------|
| Tenkan-sen | Short-period line | `tenkan` |
| Kijun-sen | Medium-period line | `kijun` |
| Senkou Span A | Cloud boundary (filled between A and B) | `senkou_a` |
| Senkou Span B | Cloud boundary | `senkou_b` |
| Chikou Span | Lagging line | `chikou` |

Each component can be toggled independently via `OverlayControls.tsx`.

### 2.5 Trade Markers

Rendered via `createOverlay()` in KLineChart. The backend returns normalized `TradeMarker` objects with:

- Entry point (timestamp + price)
- Exit point (timestamp + price)
- Stop-loss levels (array of price points)
- Take-profit levels (array of price points)
- Partial exits (array of price points)
- Direction (LONG/SHORT)
- Outcome (win/loss/breakeven)

Markers are color-coded: green for winning trades, red for losing trades. Entry arrows point up (LONG) or down (SHORT). Clicking a marker opens `TradeInspector.tsx`.

### 2.6 Pages Using KLineChart

| Page | Usage |
|------|-------|
| `/charts` | Full workspace -- candlesticks, Ichimoku, Fibonacci, all controls |
| `/backtests/[id]` | Trade markers overlaid on price chart for inspection |
| `/trades/[id]` | Chart centered on entry/exit with markers, SL/TP lines |
| `/bots` | Bot review with current position on chart |

## 3. Plotly.js -- Analytics Charts

### 3.1 Responsibilities

- Equity curves and cumulative returns
- Drawdown curves
- Monthly return heatmaps
- PnL and duration distributions
- Strategy vs. instrument heatmaps
- Ranking comparisons, composite score visualizations
- Sharpe vs. profit factor scatter charts
- Dashboard mini-charts (sparklines)
- Multi-run and multi-strategy comparisons

### 3.2 Component Structure

```
components/analytics/
  EquityCurve.tsx              Line chart -- equity over time / bar index
  DrawdownChart.tsx            Area chart -- drawdown percentage
  Heatmap.tsx                  Strategy x instrument or monthly returns
  Distribution.tsx             Histogram -- PnL or trade duration
  MiniSummary.tsx              Dashboard sparklines (compact)
  ComparisonChart.tsx          Multi-strategy / multi-run overlay
```

### 3.3 Data Pipeline

```
GET /api/v1/backtests/{id}/equity-curve
  -> { equity_curve: number[] }
    -> equity-mapper.ts -> Plotly trace

GET /api/v1/backtests/{id} (metrics_json)
  -> BacktestDetail.metrics_json
    -> heatmap-mapper.ts -> Plotly heatmap data
    -> distribution-mapper.ts -> Plotly histogram data

GET /api/v1/research/rankings
  -> ResearchResult[]
    -> heatmap-mapper.ts -> strategy x instrument heatmap
```

### 3.4 Pages Using Plotly

| Page | Components Used |
|------|----------------|
| `/` (Dashboard) | `MiniSummary` sparklines, KPI summary visuals |
| `/backtests/[id]` | `EquityCurve`, `DrawdownChart`, `Distribution` |
| `/research` | `Heatmap`, `ComparisonChart` |

## 4. Separation Rules

1. **KLineChart components** live in `components/charts/`. They handle OHLCV rendering, indicator overlays, and trade annotations.

2. **Plotly components** live in `components/analytics/`. They handle equity curves, drawdowns, heatmaps, distributions, and sparklines.

3. **Never mixed:** A single component file never imports both KLineChart and Plotly.

4. **Research visualizations** never go into `components/charts/`.

5. **Trade annotations** never go into Plotly unless explicitly needed for an analytics view.

6. **Dashboard** uses lightweight Plotly sparklines (`MiniSummary`), not KLineChart instances. KLineChart is too heavy for dashboard summary cards.

## 5. Interaction Requirements

### KLineChart Interactions

- **Zoom/Pan:** Built-in KLineChart gesture handling.
- **Marker click:** Opens `TradeInspector` panel with trade details (PnL, duration, entry/exit prices, exit reason).
- **Jump to trade:** Centers chart on a specific trade timestamp.
- **Overlay toggles:** Show/hide Ichimoku lines, Fibonacci levels, trade markers independently.
- **Instrument/timeframe switch:** Toolbar selectors trigger new data fetch via SWR.

### Plotly Interactions

- **Hover:** Tooltips showing exact values.
- **Zoom:** Plotly built-in box/lasso zoom.
- **Click heatmap cell:** Navigate to filtered backtest/research detail.
