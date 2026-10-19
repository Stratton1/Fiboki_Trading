import { GridWorkstation } from "@/components/charts/grid/GridWorkstation";

/**
 * MARKETS · multi-chart grid: `/markets/grid?layout=2x2&symbols=EURUSD,GBPUSD`.
 * A thin server entry around a client component; every chart in it reads the
 * API in the browser and loads the chart engine lazily (LazyPriceChart).
 */
export default function ChartGridPage() {
  return <GridWorkstation />;
}
