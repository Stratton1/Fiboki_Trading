import useSWR from "swr";
import { api } from "@/lib/api";

export function useBacktests() {
  return useSWR("/backtests", () => api.listBacktests());
}
export function useBacktest(id: number | null) {
  return useSWR(id ? `/backtests/${id}` : null, () => api.getBacktest(id!));
}
export function useEquityCurve(id: number | null) {
  return useSWR(id ? `/backtests/${id}/equity-curve` : null, () => api.getEquityCurve(id!));
}
export function useBacktestTrades(id: number | null, page = 1, size = 500, sort?: string) {
  const key = id ? `/backtests/${id}/trades?page=${page}&size=${size}${sort ? `&sort=${sort}` : ""}` : null;
  return useSWR(key, () => api.getBacktestTrades(id!, page, size, sort));
}
