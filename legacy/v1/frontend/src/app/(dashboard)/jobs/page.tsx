"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import {
  ListTodo,
  Loader2,
  RefreshCw,
  Trash2,
  X,
  ExternalLink,
  AlertTriangle,
  Info,
} from "lucide-react";

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

// P1-7: same shape as /backtests timeAgo so the dashboard reads consistently.
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

function fullTimestamp(dateStr: string | null): string {
  if (!dateStr) return "";
  return new Date(dateStr).toLocaleString();
}

/** Build a drill-through link for completed jobs. */
function resultLink(j: {
  job_id: string;
  job_type: string;
  result: Record<string, unknown> | null;
}): string | null {
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
  // P0-4: pause polling when nothing is active. We still fetch once on
  // mount and on demand via the Refresh button; in steady state with no
  // active jobs we stop hammering the API.
  const { data, isLoading, mutate } = useSWR(
    "/jobs",
    () => api.listJobs(),
    {
      refreshInterval: (latest) =>
        latest && latest.active_count > 0 ? 3000 : 0,
    },
  );

  const [expandedError, setExpandedError] = useState<string | null>(null);
  const [confirmClearFinished, setConfirmClearFinished] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyJobId, setBusyJobId] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);

  // P0-3: memoise via `data?.items` so the empty-array fallback doesn't
  // churn finishedJobs / finishedCount references on every render.
  const jobs = useMemo(() => data?.items ?? [], [data?.items]);
  const activeCount = data?.active_count ?? 0;
  const finishedJobs = useMemo(
    () =>
      jobs.filter(
        (j) =>
          j.state === "completed" ||
          j.state === "failed" ||
          j.state === "cancelled",
      ),
    [jobs],
  );
  const finishedCount = finishedJobs.length;

  async function handleCancel(jobId: string) {
    // P0-3: surface API errors instead of swallowing them. The most likely
    // failure here is the race between click and natural completion —
    // backend returns 400 "Cannot cancel job in state: completed" — and the
    // operator needs to see that, not a silent re-poll.
    setActionError(null);
    setBusyJobId(jobId);
    try {
      await api.cancelJob(jobId);
      mutate();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Failed to cancel job",
      );
    } finally {
      setBusyJobId(null);
    }
  }

  async function handleDelete(jobId: string) {
    setActionError(null);
    setBusyJobId(jobId);
    try {
      await api.deleteJob(jobId);
      setConfirmDelete(null);
      mutate();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Failed to remove job",
      );
    } finally {
      setBusyJobId(null);
    }
  }

  async function handleClearFinished() {
    setActionError(null);
    setClearing(true);
    try {
      await api.clearFinishedJobs();
      setConfirmClearFinished(false);
      mutate();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Failed to clear finished jobs",
      );
    } finally {
      setClearing(false);
    }
  }

  const subtitle =
    activeCount > 0
      ? `${activeCount} active · ${finishedCount} finished`
      : `Idle · ${finishedCount} finished`;

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Jobs"
        subtitle={`Background tasks — ${subtitle}`}
        actions={
          <div className="flex items-center gap-2">
            {finishedCount > 0 && (
              <button
                onClick={() => {
                  setActionError(null);
                  setConfirmClearFinished(true);
                }}
                className="btn btn-secondary text-danger"
                aria-label={`Clear ${finishedCount} finished jobs`}
              >
                <Trash2 size={14} />
                Clear finished ({finishedCount})
              </button>
            )}
            <button
              onClick={() => mutate()}
              className="btn btn-secondary"
              aria-label="Refresh jobs list"
            >
              <RefreshCw size={14} />
              Refresh
            </button>
          </div>
        }
      />

      {/* P0-6: in-memory-only banner. Jobs survive page reloads but NOT
          backend restarts — operators need to know completed-job results
          can disappear from this list at any time. */}
      <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
        <Info size={14} className="mt-0.5 shrink-0 text-amber-700" />
        <p>
          Jobs are tracked in-memory only — a backend restart clears this list.
          Backtest and research <strong>results</strong> are persisted in the
          database and remain available via{" "}
          <Link href="/backtests" className="underline hover:no-underline">
            Backtests
          </Link>{" "}
          and{" "}
          <Link href="/research" className="underline hover:no-underline">
            Research
          </Link>
          .
        </p>
      </div>

      {/* P0-3: surface API errors instead of swallowing them. */}
      {actionError && (
        <div
          role="alert"
          className="mb-4 flex items-start justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900"
        >
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-red-700" />
            <p>{actionError}</p>
          </div>
          <button
            type="button"
            onClick={() => setActionError(null)}
            className="text-red-700 hover:text-red-900"
            aria-label="Dismiss error"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* P0-1: confirm banner for the destructive bulk action. */}
      {confirmClearFinished && (
        <div
          role="alertdialog"
          aria-labelledby="confirm-clear-title"
          className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3"
        >
          <div className="flex items-start gap-2">
            <AlertTriangle
              size={16}
              className="mt-0.5 shrink-0 text-red-700"
            />
            <div className="flex-1">
              <p
                id="confirm-clear-title"
                className="text-sm font-medium text-red-900"
              >
                Clear {finishedCount} finished job
                {finishedCount === 1 ? "" : "s"}?
              </p>
              <p className="mt-0.5 text-xs text-red-800">
                Removes completed, failed, and cancelled rows from this list.
                The underlying backtest and research results remain in the
                database — only the job records are dropped.
              </p>
            </div>
          </div>
          <div className="mt-3 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setConfirmClearFinished(false)}
              disabled={clearing}
              className="btn btn-secondary text-sm disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleClearFinished}
              disabled={clearing}
              className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
            >
              {clearing ? "Clearing..." : `Clear ${finishedCount}`}
            </button>
          </div>
        </div>
      )}

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
                    description="Backtests, research runs, and scenario sandbox runs will appear here as background jobs."
                  />
                </td>
              </tr>
            )}
            {jobs.map((j) => {
              const link = j.state === "completed" ? resultLink(j) : null;
              const isBusy = busyJobId === j.job_id;
              const isPending = j.state === "pending";
              const isRunning = j.state === "running";
              return (
                <tr key={j.job_id}>
                  <td>
                    <span className="text-xs font-mono bg-background-muted px-1.5 py-0.5 rounded">
                      {j.job_type}
                    </span>
                  </td>
                  <td className="max-w-[280px]">
                    {link ? (
                      <Link
                        href={link}
                        className="font-medium text-primary hover:underline inline-flex items-center gap-1 align-middle max-w-full"
                        title={j.label}
                      >
                        <span className="truncate">{j.label}</span>
                        <ExternalLink size={11} className="shrink-0" />
                      </Link>
                    ) : (
                      <span
                        className="font-medium block truncate"
                        title={j.label}
                      >
                        {j.label}
                      </span>
                    )}
                    {j.state === "completed" &&
                      j.result &&
                      j.job_type === "research" && (
                        <span className="text-xs text-foreground-muted block mt-0.5">
                          {(j.result as Record<string, unknown>)
                            .completed as number}
                          /
                          {(j.result as Record<string, unknown>)
                            .total_combinations as number}{" "}
                          completed,{" "}
                          {(j.result as Record<string, unknown>)
                            .qualified as number}{" "}
                          qualified
                        </span>
                      )}
                    {j.state === "completed" &&
                      j.result &&
                      j.job_type === "backtest" && (
                        <span className="text-xs text-foreground-muted block mt-0.5">
                          {(j.result as Record<string, unknown>)
                            .total_trades as number}{" "}
                          trades, net{" "}
                          {(
                            (j.result as Record<string, unknown>)
                              .net_profit as number
                          )?.toFixed?.(2) ?? "—"}
                        </span>
                      )}
                    {j.state === "completed" &&
                      j.result &&
                      j.job_type === "scenario" &&
                      (() => {
                        const r = j.result as Record<string, unknown>;
                        const bots =
                          (r.per_bot as
                            | Array<Record<string, unknown>>
                            | undefined)?.length ?? 0;
                        const pnl = r.aggregate_pnl as number | undefined;
                        return (
                          <span className="text-xs text-foreground-muted block mt-0.5">
                            {bots} bots, {r.total_trades as number} trades, PnL{" "}
                            {pnl != null
                              ? (pnl >= 0 ? "+" : "") + pnl.toFixed(2)
                              : "—"}
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
                    {/* P0-5: distinguish PENDING ("waiting for worker") from
                        RUNNING (real progress). Previously both rendered the
                        same 0% bar and operators couldn't tell whether a job
                        was queued or actively executing. */}
                    {isPending ? (
                      <span className="text-xs text-foreground-muted inline-flex items-center gap-1">
                        <Loader2 size={11} className="animate-spin" />
                        Waiting for worker
                      </span>
                    ) : isRunning ? (
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
                    <span
                      className="cursor-help"
                      title={fullTimestamp(j.created_at)}
                    >
                      {timeAgo(j.created_at)}
                    </span>
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      {(isPending || isRunning) && (
                        <button
                          onClick={() => handleCancel(j.job_id)}
                          disabled={isBusy}
                          className="text-foreground-muted hover:text-danger transition p-1 disabled:opacity-40"
                          title="Cancel job"
                          aria-label={`Cancel job ${j.label}`}
                        >
                          <X size={14} />
                        </button>
                      )}
                      {j.state === "failed" && j.error && (
                        <button
                          onClick={() =>
                            setExpandedError(
                              expandedError === j.job_id ? null : j.job_id,
                            )
                          }
                          className="text-xs text-danger hover:underline inline-flex items-center gap-1"
                          title="Show full error"
                          aria-expanded={expandedError === j.job_id}
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
                      {(j.state === "completed" ||
                        j.state === "failed" ||
                        j.state === "cancelled") && (
                        <button
                          onClick={() => {
                            setActionError(null);
                            setConfirmDelete(j.job_id);
                          }}
                          disabled={isBusy}
                          className="text-foreground-muted hover:text-danger transition p-1 disabled:opacity-40"
                          title="Remove job"
                          aria-label={`Remove job ${j.label}`}
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
            {jobs
              .filter(
                (j) =>
                  j.state === "failed" &&
                  expandedError === j.job_id &&
                  j.error,
              )
              .map((j) => (
                <tr key={`${j.job_id}-error`} className="bg-red-50">
                  <td colSpan={7} className="px-4 py-2">
                    <p className="text-xs text-red-800 font-mono whitespace-pre-wrap break-all">
                      {j.error}
                    </p>
                  </td>
                </tr>
              ))}
            {/* P0-2: per-row delete confirmation as inline banner row,
                mirroring the /backtests pattern so the workstation feels
                consistent across destructive actions. */}
            {confirmDelete &&
              jobs
                .filter((j) => j.job_id === confirmDelete)
                .map((j) => (
                  <tr
                    key={`${j.job_id}-confirm-delete`}
                    className="bg-red-50"
                    role="alertdialog"
                    aria-labelledby={`confirm-delete-${j.job_id}-title`}
                  >
                    <td colSpan={7} className="px-4 py-3">
                      <div className="flex items-start gap-2">
                        <AlertTriangle
                          size={14}
                          className="mt-0.5 shrink-0 text-red-700"
                        />
                        <div className="flex-1 text-sm text-red-900">
                          <p
                            id={`confirm-delete-${j.job_id}-title`}
                            className="font-medium"
                          >
                            Remove job &ldquo;{j.label}&rdquo;?
                          </p>
                          <p className="mt-0.5 text-xs text-red-800">
                            Drops the job record from this list. The underlying
                            backtest / research / scenario result remains in
                            the database.
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setConfirmDelete(null)}
                            disabled={busyJobId === j.job_id}
                            className="btn btn-secondary text-sm disabled:opacity-50"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDelete(j.job_id)}
                            disabled={busyJobId === j.job_id}
                            className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
                          >
                            {busyJobId === j.job_id ? "Removing..." : "Remove"}
                          </button>
                        </div>
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
