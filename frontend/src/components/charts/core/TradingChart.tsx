"use client";

import { useEffect, useRef, useCallback } from "react";
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

  // Register the custom Ichimoku indicator once on mount
  useEffect(() => {
    registerIchimokuIndicator();
  }, []);

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return;

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

    chartRef.current = chart;

    return () => {
      if (containerRef.current) {
        dispose(containerRef.current);
      }
      chartRef.current = null;
      ichimokuActiveRef.current = false;
      clearIchimokuData();
    };
  }, []);

  // Apply data when it changes
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !data) return;

    // Set ichimoku data in the module cache before applying candle data
    if (data.ichimoku) {
      setIchimokuData(data.ichimoku);
    } else {
      clearIchimokuData();
    }

    const klineData: KLineData[] = mapCandlesToKLine(data.candles);

    // Use the DataLoader pattern for v10
    chart.setDataLoader({
      getBars: (params) => {
        if (params.type === "init") {
          params.callback(klineData, false);
        } else {
          params.callback([], false);
        }
      },
    });

    // Trigger data load
    chart.resetData();
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
