"use client";

import { useState, useCallback, useRef } from "react";
import ChartToolbar from "@/components/charts/panels/ChartToolbar";
import OverlayControls from "@/components/charts/panels/OverlayControls";
import DrawingToolbar from "@/components/charts/panels/DrawingToolbar";
import TradingChart from "@/components/charts/core/TradingChart";
import { useMarketData } from "@/lib/hooks/use-market-data";
import { useDrawings } from "@/lib/hooks/use-drawings";
import { PageHeader } from "@/components/PageHeader";
import { AlertTriangle, Loader2 } from "lucide-react";

export default function ChartsPage() {
  const [instrument, setInstrument] = useState("EURUSD");
  const [timeframe, setTimeframe] = useState("H1");
  const [ichimokuEnabled, setIchimokuEnabled] = useState(false);
  const [activeDrawingTool, setActiveDrawingTool] = useState<string | null>(null);

  const { data, error, isLoading } = useMarketData(instrument, timeframe);
  const {
    drawings,
    createDrawing,
    updateDrawing,
    deleteDrawing,
    clearDrawings,
  } = useDrawings(instrument, timeframe);

  // Map from klinecharts overlay IDs to backend drawing IDs.
  // Populated when saved drawings are loaded (overlay ID = `saved_${drawing.id}`).
  const lookupDrawingId = useCallback(
    (overlayId: string): number | null => {
      // Overlay IDs for saved drawings follow the pattern "saved_{id}"
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

  // Debounce drawing updates — onPressedMoving fires on every pixel of drag
  const updateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleDrawingUpdated = useCallback(
    (
      overlayId: string,
      points: Array<{ timestamp: number; value: number }>
    ) => {
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

  const handleClearAll = useCallback(async () => {
    await clearDrawings();
  }, [clearDrawings]);

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* Header */}
      <PageHeader
        title="Trading Chart"
        subtitle={`${instrument.replace("_", "/")} \u2014 ${timeframe}`}
        actions={
          <div className="flex items-center gap-4">
            <ChartToolbar
              instrument={instrument}
              timeframe={timeframe}
              onInstrumentChange={setInstrument}
              onTimeframeChange={setTimeframe}
            />
            <div className="w-px h-6 bg-border" />
            <OverlayControls
              ichimokuEnabled={ichimokuEnabled}
              onIchimokuToggle={setIchimokuEnabled}
            />
            <div className="w-px h-6 bg-border" />
            <DrawingToolbar
              activeTool={activeDrawingTool}
              onToolChange={setActiveDrawingTool}
              onClearAll={handleClearAll}
            />
          </div>
        }
      />

      {/* Chart area */}
      <div className="flex-1 min-h-0">
        {error ? (
          <div className="flex items-center justify-center h-full min-h-[500px] card">
            <div className="text-center">
              <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-3">
                <AlertTriangle size={22} className="text-danger" />
              </div>
              <p className="text-sm font-medium text-danger">Failed to load market data</p>
              <p className="text-xs text-foreground-muted mt-1 max-w-xs">
                {error.message || "Check that the backend is running."}
              </p>
            </div>
          </div>
        ) : isLoading ? (
          <div className="flex items-center justify-center h-full min-h-[500px] card">
            <div className="text-center">
              <Loader2 size={24} className="text-primary animate-spin mx-auto mb-2" />
              <p className="text-sm text-foreground-muted">Loading chart data...</p>
            </div>
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
