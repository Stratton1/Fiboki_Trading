"use client";

import { use, useState, useMemo } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { api } from "@/lib/api";
import { useBacktest, useEquityCurve, useBacktestTrades } from "@/lib/hooks/use-backtests";
import { formatPnl } from "@/lib/format-currency";
import { useMarketData } from "@/lib/hooks/use-market-data";
import { EquityCurve } from "@/components/analytics/EquityCurve";
import { DrawdownChart } from "@/components/analytics/DrawdownChart";
import type { Trade } from "@/types/contracts/trades";

const TradeMarkerChart = dynamic(
  () => import("@/components/charts/core/TradeMarkerChart"),
  { ssr: false }
);

type SortField = "entry_time" | "pnl" | "direction" | "exit_reason";
type SortDir = "asc" | "desc";

export default function BacktestDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const numId = parseInt(id, 10);
  const { data: bt, isLoading } = useBacktest(isNaN(numId) ? null : numId);
  const { data: equityData } = useEquityCurve(isNaN(numId) ? null : numId);

  // Trade list state
  const [tradePage, setTradePage] = useState(1);
  const [sortField, setSortField] = useState<SortField>("entry_time");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [focusTradeId, setFocusTradeId] = useState<number | null>(null);
  const tradeSize = 50;

  const { data: tradeData } = useBacktestTrades(
    isNaN(numId) ? null : numId,
    tradePage,
    tradeSize
  );

  // Fetch all trades for chart markers (up to 500)
  const { data: allTradeData } = useBacktestTrades(
    isNaN(numId) ? null : numId,
    1,
    500
  );

  // Market data for chart
  const { data: marketData } = useMarketData(
    bt?.instrument ?? null,
    bt?.timeframe ?? null
  );

  const [createBotLoading, setCreateBotLoading] = useState(false);
  const [createBotResult, setCreateBotResult] = useState<string | null>(null);
  const [createBotError, setCreateBotError] = useState<string | null>(null);

  // Sort trades client-side
  const sortedTrades = useMemo(() => {
    const items = tradeData?.items ?? [];
    return [...items].sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "pnl":
          cmp = a.pnl - b.pnl;
          break;
        case "direction":
          cmp = a.direction.localeCompare(b.direction);
          break;
        case "exit_reason":
          cmp = a.exit_reason.localeCompare(b.exit_reason);
          break;
        default:
          cmp = (a.entry_time ?? "").localeCompare(b.entry_time ?? "");
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [tradeData, sortField, sortDir]);

  function handleSort(field: SortField) {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  }

  function handleJumpToTrade(tradeId: number) {
    setFocusTradeId(tradeId);
  }

  async function handleCreateBot() {
    if (!bt) return;
    setCreateBotLoading(true);
    setCreateBotError(null);
    setCreateBotResult(null);
    try {
      const res = await api.createBot({
        strategy_id: bt.strategy_id,
        instrument: bt.instrument,
        timeframe: bt.timeframe,
        source_type: "backtest",
        source_id: String(bt.id),
      });
      const botId = (res as Record<string, unknown>).bot_id as string;
      setCreateBotResult(`Paper bot ${botId} created`);
    } catch (err) {
      setCreateBotError(err instanceof Error ? err.message : "Failed to create bot");
    } finally {
      setCreateBotLoading(false);
    }
  }

  if (isLoading) {
    return <p className="text-foreground-muted">Loading backtest...</p>;
  }

  if (!bt) {
    return <p className="text-foreground-muted">Backtest not found.</p>;
  }

  const metrics = bt.metrics_json ?? {};
  const equityCurve = equityData?.equity_curve ?? [];
  const allTrades: Trade[] = allTradeData?.items ?? [];
  const totalTrades = tradeData?.total ?? 0;
  const totalPages = Math.ceil(totalTrades / tradeSize);
  const sortArrow = (field: SortField) =>
    sortField === field ? (sortDir === "asc" ? " \u25B2" : " \u25BC") : "";

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <Link href="/backtests" className="text-foreground-muted hover:text-foreground text-sm">
            Backtests
          </Link>
          <span className="text-foreground-muted text-sm">/</span>
          <h2 className="text-xl font-semibold">{bt.strategy_id}</h2>
        </div>
        <button
          onClick={handleCreateBot}
          disabled={createBotLoading || !!createBotResult}
          className="btn btn-primary text-sm disabled:opacity-50"
        >
          {createBotLoading ? "Creating..." : createBotResult ? createBotResult : "Create Paper Bot"}
        </button>
      </div>
      {createBotError && (
        <div className="mb-4 p-3 rounded bg-red-50 border border-red-200 text-red-800 text-sm flex items-center justify-between">
          <span>{createBotError}</span>
          <button onClick={() => setCreateBotError(null)} className="text-red-600 hover:underline text-xs">Dismiss</button>
        </div>
      )}

      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Instrument" value={bt.instrument} />
        <MetricCard label="Timeframe" value={bt.timeframe} />
        <MetricCard label="Total Trades" value={String(bt.total_trades)} />
        <MetricCard
          label="Net Profit"
          value={formatPnl(bt.net_profit)}
          color={bt.net_profit >= 0 ? "text-primary" : "text-danger"}
        />
        <MetricCard label="Sharpe Ratio" value={bt.sharpe_ratio?.toFixed(2) ?? "-"} />
        <MetricCard
          label="Max Drawdown"
          value={bt.max_drawdown_pct != null ? `${bt.max_drawdown_pct.toFixed(1)}%` : "-"}
          color="text-danger"
        />
        <MetricCard label="Start" value={bt.start_date ?? "-"} />
        <MetricCard label="End" value={bt.end_date ?? "-"} />
      </div>

      {/* Metrics JSON */}
      {Object.keys(metrics).length > 0 && (
        <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
          <h3 className="text-sm font-medium text-foreground-muted mb-3">Detailed Metrics</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {Object.entries(metrics).map(([key, value]) => (
              <div key={key}>
                <p className="text-xs text-foreground-muted">{key.replace(/_/g, " ")}</p>
                <p className="text-sm font-medium">
                  {typeof value === "number" ? value.toFixed(4) : String(value ?? "-")}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* KLineChart with trade markers */}
      {marketData && (
        <div className="mb-6">
          <h3 className="text-sm font-medium text-foreground-muted mb-2">Price Chart with Trade Markers</h3>
          <div className="h-[450px]">
            <TradeMarkerChart
              data={marketData}
              trades={allTrades}
              focusTradeId={focusTradeId}
              onTradeClick={handleJumpToTrade}
            />
          </div>
        </div>
      )}

      {/* Charts */}
      {equityCurve.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
          <div className="bg-background-card rounded-lg border border-gray-200 p-5">
            <EquityCurve data={equityCurve} />
          </div>
          <div className="bg-background-card rounded-lg border border-gray-200 p-5">
            <DrawdownChart data={equityCurve} />
          </div>
        </div>
      )}

      {/* Trade List Table */}
      {totalTrades > 0 && (
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-foreground-muted mb-3">
            Trade List ({totalTrades})
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left text-foreground-muted">
                  <th className="pb-2 pr-4">#</th>
                  <th className="pb-2 pr-4 cursor-pointer select-none" onClick={() => handleSort("entry_time")}>
                    Entry{sortArrow("entry_time")}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none" onClick={() => handleSort("direction")}>
                    Dir{sortArrow("direction")}
                  </th>
                  <th className="pb-2 pr-4">Entry Price</th>
                  <th className="pb-2 pr-4">Exit Price</th>
                  <th className="pb-2 pr-4 cursor-pointer select-none" onClick={() => handleSort("pnl")}>
                    PnL{sortArrow("pnl")}
                  </th>
                  <th className="pb-2 pr-4 cursor-pointer select-none" onClick={() => handleSort("exit_reason")}>
                    Exit Reason{sortArrow("exit_reason")}
                  </th>
                  <th className="pb-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedTrades.map((t) => (
                  <tr key={t.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 pr-4">
                      <Link href={`/trades/${t.id}`} className="text-blue-600 hover:underline">
                        {t.id}
                      </Link>
                    </td>
                    <td className="py-2 pr-4 text-xs">{t.entry_time ?? "-"}</td>
                    <td className={`py-2 pr-4 font-medium ${t.direction === "LONG" ? "text-primary" : "text-danger"}`}>
                      {t.direction}
                    </td>
                    <td className="py-2 pr-4">{t.entry_price.toFixed(5)}</td>
                    <td className="py-2 pr-4">{t.exit_price.toFixed(5)}</td>
                    <td className={`py-2 pr-4 font-medium ${t.pnl >= 0 ? "text-primary" : "text-danger"}`}>
                      {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}
                    </td>
                    <td className="py-2 pr-4 text-xs">{t.exit_reason}</td>
                    <td className="py-2">
                      <button
                        onClick={() => handleJumpToTrade(t.id)}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        Jump
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-4">
              <p className="text-xs text-foreground-muted">
                Page {tradePage} of {totalPages}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => setTradePage((p) => Math.max(1, p - 1))}
                  disabled={tradePage <= 1}
                  className="text-xs px-3 py-1 rounded border border-gray-200 disabled:opacity-50"
                >
                  Prev
                </button>
                <button
                  onClick={() => setTradePage((p) => Math.min(totalPages, p + 1))}
                  disabled={tradePage >= totalPages}
                  className="text-xs px-3 py-1 rounded border border-gray-200 disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-background-card rounded-lg border border-gray-200 p-4">
      <p className="text-xs text-foreground-muted mb-1">{label}</p>
      <p className={`text-lg font-semibold ${color ?? ""}`}>{value}</p>
    </div>
  );
}
