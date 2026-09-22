/**
 * Per-chart Ichimoku data store.
 *
 * The Ichimoku overlay was previously a module-level singleton — a single
 * `_ichimokuData: IchimokuPoint[]` shared across every chart instance. In a
 * Quad layout that meant the last cell to call `setIchimokuData()` clobbered
 * the data the other cells' `calc` callbacks read, producing contaminated
 * overlays on the chart panels.
 *
 * This store gives each chart instance its own data slot keyed by a unique
 * chartId. The klinecharts `calc` closure registered for that id always
 * reads its own slot, so cells stay independent.
 *
 * Pure module — no klinecharts imports. Lives in its own file so it can be
 * unit-tested without a DOM.
 */

import type { IchimokuPoint } from "@/types/contracts/chart";

const _byChart: Map<string, IchimokuPoint[]> = new Map();

/** Write Ichimoku data for one chart instance. */
export function setIchimokuDataForChart(chartId: string, data: IchimokuPoint[]): void {
  // Copy the array so callers mutating the original after this call don't
  // change what the chart sees.
  _byChart.set(chartId, data.slice());
}

/** Read Ichimoku data for one chart instance. Returns an empty array when
 *  nothing has been written for that id — never throws. */
export function getIchimokuDataForChart(chartId: string): IchimokuPoint[] {
  return _byChart.get(chartId) ?? [];
}

/** Drop Ichimoku data for one chart instance. No-op if the id is unknown.
 *  Used by the chart unmount path. */
export function clearIchimokuDataForChart(chartId: string): void {
  _byChart.delete(chartId);
}

/** Number of chart instances currently holding Ichimoku data. Useful for
 *  diagnostics and tests; not part of the rendering path. */
export function chartIchimokuStoreSize(): number {
  return _byChart.size;
}

/** Wipe every chart's data. ONLY for tests / hard-reset paths — production
 *  code should call `clearIchimokuDataForChart(chartId)` per instance to
 *  avoid clobbering sibling cells. */
export function _resetIchimokuStoreForTests(): void {
  _byChart.clear();
}
