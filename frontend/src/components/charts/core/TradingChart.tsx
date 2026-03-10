"use client";

import { useEffect, useRef } from "react";
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

interface TradingChartProps {
  data: MarketDataResponse | null;
  ichimokuEnabled: boolean;
}

export default function TradingChart({
  data,
  ichimokuEnabled,
}: TradingChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const ichimokuActiveRef = useRef(false);
  const dataRef = useRef<KLineData[]>([]);

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
      clearIchimokuData();
    };
  }, []);

  // Apply data when it changes — follows klinecharts v10 pattern:
  // setSymbol → setPeriod → setDataLoader (which triggers getBars)
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
    chart.setSymbol({ ticker: instrument });
    chart.setPeriod({ span: 1, type: "day" });
    chart.setDataLoader({
      getBars: ({ type, callback }) => {
        if (type === "init" || type === "backward") {
          callback(dataRef.current, false);
        } else {
          callback([], false);
        }
      },
    });
  }, [data]);

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

  return (
    <div
      ref={containerRef}
      className="w-full h-full min-h-[500px] bg-background-card rounded-lg border border-gray-200"
    />
  );
}
