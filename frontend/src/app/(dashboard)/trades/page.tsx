"use client";

import Link from "next/link";
import { useTrades } from "@/lib/hooks/use-trades";

export default function TradesPage() {
  const { data, isLoading } = useTrades();
  const trades = data?.items ?? [];

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Trade History</h2>

      <div className="bg-background-card rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-background-muted">
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
            {trades.map((t) => (
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
          Showing {trades.length} of {data.total} trades
        </p>
      )}
    </div>
  );
}
