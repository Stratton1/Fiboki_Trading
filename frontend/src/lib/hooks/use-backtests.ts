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
