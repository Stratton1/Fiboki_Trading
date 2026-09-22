import type { Chart } from "klinecharts";
import type { TradeMarker } from "@/types/contracts/trades";

const LONG_COLOR = "#16A34A";
const SHORT_COLOR = "#EF4444";
const SL_COLOR = "#F59E0B";
const TP_COLOR = "#3B82F6";

/**
 * Creates trade entry/exit markers and SL/TP lines as overlays on the chart.
 * Returns an array of overlay IDs that can be used to remove them later.
 */
export function renderTradeMarkers(
  chart: Chart,
  trades: TradeMarker[]
): string[] {
  const overlayIds: string[] = [];

  for (const trade of trades) {
    const isLong = trade.direction === "LONG";
    const color = isLong ? LONG_COLOR : SHORT_COLOR;

    // Entry marker
    const entryTs = new Date(trade.entry.timestamp).getTime();
    const entryResult = chart.createOverlay({
      name: "simpleTag",
      points: [{ timestamp: entryTs, value: trade.entry.price }],
      styles: {
        point: { color },
      },
      extendData: { label: `${trade.direction} Entry` },
    });
    if (typeof entryResult === "string") {
      overlayIds.push(entryResult);
    }

    // Exit marker
    if (trade.exit) {
      const exitTs = new Date(trade.exit.timestamp).getTime();
      const exitResult = chart.createOverlay({
        name: "simpleTag",
        points: [{ timestamp: exitTs, value: trade.exit.price }],
        styles: {
          point: { color },
        },
        extendData: { label: `Exit (${trade.outcome})` },
      });
      if (typeof exitResult === "string") {
        overlayIds.push(exitResult);
      }
    }

    // Stop loss lines
    for (const sl of trade.stop_loss) {
      const slTs = new Date(sl.timestamp).getTime();
      const slResult = chart.createOverlay({
        name: "horizontalStraightLine",
        points: [{ timestamp: slTs, value: sl.price }],
        styles: {
          line: { color: SL_COLOR, style: "dashed" as const, size: 1 },
        },
      });
      if (typeof slResult === "string") {
        overlayIds.push(slResult);
      }
    }

    // Take profit lines
    for (const tp of trade.take_profit) {
      const tpTs = new Date(tp.timestamp).getTime();
      const tpResult = chart.createOverlay({
        name: "horizontalStraightLine",
        points: [{ timestamp: tpTs, value: tp.price }],
        styles: {
          line: { color: TP_COLOR, style: "dashed" as const, size: 1 },
        },
      });
      if (typeof tpResult === "string") {
        overlayIds.push(tpResult);
      }
    }
  }

  return overlayIds;
}

/**
 * Removes all trade marker overlays from the chart.
 */
export function clearTradeMarkers(chart: Chart) {
  chart.removeOverlay();
}
