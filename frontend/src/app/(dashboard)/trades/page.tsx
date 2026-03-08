"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { useTrades } from "@/lib/hooks/use-trades";
import { api } from "@/lib/api";

export default function TradesPage() {
  const { data, isLoading } = useTrades();
  const trades = data?.items ?? [];
  const [filterStrategy, setFilterStrategy] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  const { data: strategies } = useSWR("strategies", () => api.strategies());

  const filtered = trades.filter((t) => {
    if (filterStrategy && t.strategy_id !== filterStrategy) return false;
    if (filterDirection && t.direction !== filterDirection) return false;
    return true;
  });

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Trade History</h2>

      <div className="flex flex-wrap gap-3 mb-4">
        <select value={filterStrategy} onChange={(e) => setFilterStrategy(e.target.value)} className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-background">
          <option value="">All Strategies</option>
          {strategies?.map((s: any) => <option key={s.strategy_id} value={s.strategy_id}>{s.strategy_id}</option>)}
        </select>
        <select value={filterDirection} onChange={(e) => setFilterDirection(e.target.value)} className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-background">
          <option value="">All Directions</option>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
        </select>
      </div>

      <div className="bg-background-card rounded-lg border border-gray-300 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-300 bg-background-muted">
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Date</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Strategy</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Instrument</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Direction</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Entry</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Exit</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">PnL</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Exit Reason</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-foreground-muted">Loading...</td>
              </tr>
            )}
            {!isLoading && trades.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-foreground-muted">
                  No trades yet.
                </td>
              </tr>
            )}
            {filtered.map((t) => (
              <tr key={t.id} className="border-b border-gray-100 hover:bg-background-muted/50">
                <td className="px-4 py-3">
                  <Link href={`/trades/${t.id}`} className="text-primary hover:underline">
                    {t.entry_time ? new Date(t.entry_time).toLocaleDateString() : "-"}
                  </Link>
                </td>
                <td className="px-4 py-3">{t.strategy_id}</td>
                <td className="px-4 py-3">{t.instrument}</td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      t.direction === "LONG"
                        ? "bg-green-100 text-green-800"
                        : "bg-red-100 text-red-800"
                    }`}
                  >
                    {t.direction}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">{t.entry_price.toFixed(5)}</td>
                <td className="px-4 py-3 text-right">{t.exit_price.toFixed(5)}</td>
                <td className={`px-4 py-3 text-right font-medium ${t.pnl >= 0 ? "text-primary" : "text-danger"}`}>
                  {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}
                </td>
                <td className="px-4 py-3 text-foreground-muted">{t.exit_reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data && (
        <p className="text-xs text-foreground-muted mt-3">
          Showing {filtered.length} of {data.total} trades
        </p>
      )}
    </div>
  );
}
