"use client";

import { use, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useBacktest, useEquityCurve } from "@/lib/hooks/use-backtests";
import { EquityCurve } from "@/components/analytics/EquityCurve";
import { DrawdownChart } from "@/components/analytics/DrawdownChart";

export default function BacktestDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const numId = parseInt(id, 10);
  const { data: bt, isLoading } = useBacktest(isNaN(numId) ? null : numId);
  const { data: equityData } = useEquityCurve(isNaN(numId) ? null : numId);

  const [createBotLoading, setCreateBotLoading] = useState(false);
  const [createBotResult, setCreateBotResult] = useState<string | null>(null);
  const [createBotError, setCreateBotError] = useState<string | null>(null);

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
          value={`${bt.net_profit >= 0 ? "+" : ""}$${bt.net_profit.toFixed(2)}`}
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

      {/* Charts */}
      {equityCurve.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-background-card rounded-lg border border-gray-200 p-5">
            <EquityCurve data={equityCurve} />
          </div>
          <div className="bg-background-card rounded-lg border border-gray-200 p-5">
            <DrawdownChart data={equityCurve} />
          </div>
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
