"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useBacktests } from "@/lib/hooks/use-backtests";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { useManifest } from "@/lib/hooks/use-manifest";
import { BarChart3, GitCompareArrows, Loader2 } from "lucide-react";
import { useBookmarks } from "@/lib/hooks/use-bookmarks";
import { BookmarkButton } from "@/components/BookmarkButton";

export default function BacktestsPage() {
  const { data: backtests, mutate, isLoading } = useBacktests();
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { data: instruments } = useSWR("instruments", () => api.instruments());
  const [strategy, setStrategy] = useState("");
  const [instrument, setInstrument] = useState("");
  const [timeframe, setTimeframe] = useState("H1");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { hasData, availableTimeframes, datasetInfo } = useManifest();
  const { isBookmarked, toggle: toggleBookmark } = useBookmarks("backtest");
  const [showBookmarked, setShowBookmarked] = useState(false);

  const ALL_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"];
  const manifestTimeframes = instrument ? availableTimeframes(instrument) : [];
  const noDataForCombo = instrument && timeframe && !hasData(instrument, timeframe);
  const comboInfo = instrument && timeframe ? datasetInfo(instrument, timeframe) : undefined;

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (!strategy || !instrument) return;
    setRunning(true);
    setError(null);
    try {
      await api.runBacktest({ strategy_id: strategy, instrument, timeframe });
      await mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run backtest");
    } finally {
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
              {strategies?.map((s: any) => (
                <option key={s.strategy_id} value={s.strategy_id}>{s.strategy_name || s.strategy_id}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Instrument</label>
            <GroupedInstrumentSelect
              instruments={instruments ?? []}
              value={instrument}
              onChange={setInstrument}
              className="input"
              showDataIndicator
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
            {running ? "Running..." : "Run Backtest"}
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
        {error && <p className="text-danger text-sm mt-3">{error}</p>}
      </form>

      {/* Bookmark filter */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setShowBookmarked(!showBookmarked)}
          className={`text-xs px-3 py-1 rounded border ${showBookmarked ? "bg-amber-50 border-amber-300 text-amber-700" : "border-gray-200"}`}
        >
          {showBookmarked ? "Showing Bookmarked" : "Show Bookmarked"}
        </button>
      </div>

      {/* Results Table */}
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th className="w-8"></th>
              <th className="text-left">Strategy</th>
              <th className="text-left">Instrument</th>
              <th className="text-left">TF</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Net Profit</th>
              <th className="text-right">Sharpe</th>
              <th className="text-right">Max DD</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={8}>
                  <div className="flex items-center justify-center gap-2 py-8 text-foreground-muted">
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-sm">Loading backtests...</span>
                  </div>
                </td>
              </tr>
            )}
            {!isLoading && (!backtests || backtests.length === 0) && (
              <tr>
                <td colSpan={8}>
                  <EmptyState
                    icon={<BarChart3 size={36} strokeWidth={1.5} />}
                    title="No backtests yet"
                    description="Run your first backtest above to see results here."
                  />
                </td>
              </tr>
            )}
            {backtests
              ?.filter((bt) => !showBookmarked || isBookmarked("backtest", bt.id))
              .map((bt) => (
              <tr key={bt.id}>
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
                <td className="text-right tabular-nums">{bt.total_trades}</td>
                <td className={`text-right tabular-nums font-medium ${bt.net_profit >= 0 ? "text-primary" : "text-danger"}`}>
                  {bt.net_profit >= 0 ? "+" : ""}${bt.net_profit.toFixed(2)}
                </td>
                <td className="text-right tabular-nums">{bt.sharpe_ratio?.toFixed(2) ?? "—"}</td>
                <td className="text-right tabular-nums text-danger">
                  {bt.max_drawdown_pct != null ? `${bt.max_drawdown_pct.toFixed(1)}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
