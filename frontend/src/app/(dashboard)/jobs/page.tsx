"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { ListTodo, Loader2, RefreshCw, Trash2, X, ExternalLink, AlertTriangle } from "lucide-react";

function stateVariant(state: string): "ok" | "warn" | "error" | "info" {
  switch (state) {
    case "completed":
      return "ok";
    case "running":
    case "pending":
      return "warn";
    case "failed":
      return "error";
    case "cancelled":
      return "info";
    default:
      return "info";
  }
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const diff = Math.max(0, e - s);
  if (diff < 1000) return "<1s";
  if (diff < 60000) return `${Math.round(diff / 1000)}s`;
  return `${Math.round(diff / 60000)}m`;
}

/** Build a drill-through link for completed jobs */
function resultLink(j: { job_id: string; job_type: string; result: Record<string, unknown> | null }): string | null {
  if (!j.result) return null;
  if (j.job_type === "research") {
    const runId = j.result.run_id as string | undefined;
    return runId ? `/research` : null;
  }
  if (j.job_type === "backtest") {
    const btId = j.result.backtest_run_id as number | undefined;
    return btId ? `/backtests/${btId}` : null;
  }
  if (j.job_type === "scenario") return `/scenarios?job=${j.job_id}`;
  return null;
}

export default function JobsPage() {
  const { data, isLoading, mutate } = useSWR("/jobs", () => api.listJobs(), {
    refreshInterval: 3000,
  });
  const [expandedError, setExpandedError] = useState<string | null>(null);

  const jobs = data?.items ?? [];
  const activeCount = data?.active_count ?? 0;
  const finishedCount = jobs.filter(
    (j) => j.state === "completed" || j.state === "failed" || j.state === "cancelled"
  ).length;

  async function handleCancel(jobId: string) {
    await api.cancelJob(jobId);
    mutate();
  }

  async function handleDelete(jobId: string) {
    await api.deleteJob(jobId);
    mutate();
  }

  async function handleClearFinished() {
    await api.clearFinishedJobs();
    mutate();
  }

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Jobs"
        subtitle={`Background tasks — ${activeCount} active`}
        actions={
          <div className="flex items-center gap-2">
            {finishedCount > 0 && (
              <button
                onClick={handleClearFinished}
                className="btn btn-secondary text-danger"
              >
                <Trash2 size={14} />
                Clear finished ({finishedCount})
              </button>
            )}
            <button onClick={() => mutate()} className="btn btn-secondary">
              <RefreshCw size={14} />
              Refresh
            </button>
          </div>
        }
      />

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th className="text-left">Type</th>
              <th className="text-left">Label</th>
              <th className="text-left">State</th>
              <th className="text-left">Progress</th>
              <th className="text-left">Duration</th>
              <th className="text-left">Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={7}>
                  <div className="flex items-center justify-center gap-2 py-8 text-foreground-muted">
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-sm">Loading jobs...</span>
                  </div>
                </td>
              </tr>
            )}
            {!isLoading && jobs.length === 0 && (
              <tr>
                <td colSpan={7}>
                  <EmptyState
                    icon={<ListTodo size={36} strokeWidth={1.5} />}
                    title="No jobs yet"
                    description="Backtests and research runs will appear here as background jobs."
                  />
                </td>
              </tr>
            )}
            {jobs.map((j) => {
              const link = j.state === "completed" ? resultLink(j) : null;
              return (
                <tr key={j.job_id}>
                  <td>
                    <span className="text-xs font-mono bg-background-muted px-1.5 py-0.5 rounded">
                      {j.job_type}
                    </span>
                  </td>
                  <td>
                    {link ? (
                      <Link href={link} className="font-medium text-primary hover:underline inline-flex items-center gap-1">
                        {j.label}
                        <ExternalLink size={11} />
                      </Link>
                    ) : (
                      <span className="font-medium">{j.label}</span>
                    )}
                    {j.state === "completed" && j.result && j.job_type === "research" && (
                      <span className="text-xs text-foreground-muted ml-2">
                        {(j.result as Record<string, unknown>).completed as number}/{(j.result as Record<string, unknown>).total_combinations as number} completed,{" "}
                        {(j.result as Record<string, unknown>).qualified as number} qualified
                      </span>
                    )}
                    {j.state === "completed" && j.result && j.job_type === "backtest" && (
                      <span className="text-xs text-foreground-muted ml-2">
                        {(j.result as Record<string, unknown>).total_trades as number} trades,{" "}
                        net {((j.result as Record<string, unknown>).net_profit as number)?.toFixed?.(2) ?? "—"}
                      </span>
                    )}
                    {j.state === "completed" && j.result && j.job_type === "scenario" && (() => {
                      const r = j.result as Record<string, unknown>;
                      const bots = (r.per_bot as Array<Record<string, unknown>> | undefined)?.length ?? 0;
                      const pnl = r.aggregate_pnl as number | undefined;
                      return (
                        <span className="text-xs text-foreground-muted ml-2">
                          {bots} bots, {r.total_trades as number} trades,{" "}
                          PnL {pnl != null ? (pnl >= 0 ? "+" : "") + pnl.toFixed(2) : "—"}
                        </span>
                      );
                    })()}
                  </td>
                  <td>
                    <StatusBadge variant={stateVariant(j.state)}>
                      {j.state}
                    </StatusBadge>
                  </td>
                  <td>
                    {(j.state === "running" || j.state === "pending") ? (
                      <div className="flex items-center gap-2">
                        <div className="w-20 h-1.5 bg-background-muted rounded-full overflow-hidden">
                          <div
                            className="h-full bg-primary rounded-full transition-all duration-300"
                            style={{ width: `${j.progress}%` }}
                          />
                        </div>
                        <span className="text-xs tabular-nums text-foreground-muted">
                          {j.progress}%
                        </span>
                      </div>
                    ) : j.state === "completed" ? (
                      <span className="text-xs text-foreground-muted">100%</span>
                    ) : (
                      <span className="text-xs text-foreground-muted">—</span>
                    )}
                  </td>
                  <td className="text-sm tabular-nums text-foreground-muted">
                    {formatDuration(j.started_at, j.completed_at)}
                  </td>
                  <td className="text-sm text-foreground-muted">
                    {new Date(j.created_at).toLocaleTimeString()}
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      {(j.state === "running" || j.state === "pending") && (
                        <button
                          onClick={() => handleCancel(j.job_id)}
                          className="text-foreground-muted hover:text-danger transition p-1"
                          title="Cancel job"
                        >
                          <X size={14} />
                        </button>
                      )}
                      {j.state === "failed" && j.error && (
                        <button
                          onClick={() => setExpandedError(expandedError === j.job_id ? null : j.job_id)}
                          className="text-xs text-danger hover:underline inline-flex items-center gap-1"
                          title="Show full error"
                        >
                          <AlertTriangle size={12} />
                          {expandedError === j.job_id ? "Hide" : "Error details"}
                        </button>
                      )}
                      {link && j.state === "completed" && (
                        <Link
                          href={link}
                          className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                        >
                          View result
                          <ExternalLink size={11} />
                        </Link>
                      )}
                      {(j.state === "completed" || j.state === "failed" || j.state === "cancelled") && (
                        <button
                          onClick={() => handleDelete(j.job_id)}
                          className="text-foreground-muted hover:text-danger transition p-1"
                          title="Remove job"
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {/* Expanded error rows */}
            {jobs.filter((j) => j.state === "failed" && expandedError === j.job_id && j.error).map((j) => (
              <tr key={`${j.job_id}-error`} className="bg-red-50">
                <td colSpan={7} className="px-4 py-2">
                  <p className="text-xs text-red-800 font-mono whitespace-pre-wrap break-all">{j.error}</p>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
