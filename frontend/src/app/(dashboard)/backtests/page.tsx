"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useBacktests } from "@/lib/hooks/use-backtests";
import { formatPnl } from "@/lib/format-currency";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";
import WatchlistPicker from "@/components/WatchlistPicker";
import { useWatchlists } from "@/lib/hooks/use-watchlists";
import { EmptyState } from "@/components/EmptyState";
import { useManifest } from "@/lib/hooks/use-manifest";
import {
  BarChart3, GitCompareArrows, Loader2, Trash2, CheckSquare, Square,
  AlertTriangle, Star, ChevronDown, ChevronUp, ArrowUpDown,
  Eye, LineChart, Search, Filter, Bot, ExternalLink,
  Beaker, Clock, TrendingUp, ShieldAlert, Bookmark,
} from "lucide-react";
import { useBookmarks } from "@/lib/hooks/use-bookmarks";
import { BookmarkButton } from "@/components/BookmarkButton";
import { InfoTip } from "@/components/InfoTip";
import { useShortlist } from "@/lib/hooks/use-shortlist";
import { strategyShortName } from "@/lib/strategy-names";
import type { BacktestSummary } from "@/types/contracts/analytics";

const MIN_TRADES_FOR_RANKING = 80;
const LEGACY_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

// ─── Sorting ──────────────────────────────────────────────
type SortField = "strategy_id" | "instrument" | "timeframe" | "total_trades" | "net_profit" | "sharpe_ratio" | "max_drawdown_pct" | "created_at";
type SortDir = "asc" | "desc";

function compareField(a: BacktestSummary, b: BacktestSummary, field: SortField): number {
  switch (field) {
    case "strategy_id": return a.strategy_id.localeCompare(b.strategy_id);
    case "instrument": return a.instrument.localeCompare(b.instrument);
    case "timeframe": return a.timeframe.localeCompare(b.timeframe);
    case "total_trades": return a.total_trades - b.total_trades;
    case "net_profit": return a.net_profit - b.net_profit;
    case "sharpe_ratio": return (a.sharpe_ratio ?? -999) - (b.sharpe_ratio ?? -999);
    case "max_drawdown_pct": return (a.max_drawdown_pct ?? 0) - (b.max_drawdown_pct ?? 0);
    case "created_at": return (a.created_at ?? "").localeCompare(b.created_at ?? "");
    default: return 0;
  }
}

// ─── Relative time ────────────────────────────────────────
function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "—";
  const ms = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

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
  const { shortlist, save: saveToShortlist, isShortlisted } = useShortlist();
  const [deleting, setDeleting] = useState<number | null>(null);
  const [promoting, setPromoting] = useState<number | null>(null);
  const [showShortlistPicker, setShowShortlistPicker] = useState(false);

  // Bulk selection
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [confirmBulk, setConfirmBulk] = useState(false);

  // Filters
  const [filterStrategy, setFilterStrategy] = useState("");
  const [filterInstrument, setFilterInstrument] = useState("");
  const [filterBookmarked, setFilterBookmarked] = useState(false);
  const [hideLegacy, setHideLegacy] = useState(true);
  const [filterProfitable, setFilterProfitable] = useState(false);

  // Sorting
  const [sortField, setSortField] = useState<SortField>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const [runProgress, setRunProgress] = useState(0);

  // ─── Helpers ──────────────────────────────────────────────
  function isLegacy(bt: { total_trades: number; created_at: string | null }) {
    if (bt.total_trades === 0) return true;
    if (!bt.created_at) return true;
    const age = Date.now() - new Date(bt.created_at).getTime();
    return age > LEGACY_AGE_MS;
  }

  // ─── Derived data ────────────────────────────────────────
  const allBts = backtests ?? [];
  const legacyCount = allBts.filter(isLegacy).length;
  const bookmarkedCount = allBts.filter((bt) => isBookmarked("backtest", bt.id)).length;

  // Unique values for filter dropdowns
  const uniqueStrategies = useMemo(() => [...new Set(allBts.map((b) => b.strategy_id))].sort(), [allBts]);
  const uniqueInstruments = useMemo(() => [...new Set(allBts.map((b) => b.instrument))].sort(), [allBts]);

  // Apply filters
  const filteredBacktests = useMemo(() => {
    let list = allBts;
    if (filterStrategy) list = list.filter((bt) => bt.strategy_id === filterStrategy);
    if (filterInstrument) list = list.filter((bt) => bt.instrument === filterInstrument);
    if (filterBookmarked) list = list.filter((bt) => isBookmarked("backtest", bt.id));
    if (hideLegacy) list = list.filter((bt) => !isLegacy(bt));
    if (filterProfitable) list = list.filter((bt) => bt.net_profit > 0);
    return list;
  }, [allBts, filterStrategy, filterInstrument, filterBookmarked, hideLegacy, filterProfitable, isBookmarked]);

  // Apply sort
  const displayBacktests = useMemo(() => {
    const sorted = [...filteredBacktests].sort((a, b) => compareField(a, b, sortField));
    return sortDir === "desc" ? sorted.reverse() : sorted;
  }, [filteredBacktests, sortField, sortDir]);

  const visibleIds = displayBacktests.map((bt) => bt.id);
  const allVisible = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
  const activeFilterCount = [filterStrategy, filterInstrument, filterBookmarked, hideLegacy, filterProfitable].filter(Boolean).length;

  // Summary stats
  const bestSharpe = displayBacktests.reduce((max, bt) => Math.max(max, bt.sharpe_ratio ?? -Infinity), -Infinity);
  const bestProfit = displayBacktests.reduce((max, bt) => Math.max(max, bt.net_profit), -Infinity);

  // ─── Handlers ─────────────────────────────────────────────
  async function handleDelete(id: number) {
    if (!confirm("Delete this backtest run and all its trades?")) return;
    setDeleting(id);
    try {
      await api.deleteBacktest(id);
      const next = new Set(selectedIds);
      next.delete(id);
      setSelectedIds(next);
      mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete backtest");
    } finally { setDeleting(null); }
  }

  async function handleBulkDelete() {
    if (selectedIds.size === 0) return;
    setBulkDeleting(true);
    try {
      await api.bulkDeleteBacktests(Array.from(selectedIds));
      setSelectedIds(new Set());
      setConfirmBulk(false);
      mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete backtests");
    } finally { setBulkDeleting(false); }
  }

  function toggleSelect(id: number) {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelectedIds(next);
  }

  function toggleSelectAll(ids: number[]) {
    const allSelected = ids.length > 0 && ids.every((id) => selectedIds.has(id));
    setSelectedIds(allSelected ? new Set() : new Set(ids));
  }

  function handleSort(field: SortField) {
    if (sortField === field) setSortDir((d) => d === "asc" ? "desc" : "asc");
    else { setSortField(field); setSortDir("desc"); }
  }

  async function handlePromote(bt: BacktestSummary) {
    setPromoting(bt.id);
    try {
      await saveToShortlist({
        strategy_id: bt.strategy_id,
        instrument: bt.instrument,
        timeframe: bt.timeframe,
        score: bt.sharpe_ratio ?? 0,
        note: `Promoted from backtest #${bt.id}`,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save to shortlist");
    } finally { setPromoting(null); }
  }

  function loadFromShortlist(entry: { strategy_id: string; instrument: string; timeframe: string }) {
    setStrategy(entry.strategy_id);
    setInstrument(entry.instrument);
    setTimeframe(entry.timeframe);
    setShowShortlistPicker(false);
  }

  const ALL_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"];
  const manifestTimeframes = instrument ? availableTimeframes(instrument) : [];
  const noDataForCombo = instrument && timeframe && !hasData(instrument, timeframe);
  const comboInfo = instrument && timeframe ? datasetInfo(instrument, timeframe) : undefined;
  const canRun = !!strategy && !!instrument && !noDataForCombo && !running;

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
          if (job.state === "completed") { clearInterval(poll); await mutate(); setRunning(false); }
          else if (job.state === "failed") { clearInterval(poll); setError(job.error || "Backtest failed"); setRunning(false); }
          else if (job.state === "cancelled") { clearInterval(poll); setError("Backtest was cancelled"); setRunning(false); }
        } catch { /* poll error */ }
      }, 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run backtest");
      setRunning(false);
    }
  }

  // Sort arrow helper
  const SortHeader = ({ field, label, align, tip }: { field: SortField; label: string; align?: string; tip?: string }) => (
    <th
      className={`${align ?? "text-left"} cursor-pointer select-none hover:text-foreground transition-colors`}
      onClick={() => handleSort(field)}
      data-testid={`sort-${field}`}
    >
      <span className="inline-flex items-center gap-0.5">
        {label}
        {sortField === field ? (
          sortDir === "asc" ? <ChevronUp size={12} /> : <ChevronDown size={12} />
        ) : (
          <ArrowUpDown size={10} className="opacity-30" />
        )}
        {tip && <InfoTip text={tip} />}
      </span>
    </th>
  );

  return (
    <div className="max-w-7xl" data-testid="backtests-page">
      {/* ── Page Header ──────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4 mb-5">
        <div className="flex items-center gap-2">
          <Beaker size={22} className="text-primary" />
          <div>
            <h1 className="text-xl font-bold text-foreground tracking-tight leading-tight">
              Backtests
              <InfoTip text="Run strategy backtests against historical data, review results, and promote strong candidates to your Shortlist or Paper Trading." />
            </h1>
            <p className="text-xs text-foreground-muted mt-0.5">
              Run, review, compare, and promote strategy backtests
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/backtests/compare"
            className={`btn btn-secondary text-sm ${selectedIds.size < 2 ? "" : "ring-2 ring-primary/30"}`}
            data-testid="compare-btn"
          >
            <GitCompareArrows size={14} />
            Compare {selectedIds.size >= 2 ? `(${selectedIds.size})` : ""}
          </Link>
        </div>
      </div>

      {/* ── Run Backtest Form ────────────────────────────────── */}
      <form onSubmit={handleRun} className="card-elevated mb-5" data-testid="run-form">
        <div className="flex items-center gap-2 mb-3">
          <p className="section-label mb-0">Run Backtest</p>
          <InfoTip text="Select a strategy, instrument, and timeframe, then click Run. The backtest runs asynchronously — you'll see progress here and results appear in the table below." />
        </div>

        <div className="flex flex-wrap gap-3 items-end">
          {/* Strategy */}
          <div data-testid="strategy-field">
            <label className="block text-xs text-foreground-muted mb-1">Strategy</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="input"
              data-testid="strategy-select"
            >
              <option value="">Select strategy</option>
              {strategies?.map((s: Record<string, unknown>) => (
                <option key={s.id as string} value={s.id as string}>
                  {(s.name as string) || (s.id as string)}
                </option>
              ))}
            </select>
            {!strategy && <p className="text-[10px] text-foreground-muted mt-0.5">Required</p>}
          </div>

          {/* Instrument */}
          <div data-testid="instrument-field">
            <label className="flex items-center gap-2 text-xs text-foreground-muted mb-1">
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
            {!instrument && <p className="text-[10px] text-foreground-muted mt-0.5">Required</p>}
          </div>

          {/* Timeframe — quick buttons */}
          <div data-testid="timeframe-field">
            <label className="block text-xs text-foreground-muted mb-1">Timeframe</label>
            <div className="flex gap-0.5">
              {ALL_TIMEFRAMES.map((tf) => {
                const available = manifestTimeframes.length === 0 || manifestTimeframes.includes(tf);
                return (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => setTimeframe(tf)}
                    disabled={!available}
                    className={`px-2 py-1 text-xs rounded transition ${
                      timeframe === tf
                        ? "bg-primary text-white font-medium"
                        : available
                          ? "bg-background text-foreground-muted hover:text-foreground hover:bg-background-muted border border-gray-200"
                          : "bg-background text-foreground-muted/40 border border-gray-100 cursor-not-allowed"
                    }`}
                    title={!available ? `No data for ${instrument}/${tf}` : tf}
                    data-testid={`tf-btn-${tf}`}
                  >
                    {tf}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Run button */}
          <button
            type="submit"
            disabled={!canRun}
            className="btn btn-primary"
            data-testid="run-btn"
            title={
              !strategy ? "Select a strategy first"
                : !instrument ? "Select an instrument first"
                  : noDataForCombo ? `No data for ${instrument}/${timeframe}`
                    : running ? "Backtest in progress..."
                      : `Run ${strategy} on ${instrument} ${timeframe}`
            }
          >
            {running && <Loader2 size={14} className="animate-spin" />}
            {running ? `Running (${runProgress}%)` : "Run Backtest"}
          </button>

          {/* From Shortlist */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowShortlistPicker(!showShortlistPicker)}
              className="btn btn-secondary text-xs"
              disabled={shortlist.length === 0}
              title={shortlist.length === 0 ? "No shortlisted combos — go to Research to build your shortlist" : "Load a strategy/instrument/timeframe combo from your Shortlist"}
              data-testid="from-shortlist-btn"
            >
              <Star size={12} />
              From Shortlist {shortlist.filter((e) => e.status === "active").length > 0 && `(${shortlist.filter((e) => e.status === "active").length})`}
              <ChevronDown size={12} />
            </button>
            {showShortlistPicker && shortlist.length > 0 && (
              <div className="absolute top-full left-0 mt-1 bg-white border border-border rounded-lg shadow-lg z-20 w-80 max-h-60 overflow-y-auto" data-testid="shortlist-dropdown">
                <div className="px-3 py-1.5 border-b border-border text-[10px] text-foreground-muted font-medium uppercase tracking-wide">
                  Shortlisted Combos
                </div>
                {shortlist.filter((e) => e.status === "active").map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() => loadFromShortlist(e)}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-background-muted transition-colors flex items-center gap-2"
                  >
                    <span className="font-medium">{e.strategy_id}</span>
                    <span className="text-[10px] text-foreground-muted truncate max-w-[100px]">{strategyShortName(e.strategy_id)}</span>
                    <span className="text-foreground-muted">{e.instrument}</span>
                    <span className="text-foreground-muted">{e.timeframe}</span>
                    <span className="ml-auto text-xs text-foreground-muted tabular-nums">{e.score.toFixed(2)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Data info / warnings */}
        {noDataForCombo && (
          <p className="text-amber-600 text-sm mt-2 flex items-center gap-1" data-testid="no-data-warning">
            <AlertTriangle size={13} />
            No data for {instrument}/{timeframe}. Select a different timeframe or ingest data first.
          </p>
        )}
        {comboInfo && !noDataForCombo && (
          <p className="text-foreground-muted text-xs mt-2" data-testid="data-info">
            {comboInfo.bars.toLocaleString()} bars &middot; {comboInfo.from_date.slice(0, 10)} to {comboInfo.to_date.slice(0, 10)} &middot; {comboInfo.provider}
          </p>
        )}
        {running && (
          <div className="w-full h-1.5 bg-background-muted rounded-full overflow-hidden mt-3" data-testid="run-progress">
            <div className="h-full bg-primary rounded-full transition-all duration-300" style={{ width: `${runProgress}%` }} />
          </div>
        )}
        {error && <p className="text-danger text-sm mt-3" data-testid="run-error">{error}</p>}
      </form>

      {/* ── Summary Strip ────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 mb-3 text-xs text-foreground-muted" data-testid="summary-strip">
        <span className="flex items-center gap-1">
          <BarChart3 size={12} />
          <strong className="text-foreground">{displayBacktests.length}</strong> shown
          {allBts.length !== displayBacktests.length && ` of ${allBts.length}`}
        </span>
        {bookmarkedCount > 0 && (
          <span className="flex items-center gap-1">
            <Bookmark size={11} /> {bookmarkedCount} bookmarked
          </span>
        )}
        {hideLegacy && legacyCount > 0 && (
          <span className="flex items-center gap-1">
            <ShieldAlert size={11} /> {legacyCount} legacy hidden
          </span>
        )}
        {displayBacktests.length > 0 && bestSharpe > -Infinity && (
          <span className="flex items-center gap-1">
            <TrendingUp size={11} /> Best Sharpe: <strong className="text-foreground">{bestSharpe.toFixed(2)}</strong>
          </span>
        )}
        {displayBacktests.length > 0 && bestProfit > -Infinity && (
          <span>Best PnL: <strong className={bestProfit >= 0 ? "text-primary" : "text-danger"}>{formatPnl(bestProfit)}</strong></span>
        )}
      </div>

      {/* ── Filter Bar ───────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2 mb-3" data-testid="filter-bar">
        <Filter size={13} className="text-foreground-muted" />

        {/* Strategy filter */}
        {uniqueStrategies.length > 1 && (
          <select
            value={filterStrategy}
            onChange={(e) => setFilterStrategy(e.target.value)}
            className="text-xs px-2 py-1 rounded border border-gray-200 bg-background"
            data-testid="filter-strategy"
          >
            <option value="">All Strategies</option>
            {uniqueStrategies.map((s) => <option key={s} value={s}>{s} — {strategyShortName(s)}</option>)}
          </select>
        )}

        {/* Instrument filter */}
        {uniqueInstruments.length > 1 && (
          <select
            value={filterInstrument}
            onChange={(e) => setFilterInstrument(e.target.value)}
            className="text-xs px-2 py-1 rounded border border-gray-200 bg-background"
            data-testid="filter-instrument"
          >
            <option value="">All Instruments</option>
            {uniqueInstruments.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}

        {/* Bookmarked */}
        <button
          onClick={() => setFilterBookmarked(!filterBookmarked)}
          className={`text-xs px-2.5 py-1 rounded border transition ${filterBookmarked ? "bg-amber-50 border-amber-300 text-amber-700" : "border-gray-200 hover:border-gray-300"}`}
          data-testid="filter-bookmarked"
        >
          <span className="flex items-center gap-1">
            <Bookmark size={11} />
            Bookmarked
          </span>
        </button>

        {/* Hide legacy */}
        <button
          onClick={() => setHideLegacy(!hideLegacy)}
          className={`text-xs px-2.5 py-1 rounded border transition ${hideLegacy ? "bg-blue-50 border-blue-300 text-blue-700" : "border-gray-200 hover:border-gray-300"}`}
          data-testid="filter-legacy"
          title="Legacy = 0 trades, no timestamp, or older than 30 days"
        >
          <span className="flex items-center gap-1">
            <ShieldAlert size={11} />
            {hideLegacy ? "Legacy Hidden" : "Show All"}
            {legacyCount > 0 && ` (${legacyCount})`}
          </span>
        </button>

        {/* Profitable only */}
        <button
          onClick={() => setFilterProfitable(!filterProfitable)}
          className={`text-xs px-2.5 py-1 rounded border transition ${filterProfitable ? "bg-green-50 border-green-300 text-green-700" : "border-gray-200 hover:border-gray-300"}`}
          data-testid="filter-profitable"
        >
          <span className="flex items-center gap-1">
            <TrendingUp size={11} />
            Profitable
          </span>
        </button>

        {/* Clear filters */}
        {activeFilterCount > 1 && (
          <button
            onClick={() => { setFilterStrategy(""); setFilterInstrument(""); setFilterBookmarked(false); setHideLegacy(true); setFilterProfitable(false); }}
            className="text-xs text-foreground-muted hover:text-foreground underline"
            data-testid="clear-filters"
          >
            Clear filters
          </button>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Bulk actions */}
        {selectedIds.size > 0 && (
          <div className="flex items-center gap-2" data-testid="bulk-actions">
            <span className="text-xs text-foreground-muted">{selectedIds.size} selected</span>
            <button
              onClick={() => setConfirmBulk(true)}
              disabled={bulkDeleting}
              className="text-xs px-2.5 py-1 rounded border border-red-300 text-red-700 hover:bg-red-50"
              data-testid="bulk-delete-btn"
            >
              {bulkDeleting ? "Deleting..." : "Delete Selected"}
            </button>
            <button
              onClick={() => setSelectedIds(new Set())}
              className="text-xs text-foreground-muted hover:underline"
            >
              Clear
            </button>
          </div>
        )}
      </div>

      {/* Bulk delete confirmation */}
      {confirmBulk && (
        <div className="mb-4 p-3 rounded bg-red-50 border border-red-200 text-sm flex items-center gap-3" data-testid="bulk-confirm">
          <AlertTriangle size={16} className="text-red-600 shrink-0" />
          <span className="text-red-800">
            Delete {selectedIds.size} backtest{selectedIds.size !== 1 ? "s" : ""} and all associated trades? This cannot be undone.
          </span>
          <button
            onClick={handleBulkDelete}
            disabled={bulkDeleting}
            className="ml-auto text-xs px-3 py-1 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
            data-testid="confirm-delete-btn"
          >
            {bulkDeleting ? "Deleting..." : "Confirm Delete"}
          </button>
          <button onClick={() => setConfirmBulk(false)} className="text-xs text-red-600 hover:underline">
            Cancel
          </button>
        </div>
      )}

      {/* ── Results Table ────────────────────────────────────── */}
      <div className="table-container" data-testid="results-table">
        <table>
          <thead>
            <tr>
              <th className="w-8">
                <button
                  onClick={() => toggleSelectAll(visibleIds)}
                  className="text-foreground-muted hover:text-foreground"
                  title={allVisible ? "Deselect all" : "Select all visible"}
                  data-testid="select-all"
                >
                  {allVisible && visibleIds.length > 0 ? <CheckSquare size={14} /> : <Square size={14} />}
                </button>
              </th>
              <th className="w-8"></th>
              <SortHeader field="strategy_id" label="Strategy" />
              <SortHeader field="instrument" label="Instrument" />
              <SortHeader field="timeframe" label="TF" />
              <SortHeader field="total_trades" label="Trades" align="text-right" tip="Total number of completed trades. Below 80 trades is considered statistically weak for primary ranking." />
              <SortHeader field="net_profit" label="Net Profit" align="text-right" tip="Total profit or loss in account currency after all trades, including spread and slippage costs." />
              <SortHeader field="sharpe_ratio" label="Sharpe" align="text-right" tip="Sharpe Ratio: risk-adjusted return per unit of volatility. Above 1.0 is good, above 2.0 is excellent." />
              <SortHeader field="max_drawdown_pct" label="Max DD" align="text-right" tip="Maximum Drawdown: largest peak-to-trough decline in equity. Lower is better — indicates worst-case loss scenario." />
              <SortHeader field="created_at" label="Created" tip="When this backtest was run. Recent results are more trustworthy than legacy ones." />
              <th className="text-left">
                <span className="inline-flex items-center gap-0.5">
                  Actions
                  <InfoTip text="View details, open chart, save to shortlist, or delete. Use checkboxes for bulk operations." />
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={11}>
                  <div className="flex items-center justify-center gap-2 py-8 text-foreground-muted" data-testid="loading-state">
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-sm">Loading backtests...</span>
                  </div>
                </td>
              </tr>
            )}
            {!isLoading && displayBacktests.length === 0 && (
              <tr>
                <td colSpan={11}>
                  <div data-testid="empty-state">
                    <EmptyState
                      icon={<BarChart3 size={36} strokeWidth={1.5} />}
                      title={
                        filterBookmarked ? "No bookmarked backtests"
                          : filterProfitable ? "No profitable backtests"
                            : hideLegacy && legacyCount > 0 ? "No recent backtests"
                              : allBts.length === 0 ? "No backtests yet"
                                : "No backtests match filters"
                      }
                      description={
                        allBts.length === 0
                          ? "Run your first backtest above, or load a combo from your Research shortlist."
                          : filterBookmarked ? "Bookmark backtests to see them here."
                            : hideLegacy && legacyCount > 0 ? `${legacyCount} legacy run${legacyCount !== 1 ? "s" : ""} hidden. Click "Show All" to see them.`
                              : "Try adjusting your filters to see more results."
                      }
                      action={
                        allBts.length === 0 ? (
                          <Link href="/research" className="text-xs text-primary hover:underline flex items-center gap-1">
                            <Search size={12} /> Go to Research
                          </Link>
                        ) : activeFilterCount > 1 ? (
                          <button
                            onClick={() => { setFilterStrategy(""); setFilterInstrument(""); setFilterBookmarked(false); setHideLegacy(false); setFilterProfitable(false); }}
                            className="text-xs text-primary hover:underline"
                          >
                            Clear all filters
                          </button>
                        ) : undefined
                      }
                    />
                  </div>
                </td>
              </tr>
            )}
            {displayBacktests.map((bt) => {
              const legacy = isLegacy(bt);
              const shortlisted = isShortlisted(bt.strategy_id, bt.instrument, bt.timeframe);
              const lowTrades = bt.total_trades > 0 && bt.total_trades < MIN_TRADES_FOR_RANKING;
              return (
                <tr key={bt.id} className={`${legacy ? "opacity-60" : ""} ${selectedIds.has(bt.id) ? "bg-blue-50/50" : ""}`} data-testid="bt-row">
                  <td>
                    <button
                      onClick={() => toggleSelect(bt.id)}
                      className="text-foreground-muted hover:text-foreground"
                      data-testid="row-select"
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
                    <Link href={`/backtests/${bt.id}`} className="text-primary font-medium hover:underline" data-testid="row-strategy">
                      {bt.strategy_id}
                    </Link>
                    <div className="text-[10px] text-foreground-muted truncate max-w-[180px]" title={strategyShortName(bt.strategy_id)}>
                      {strategyShortName(bt.strategy_id)}
                    </div>
                    <div className="flex items-center gap-1 mt-0.5">
                      {legacy && (
                        <span className="text-[9px] px-1 py-0.5 rounded bg-orange-100 text-orange-700 font-medium" data-testid="legacy-badge">LEGACY</span>
                      )}
                      {shortlisted && (
                        <span className="text-[9px] px-1 py-0.5 rounded bg-amber-100 text-amber-700 font-medium" data-testid="shortlisted-badge">SHORTLISTED</span>
                      )}
                    </div>
                  </td>
                  <td className="text-sm">{bt.instrument}</td>
                  <td className="text-foreground-muted text-sm">{bt.timeframe}</td>
                  <td className="text-right tabular-nums text-sm">
                    <span className={lowTrades ? "text-amber-600" : ""}>
                      {bt.total_trades === 0 ? <span className="text-foreground-muted">0</span> : bt.total_trades}
                    </span>
                    {lowTrades && (
                      <span className="ml-1 text-[9px] text-amber-500" title="Below 80-trade minimum for reliable ranking">
                        <AlertTriangle size={10} className="inline" />
                      </span>
                    )}
                  </td>
                  <td className={`text-right tabular-nums font-medium text-sm ${bt.net_profit >= 0 ? "text-primary" : "text-danger"}`}>
                    {formatPnl(bt.net_profit)}
                  </td>
                  <td className="text-right tabular-nums text-sm">{bt.sharpe_ratio?.toFixed(2) ?? "—"}</td>
                  <td className="text-right tabular-nums text-danger text-sm">
                    {bt.max_drawdown_pct != null ? `${bt.max_drawdown_pct.toFixed(1)}%` : "—"}
                  </td>
                  <td className="text-xs text-foreground-muted" data-testid="row-created">
                    <span title={bt.created_at ? new Date(bt.created_at).toLocaleString() : ""}>
                      {timeAgo(bt.created_at)}
                    </span>
                  </td>
                  <td>
                    <div className="flex items-center gap-0.5">
                      <Link
                        href={`/backtests/${bt.id}`}
                        className="p-1 text-foreground-muted hover:text-primary transition-colors"
                        title="View details"
                        data-testid="row-view"
                      >
                        <Eye size={13} />
                      </Link>
                      <Link
                        href={`/charts?instrument=${bt.instrument}`}
                        className="p-1 text-foreground-muted hover:text-primary transition-colors"
                        title={`Open ${bt.instrument} chart`}
                        data-testid="row-chart"
                      >
                        <LineChart size={13} />
                      </Link>
                      <button
                        onClick={() => handlePromote(bt)}
                        disabled={promoting === bt.id || shortlisted}
                        className={`transition-colors disabled:opacity-50 p-1 ${shortlisted ? "text-amber-500" : "text-foreground-muted hover:text-amber-500"}`}
                        title={shortlisted ? "Already in Shortlist" : "Save to Shortlist"}
                        data-testid="row-promote"
                      >
                        {promoting === bt.id ? <Loader2 size={13} className="animate-spin" /> : <Star size={13} fill={shortlisted ? "currentColor" : "none"} />}
                      </button>
                      <button
                        onClick={() => handleDelete(bt.id)}
                        disabled={deleting === bt.id}
                        className="text-foreground-muted hover:text-danger transition-colors disabled:opacity-50 p-1"
                        title="Delete backtest"
                        data-testid="row-delete"
                      >
                        {deleting === bt.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* ── Workflow links footer ─────────────────────────────── */}
      <div className="flex items-center gap-4 mt-4 text-xs text-foreground-muted" data-testid="workflow-links">
        <Link href="/research" className="flex items-center gap-1 hover:text-primary transition-colors">
          <Search size={12} /> Research
        </Link>
        <Link href="/charts" className="flex items-center gap-1 hover:text-primary transition-colors">
          <LineChart size={12} /> Charts
        </Link>
        <Link href="/bots" className="flex items-center gap-1 hover:text-primary transition-colors">
          <Bot size={12} /> Paper Bots
        </Link>
        <Link href="/backtests/compare" className="flex items-center gap-1 hover:text-primary transition-colors">
          <GitCompareArrows size={12} /> Compare
        </Link>
      </div>
    </div>
  );
}
