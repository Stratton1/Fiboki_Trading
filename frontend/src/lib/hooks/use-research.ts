import useSWR from "swr";
import { api } from "@/lib/api";

export function useRankings(params?: string) {
  return useSWR(`/research/rankings${params || ""}`, () => api.rankings(params));
}
