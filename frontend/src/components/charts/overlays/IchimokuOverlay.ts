import type { IchimokuPoint } from "@/types/contracts/chart";
import { registerIndicator } from "klinecharts";
import type { KLineData } from "klinecharts";

/**
 * Module-level Ichimoku data cache.
 * Must be set before chart data is applied so the calc callback can map it.
 */
let _ichimokuData: IchimokuPoint[] = [];

export function setIchimokuData(data: IchimokuPoint[]) {
  _ichimokuData = data;
}

export function clearIchimokuData() {
  _ichimokuData = [];
}

interface IchimokuResult {
  tenkan: number | null;
  kijun: number | null;
  senkou_a: number | null;
  senkou_b: number | null;
  chikou: number | null;
}

let _registered = false;

export function registerIchimokuIndicator() {
  if (_registered) return;
  _registered = true;

  registerIndicator<IchimokuResult>({
    name: "ICHIMOKU",
    shortName: "Ichimoku",
    series: "price",
    precision: 5,
    figures: [
      {
        key: "tenkan",
        title: "Tenkan: ",
        type: "line",
        styles: () => ({ color: "#2196F3" }),
      },
      {
        key: "kijun",
        title: "Kijun: ",
        type: "line",
        styles: () => ({ color: "#FF5722" }),
      },
      {
        key: "senkou_a",
        title: "Senkou A: ",
        type: "line",
        styles: () => ({ color: "#4CAF50" }),
      },
      {
        key: "senkou_b",
        title: "Senkou B: ",
        type: "line",
        styles: () => ({ color: "#F44336" }),
      },
      {
        key: "chikou",
        title: "Chikou: ",
        type: "line",
        styles: () => ({ color: "#9C27B0" }),
      },
    ],
    calc: (dataList: KLineData[]): IchimokuResult[] => {
      // Build a map from timestamp to ichimoku values for fast lookup
      const ichMap = new Map<number, IchimokuPoint>();
      for (const pt of _ichimokuData) {
        ichMap.set(pt.timestamp, pt);
      }

      return dataList.map((kline) => {
        const ich = ichMap.get(kline.timestamp);
        return {
          tenkan: ich?.tenkan ?? null,
          kijun: ich?.kijun ?? null,
          senkou_a: ich?.senkou_a ?? null,
          senkou_b: ich?.senkou_b ?? null,
          chikou: ich?.chikou ?? null,
        };
      });
    },
  });
}

export const ICHIMOKU_INDICATOR_NAME = "ICHIMOKU";
