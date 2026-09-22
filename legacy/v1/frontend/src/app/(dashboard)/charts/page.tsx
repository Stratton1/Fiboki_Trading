"use client";

import MultiChartLayout from "@/components/charts/core/MultiChartLayout";
import { InfoTip } from "@/components/InfoTip";
import { BarChart3, Keyboard } from "lucide-react";

export default function ChartsPage() {
  return (
    <div className="flex flex-col gap-2 h-full" data-testid="charts-page">
      {/* Compact workstation header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <BarChart3 size={20} className="text-primary" />
          <div>
            <h1 className="text-lg font-bold text-foreground tracking-tight leading-tight">
              Charts
              <InfoTip text="Multi-instrument charting workstation. Use drawing tools to annotate, toggle Ichimoku overlays, and link to backtests and research." />
            </h1>
            <p className="text-[11px] text-foreground-muted">
              Analysis workstation — draw, overlay, compare
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 text-[10px] text-foreground-muted">
          <span className="flex items-center gap-1">
            <Keyboard size={11} />
            Scroll to pan · Ctrl+Scroll to zoom
          </span>
          <span className="hidden lg:inline text-foreground-muted/70">
            V Select · T Trend · H H-line · F Fib · R Ray · C Channel
          </span>
        </div>
      </div>

      {/* Chart layout */}
      <div className="flex-1 min-h-0">
        <MultiChartLayout />
      </div>
    </div>
  );
}
