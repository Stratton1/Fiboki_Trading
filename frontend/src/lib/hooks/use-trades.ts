import useSWR from "swr";
import { api } from "@/lib/api";

export function useTrades(params?: string) {
  return useSWR(`/trades/${params || ""}`, () => api.listTrades(params));
}
export function useTrade(id: number | null) {
  return useSWR(id ? `/trades/${id}` : null, () => api.getTrade(id!));
}
