"use client";

import { useState } from "react";
import ChartToolbar from "@/components/charts/panels/ChartToolbar";
import OverlayControls from "@/components/charts/panels/OverlayControls";
import TradingChart from "@/components/charts/core/TradingChart";
import { useMarketData } from "@/lib/hooks/use-market-data";
import { PageHeader } from "@/components/PageHeader";
import { AlertTriangle, Loader2 } from "lucide-react";

export default function ChartsPage() {
  const [instrument, setInstrument] = useState("EURUSD");
  const [timeframe, setTimeframe] = useState("H1");
  const [ichimokuEnabled, setIchimokuEnabled] = useState(false);

  const { data, error, isLoading } = useMarketData(instrument, timeframe);

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
          <TradingChart data={data} ichimokuEnabled={ichimokuEnabled} />
        )}
      </div>
    </div>
  );
}
