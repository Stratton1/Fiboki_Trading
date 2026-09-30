"use client";

import { lazy, Suspense } from "react";
import type { Series } from "@/lib/types";
import type { PriceChartProps } from "./charts/PriceChart";

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

/**
 * The price chart (TradingView Lightweight Charts, plan D-F5), behind a
 * dynamic import: the engine is fetched when the chart workstation first
 * draws, so it is in no route's first-load JavaScript, the shell's included.
 * `preloadPriceChart()` starts the fetch while the bars are still loading.
 * source-rules.spec.ts fails if anything imports ./charts/PriceChart
 * statically.
 */
export const preloadPriceChart = () => import("./charts/PriceChart");
const PriceChartImpl = lazy(preloadPriceChart);

export function LazyPriceChart(props: PriceChartProps) {
  return (
    <Suspense
      fallback={
        <div className="price-chart price-chart--pending" data-testid="price-chart-pending" role="status">
          Loading the price chart engine to draw {props.model.times.length.toLocaleString("en-GB")} bars…
        </div>
      }
    >
      <PriceChartImpl {...props} />
    </Suspense>
  );
}
