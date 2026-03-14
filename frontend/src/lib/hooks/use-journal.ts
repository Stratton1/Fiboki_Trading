import useSWR from "swr";
import { api } from "@/lib/api";
import type { JournalEntry } from "@/types/contracts/trades";

/** Hook for a single trade's journal entry */
export function useJournal(tradeId: number | null) {
  const { data, error, isLoading, mutate } = useSWR(
    tradeId ? `/trades/${tradeId}/journal` : null,
    () => api.getJournal(tradeId!),
  );

  const save = async (body: { note?: string; tags?: string[] }) => {
    if (!tradeId) return;
    if (data) {
      const updated = await api.updateJournal(tradeId, body);
      await mutate(updated, false);
      return updated;
    } else {
      const created = await api.createJournal(tradeId, body);
      await mutate(created, false);
      return created;
    }
  };

  const remove = async () => {
    if (!tradeId) return;
    await api.deleteJournal(tradeId);
    await mutate(null, false);
  };

  return {
    entry: data ?? null,
    error,
    isLoading,
    mutate,
    save,
    remove,
  };
}

/** Hook for listing all journal entries (for the trades page) */
export function useJournalList(tag?: string | null) {
  const params = tag ? `tag=${tag}` : undefined;
  const { data, error, isLoading, mutate } = useSWR(
    `/journal${params ? `?${params}` : ""}`,
    () => api.listJournal(params),
  );

  const journalMap = new Map<number, JournalEntry>();
  if (data?.items) {
    for (const entry of data.items) {
      journalMap.set(entry.trade_id, entry);
    }
  }

  return {
    entries: data?.items ?? [],
    journalMap,
    total: data?.total ?? 0,
    error,
    isLoading,
    mutate,
  };
}
