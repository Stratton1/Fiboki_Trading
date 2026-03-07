import useSWR from "swr";
import { api } from "@/lib/api";
import type { MarketDataResponse } from "@/types/contracts/chart";

export function useMarketData(instrument: string, timeframe: string) {
  const { data, error, isLoading, mutate } = useSWR<MarketDataResponse>(
    instrument && timeframe ? `/market-data/${instrument}/${timeframe}` : null,
    () => api.marketData(instrument, timeframe),
    {
      revalidateOnFocus: false,
      dedupingInterval: 30_000,
    }
  );

  return {
    data: data ?? null,
    error: error as Error | null,
    isLoading,
    refresh: mutate,
  };
}
