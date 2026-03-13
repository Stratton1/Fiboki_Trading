"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { ListTodo, Loader2, RefreshCw, Trash2, X } from "lucide-react";

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

export default function JobsPage() {
  const { data, isLoading, mutate } = useSWR("/jobs", () => api.listJobs(), {
    refreshInterval: 3000,
  });

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
            {jobs.map((j) => (
              <tr key={j.job_id}>
                <td>
                  <span className="text-xs font-mono bg-background-muted px-1.5 py-0.5 rounded">
                    {j.job_type}
                  </span>
                </td>
                <td>
                  <span className="font-medium">{j.label}</span>
                  {j.state === "completed" && j.result && j.job_type === "research" && (
                    <span className="text-xs text-foreground-muted ml-2">
                      {(j.result as Record<string, unknown>).completed as number}/{(j.result as Record<string, unknown>).total_combinations as number} completed,{" "}
                      {(j.result as Record<string, unknown>).qualified as number} qualified
                    </span>
                  )}
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
                      <span className="text-xs text-danger max-w-xs truncate" title={j.error}>
                        {j.error}
                      </span>
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
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
