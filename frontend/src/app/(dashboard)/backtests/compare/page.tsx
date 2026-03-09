"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useBacktests } from "@/lib/hooks/use-backtests";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { ArrowLeft, BarChart3, Check, Loader2, X } from "lucide-react";

interface BacktestSummary {
  id: number;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  total_trades: number;
  net_profit: number;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
}

export default function CompareBacktestsPage() {
  const { data: backtests, isLoading } = useBacktests();
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  function toggleSelection(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  const selected = (backtests ?? []).filter((bt: BacktestSummary) =>
    selectedIds.has(bt.id)
  ) as BacktestSummary[];

  const metrics = [
    { key: "total_trades", label: "Total Trades", fmt: (v: number) => v.toString() },
    {
      key: "net_profit",
      label: "Net Profit",
      fmt: (v: number) => `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`,
      color: (v: number) => (v >= 0 ? "text-primary" : "text-danger"),
    },
    {
      key: "sharpe_ratio",
      label: "Sharpe Ratio",
      fmt: (v: number | null) => (v != null ? v.toFixed(2) : "—"),
    },
    {
      key: "max_drawdown_pct",
      label: "Max Drawdown",
      fmt: (v: number | null) => (v != null ? `${v.toFixed(1)}%` : "—"),
      color: () => "text-danger",
    },
  ];

  // Find best value for each metric for highlighting
  function isBest(metric: string, value: number | null, allValues: (number | null)[]) {
    if (value == null) return false;
    const valid = allValues.filter((v) => v != null) as number[];
    if (valid.length < 2) return false;
    if (metric === "max_drawdown_pct") return value === Math.min(...valid);
    return value === Math.max(...valid);
  }

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Compare Backtests"
        subtitle={`${selected.length} selected for comparison`}
        actions={
          <Link href="/backtests" className="btn btn-secondary text-sm">
            <ArrowLeft size={14} />
            Back to Backtests
          </Link>
        }
      />

      {/* Selection panel */}
      <div className="card mb-6">
        <p className="section-label">Select Backtests to Compare</p>
        {isLoading ? (
          <div className="flex items-center gap-2 py-4 text-foreground-muted">
            <Loader2 size={14} className="animate-spin" />
            <span className="text-sm">Loading...</span>
          </div>
        ) : !backtests || backtests.length === 0 ? (
          <EmptyState
            icon={<BarChart3 size={28} strokeWidth={1.5} />}
            title="No backtests available"
            description="Run some backtests first to compare them."
          />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 max-h-64 overflow-y-auto">
            {(backtests as BacktestSummary[]).map((bt) => {
              const isSelected = selectedIds.has(bt.id);
              return (
                <button
                  key={bt.id}
                  onClick={() => toggleSelection(bt.id)}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border text-left text-sm transition-all ${
                    isSelected
                      ? "border-primary bg-primary/5 text-foreground"
                      : "border-border-muted hover:border-border hover:bg-background-muted text-foreground-muted"
                  }`}
                >
                  <div
                    className={`w-5 h-5 rounded flex items-center justify-center shrink-0 ${
                      isSelected
                        ? "bg-primary text-white"
                        : "border border-border-muted"
                    }`}
                  >
                    {isSelected && <Check size={12} />}
                  </div>
                  <div className="min-w-0">
                    <p className="font-medium truncate">{bt.strategy_id}</p>
                    <p className="text-xs text-foreground-muted">
                      {bt.instrument} / {bt.timeframe} — {bt.total_trades} trades
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Comparison table */}
      {selected.length >= 2 && (
        <div className="card">
          <p className="section-label">Side-by-Side Comparison</p>
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th className="text-left">Metric</th>
                  {selected.map((bt) => (
                    <th key={bt.id} className="text-right min-w-[140px]">
                      <div className="flex items-center justify-end gap-1">
                        <span>{bt.strategy_id}</span>
                        <button
                          onClick={() => toggleSelection(bt.id)}
                          className="text-foreground-muted hover:text-danger p-0.5"
                        >
                          <X size={12} />
                        </button>
                      </div>
                      <div className="text-xs text-foreground-muted font-normal">
                        {bt.instrument} / {bt.timeframe}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {metrics.map((m) => {
                  const allValues = selected.map(
                    (bt) => (bt as any)[m.key] as number | null
                  );
                  return (
                    <tr key={m.key}>
                      <td className="font-medium">{m.label}</td>
                      {selected.map((bt, i) => {
                        const val = (bt as any)[m.key];
                        const best = isBest(m.key, val, allValues);
                        const colorClass =
                          m.color
                            ? typeof m.color === "function"
                              ? m.color(val)
                              : ""
                            : "";
                        return (
                          <td
                            key={bt.id}
                            className={`text-right tabular-nums ${colorClass} ${
                              best ? "font-bold" : ""
                            }`}
                          >
                            {m.fmt(val)}
                            {best && (
                              <span className="ml-1 text-xs text-primary">★</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selected.length === 1 && (
        <div className="card">
          <EmptyState
            icon={<BarChart3 size={28} strokeWidth={1.5} />}
            title="Select at least 2 backtests"
            description="Choose one more backtest above to see a side-by-side comparison."
          />
        </div>
      )}
    </div>
  );
}
