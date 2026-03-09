"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";

const TIMEFRAMES = ["M15", "M30", "H1", "H4"] as const;

interface ChartToolbarProps {
  instrument: string;
  timeframe: string;
  onInstrumentChange: (instrument: string) => void;
  onTimeframeChange: (timeframe: string) => void;
}

export default function ChartToolbar({
  instrument,
  timeframe,
  onInstrumentChange,
  onTimeframeChange,
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
    </div>
  );
}
