"use client";

import { useState, useEffect } from "react";
import ChartCell from "./ChartCell";
import { LayoutGrid, Square, Columns2, Grid2x2 } from "lucide-react";

export type LayoutMode = "1x1" | "1x2" | "2x2";

const LAYOUT_OPTIONS: { mode: LayoutMode; label: string; icon: typeof Square; cells: number }[] = [
  { mode: "1x1", label: "Single", icon: Square, cells: 1 },
  { mode: "1x2", label: "Side by Side", icon: Columns2, cells: 2 },
  { mode: "2x2", label: "Quad", icon: Grid2x2, cells: 4 },
];

const DEFAULT_CONFIGS: Array<{ instrument: string; timeframe: string }> = [
  { instrument: "EURUSD", timeframe: "H1" },
  { instrument: "GBPUSD", timeframe: "H1" },
  { instrument: "USDJPY", timeframe: "H1" },
  { instrument: "XAUUSD", timeframe: "H1" },
];

const STORAGE_KEY = "fiboki_chart_layout";

interface StoredLayout {
  mode: LayoutMode;
  configs: Array<{ instrument: string; timeframe: string }>;
}

function loadLayout(): StoredLayout {
  if (typeof window === "undefined") return { mode: "1x1", configs: DEFAULT_CONFIGS };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as StoredLayout;
  } catch { /* ignore */ }
  return { mode: "1x1", configs: DEFAULT_CONFIGS };
}

function saveLayout(layout: StoredLayout) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch { /* ignore */ }
}

interface MultiChartLayoutProps {
  /** Rendered in the page header area alongside the layout selector */
  headerSlot?: React.ReactNode;
}

export default function MultiChartLayout({ headerSlot }: MultiChartLayoutProps) {
  const [layout, setLayout] = useState<StoredLayout>(() => loadLayout());

  useEffect(() => {
    saveLayout(layout);
  }, [layout]);

  const handleModeChange = (mode: LayoutMode) => {
    setLayout((prev) => ({ ...prev, mode }));
  };

  const handleCellConfigChange = (index: number, instrument: string, timeframe: string) => {
    setLayout((prev) => {
      const configs = [...prev.configs];
      configs[index] = { instrument, timeframe };
      return { ...prev, configs };
    });
  };

  const option = LAYOUT_OPTIONS.find((o) => o.mode === layout.mode) ?? LAYOUT_OPTIONS[0];
  const cellCount = option.cells;

  const gridClass =
    layout.mode === "1x1"
      ? "grid-cols-1 grid-rows-1"
      : layout.mode === "1x2"
        ? "grid-cols-2 grid-rows-1"
        : "grid-cols-2 grid-rows-2";

  return (
    <div className="flex flex-col h-full gap-3">
      {/* Toolbar row */}
      <div className="flex items-center gap-3" data-testid="layout-toolbar">
        {headerSlot}
        <div className="flex items-center gap-1">
          <LayoutGrid size={14} className="text-foreground-muted mr-1" />
          {LAYOUT_OPTIONS.map((opt) => {
            const Icon = opt.icon;
            const active = layout.mode === opt.mode;
            return (
              <button
                key={opt.mode}
                onClick={() => handleModeChange(opt.mode)}
                title={opt.label}
                data-testid={`layout-${opt.mode}`}
                className={`px-2 py-1 text-xs rounded transition flex items-center gap-1 ${
                  active
                    ? "bg-primary text-white font-medium"
                    : "bg-background-card text-foreground-muted hover:text-foreground hover:bg-background-muted border border-gray-200"
                }`}
              >
                <Icon size={12} />
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Chart grid */}
      <div className={`flex-1 min-h-0 grid ${gridClass} gap-2`} data-testid="chart-grid">
        {Array.from({ length: cellCount }).map((_, i) => (
          <ChartCell
            key={`cell-${i}-${layout.mode}`}
            defaultInstrument={layout.configs[i]?.instrument ?? DEFAULT_CONFIGS[i]?.instrument ?? "EURUSD"}
            defaultTimeframe={layout.configs[i]?.timeframe ?? DEFAULT_CONFIGS[i]?.timeframe ?? "H1"}
            compact={cellCount > 1}
            onConfigChange={(inst, tf) => handleCellConfigChange(i, inst, tf)}
          />
        ))}
      </div>
    </div>
  );
}
