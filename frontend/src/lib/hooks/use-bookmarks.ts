import { useCallback } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";

export function useBookmarks(entityType?: string) {
  const key = `/bookmarks${entityType ? `?entity_type=${entityType}` : ""}`;
  const { data, mutate, isLoading } = useSWR(key, () => api.listBookmarks(entityType));

  const isBookmarked = useCallback(
    (type: string, id: number) =>
      data?.some((b) => b.entity_type === type && b.entity_id === id) ?? false,
    [data]
  );

  const toggle = useCallback(
    async (type: string, id: number) => {
      const existing = data?.find((b) => b.entity_type === type && b.entity_id === id);
      if (existing) {
        await api.deleteBookmark(existing.id);
      } else {
        await api.createBookmark({ entity_type: type, entity_id: id });
      }
      await mutate();
    },
    [data, mutate]
  );

  return { bookmarks: data ?? [], isBookmarked, toggle, isLoading, mutate };
}
