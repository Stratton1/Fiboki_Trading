"use client";

import { useEffect, useRef, useCallback, useImperativeHandle, forwardRef } from "react";
import { init, dispose } from "klinecharts";
import type { Chart, KLineData } from "klinecharts";
import { mapCandlesToKLine } from "@/lib/chart-mappers/candle-mapper";
import {
  registerIchimokuIndicator,
  setIchimokuData,
  clearIchimokuData,
  ICHIMOKU_INDICATOR_NAME,
} from "@/components/charts/overlays/IchimokuOverlay";
import type { MarketDataResponse } from "@/types/contracts/chart";
import type { ChartDrawing } from "@/types/contracts/drawings";

/** Extract valid {timestamp, value} entries from klinecharts Partial<Point>[] */
function extractPoints(
  pts: Array<{ timestamp?: number; value?: number }>
): Array<{ timestamp: number; value: number }> {
  return pts
    .filter(
      (p): p is { timestamp: number; value: number } =>
        p.timestamp !== undefined && p.value !== undefined
    )
    .map((p) => ({ timestamp: p.timestamp, value: p.value }));
}

export interface TradingChartHandle {
  resetView: () => void;
  fitToData: () => void;
}

interface TradingChartProps {
  data: MarketDataResponse | null;
  ichimokuEnabled: boolean;
  activeDrawingTool: string | null;
  savedDrawings: ChartDrawing[];
  onDrawingCreated: (drawing: {
    tool_type: string;
    points: Array<{ timestamp: number; value: number }>;
  }) => void;
  onDrawingUpdated: (overlayId: string, points: Array<{ timestamp: number; value: number }>) => void;
  onDrawingRemoved: (overlayId: string) => void;
  /** Instrument timeframe (M15, M30, H1, H4, D1). Drives klinecharts period
   * so tooltip dates and time-axis grouping match what's actually rendered. */
  timeframe?: string;
  /** Price precision for the y-axis formatter (e.g. 3 for JPY pairs, 5 for FX). */
  precision?: number;
}

/** Map an internal timeframe code to klinecharts {span, type}. Falls back to
 * H1 for unknowns so the chart still renders. */
function timeframeToPeriod(tf?: string): { span: number; type: "minute" | "hour" | "day" } {
  switch (tf) {
    case "M15": return { span: 15, type: "minute" };
    case "M30": return { span: 30, type: "minute" };
    case "H4":  return { span: 4,  type: "hour" };
    case "D1":  return { span: 1,  type: "day" };
    case "H1":
    default:    return { span: 1,  type: "hour" };
  }
}

const TradingChart = forwardRef<TradingChartHandle, TradingChartProps>(function TradingChart({
  data,
  ichimokuEnabled,
  activeDrawingTool,
  savedDrawings,
  onDrawingCreated,
  onDrawingUpdated,
  onDrawingRemoved,
  timeframe,
  precision,
}, ref) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const ichimokuActiveRef = useRef(false);
  const dataRef = useRef<KLineData[]>([]);

  // Track drawing overlay IDs to avoid conflicting with Ichimoku overlays
  const drawingOverlayIdsRef = useRef<Set<string>>(new Set());
  // Map klinecharts overlay ID -> backend drawing ID
  const overlayToDrawingIdRef = useRef<Map<string, number>>(new Map());

  // Store callbacks in refs so overlay handlers always see latest versions
  const onDrawingCreatedRef = useRef(onDrawingCreated);
  onDrawingCreatedRef.current = onDrawingCreated;
  const onDrawingUpdatedRef = useRef(onDrawingUpdated);
  onDrawingUpdatedRef.current = onDrawingUpdated;
  const onDrawingRemovedRef = useRef(onDrawingRemoved);
  onDrawingRemovedRef.current = onDrawingRemoved;

  // Expose resetView and fitToData to parent via ref
  useImperativeHandle(ref, () => ({
    resetView() {
      const chart = chartRef.current;
      if (!chart) return;
      chart.scrollToRealTime();
    },
    fitToData() {
      const chart = chartRef.current;
      if (!chart || !dataRef.current.length || !containerRef.current) return;
      // True "fit": size each bar so all bars fit horizontally, then scroll
      // to the first bar. `scrollToDataIndex` alone only scrolls — it does
      // not change zoom — so without setBarSpace the operator sees the first
      // bar at the previous zoom level instead of the full series.
      const bars = dataRef.current.length;
      // Leave ~60px on the right for the price axis.
      const usable = Math.max(100, containerRef.current.clientWidth - 60);
      const space = Math.max(2, Math.floor(usable / bars));
      chart.setBarSpace(space);
      chart.scrollToDataIndex(0, 300);
    },
  }), []);

  // Register the custom Ichimoku indicator once on mount
  useEffect(() => {
    registerIchimokuIndicator();
  }, []);

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return;

    // klinecharts uses height:100% internally, which resolves against the
    // parent's explicit `height` — not `min-height`. Convert the rendered
    // min-height into a concrete pixel height so the inner 100% works.
    const el = containerRef.current;
    el.style.height = `${el.offsetHeight}px`;

    const chart = init(containerRef.current, {
      styles: {
        candle: {
          type: "candle_solid",
          priceMark: {
            show: true,
            last: {
              show: true,
              upColor: "#16A34A",
              downColor: "#EF4444",
              noChangeColor: "#888888",
            },
          },
          bar: {
            upColor: "#16A34A",
            downColor: "#EF4444",
            noChangeColor: "#888888",
            upBorderColor: "#16A34A",
            downBorderColor: "#EF4444",
            noChangeBorderColor: "#888888",
            upWickColor: "#16A34A",
            downWickColor: "#EF4444",
            noChangeWickColor: "#888888",
          },
          tooltip: {
            showRule: "follow_cross",
          },
        },
        grid: {
          horizontal: { show: true, color: "rgba(150,150,150,0.1)" },
          vertical: { show: true, color: "rgba(150,150,150,0.1)" },
        },
      },
    });

    chartRef.current = chart!;

    // Resize chart when window size changes
    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      if (containerRef.current) {
        dispose(containerRef.current);
      }
      chartRef.current = null;
      ichimokuActiveRef.current = false;
      drawingOverlayIdsRef.current.clear();
      overlayToDrawingIdRef.current.clear();
      clearIchimokuData();
    };
  }, []);

  // Apply data when it changes
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !data) return;

    if (data.ichimoku) {
      setIchimokuData(data.ichimoku);
    } else {
      clearIchimokuData();
    }

    const klineData = mapCandlesToKLine(data.candles);
    dataRef.current = klineData;

    const instrument = data.instrument || "UNKNOWN";
    // klinecharts v10 sets price/volume precision via setSymbol — without
    // pricePrecision the y-axis rounds to ~2dp and tight FX ranges show
    // duplicate "1.04" tick labels.
    chart.setSymbol({
      ticker: instrument,
      ...(precision != null ? { pricePrecision: precision, volumePrecision: 0 } : {}),
    });
    // Map the active timeframe to klinecharts' period so tooltip date
    // formatting and intra-bar grouping match the rendered data.
    chart.setPeriod(timeframeToPeriod(timeframe));
    chart.setDataLoader({
      getBars: ({ type, callback }) => {
        if (type === "init" || type === "backward") {
          callback(dataRef.current, false);
        } else {
          callback([], false);
        }
      },
    });
  }, [data, timeframe, precision]);

  // Toggle Ichimoku indicator
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    if (ichimokuEnabled && !ichimokuActiveRef.current) {
      chart.createIndicator(ICHIMOKU_INDICATOR_NAME, true);
      ichimokuActiveRef.current = true;
    } else if (!ichimokuEnabled && ichimokuActiveRef.current) {
      chart.removeIndicator({ name: ICHIMOKU_INDICATOR_NAME });
      ichimokuActiveRef.current = false;
    }
  }, [ichimokuEnabled]);

  // Active drawing tool — enter drawing mode when tool is selected
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !activeDrawingTool) return;

    chart.createOverlay({
      name: activeDrawingTool,
      onDrawEnd: ({ overlay }) => {
        const points = extractPoints(overlay.points ?? []);
        if (overlay.id) {
          drawingOverlayIdsRef.current.add(overlay.id);
        }
        onDrawingCreatedRef.current({ tool_type: overlay.name, points });
      },
      onPressedMoving: ({ overlay }) => {
        if (!overlay.id) return;
        const points = extractPoints(overlay.points ?? []);
        onDrawingUpdatedRef.current(overlay.id, points);
      },
      onRemoved: ({ overlay }) => {
        if (overlay.id) {
          drawingOverlayIdsRef.current.delete(overlay.id);
          onDrawingRemovedRef.current(overlay.id);
        }
      },
    });
  }, [activeDrawingTool]);

  // Load saved drawings onto the chart
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Remove only existing drawing overlays, not Ichimoku/indicator overlays
    for (const overlayId of drawingOverlayIdsRef.current) {
      chart.removeOverlay({ id: overlayId });
    }
    drawingOverlayIdsRef.current.clear();
    overlayToDrawingIdRef.current.clear();

    if (!savedDrawings.length) return;

    for (const drawing of savedDrawings) {
      const overlayId = `saved_${drawing.id}`;

      chart.createOverlay({
        name: drawing.tool_type,
        id: overlayId,
        points: drawing.points.map((p) => ({ timestamp: p.timestamp, value: p.value })),
        lock: drawing.lock,
        visible: drawing.visible,
        styles: drawing.styles || undefined,
        onPressedMoving: ({ overlay }) => {
          if (!overlay.id) return;
          const points = extractPoints(overlay.points ?? []);
          onDrawingUpdatedRef.current(overlay.id, points);
        },
        onRemoved: ({ overlay }) => {
          if (overlay.id) {
            drawingOverlayIdsRef.current.delete(overlay.id);
            onDrawingRemovedRef.current(overlay.id);
          }
        },
      });

      drawingOverlayIdsRef.current.add(overlayId);
      overlayToDrawingIdRef.current.set(overlayId, drawing.id);
    }
  }, [savedDrawings]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full min-h-[500px] bg-background-card rounded-lg border border-gray-200"
    />
  );
});

export default TradingChart;
