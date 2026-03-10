import useSWR from "swr";
import { api } from "@/lib/api";
import type { ManifestDataset } from "@/types/contracts/chart";

export function useManifest() {
  const { data, error, mutate } = useSWR("manifest", () => api.manifest(), {
    revalidateOnFocus: false,
    dedupingInterval: 60_000,
    // Manifest may not exist in production — treat 404 as empty
    onErrorRetry: (err, _key, _config, revalidate, { retryCount }) => {
      if (err?.status === 404) return;
      if (retryCount >= 2) return;
      setTimeout(() => revalidate({ retryCount }), 5000);
    },
  });

  const datasets = data?.datasets ?? [];

  function hasData(symbol: string, timeframe: string): boolean {
    return datasets.some(
      (d) =>
        d.symbol.toUpperCase() === symbol.toUpperCase() &&
        d.timeframe.toUpperCase() === timeframe.toUpperCase()
    );
  }

  function availableTimeframes(symbol: string): string[] {
    return [
      ...new Set(
        datasets
          .filter((d) => d.symbol.toUpperCase() === symbol.toUpperCase())
          .map((d) => d.timeframe.toUpperCase())
      ),
    ];
  }

  function datasetInfo(
    symbol: string,
    timeframe: string
  ): ManifestDataset | undefined {
    return datasets.find(
      (d) =>
        d.symbol.toUpperCase() === symbol.toUpperCase() &&
        d.timeframe.toUpperCase() === timeframe.toUpperCase()
    );
  }

  return {
    manifest: data ?? null,
    datasets,
    hasData,
    availableTimeframes,
    datasetInfo,
    error,
    refresh: mutate,
  };
}
