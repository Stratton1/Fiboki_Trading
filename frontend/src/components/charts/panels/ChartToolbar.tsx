"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";
import type { ChartMode } from "@/lib/hooks/use-market-data";

const TIMEFRAMES = ["M15", "M30", "H1", "H4"] as const;

interface ChartToolbarProps {
  instrument: string;
  timeframe: string;
  onInstrumentChange: (instrument: string) => void;
  onTimeframeChange: (timeframe: string) => void;
  mode: ChartMode;
  onModeChange: (mode: ChartMode) => void;
  liveAvailable: boolean;
}

export default function ChartToolbar({
  instrument,
  timeframe,
  onInstrumentChange,
  onTimeframeChange,
  mode,
  onModeChange,
  liveAvailable,
}: ChartToolbarProps) {
  const { data: instruments } = useSWR("instruments", () => api.instruments());

  return (
    <div className="flex items-center gap-4">
      {/* Instrument selector */}
      <GroupedInstrumentSelect
        instruments={instruments ?? []}
        value={instrument}
        onChange={onInstrumentChange}
        className="bg-background-card border border-gray-200 rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
      />

      {/* Timeframe buttons */}
      <div className="flex gap-1">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            onClick={() => onTimeframeChange(tf)}
            className={`px-3 py-1.5 text-sm rounded-md transition ${
              timeframe === tf
                ? "bg-primary text-white font-medium"
                : "bg-background-card text-foreground-muted hover:text-foreground hover:bg-background-muted border border-gray-200"
            }`}
          >
            {tf}
          </button>
        ))}
      </div>

      {/* Mode toggle */}
      <div className="flex gap-1 bg-background-card border border-gray-200 rounded-md p-0.5">
        <button
          onClick={() => onModeChange("historical")}
          className={`px-2.5 py-1 text-xs rounded transition ${
            mode === "historical"
              ? "bg-primary text-white font-medium"
              : "text-foreground-muted hover:text-foreground"
          }`}
        >
          Historical
        </button>
        <button
          onClick={() => liveAvailable && onModeChange("live")}
          disabled={!liveAvailable}
          title={!liveAvailable ? "IG demo credentials not configured" : "Switch to live chart data"}
          className={`px-2.5 py-1 text-xs rounded transition ${
            mode === "live"
              ? "bg-green-600 text-white font-medium"
              : liveAvailable
                ? "text-foreground-muted hover:text-foreground"
                : "text-foreground-muted/40 cursor-not-allowed"
          }`}
        >
          Live
        </button>
      </div>
    </div>
  );
}
