import type { CandleBar } from "@/types/contracts/chart";

export function mapCandlesToKLine(candles: CandleBar[]) {
  return candles.map((c) => ({
    timestamp: c.timestamp,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
  }));
}
