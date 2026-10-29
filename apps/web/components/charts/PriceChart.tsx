"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineWidth,
  type Logical,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  visiblePanes,
  type ChartModel,
  type LineStyleName,
  type PlacedTrade,
} from "@/lib/chart-model";
import { readChartTheme, type ChartTheme } from "@/lib/chart-theme";
import { distToSegment } from "@/lib/drawing-geometry";
import { PROVENANCE_EXECUTED } from "@/lib/format";
import type { Drawing } from "@/lib/types";
import {
  DrawingController,
  DrawingPrimitive,
  type DrawingControls,
} from "./drawing-primitive";
import { formatBarTime } from "./time";
import { OverlayPrimitive, type OverlayLayers } from "./overlay-primitive";
import { TradePrimitive } from "./trade-primitive";
import { TradeTooltip } from "./TradeTooltip";

/**
 * THE price chart: TradingView Lightweight Charts (Apache-2.0, plan D-F5).
 *
 * This module is the only one that imports the library, and it is only ever
 * reached through a dynamic import (components/LazyChart.tsx), so the engine
 * is in no route's first-load JavaScript (source-rules.spec.ts checks both).
 * Client-only: it touches the DOM in effects and nothing else.
 *
 * It draws what the API sent and nothing more. Candles from /bars; every
 * series, marker, level and regime from /overlays through the drawing model
 * (lib/chart-model.ts). The library's own indicator-free design is the reason
 * it was chosen over KLineChart (D-F5): there is nothing here to compute with.
 *
 * Colours are the design tokens, read from the page when the chart mounts and
 * again whenever `data-theme` or `data-pnl` changes on <html>.
 *
 * Lifecycle: one chart per mount; a ResizeObserver keeps it the size of its
 * box; every subscription, observer, price line and the chart itself are
 * released on unmount.
 *
 * Forming bar: when the bars payload sets `forming`, that candle is drawn at
 * 50% opacity. The browser does not decide which bar is still open.
 */

export interface PriceChartProps {
  model: ChartModel;
  /** Series groups the operator has hidden (OverlayLegend). */
  hidden: ReadonlySet<string>;
  layers: OverlayLayers & { levels: boolean };
  /** Price decimals from the instrument's pip size; null when not known. */
  priceDecimals: number | null;
  /** Decimals per non-price pane (undeclared precision, lib/format.ts rule). */
  paneDecimals: ReadonlyMap<string, number>;
  label: string;
  describedBy?: string;
  /** The bar the keyboard cursor is on, or null (follow the pointer). */
  cursor: number | null;
  onCursor: (index: number | null, source: "pointer" | "keyboard") => void;
  /** Paper and backtest trades placed on the bars (lib/chart-model.ts). */
  trades?: readonly PlacedTrade[];
  tradeLayers?: { backtest: boolean; paper: boolean };
  selectedTrade?: string | null;
  /** A request to bring a trade into view; `nonce` repeats a request for the same trade. */
  focusTrade?: { key: string; nonce: number } | null;
  onTradeClick?: (key: string) => void;
  /** Operator drawings; absent until the drawing layer has loaded. */
  drawings?: ChartDrawings | null;
}

/** What the chart needs to draw and edit the operator's drawings. */
export interface ChartDrawings {
  /** In scope for this timeframe. */
  visible: readonly Drawing[];
  /** The whole set, every timeframe (what a commit replaces). */
  all: readonly Drawing[];
  others: readonly { owner: string; drawing: Drawing }[];
  selectedId: string | null;
  hiddenAll: boolean;
  controls: DrawingControls;
  onCommit: (next: Drawing[], selectId: string | null) => void;
  onSelect: (id: string | null) => void;
  onRequestText: (
    at: { t: string; price: number },
    screen: { x: number; y: number },
    editId: string | null,
  ) => void;
  onToolDone: () => void;
  /** A finished measurement, as words (for a live region), or null when cleared. */
  onMeasure: (text: string | null) => void;
}

const LIBRARY_LINE_STYLE: Record<LineStyleName, LineStyle> = {
  solid: LineStyle.Solid,
  dashed: LineStyle.Dashed,
  dotted: LineStyle.Dotted,
  "large-dashed": LineStyle.LargeDashed,
  "sparse-dotted": LineStyle.SparseDotted,
};

const LEVEL_STYLE: Record<"entry" | "stop" | "target", LineStyle> = {
  entry: LineStyle.Solid,
  stop: LineStyle.Dashed,
  target: LineStyle.Dotted,
};

function priceFormat(decimals: number) {
  return { type: "price" as const, precision: decimals, minMove: 10 ** -decimals };
}

function chartOptions(theme: ChartTheme) {
  return {
    layout: {
      background: { type: ColorType.Solid, color: theme.background },
      textColor: theme.textMuted,
      fontFamily: theme.fontUi,
      fontSize: 11,
      // The logo injects a <style> element, which the CSP forbids; TradingView
      // is credited in a text link under the chart instead (Apache-2.0 NOTICE).
      attributionLogo: false,
      panes: { separatorColor: theme.axis, separatorHoverColor: theme.grid, enableResize: true },
    },
    grid: {
      vertLines: { color: theme.grid },
      horzLines: { color: theme.grid },
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshairLabel },
      horzLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshairLabel },
    },
    rightPriceScale: { borderColor: theme.axis },
    timeScale: { borderColor: theme.axis },
  };
}

/** Half-opacity ink for a bar the server marked forming. Hex only; other forms stay solid. */
function fade(color: string): string {
  return color.startsWith("#") && color.length === 7 ? `${color}80` : color;
}

function candleOptions(theme: ChartTheme) {
  // Neutral ink. Rising bars hollow (body in the chart background), falling
  // bars filled: direction is a shape, and green/red stay P&L-only.
  return {
    upColor: theme.background,
    downColor: theme.candle,
    borderUpColor: theme.candle,
    borderDownColor: theme.candle,
    wickUpColor: theme.candle,
    wickDownColor: theme.candle,
    borderVisible: true,
  };
}

export default function PriceChart({
  model,
  hidden,
  layers,
  priceDecimals,
  paneDecimals,
  label,
  describedBy,
  cursor,
  onCursor,
  trades = [],
  tradeLayers = { backtest: true, paper: true },
  selectedTrade = null,
  focusTrade = null,
  onTradeClick,
  drawings = null,
}: PriceChartProps) {
  const box = useRef<HTMLDivElement>(null);
  const tradeRef = useRef<TradePrimitive | null>(null);
  const drawingRef = useRef<DrawingPrimitive | null>(null);
  const controllerRef = useRef<DrawingController | null>(null);
  const [hover, setHover] = useState<{ key: string; x: number; y: number } | null>(null);
  const tradesRef = useRef(trades);
  const layersRef = useRef(tradeLayers);
  const onTradeClickRef = useRef(onTradeClick);
  const drawingsRef = useRef(drawings);
  const selectedRef = useRef(selectedTrade);
  const writeSelectedRef = useRef<() => void>(() => undefined);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const primitiveRef = useRef<OverlayPrimitive | null>(null);
  const extraRef = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);
  const linesRef = useRef<IPriceLine[]>([]);
  // Read on the client only: this module is reached through a lazy import
  // that renders after data arrives, never during server rendering.
  const [theme, setTheme] = useState<ChartTheme | null>(() =>
    typeof document === "undefined" ? null : readChartTheme(),
  );
  const onCursorRef = useRef(onCursor);
  const modelRef = useRef(model);
  useEffect(() => {
    onCursorRef.current = onCursor;
    modelRef.current = model;
    tradesRef.current = trades;
    layersRef.current = tradeLayers;
    onTradeClickRef.current = onTradeClick;
    drawingsRef.current = drawings;
    selectedRef.current = selectedTrade;
    writeSelectedRef.current = () => {
      const el = box.current;
      const chart = chartRef.current;
      const candles = candleRef.current;
      if (!el || !chart || !candles) return;
      const trade = tradesRef.current.find((t) => t.key === selectedRef.current);
      const point = (index: number, price: number | null) => {
        if (price === null) return "";
        const x = chart.timeScale().logicalToCoordinate(index as Logical);
        const y = candles.priceToCoordinate(price);
        return x === null || y === null ? "" : `${Math.round(x)},${Math.round(y)}`;
      };
      el.dataset.selectedTrade = trade ? trade.key : "";
      el.dataset.selectedEntry = trade ? point(trade.entryIndex, trade.entryPrice) : "";
      el.dataset.selectedExit = trade ? point(trade.exitIndex, trade.exitPrice) : "";
    };
  });

  // ---- mount: the chart, the candle series, the overlay layer, observers.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const initial = readChartTheme();
    const chart = createChart(el, {
      ...chartOptions(initial),
      width: Math.max(1, el.clientWidth),
      height: Math.max(1, el.clientHeight),
      localization: {
        locale: "en-GB",
        timeFormatter: (time: Time) => formatBarTime(time as UTCTimestamp),
      },
      timeScale: {
        ...chartOptions(initial).timeScale,
        timeVisible: true,
        secondsVisible: false,
        barSpacing: 8,
        rightOffset: 4,
      },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      ...candleOptions(initial),
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const primitive = new OverlayPrimitive({
      model: modelRef.current,
      theme: initial,
      layers: { regimes: true, signals: true, fills: true },
    });
    candles.attachPrimitive(primitive);
    const tradeLayer = new TradePrimitive({
      trades: [],
      theme: initial,
      backtest: true,
      paper: true,
      selected: null,
      hovered: null,
    });
    candles.attachPrimitive(tradeLayer);
    const drawingLayer = new DrawingPrimitive({
      drawings: [],
      others: [],
      times: modelRef.current.times,
      theme: initial,
      selectedId: null,
      hiddenAll: false,
      priceDecimals: null,
    });
    candles.attachPrimitive(drawingLayer);
    const controller = new DrawingController(el, chart, candles, drawingLayer, {
      controls: () =>
        drawingsRef.current?.controls ?? { tool: "select", canEdit: false, lockedAll: true, scope: null },
      commit: (next, id) => drawingsRef.current?.onCommit(next, id),
      select: (id) => drawingsRef.current?.onSelect(id),
      requestText: (at, screen, editId) => drawingsRef.current?.onRequestText(at, screen, editId),
      toolDone: () => drawingsRef.current?.onToolDone(),
      measured: (text) => drawingsRef.current?.onMeasure(text),
      allDrawings: () => [...(drawingsRef.current?.all ?? [])],
    });
    chartRef.current = chart;
    candleRef.current = candles;
    primitiveRef.current = primitive;
    tradeRef.current = tradeLayer;
    drawingRef.current = drawingLayer;
    controllerRef.current = controller;

    const resize = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      chart.resize(Math.max(1, Math.floor(rect.width)), Math.max(1, Math.floor(rect.height)));
    });
    resize.observe(el);

    const retheme = new MutationObserver(() => setTheme(readChartTheme()));
    retheme.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "data-pnl"],
    });

    // The trade under the pointer: nearest entry or exit marker, or connector,
    // within a few pixels. Positions are the chart's own conversions.
    const tradeAt = (x: number, y: number): string | null => {
      const ts = chart.timeScale();
      let best: { key: string; d: number } | null = null;
      for (const trade of tradesRef.current) {
        const shown = trade.source === "backtest" ? layersRef.current.backtest : layersRef.current.paper;
        if (!shown || trade.entryPrice === null || trade.exitPrice === null) continue;
        const x1 = ts.logicalToCoordinate(trade.entryIndex as Logical);
        const x2 = ts.logicalToCoordinate(trade.exitIndex as Logical);
        const y1 = candles.priceToCoordinate(trade.entryPrice);
        const y2 = candles.priceToCoordinate(trade.exitPrice);
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue;
        if (x < Math.min(x1, x2) - 10 || x > Math.max(x1, x2) + 10) continue;
        const d = Math.min(
          Math.hypot(x - x1, y - y1),
          Math.hypot(x - x2, y - y2),
          distToSegment({ x, y }, { x: x1, y: y1 }, { x: x2, y: y2 }) + 2,
        );
        if (d <= 8 && (best === null || d < best.d)) best = { key: trade.key, d };
      }
      return best ? best.key : null;
    };

    const onMove = (param: MouseEventParams<Time>) => {
      // Programmatic moves (the keyboard cursor) carry no source event.
      if (param.sourceEvent === undefined) return;
      if (param.point === undefined || param.logical === undefined) {
        onCursorRef.current(null, "pointer");
        setHover(null);
        return;
      }
      const index = Math.round(param.logical);
      const count = modelRef.current.times.length;
      onCursorRef.current(index >= 0 && index < count ? index : null, "pointer");
      const key = tradeAt(param.point.x, param.point.y);
      setHover((prev) => {
        if (key === null) return prev === null ? prev : null;
        const point = param.point as { x: number; y: number };
        if (prev && prev.key === key && Math.abs(prev.x - point.x) < 24 && Math.abs(prev.y - point.y) < 24) {
          return prev;
        }
        return { key, x: point.x, y: point.y };
      });
    };
    chart.subscribeCrosshairMove(onMove);

    // What the chart shows, as DOM (tests and assistive tech read these; the
    // canvas cannot be read): the visible bar range, and where the selected
    // trade's entry and exit markers are.
    const writeRange = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      el.dataset.rangeFrom = range ? range.from.toFixed(2) : "";
      el.dataset.rangeTo = range ? range.to.toFixed(2) : "";
      writeSelectedRef.current();
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(writeRange);

    const onClick = (param: MouseEventParams<Time>) => {
      if (!param.point) return;
      const key = tradeAt(param.point.x, param.point.y);
      if (key) onTradeClickRef.current?.(key);
    };
    chart.subscribeClick(onClick);

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(writeRange);
      chart.unsubscribeClick(onClick);
      controller.dispose();
      candles.detachPrimitive(tradeLayer);
      candles.detachPrimitive(drawingLayer);
      tradeRef.current = null;
      drawingRef.current = null;
      controllerRef.current = null;
      chart.unsubscribeCrosshairMove(onMove);
      resize.disconnect();
      retheme.disconnect();
      candles.detachPrimitive(primitive);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      primitiveRef.current = null;
      extraRef.current = [];
      linesRef.current = [];
    };
  }, []);

  // ---- theme: every colour, on every change of theme or P&L preset.
  useEffect(() => {
    const chart = chartRef.current;
    const candles = candleRef.current;
    if (!chart || !candles || !theme) return;
    chart.applyOptions(chartOptions(theme));
    candles.applyOptions(candleOptions(theme));
  }, [theme]);

  // ---- data: candles, volume and the indicator series, pane by pane.
  useEffect(() => {
    const chart = chartRef.current;
    const candles = candleRef.current;
    if (!chart || !candles || !theme) return;

    const formingInk = fade(theme.candle);
    candles.setData(
      model.bars.map((b, i) => ({
        time: model.times[i] as UTCTimestamp,
        open: b.o,
        high: b.h,
        low: b.l,
        close: b.c,
        ...(b.forming
          ? { color: formingInk, borderColor: formingInk, wickColor: formingInk }
          : {}),
      })),
    );
    if (priceDecimals !== null) candles.applyOptions({ priceFormat: priceFormat(priceDecimals) });

    for (const s of extraRef.current) chart.removeSeries(s);
    extraRef.current = [];

    const panes = visiblePanes(model, hidden);
    const paneIndex = new Map(panes.map((p, i) => [p.id, i]));
    if (model.hasVolume) {
      const volume = chart.addSeries(
        HistogramSeries,
        {
          color: theme.volume,
          priceFormat: { type: "volume" },
          priceLineVisible: false,
          lastValueVisible: false,
        },
        paneIndex.get("volume"),
      );
      volume.setData(
        model.bars.map((b, i) =>
          typeof b.v === "number"
            ? { time: model.times[i] as UTCTimestamp, value: b.v }
            : { time: model.times[i] as UTCTimestamp },
        ),
      );
      extraRef.current.push(volume);
    }
    for (const s of model.series) {
      if (hidden.has(s.group)) continue;
      const decimals = s.pane === "price" ? priceDecimals : paneDecimals.get(s.pane);
      const line = chart.addSeries(
        LineSeries,
        {
          color: theme.series[s.role],
          lineWidth: 1 as LineWidth,
          lineStyle: LIBRARY_LINE_STYLE[s.lineStyle],
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          title: "",
          ...(decimals === null || decimals === undefined ? {} : { priceFormat: priceFormat(decimals) }),
        },
        paneIndex.get(s.pane),
      );
      // A missing value is whitespace (a gap in the line), never a zero.
      line.setData(
        s.values.map((v, i) =>
          v === null
            ? { time: model.times[i] as UTCTimestamp }
            : { time: model.times[i] as UTCTimestamp, value: v },
        ),
      );
      extraRef.current.push(line);
    }
    // Price gets the room; every other pane an equal smaller share.
    chart.panes().forEach((pane, i) => pane.setStretchFactor(i === 0 ? 4 : 1));
  }, [model, hidden, theme, priceDecimals, paneDecimals]);

  // ---- levels: entry, stop and target as labelled price lines.
  useEffect(() => {
    const candles = candleRef.current;
    if (!candles || !theme) return;
    for (const line of linesRef.current) candles.removePriceLine(line);
    linesRef.current = [];
    if (!layers.levels) return;
    for (const level of model.levels) {
      if (level.price.value === null) continue;
      // A closed trade's recorded stop or target stood for that trade's life:
      // it is a segment on the trade layer, never a line across the chart.
      if (level.to !== null) continue;
      linesRef.current.push(
        candles.createPriceLine({
          price: level.price.value,
          color: theme.provenance[level.provenance],
          lineWidth: 1,
          lineStyle: LEVEL_STYLE[level.role],
          axisLabelVisible: true,
          title: `${level.role.toUpperCase()}${PROVENANCE_EXECUTED[level.provenance] ? "" : " (sim)"}`,
        }),
      );
    }
  }, [model, theme, layers.levels]);

  // ---- the overlay layer follows the model, theme and layer toggles.
  useEffect(() => {
    if (!theme) return;
    primitiveRef.current?.update({
      model,
      theme,
      layers: { regimes: layers.regimes, signals: layers.signals, fills: layers.fills },
    });
  }, [model, theme, layers.regimes, layers.signals, layers.fills]);

  // ---- the trade layer follows the trades, toggles, selection and hover.
  const hovered = hover?.key ?? null;
  useEffect(() => {
    if (!theme) return;
    tradeRef.current?.update({
      trades,
      theme,
      backtest: tradeLayers.backtest,
      paper: tradeLayers.paper,
      selected: selectedTrade,
      hovered,
    });
    writeSelectedRef.current();
  }, [trades, theme, tradeLayers.backtest, tradeLayers.paper, selectedTrade, hovered]);

  // ---- bring a trade into view when the table asks.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !focusTrade) return;
    const trade = tradesRef.current.find((t) => t.key === focusTrade.key);
    if (!trade) return;
    const ts = chart.timeScale();
    const range = ts.getVisibleLogicalRange();
    const span = trade.exitIndex - trade.entryIndex;
    const current = range ? range.to - range.from : 80;
    const width = Math.max(Math.min(current, 160), span * 1.6 + 12);
    const mid = (trade.entryIndex + trade.exitIndex) / 2;
    // Centre the trade, but never scroll past the data into empty space.
    const last = modelRef.current.times.length - 1;
    let from = mid - width / 2;
    let to = mid + width / 2;
    if (to > last + 4) {
      from -= to - (last + 4);
      to = last + 4;
    }
    if (from < -4) {
      to += -4 - from;
      from = -4;
    }
    ts.setVisibleLogicalRange({ from, to });
    requestAnimationFrame(() => writeSelectedRef.current());
  }, [focusTrade]);

  // ---- the drawing layer follows the operator's set and the view toggles.
  const drawingTimes = model.times;
  useEffect(() => {
    if (!theme) return;
    drawingRef.current?.update({
      drawings: drawings?.visible ?? [],
      others: drawings?.others ?? [],
      times: drawingTimes,
      theme,
      selectedId: drawings?.selectedId ?? null,
      hiddenAll: drawings?.hiddenAll ?? false,
      priceDecimals,
    });
    controllerRef.current?.sync();
  }, [drawings, drawingTimes, theme, priceDecimals]);

  // ---- keyboard cursor: the crosshair follows it; the readout reads it.
  useEffect(() => {
    const chart = chartRef.current;
    const candles = candleRef.current;
    if (!chart || !candles) return;
    if (cursor === null) return;
    const bar = model.bars[cursor];
    const time = model.times[cursor];
    if (!bar || time === undefined) return;
    const range = chart.timeScale().getVisibleLogicalRange();
    if (range && (cursor < range.from || cursor > range.to)) {
      const width = range.to - range.from;
      chart.timeScale().setVisibleLogicalRange({ from: cursor - width / 2, to: cursor + width / 2 });
    }
    chart.setCrosshairPosition(bar.c, time as UTCTimestamp, candles);
  }, [cursor, model]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const count = model.times.length;
    if (count === 0) return;
    const current = cursor === null ? count - 1 : cursor;
    let next: number | null = current;
    switch (event.key) {
      case "ArrowLeft":
        next = Math.max(0, current - (event.shiftKey ? 10 : 1));
        break;
      case "ArrowRight":
        next = Math.min(count - 1, current + (event.shiftKey ? 10 : 1));
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = count - 1;
        break;
      case "Escape":
        chartRef.current?.clearCrosshairPosition();
        next = null;
        break;
      default:
        return;
    }
    event.preventDefault();
    onCursor(next, "keyboard");
  };

  const tool = drawings?.controls.tool ?? "select";
  const hoveredTrade = hover ? trades.find((t) => t.key === hover.key) ?? null : null;
  return (
    <div className="price-chart-wrap" data-tool={tool}>
      <div
        ref={box}
        className="price-chart"
        data-testid="price-chart"
        data-ready={theme !== null}
        data-tool={tool}
        role="group"
        tabIndex={0}
        aria-label={label}
        aria-describedby={describedBy}
        aria-roledescription="price chart"
        onKeyDown={onKeyDown}
        onPointerLeave={() => {
          onCursor(null, "pointer");
          setHover(null);
        }}
      />
      {hoveredTrade && hover ? (
        <TradeTooltip trade={hoveredTrade} x={hover.x} y={hover.y} priceDecimals={priceDecimals} />
      ) : null}
    </div>
  );
}
