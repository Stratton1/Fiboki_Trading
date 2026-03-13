import useSWR from "swr";
import { api } from "@/lib/api";

export function useRankings(runId?: string | null) {
  const params = runId ? `?run_id=${runId}` : "";
  return useSWR(
    `/research/rankings${params}`,
    () => api.rankings(params || undefined),
  );
}
