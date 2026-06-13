/**
 * Klinecharts Ichimoku indicator registration — per chart instance.
 *
 * Each chart instance gets a uniquely-named indicator (`ICHIMOKU_${chartId}`)
 * whose `calc` closure reads from its own slot in the per-chart store. The
 * indicator name is what the chart calls `createIndicator(name, true)` with,
 * so we return it from `registerIchimokuForChart()`.
 *
 * Why this matters: klinecharts indicators are registered globally on its
 * internal registry. Before this refactor we registered a single "ICHIMOKU"
 * indicator whose calc closed over a module-level data array — in a Quad
 * layout the last chart to call the legacy `setIchimokuData()` overwrote
 * what the others' calc callbacks read, contaminating their overlays.
 */

import { registerIndicator } from "klinecharts";
import type { KLineData } from "klinecharts";
import type { IchimokuPoint } from "@/types/contracts/chart";
import { getIchimokuDataForChart } from "./ichimoku-store";

export { setIchimokuDataForChart, clearIchimokuDataForChart } from "./ichimoku-store";

interface IchimokuResult {
  tenkan: number | null;
  kijun: number | null;
  senkou_a: number | null;
  senkou_b: number | null;
  chikou: number | null;
}

/** Tracks which per-chart indicator names we've already registered with
 *  klinecharts so re-mounts don't re-register and trigger warnings. */
const _registered: Set<string> = new Set();

/**
 * Register an Ichimoku indicator scoped to a single chart instance and
 * return its indicator name. Safe to call repeatedly for the same id.
 *
 * The returned name is what the chart should pass to
 * `chart.createIndicator(name, true)` to actually display the overlay.
 */
export function registerIchimokuForChart(chartId: string): string {
  const name = ichimokuIndicatorName(chartId);
  if (_registered.has(name)) return name;
  _registered.add(name);

  registerIndicator<IchimokuResult>({
    name,
    shortName: "Ichimoku",
    series: "price",
    precision: 5,
    figures: [
      { key: "tenkan",   title: "Tenkan: ",   type: "line", styles: () => ({ color: "#2196F3" }) },
      { key: "kijun",    title: "Kijun: ",    type: "line", styles: () => ({ color: "#FF5722" }) },
      { key: "senkou_a", title: "Senkou A: ", type: "line", styles: () => ({ color: "#4CAF50" }) },
      { key: "senkou_b", title: "Senkou B: ", type: "line", styles: () => ({ color: "#F44336" }) },
      { key: "chikou",   title: "Chikou: ",   type: "line", styles: () => ({ color: "#9C27B0" }) },
    ],
    calc: (dataList: KLineData[]): IchimokuResult[] => {
      const data: IchimokuPoint[] = getIchimokuDataForChart(chartId);
      const ichMap = new Map<number, IchimokuPoint>();
      for (const pt of data) ichMap.set(pt.timestamp, pt);
      return dataList.map((kline) => {
        const ich = ichMap.get(kline.timestamp);
        return {
          tenkan:   ich?.tenkan   ?? null,
          kijun:    ich?.kijun    ?? null,
          senkou_a: ich?.senkou_a ?? null,
          senkou_b: ich?.senkou_b ?? null,
          chikou:   ich?.chikou   ?? null,
        };
      });
    },
  });

  return name;
}

/** Derive the indicator name for a chartId without registering it.
 *  Lets callers cheaply check `chart.removeIndicator({ name })`. */
export function ichimokuIndicatorName(chartId: string): string {
  return `ICHIMOKU_${chartId}`;
}
