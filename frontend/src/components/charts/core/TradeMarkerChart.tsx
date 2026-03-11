"use client";

import { useEffect, useRef, useCallback } from "react";
import { init, dispose } from "klinecharts";
import type { Chart, KLineData } from "klinecharts";
import { mapCandlesToKLine } from "@/lib/chart-mappers/candle-mapper";
import type { MarketDataResponse } from "@/types/contracts/chart";
import type { Trade } from "@/types/contracts/trades";

interface TradeMarkerChartProps {
  data: MarketDataResponse | null;
  trades: Trade[];
  focusTradeId?: number | null;
  onTradeClick?: (tradeId: number) => void;
}

export default function TradeMarkerChart({
  data,
  trades,
  focusTradeId,
  onTradeClick,
}: TradeMarkerChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const dataRef = useRef<KLineData[]>([]);

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return;

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
          tooltip: { showRule: "follow_cross" },
        },
        grid: {
          horizontal: { show: true, color: "rgba(150,150,150,0.1)" },
          vertical: { show: true, color: "rgba(150,150,150,0.1)" },
        },
      },
    });

    chartRef.current = chart!;

    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      if (containerRef.current) {
        dispose(containerRef.current);
      }
      chartRef.current = null;
    };
  }, []);

  // Apply market data
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !data) return;

    const klineData = mapCandlesToKLine(data.candles);
    dataRef.current = klineData;

    chart.setSymbol({ ticker: data.instrument || "UNKNOWN" });
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

  // Add trade marker overlays
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !trades.length) return;

    // Remove existing trade overlays
    chart.removeOverlay({ groupId: "trade_markers" });

    for (const trade of trades) {
      if (!trade.entry_time || !trade.exit_time) continue;

      const entryTs = new Date(trade.entry_time).getTime();
      const exitTs = new Date(trade.exit_time).getTime();
      const isLong = trade.direction === "LONG";
      const isProfitable = trade.pnl >= 0;
      const color = isProfitable ? "#16A34A" : "#EF4444";

      // Entry arrow
      chart.createOverlay({
        name: "simpleTag",
        groupId: "trade_markers",
        points: [{ timestamp: entryTs, value: trade.entry_price }],
        styles: {
          text: {
            color: "#FFFFFF",
            backgroundColor: isLong ? "#16A34A" : "#EF4444",
            size: 10,
            borderRadius: 2,
            paddingLeft: 3,
            paddingRight: 3,
            paddingTop: 2,
            paddingBottom: 2,
          },
        },
        extendData: isLong ? "\u25B2 LONG" : "\u25BC SHORT",
        lock: true,
        onPressedMoving: () => {},
        onClick: () => {
          onTradeClick?.(trade.id);
        },
      });

      // Exit arrow
      chart.createOverlay({
        name: "simpleTag",
        groupId: "trade_markers",
        points: [{ timestamp: exitTs, value: trade.exit_price }],
        styles: {
          text: {
            color: "#FFFFFF",
            backgroundColor: color,
            size: 10,
            borderRadius: 2,
            paddingLeft: 3,
            paddingRight: 3,
            paddingTop: 2,
            paddingBottom: 2,
          },
        },
        extendData: `EXIT ${trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(2)}`,
        lock: true,
        onPressedMoving: () => {},
      });

      // SL/TP horizontal lines
      if (trade.entry_price && trade.exit_price) {
        chart.createOverlay({
          name: "segment",
          groupId: "trade_markers",
          points: [
            { timestamp: entryTs, value: trade.entry_price },
            { timestamp: exitTs, value: trade.exit_price },
          ],
          styles: {
            line: {
              style: "dashed",
              color: color,
              size: 1,
            },
          },
          lock: true,
          onPressedMoving: () => {},
        });
      }
    }
  }, [trades, onTradeClick]);

  // Focus on a specific trade
  const scrollToTrade = useCallback(
    (tradeId: number) => {
      const chart = chartRef.current;
      if (!chart) return;
      const trade = trades.find((t) => t.id === tradeId);
      if (!trade?.entry_time) return;

      const entryTs = new Date(trade.entry_time).getTime();
      const klineData = dataRef.current;
      const barIndex = klineData.findIndex((d) => d.timestamp >= entryTs);
      if (barIndex >= 0) {
        chart.scrollToDataIndex(Math.max(0, barIndex - 10));
      }
    },
    [trades]
  );

  useEffect(() => {
    if (focusTradeId != null) {
      scrollToTrade(focusTradeId);
    }
  }, [focusTradeId, scrollToTrade]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full min-h-[400px] bg-background-card rounded-lg border border-gray-200"
    />
  );
}
