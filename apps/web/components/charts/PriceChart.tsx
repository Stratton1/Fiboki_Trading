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
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { visiblePanes, type ChartModel, type LineStyleName } from "@/lib/chart-model";
import { readChartTheme, type ChartTheme } from "@/lib/chart-theme";
import { PROVENANCE_EXECUTED } from "@/lib/format";
import { formatBarTime } from "./time";
import { OverlayPrimitive, type OverlayLayers } from "./overlay-primitive";

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
 * Forming bar: the bars endpoint does not flag a forming (still open) bar, so
 * no bar is drawn as forming. When the API adds the flag, draw that bar at 50%
 * opacity here (a per-bar `color`/`borderColor`/`wickColor` override).
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
}: PriceChartProps) {
  const box = useRef<HTMLDivElement>(null);
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
    chartRef.current = chart;
    candleRef.current = candles;
    primitiveRef.current = primitive;

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

    const onMove = (param: MouseEventParams<Time>) => {
      // Programmatic moves (the keyboard cursor) carry no source event.
      if (param.sourceEvent === undefined) return;
      if (param.point === undefined || param.logical === undefined) {
        onCursorRef.current(null, "pointer");
        return;
      }
      const index = Math.round(param.logical);
      const count = modelRef.current.times.length;
      onCursorRef.current(index >= 0 && index < count ? index : null, "pointer");
    };
    chart.subscribeCrosshairMove(onMove);

    return () => {
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

    candles.setData(
      model.bars.map((b, i) => ({
        time: model.times[i] as UTCTimestamp,
        open: b.o,
        high: b.h,
        low: b.l,
        close: b.c,
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

  return (
    <div
      ref={box}
      className="price-chart"
      data-testid="price-chart"
      data-ready={theme !== null}
      role="group"
      tabIndex={0}
      aria-label={label}
      aria-describedby={describedBy}
      aria-roledescription="price chart"
      onKeyDown={onKeyDown}
      onPointerLeave={() => onCursor(null, "pointer")}
    />
  );
}
