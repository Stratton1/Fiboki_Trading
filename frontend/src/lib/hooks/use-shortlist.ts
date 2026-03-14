import useSWR from "swr";
import { api } from "@/lib/api";
import type { ShortlistEntry } from "@/types/contracts/research";

export function useShortlist() {
  const { data, error, isLoading, mutate } = useSWR(
    "/research/shortlist",
    () => api.listShortlist(),
  );

  const save = async (entry: {
    strategy_id: string;
    instrument: string;
    timeframe: string;
    score: number;
    source_run_id?: string;
    metrics_snapshot?: Record<string, unknown>;
    note?: string;
  }) => {
    const result = await api.saveToShortlist(entry);
    await mutate();
    return result;
  };

  const update = async (id: number, updates: { note?: string; status?: string }) => {
    const result = await api.updateShortlistEntry(id, updates);
    await mutate();
    return result;
  };

  const remove = async (id: number) => {
    await api.deleteShortlistEntry(id);
    await mutate();
  };

  const isShortlisted = (strategyId: string, instrument: string, timeframe: string) =>
    data?.some(
      (e: ShortlistEntry) =>
        e.strategy_id === strategyId &&
        e.instrument === instrument &&
        e.timeframe === timeframe &&
        e.status === "active",
    ) ?? false;

  return {
    shortlist: data ?? [],
    error,
    isLoading,
    mutate,
    save,
    update,
    remove,
    isShortlisted,
  };
}
