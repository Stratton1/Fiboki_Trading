"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useBacktests } from "@/lib/hooks/use-backtests";
import { formatPnl } from "@/lib/format-currency";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";
import WatchlistPicker from "@/components/WatchlistPicker";
import { useWatchlists } from "@/lib/hooks/use-watchlists";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { useManifest } from "@/lib/hooks/use-manifest";
import { BarChart3, GitCompareArrows, Loader2, Trash2, CheckSquare, Square, AlertTriangle } from "lucide-react";
import { useBookmarks } from "@/lib/hooks/use-bookmarks";
import { BookmarkButton } from "@/components/BookmarkButton";
import { InfoTip } from "@/components/InfoTip";

export default function BacktestsPage() {
  const { data: backtests, mutate, isLoading } = useBacktests();
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { data: instruments } = useSWR("instruments", () => api.instruments());
  const { filterSet } = useWatchlists();
  const [strategy, setStrategy] = useState("");
  const [instrument, setInstrument] = useState("");
  const [timeframe, setTimeframe] = useState("H1");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { hasData, availableTimeframes, datasetInfo } = useManifest();
  const { isBookmarked, toggle: toggleBookmark } = useBookmarks("backtest");
  const [showBookmarked, setShowBookmarked] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);

  // Bulk selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [showLegacyOnly, setShowLegacyOnly] = useState(false);
  const [confirmBulk, setConfirmBulk] = useState(false);

  async function handleDelete(id: number) {
    if (!confirm("Delete this backtest run and all its trades?")) return;
    setDeleting(id);
    try {
      await api.deleteBacktest(id);
      const next = new Set(selectedIds);
      next.delete(id);
      setSelectedIds(next);
      mutate();
    } catch {
      /* ignore */
    } finally {
      setDeleting(null);
    }
  }

  async function handleBulkDelete() {
    if (selectedIds.size === 0) return;
    setBulkDeleting(true);
    try {
      await api.bulkDeleteBacktests(Array.from(selectedIds));
      setSelectedIds(new Set());
      setConfirmBulk(false);
      mutate();
    } catch {
      /* ignore */
    } finally {
      setBulkDeleting(false);
    }
  }

  function toggleSelect(id: number) {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedIds(next);
  }

  function toggleSelectAll(ids: number[]) {
    const allSelected = ids.length > 0 && ids.every((id) => selectedIds.has(id));
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(ids));
    }
  }

  // Determine which backtests are "legacy" (0 trades or very old)
  function isLegacy(bt: { total_trades: number; created_at: string | null }) {
    if (bt.total_trades === 0) return true;
    if (!bt.created_at) return true;
    const age = Date.now() - new Date(bt.created_at).getTime();
    const thirtyDays = 30 * 24 * 60 * 60 * 1000;
    return age > thirtyDays;
  }

  const ALL_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"];
  const manifestTimeframes = instrument ? availableTimeframes(instrument) : [];
  const noDataForCombo = instrument && timeframe && !hasData(instrument, timeframe);
  const comboInfo = instrument && timeframe ? datasetInfo(instrument, timeframe) : undefined;

  const [runProgress, setRunProgress] = useState(0);

  // Filter backtests
  const displayBacktests = (backtests ?? []).filter((bt) => {
    if (showBookmarked && !isBookmarked("backtest", bt.id)) return false;
    if (showLegacyOnly && !isLegacy(bt)) return false;
    return true;
  });

  const visibleIds = displayBacktests.map((bt) => bt.id);
  const allVisible = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
  const legacyCount = (backtests ?? []).filter(isLegacy).length;

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (!strategy || !instrument) return;
    setRunning(true);
    setRunProgress(0);
    setError(null);
    try {
      const res = await api.runBacktest({ strategy_id: strategy, instrument, timeframe }, true);
      const jobId = (res as Record<string, unknown>).job_id as string;

      const poll = setInterval(async () => {
        try {
          const job = await api.getJob(jobId);
          setRunProgress(job.progress ?? 0);

          if (job.state === "completed") {
            clearInterval(poll);
            await mutate();
            setRunning(false);
          } else if (job.state === "failed") {
            clearInterval(poll);
            setError(job.error || "Backtest failed");
            setRunning(false);
          } else if (job.state === "cancelled") {
            clearInterval(poll);
            setError("Backtest was cancelled");
            setRunning(false);
          }
        } catch {
          // poll error — keep trying
        }
      }, 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run backtest");
      setRunning(false);
    }
  }

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Backtests"
        subtitle="Run strategy backtests and review historical performance"
        actions={
          <Link href="/backtests/compare" className="btn btn-secondary text-sm">
            <GitCompareArrows size={14} />
            Compare
          </Link>
        }
      />

      {/* Run Backtest Form */}
      <form onSubmit={handleRun} className="card-elevated mb-6">
        <p className="section-label">Run Backtest</p>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Strategy</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="input"
            >
              <option value="">Select strategy</option>
              {strategies?.map((s: Record<string, unknown>) => (
                <option key={s.id as string} value={s.id as string}>{(s.name as string) || (s.id as string)}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="flex items-center gap-2 text-xs text-foreground-muted mb-1.5">
              Instrument
              <WatchlistPicker />
            </label>
            <GroupedInstrumentSelect
              instruments={instruments ?? []}
              value={instrument}
              onChange={setInstrument}
              className="input"
              showDataIndicator
              watchlistFilter={filterSet}
            />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Timeframe</label>
            <select
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="input"
            >
              {ALL_TIMEFRAMES.map((tf) => {
                const available = manifestTimeframes.length === 0 || manifestTimeframes.includes(tf);
                return (
                  <option key={tf} value={tf} disabled={!available}>
                    {tf}{!available ? " (no data)" : ""}
                  </option>
                );
              })}
            </select>
          </div>
          <button
            type="submit"
            disabled={running || !strategy || !instrument || !!noDataForCombo}
            className="btn btn-primary"
          >
            {running && <Loader2 size={14} className="animate-spin" />}
            {running ? `Running (${runProgress}%)` : "Run Backtest"}
          </button>
        </div>
        {noDataForCombo && (
          <p className="text-amber-600 text-sm mt-2">
            No data available for {instrument}/{timeframe}. Select a different timeframe or ingest data first.
          </p>
        )}
        {comboInfo && !noDataForCombo && (
          <p className="text-foreground-muted text-xs mt-2">
            {comboInfo.bars.toLocaleString()} bars &middot; {comboInfo.from_date.slice(0, 10)} to {comboInfo.to_date.slice(0, 10)} &middot; {comboInfo.provider}
          </p>
        )}
        {running && (
          <div className="w-full h-1.5 bg-background-muted rounded-full overflow-hidden mt-3">
            <div
              className="h-full bg-primary rounded-full transition-all duration-300"
              style={{ width: `${runProgress}%` }}
            />
          </div>
        )}
        {error && <p className="text-danger text-sm mt-3">{error}</p>}
      </form>

      {/* Filter + bulk controls */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          onClick={() => setShowBookmarked(!showBookmarked)}
          className={`text-xs px-3 py-1 rounded border ${showBookmarked ? "bg-amber-50 border-amber-300 text-amber-700" : "border-gray-200"}`}
        >
          {showBookmarked ? "Showing Bookmarked" : "Show Bookmarked"}
        </button>
        {legacyCount > 0 && (
          <button
            onClick={() => setShowLegacyOnly(!showLegacyOnly)}
            className={`text-xs px-3 py-1 rounded border ${showLegacyOnly ? "bg-orange-50 border-orange-300 text-orange-700" : "border-gray-200"}`}
          >
            {showLegacyOnly ? `Legacy Only (${legacyCount})` : `Show Legacy (${legacyCount})`}
          </button>
        )}
        {selectedIds.size > 0 && (
          <>
            <span className="text-xs text-foreground-muted">
              {selectedIds.size} selected
            </span>
            <button
              onClick={() => setConfirmBulk(true)}
              disabled={bulkDeleting}
              className="text-xs px-3 py-1 rounded border border-red-300 text-red-700 hover:bg-red-50"
            >
              {bulkDeleting ? "Deleting..." : "Delete Selected"}
            </button>
            <button
              onClick={() => setSelectedIds(new Set())}
              className="text-xs text-foreground-muted hover:underline"
            >
              Clear selection
            </button>
          </>
        )}
      </div>

      {/* Bulk delete confirmation */}
      {confirmBulk && (
        <div className="mb-4 p-3 rounded bg-red-50 border border-red-200 text-sm flex items-center gap-3">
          <AlertTriangle size={16} className="text-red-600 shrink-0" />
          <span className="text-red-800">
            Delete {selectedIds.size} backtest{selectedIds.size !== 1 ? "s" : ""} and all associated trades? This cannot be undone.
          </span>
          <button
            onClick={handleBulkDelete}
            disabled={bulkDeleting}
            className="ml-auto text-xs px-3 py-1 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
          >
            {bulkDeleting ? "Deleting..." : "Confirm Delete"}
          </button>
          <button
            onClick={() => setConfirmBulk(false)}
            className="text-xs text-red-600 hover:underline"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Results Table */}
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th className="w-8">
                <button
                  onClick={() => toggleSelectAll(visibleIds)}
                  className="text-foreground-muted hover:text-foreground"
                  title={allVisible ? "Deselect all" : "Select all visible"}
                >
                  {allVisible && visibleIds.length > 0 ? <CheckSquare size={14} /> : <Square size={14} />}
                </button>
              </th>
              <th className="w-8"></th>
              <th className="text-left">Strategy</th>
              <th className="text-left">Instrument</th>
              <th className="text-left">TF</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Net Profit</th>
              <th className="text-right">Sharpe<InfoTip text="Sharpe Ratio: risk-adjusted return. Above 1.0 is good, above 2.0 is excellent. Measures return per unit of volatility." /></th>
              <th className="text-right">Max DD<InfoTip text="Maximum Drawdown: largest peak-to-trough decline in equity. Lower is better — indicates worst-case loss scenario." /></th>
              <th className="text-left">Created</th>
              <th className="w-8"></th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={11}>
                  <div className="flex items-center justify-center gap-2 py-8 text-foreground-muted">
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-sm">Loading backtests...</span>
                  </div>
                </td>
              </tr>
            )}
            {!isLoading && displayBacktests.length === 0 && (
              <tr>
                <td colSpan={11}>
                  <EmptyState
                    icon={<BarChart3 size={36} strokeWidth={1.5} />}
                    title={showLegacyOnly ? "No legacy backtests" : "No backtests yet"}
                    description={showLegacyOnly ? "No legacy backtests found." : "Run your first backtest above to see results here."}
                  />
                </td>
              </tr>
            )}
            {displayBacktests.map((bt) => {
              const legacy = isLegacy(bt);
              return (
                <tr key={bt.id} className={legacy ? "opacity-70" : ""}>
                  <td>
                    <button
                      onClick={() => toggleSelect(bt.id)}
                      className="text-foreground-muted hover:text-foreground"
                    >
                      {selectedIds.has(bt.id) ? <CheckSquare size={14} /> : <Square size={14} />}
                    </button>
                  </td>
                  <td>
                    <BookmarkButton
                      isBookmarked={isBookmarked("backtest", bt.id)}
                      onToggle={() => toggleBookmark("backtest", bt.id)}
                    />
                  </td>
                  <td>
                    <Link href={`/backtests/${bt.id}`} className="text-primary font-medium hover:underline">
                      {bt.strategy_id}
                    </Link>
                  </td>
                  <td>{bt.instrument}</td>
                  <td className="text-foreground-muted">{bt.timeframe}</td>
                  <td className="text-right tabular-nums">
                    {bt.total_trades === 0 ? (
                      <span className="text-foreground-muted">0</span>
                    ) : (
                      bt.total_trades
                    )}
                  </td>
                  <td className={`text-right tabular-nums font-medium ${bt.net_profit >= 0 ? "text-primary" : "text-danger"}`}>
                    {formatPnl(bt.net_profit)}
                  </td>
                  <td className="text-right tabular-nums">{bt.sharpe_ratio?.toFixed(2) ?? "—"}</td>
                  <td className="text-right tabular-nums text-danger">
                    {bt.max_drawdown_pct != null ? `${bt.max_drawdown_pct.toFixed(1)}%` : "—"}
                  </td>
                  <td className="text-xs text-foreground-muted">
                    {bt.created_at
                      ? new Date(bt.created_at).toLocaleDateString()
                      : "—"}
                  </td>
                  <td>
                    <button
                      onClick={() => handleDelete(bt.id)}
                      disabled={deleting === bt.id}
                      className="text-foreground-muted hover:text-danger transition-colors disabled:opacity-50"
                      title="Delete backtest"
                    >
                      {deleting === bt.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
