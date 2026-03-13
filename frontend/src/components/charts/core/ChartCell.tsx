"use client";

import { useState, useCallback, useRef } from "react";
import TradingChart from "./TradingChart";
import { useMarketData, useLiveStatus } from "@/lib/hooks/use-market-data";
import type { ChartMode } from "@/lib/hooks/use-market-data";
import { useDrawings } from "@/lib/hooks/use-drawings";
import { AlertTriangle, Database, Loader2, Clock } from "lucide-react";
import { MARKET_SESSIONS, getSessionForTimestamp } from "@/lib/sessions";

const INSTRUMENTS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"];
const TIMEFRAMES = ["M15", "H1", "H4", "D1"];

interface ChartCellProps {
  defaultInstrument?: string;
  defaultTimeframe?: string;
  compact?: boolean;
  onConfigChange?: (instrument: string, timeframe: string) => void;
}

export default function ChartCell({
  defaultInstrument = "EURUSD",
  defaultTimeframe = "H1",
  compact = false,
  onConfigChange,
}: ChartCellProps) {
  const [instrument, setInstrument] = useState(defaultInstrument);
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  const [ichimokuEnabled, setIchimokuEnabled] = useState(false);
  const [sessionsVisible, setSessionsVisible] = useState(false);
  const [activeDrawingTool, setActiveDrawingTool] = useState<string | null>(null);
  const [chartMode] = useState<ChartMode>("historical");

  const { data, error, isLoading } = useMarketData(instrument, timeframe, chartMode);
  const {
    drawings,
    createDrawing,
    updateDrawing,
    deleteDrawing,
  } = useDrawings(instrument, timeframe);

  const lookupDrawingId = useCallback(
    (overlayId: string): number | null => {
      const match = overlayId.match(/^saved_(\d+)$/);
      if (match) return parseInt(match[1], 10);
      return null;
    },
    []
  );

  const handleDrawingCreated = useCallback(
    async (drawing: {
      tool_type: string;
      points: Array<{ timestamp: number; value: number }>;
    }) => {
      await createDrawing({
        instrument,
        timeframe,
        tool_type: drawing.tool_type,
        points: drawing.points,
      });
      setActiveDrawingTool(null);
    },
    [createDrawing, instrument, timeframe]
  );

  const updateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleDrawingUpdated = useCallback(
    (overlayId: string, points: Array<{ timestamp: number; value: number }>) => {
      if (updateTimerRef.current) clearTimeout(updateTimerRef.current);
      updateTimerRef.current = setTimeout(async () => {
        const drawingId = lookupDrawingId(overlayId);
        if (drawingId !== null) {
          await updateDrawing(drawingId, { points });
        }
      }, 300);
    },
    [lookupDrawingId, updateDrawing]
  );

  const handleDrawingRemoved = useCallback(
    async (overlayId: string) => {
      const drawingId = lookupDrawingId(overlayId);
      if (drawingId !== null) {
        await deleteDrawing(drawingId);
      }
    },
    [lookupDrawingId, deleteDrawing]
  );

  const handleInstrumentChange = (v: string) => {
    setInstrument(v);
    onConfigChange?.(v, timeframe);
  };
  const handleTimeframeChange = (v: string) => {
    setTimeframe(v);
    onConfigChange?.(instrument, v);
  };

  return (
    <div className="flex flex-col h-full border border-border rounded-lg overflow-hidden bg-background-card">
      {/* Compact toolbar */}
      <div className="flex items-center gap-2 px-2 py-1.5 border-b border-border bg-background text-xs">
        <select
          value={instrument}
          onChange={(e) => handleInstrumentChange(e.target.value)}
          className="bg-background border border-border rounded px-1.5 py-0.5 text-xs"
        >
          {INSTRUMENTS.map((i) => (
            <option key={i} value={i}>{i.replace("_", "/")}</option>
          ))}
        </select>
        <select
          value={timeframe}
          onChange={(e) => handleTimeframeChange(e.target.value)}
          className="bg-background border border-border rounded px-1.5 py-0.5 text-xs"
        >
          {TIMEFRAMES.map((tf) => (
            <option key={tf} value={tf}>{tf}</option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={ichimokuEnabled}
            onChange={(e) => setIchimokuEnabled(e.target.checked)}
            className="w-3 h-3"
          />
          Ichimoku
        </label>
        <label className="flex items-center gap-1 text-foreground-muted cursor-pointer">
          <input
            type="checkbox"
            checked={sessionsVisible}
            onChange={(e) => setSessionsVisible(e.target.checked)}
            className="w-3 h-3"
          />
          <Clock size={10} />
          Sessions
        </label>
        {data && !compact && (
          <span className="text-foreground-muted ml-auto flex items-center gap-1">
            <Database size={10} />
            {data.total_bars?.toLocaleString() ?? "?"} bars
          </span>
        )}
      </div>

      {/* Session legend */}
      {sessionsVisible && (
        <div className="flex items-center gap-3 px-2 py-1 border-b border-border text-[10px]">
          {MARKET_SESSIONS.map((s) => {
            const currentSession = data?.candles?.length
              ? getSessionForTimestamp(data.candles[data.candles.length - 1].timestamp)
              : null;
            const isCurrent = currentSession?.name === s.name;
            return (
              <span key={s.name} className={`flex items-center gap-1 ${isCurrent ? "font-bold text-foreground" : "text-foreground-muted"}`}>
                <span className="w-2 h-2 rounded-sm inline-block" style={{ backgroundColor: s.color.replace("0.06", "0.4") }} />
                {s.name}
                {isCurrent && " *"}
              </span>
            );
          })}
        </div>
      )}

      {/* Chart */}
      <div className="flex-1 min-h-0">
        {error ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <AlertTriangle size={16} className="text-danger mx-auto mb-1" />
              <p className="text-xs text-danger">Failed to load data</p>
            </div>
          </div>
        ) : isLoading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 size={16} className="text-primary animate-spin" />
          </div>
        ) : (
          <TradingChart
            data={data}
            ichimokuEnabled={ichimokuEnabled}
            activeDrawingTool={activeDrawingTool}
            savedDrawings={drawings}
            onDrawingCreated={handleDrawingCreated}
            onDrawingUpdated={handleDrawingUpdated}
            onDrawingRemoved={handleDrawingRemoved}
          />
        )}
      </div>
    </div>
  );
}
