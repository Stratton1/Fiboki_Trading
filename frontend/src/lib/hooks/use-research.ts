import useSWR from "swr";
import { api } from "@/lib/api";

export function useRankings(runId?: string | null, deduplicate?: boolean) {
  const parts: string[] = [];
  if (runId) parts.push(`run_id=${runId}`);
  if (deduplicate) parts.push("deduplicate=true");
  const params = parts.length > 0 ? `?${parts.join("&")}` : "";
  return useSWR(
    `/research/rankings${params}`,
    () => api.rankings(params || undefined),
  );
}

export function useResearchRuns() {
  const { data, error, isLoading, mutate } = useSWR(
    "/research/runs",
    () => api.listResearchRuns(),
  );

  return {
    runs: data ?? [],
    error,
    isLoading,
    mutate,
  };
}
