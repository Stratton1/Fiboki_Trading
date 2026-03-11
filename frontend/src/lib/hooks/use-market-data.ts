import useSWR from "swr";
import { api } from "@/lib/api";
import type { LiveStatusResponse, MarketDataResponse } from "@/types/contracts/chart";

/** Polling interval when in live mode (ms). */
const LIVE_REFRESH_INTERVAL = 5_000;

export type ChartMode = "historical" | "live";

export function useMarketData(
  instrument: string | null,
  timeframe: string | null,
  mode: ChartMode = "historical"
) {
  const { data, error, isLoading, mutate } = useSWR<MarketDataResponse>(
    instrument && timeframe
      ? `/market-data/${instrument}/${timeframe}?mode=${mode}`
      : null,
    () => api.marketData(instrument!, timeframe!, mode),
    {
      revalidateOnFocus: false,
      dedupingInterval: mode === "live" ? LIVE_REFRESH_INTERVAL : 30_000,
      refreshInterval: mode === "live" ? LIVE_REFRESH_INTERVAL : 0,
    }
  );

  return {
    data: data ?? null,
    error: error as Error | null,
    isLoading,
    refresh: mutate,
  };
}

export function useLiveStatus() {
  const { data, error } = useSWR<LiveStatusResponse>(
    "/market-data/live/status",
    () => api.liveStatus(),
    {
      revalidateOnFocus: false,
      dedupingInterval: 60_000,
    }
  );

  return {
    available: data?.available ?? false,
    reason: data?.reason ?? null,
    error: error as Error | null,
  };
}
