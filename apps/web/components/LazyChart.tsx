"use client";

import { lazy, Suspense } from "react";
import type { Series } from "@/lib/types";

/**
 * The line chart, loaded after the page's first paint. On the Overview it is
 * below the fold, and the shell's 180 KiB first-load budget (plan D-F7) is
 * better spent on what an operator reads first. The placeholder says what is
 * coming; it is never an empty or a zero chart.
 */
const Impl = lazy(() => import("./charts").then((m) => ({ default: m.LineChart })));

export function LazyLineChart({ series }: { series: Series }) {
  return (
    <Suspense
      fallback={
        <p className="muted" data-testid="chart-pending" role="status">
          Drawing {series.name} ({series.points.length} points)…
        </p>
      }
    >
      <Impl series={series} />
    </Suspense>
  );
}
