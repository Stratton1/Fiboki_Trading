"use client";

import { useState } from "react";
import ChartToolbar from "@/components/charts/panels/ChartToolbar";
import OverlayControls from "@/components/charts/panels/OverlayControls";
import TradingChart from "@/components/charts/core/TradingChart";
import { useMarketData } from "@/lib/hooks/use-market-data";

export default function ChartsPage() {
  const [instrument, setInstrument] = useState("EURUSD");
  const [timeframe, setTimeframe] = useState("H1");
  const [ichimokuEnabled, setIchimokuEnabled] = useState(false);

  const { data, error, isLoading } = useMarketData(instrument, timeframe);

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-xl font-bold text-foreground">Trading Chart</h1>
          <p className="text-sm text-foreground-muted">
            {instrument.replace("_", "/")} - {timeframe}
          </p>
        </div>
        <div className="flex items-center gap-6">
          <ChartToolbar
            instrument={instrument}
            timeframe={timeframe}
            onInstrumentChange={setInstrument}
            onTimeframeChange={setTimeframe}
          />
          <OverlayControls
            ichimokuEnabled={ichimokuEnabled}
            onIchimokuToggle={setIchimokuEnabled}
          />
        </div>
      </div>

      {/* Chart area */}
      <div className="flex-1 min-h-0">
        {error ? (
          <div className="flex items-center justify-center h-full min-h-[500px] bg-background-card rounded-lg border border-gray-200">
            <div className="text-center">
              <p className="text-danger font-medium">Failed to load market data</p>
              <p className="text-sm text-foreground-muted mt-1">
                {error.message || "Check that the backend is running."}
              </p>
            </div>
          </div>
        ) : isLoading ? (
          <div className="flex items-center justify-center h-full min-h-[500px] bg-background-card rounded-lg border border-gray-200">
            <p className="text-foreground-muted">Loading chart data...</p>
          </div>
        ) : (
          <TradingChart data={data} ichimokuEnabled={ichimokuEnabled} />
        )}
      </div>
    </div>
  );
}
