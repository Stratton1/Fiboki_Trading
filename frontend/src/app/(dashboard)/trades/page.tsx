"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { formatPnl } from "@/lib/format-currency";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { History, Loader2, ChevronLeft, ChevronRight, Tag } from "lucide-react";
import { useBookmarks } from "@/lib/hooks/use-bookmarks";
import { BookmarkButton } from "@/components/BookmarkButton";
import { InfoTip } from "@/components/InfoTip";
import { TpHitSpreadTip } from "@/components/TpHitSpreadTip";
import { strategyShortName } from "@/lib/strategy-names";
import { useJournalList } from "@/lib/hooks/use-journal";

const PAGE_SIZE = 50;

export default function TradesPage() {
  const [page, setPage] = useState(1);
  const [filterStrategy, setFilterStrategy] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  const [filterTag, setFilterTag] = useState("");
  const [showBookmarked, setShowBookmarked] = useState(false);
  const [showJournaledOnly, setShowJournaledOnly] = useState(false);

  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { isBookmarked, toggle: toggleBookmark } = useBookmarks("trade");
  const { journalMap } = useJournalList(filterTag || null);

  // Build server-side query params
  const queryParams = useMemo(() => {
    const parts: string[] = [`page=${page}`, `size=${PAGE_SIZE}`];
    if (filterStrategy) parts.push(`strategy_id=${filterStrategy}`);
    if (filterDirection) parts.push(`direction=${filterDirection}`);
    return parts.join("&");
  }, [page, filterStrategy, filterDirection]);

  const { data, isLoading } = useSWR(
    `/trades?${queryParams}`,
    () => api.listTrades(queryParams),
  );

  const trades = data?.items ?? [];
  const totalTrades = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalTrades / PAGE_SIZE));

  // Collect all unique tags from journal entries for the filter dropdown
  const allTags = useMemo(() => {
    const tagSet = new Set<string>();
    for (const entry of journalMap.values()) {
      for (const t of entry.tags) tagSet.add(t);
    }
    return Array.from(tagSet).sort();
  }, [journalMap]);

  // Client-side filters (bookmarks, journal)
  const displayTrades = trades.filter((t) => {
    if (showBookmarked && !isBookmarked("trade", t.id)) return false;
    if (showJournaledOnly && !journalMap.has(t.id)) return false;
    if (filterTag && !journalMap.get(t.id)?.tags.includes(filterTag)) return false;
    return true;
  });

  function handleFilterChange(setter: (v: string) => void) {
    return (e: React.ChangeEvent<HTMLSelectElement>) => {
      setter(e.target.value);
      setPage(1);
    };
  }

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Trade History"
        subtitle="Review completed trades across all strategies"
      />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-5">
        <select value={filterStrategy} onChange={handleFilterChange(setFilterStrategy)} className="input">
          <option value="">All Strategies</option>
          {strategies?.map((s: Record<string, unknown>) => (
            <option key={s.strategy_id as string} value={s.strategy_id as string}>
              {(s.name as string) || (s.strategy_id as string)}
            </option>
          ))}
        </select>
        <select value={filterDirection} onChange={handleFilterChange(setFilterDirection)} className="input">
          <option value="">All Directions</option>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
        </select>
        {allTags.length > 0 && (
          <select
            value={filterTag}
            onChange={(e) => { setFilterTag(e.target.value); setPage(1); }}
            className="input"
          >
            <option value="">All Tags</option>
            {allTags.map((tag) => (
              <option key={tag} value={tag}>{tag}</option>
            ))}
          </select>
        )}
        <button
          onClick={() => setShowBookmarked(!showBookmarked)}
          className={`text-xs px-3 py-1 rounded border ${showBookmarked ? "bg-amber-50 border-amber-300 text-amber-700" : "border-gray-200"}`}
        >
          {showBookmarked ? "Showing Bookmarked" : "Show Bookmarked"}
        </button>
        <button
          onClick={() => setShowJournaledOnly(!showJournaledOnly)}
          className={`text-xs px-3 py-1 rounded border flex items-center gap-1 ${showJournaledOnly ? "bg-primary/10 border-primary/30 text-primary" : "border-gray-200"}`}
        >
          <Tag size={11} />
          {showJournaledOnly ? "Showing Journaled" : "Show Journaled"}
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
              <th className="text-right">PnL<InfoTip text="Profit/Loss for this trade. Green = profit, red = loss. Based on entry vs exit price." /></th>
              <th className="text-left">Exit Reason<InfoTip text="Why the trade was closed: signal reversal, stop loss, take profit, trailing stop, or manual close." /></th>
              <th className="text-left">Journal</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={10}>
                  <div className="flex items-center justify-center gap-2 py-8 text-foreground-muted">
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-sm">Loading trades...</span>
                  </div>
                </td>
              </tr>
            )}
            {!isLoading && trades.length === 0 && (
              <tr>
                <td colSpan={10}>
                  <EmptyState
                    icon={<History size={36} strokeWidth={1.5} />}
                    title="No trades recorded"
                    description="Trades from backtests and paper bots will appear here."
                  />
                </td>
              </tr>
            )}
            {displayTrades.map((t) => {
              const journal = journalMap.get(t.id);
              return (
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
                  <td title={strategyShortName(t.strategy_id)}>{t.strategy_id}</td>
                  <td>{t.instrument}</td>
                  <td>
                    <StatusBadge variant={t.direction === "LONG" ? "ok" : "error"}>
                      {t.direction}
                    </StatusBadge>
                  </td>
                  <td className="text-right tabular-nums">{t.entry_price.toFixed(5)}</td>
                  <td className="text-right tabular-nums">{t.exit_price.toFixed(5)}</td>
                  <td className={`text-right tabular-nums font-medium ${t.pnl >= 0 ? "text-primary" : "text-danger"}`}>
                    {formatPnl(t.pnl)}
                  </td>
                  <td className="text-foreground-muted">
                    {t.exit_reason}
                    <TpHitSpreadTip trade={t} />
                  </td>
                  <td>
                    {journal ? (
                      <div className="flex flex-wrap gap-1">
                        {journal.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary"
                          >
                            {tag}
                          </span>
                        ))}
                        {journal.tags.length > 3 && (
                          <span className="text-[10px] text-foreground-muted">
                            +{journal.tags.length - 3}
                          </span>
                        )}
                        {journal.tags.length === 0 && journal.note && (
                          <span className="text-[10px] text-foreground-muted">noted</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-[10px] text-foreground-muted">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between mt-4">
        <p className="text-xs text-foreground-muted">
          {totalTrades > 0
            ? `Showing ${(page - 1) * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE, totalTrades)} of ${totalTrades} trades`
            : "No trades"}
        </p>
        {totalPages > 1 && (
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(1)}
              disabled={page <= 1}
              className="text-xs px-2 py-1 rounded border border-gray-200 disabled:opacity-40"
            >
              First
            </button>
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="text-xs px-2 py-1 rounded border border-gray-200 disabled:opacity-40 flex items-center"
            >
              <ChevronLeft size={12} />
            </button>
            <span className="text-xs px-2 tabular-nums">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="text-xs px-2 py-1 rounded border border-gray-200 disabled:opacity-40 flex items-center"
            >
              <ChevronRight size={12} />
            </button>
            <button
              onClick={() => setPage(totalPages)}
              disabled={page >= totalPages}
              className="text-xs px-2 py-1 rounded border border-gray-200 disabled:opacity-40"
            >
              Last
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
