"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useBacktests } from "@/lib/hooks/use-backtests";

export default function BacktestsPage() {
  const { data: backtests, mutate, isLoading } = useBacktests();
  const [strategy, setStrategy] = useState("");
  const [instrument, setInstrument] = useState("");
  const [timeframe, setTimeframe] = useState("H1");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    <div>
      <h2 className="text-xl font-semibold mb-6">Backtests</h2>

      {/* Run Backtest Form */}
      <form onSubmit={handleRun} className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">Run Backtest</h3>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Strategy</label>
            <input
              type="text"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              placeholder="e.g. ichimoku_tk_cross"
              className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-background"
            />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Instrument</label>
            <input
              type="text"
              value={instrument}
              onChange={(e) => setInstrument(e.target.value)}
              placeholder="e.g. EUR_USD"
              className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-background"
            />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Timeframe</label>
            <select
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-background"
            >
              <option value="M15">M15</option>
              <option value="H1">H1</option>
              <option value="H4">H4</option>
              <option value="D">D</option>
            </select>
          </div>
          <button
            type="submit"
            disabled={running || !strategy || !instrument}
            className="bg-primary text-white px-4 py-1.5 rounded text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            {running ? "Running..." : "Run Backtest"}
          </button>
        </div>
        {error && <p className="text-danger text-sm mt-2">{error}</p>}
      </form>

      {/* Results Table */}
      <div className="bg-background-card rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-background-muted">
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Strategy</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Instrument</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">TF</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Trades</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Net Profit</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Sharpe</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Max DD</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-foreground-muted">Loading...</td>
              </tr>
            )}
            {!isLoading && (!backtests || backtests.length === 0) && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-foreground-muted">
                  No backtests yet. Run one above to get started.
                </td>
              </tr>
            )}
            {backtests?.map((bt) => (
              <tr key={bt.id} className="border-b border-gray-100 hover:bg-background-muted/50">
                <td className="px-4 py-3">
                  <Link href={`/backtests/${bt.id}`} className="text-primary hover:underline">
                    {bt.strategy_id}
                  </Link>
                </td>
                <td className="px-4 py-3">{bt.instrument}</td>
                <td className="px-4 py-3">{bt.timeframe}</td>
                <td className="px-4 py-3 text-right">{bt.total_trades}</td>
                <td className={`px-4 py-3 text-right ${bt.net_profit >= 0 ? "text-primary" : "text-danger"}`}>
                  {bt.net_profit >= 0 ? "+" : ""}${bt.net_profit.toFixed(2)}
                </td>
                <td className="px-4 py-3 text-right">{bt.sharpe_ratio?.toFixed(2) ?? "-"}</td>
                <td className="px-4 py-3 text-right text-danger">
                  {bt.max_drawdown_pct != null ? `${bt.max_drawdown_pct.toFixed(1)}%` : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
