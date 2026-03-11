"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { useTrades } from "@/lib/hooks/use-trades";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { History, Loader2 } from "lucide-react";
import { useBookmarks } from "@/lib/hooks/use-bookmarks";
import { BookmarkButton } from "@/components/BookmarkButton";

export default function TradesPage() {
  const { data, isLoading } = useTrades();
  const trades = data?.items ?? [];
  const [filterStrategy, setFilterStrategy] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { isBookmarked, toggle: toggleBookmark } = useBookmarks("trade");
  const [showBookmarked, setShowBookmarked] = useState(false);

  const filtered = trades.filter((t) => {
    if (filterStrategy && t.strategy_id !== filterStrategy) return false;
    if (filterDirection && t.direction !== filterDirection) return false;
    return true;
  });

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Trade History"
        subtitle="Review completed trades across all strategies"
      />

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-5">
        <select value={filterStrategy} onChange={(e) => setFilterStrategy(e.target.value)} className="input">
          <option value="">All Strategies</option>
          {strategies?.map((s: any) => <option key={s.strategy_id} value={s.strategy_id}>{s.strategy_id}</option>)}
        </select>
        <select value={filterDirection} onChange={(e) => setFilterDirection(e.target.value)} className="input">
          <option value="">All Directions</option>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
        </select>
      </div>

      {/* Bookmark filter */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setShowBookmarked(!showBookmarked)}
          className={`text-xs px-3 py-1 rounded border ${showBookmarked ? "bg-amber-50 border-amber-300 text-amber-700" : "border-gray-200"}`}
        >
          {showBookmarked ? "Showing Bookmarked" : "Show Bookmarked"}
        </button>
      </div>

      {/* Trades Table */}
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th className="w-8"></th>
              <th className="text-left">Date</th>
              <th className="text-left">Strategy</th>
              <th className="text-left">Instrument</th>
              <th className="text-left">Direction</th>
              <th className="text-right">Entry</th>
              <th className="text-right">Exit</th>
              <th className="text-right">PnL</th>
              <th className="text-left">Exit Reason</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={9}>
                  <div className="flex items-center justify-center gap-2 py-8 text-foreground-muted">
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-sm">Loading trades...</span>
                  </div>
                </td>
              </tr>
            )}
            {!isLoading && trades.length === 0 && (
              <tr>
                <td colSpan={9}>
                  <EmptyState
                    icon={<History size={36} strokeWidth={1.5} />}
                    title="No trades recorded"
                    description="Trades from backtests and paper bots will appear here."
                  />
                </td>
              </tr>
            )}
            {filtered
              .filter((t) => !showBookmarked || isBookmarked("trade", t.id))
              .map((t) => (
              <tr key={t.id}>
                <td>
                  <BookmarkButton
                    isBookmarked={isBookmarked("trade", t.id)}
                    onToggle={() => toggleBookmark("trade", t.id)}
                  />
                </td>
                <td>
                  <Link href={`/trades/${t.id}`} className="text-primary font-medium hover:underline">
                    {t.entry_time ? new Date(t.entry_time).toLocaleDateString() : "—"}
                  </Link>
                </td>
                <td>{t.strategy_id}</td>
                <td>{t.instrument}</td>
                <td>
                  <StatusBadge variant={t.direction === "LONG" ? "ok" : "error"}>
                    {t.direction}
                  </StatusBadge>
                </td>
                <td className="text-right tabular-nums">{t.entry_price.toFixed(5)}</td>
                <td className="text-right tabular-nums">{t.exit_price.toFixed(5)}</td>
                <td className={`text-right tabular-nums font-medium ${t.pnl >= 0 ? "text-primary" : "text-danger"}`}>
                  {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}
                </td>
                <td className="text-foreground-muted">{t.exit_reason}</td>
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
