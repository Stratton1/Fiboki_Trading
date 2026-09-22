import useSWR from "swr";
import { api } from "@/lib/api";

export interface AccountData {
  balance: number;
  equity: number;
  initial_balance: number;
  currency: string;
  daily_pnl: number;
  weekly_pnl: number;
  open_positions: number;
  total_trades: number;
}

export function useBots() {
  return useSWR("/paper/bots", () => api.listBots(), { refreshInterval: 5000 });
}

export function useAccount() {
  return useSWR("/paper/account", () => api.account() as Promise<AccountData>, { refreshInterval: 5000 });
}
