"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { RECON_TONE, sessionReconciliationPath, type ReconciliationView } from "@/lib/ops";
import { invalidatePath, useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { SourceBadge } from "../primitives";
import { useExecutionMode, useOperator } from "../shell/platform";
import { Stat } from "../Stat";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";

/**
 * Backtest-versus-paper reconciliation for one bot (Wave 3O).
 *
 * The API re-runs the same bound document through the backtest engine over the
 * same closed bars (in a separate job process, never the API) and compares the
 * paper ledger trade for trade. This renders its verdict: the status, the
 * counts, every divergence with its field, both values and its REASON, and
 * which reasons are attributed. A request is recorded with a reason and runs
 * when `fiboki ops jobs` does; the panel says so rather than spinning.
 */
export function Reconciliation({ sessionId }: { sessionId: string }) {
  const path = sessionReconciliationPath(sessionId);
  const state = useApi<Envelope<ReconciliationView>>(path, { refreshMs: 20_000 });
  return (
    <AsyncBoundary state={state} label="the reconciliation" onRetry={state.reload}>
      {(env) => {
        const { job, report } = env.data;
        return (
          <div data-testid="reconciliation" data-status={report?.status ?? "none"}>
            <SourceBadge source={env.source} />
            <div className="ops-row">
              {report ? (
                <span className={`badge badge--${RECON_TONE[report.status] ?? "unknown"}`} data-testid="recon-status">
                  {report.status.replace("_", " ").toUpperCase()}
                </span>
              ) : (
                <span className="badge badge--unknown" data-testid="recon-status">
                  NO REPORT
                </span>
              )}
              <span className="ops-meaning">{report ? report.status_reason : "No reconciliation has been run for this bot."}</span>
              <RequestReconciliation sessionId={sessionId} path={path} />
            </div>
            {job ? (
              <p className="ops-hint" data-testid="recon-job" data-status={job.status}>
                Job {job.job_id}: <strong>{job.status}</strong> · requested by {job.requested_by} at{" "}
                {formatTimestamp(job.requested_at)} ({job.reason}){job.error ? ` · error: ${job.error}` : ""}.{" "}
                {job.status === "queued" ? job.runner_note : null}
              </p>
            ) : null}
            {report ? (
              <>
                <div className="tiles">
                  <Stat label="Paper trades" figure={report.paper_trades} />
                  <Stat label="Backtest trades" figure={report.backtest_trades} />
                  <Stat label="Matched" figure={report.matched} />
                  <Stat label="Identical" figure={report.matched_identical} />
                  <Stat label="Attributed" figure={report.explained} />
                  <Stat label="Unexplained" figure={report.unexplained} />
                </div>
                <p className="ops-hint">
                  Window {report.window_start ? formatTimestamp(report.window_start) : "—"} to{" "}
                  {report.window_end ? formatTimestamp(report.window_end) : "—"}
                  {report.first_divergence_at ? ` · first divergence ${formatTimestamp(report.first_divergence_at)}` : ""}
                  {" · digest "}
                  <span className="ops-chain">{report.digest.slice(0, 12)}</span>
                  {report.generated_at ? ` · run ${formatTimestamp(report.generated_at)}` : ""}
                </p>
                {report.divergences.length > 0 ? (
                  <div className="table-wrap">
                    <table className="dense" data-testid="recon-divergences">
                      <thead>
                        <tr>
                          <th scope="col">Entry (UTC)</th>
                          <th scope="col">Instrument</th>
                          <th scope="col">Kind</th>
                          <th scope="col">Field</th>
                          <th scope="col" className="num">
                            Paper
                          </th>
                          <th scope="col" className="num">
                            Backtest
                          </th>
                          <th scope="col">Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.divergences.map((d, i) => (
                          <tr key={`${d.entry_time}-${d.field}-${i}`} data-testid="recon-divergence" data-reason={d.reason} data-explained={d.explained}>
                            <td className="num">{formatTimestamp(d.entry_time)}</td>
                            <td className="mono">
                              {d.instrument} {d.direction}
                            </td>
                            <td>{d.kind.replace("_", " ")}</td>
                            <td className="mono">{d.field}</td>
                            <td className="num">
                              {d.paper ? <FigureValue figure={d.paper} showChip={false} /> : (d.paper_text ?? "—")}
                            </td>
                            <td className="num">
                              {d.backtest ? <FigureValue figure={d.backtest} showChip={false} /> : (d.backtest_text ?? "—")}
                            </td>
                            <td className="wrap" title={d.detail}>
                              <span className={`badge badge--${d.explained ? "neutral" : "degraded"}`}>{d.reason}</span>{" "}
                              <span className="ops-hint">{d.detail}</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                {report.approximations.length > 0 ? (
                  <details className="ops-hint">
                    <summary>Known approximations ({report.approximations.length})</summary>
                    <ul>
                      {report.approximations.map((a) => (
                        <li key={a}>{a}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </>
            ) : null}
          </div>
        );
      }}
    </AsyncBoundary>
  );
}

function RequestReconciliation({ sessionId, path }: { sessionId: string; path: string }) {
  const client = useQueryClient();
  const { mode, mutationsAllowed } = useExecutionMode();
  const operator = useOperator();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blockedId = useId();
  // Mirrors require_operator on POST .../reconciliation; the server decides.
  const blocked = !mutationsAllowed
    ? "Blocked: execution mode unknown or disconnected."
    : operator !== null && operator.role === "viewer"
      ? "Blocked: operator role required."
      : null;
  async function queueReconciliation(_choice: string, reason: string) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(path, { method: "POST", body: JSON.stringify({ reason }) });
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (${err.code})` : "The request failed.");
      setBusy(false);
      return;
    }
    await invalidatePath(client, path);
    setBusy(false);
    setOpen(false);
  }
  return (
    <span className="ops-control">
      <Button size="sm" data-testid="recon-request" disabled={blocked !== null} aria-describedby={blocked ? blockedId : undefined} onClick={() => setOpen(true)}>
        Request reconciliation
      </Button>
      {blocked ? (
        <span id={blockedId} className="ack__blocked">
          {blocked}
        </span>
      ) : null}
      <ConfirmDialog
        open={open}
        title={`Reconcile ${sessionId} against the backtest engine`}
        executionMode={mode}
        choices={[
          {
            id: "reconcile",
            title: "QUEUE",
            body: "Re-run the same binding through the backtest engine over the same closed bars and compare trade for trade.",
            consequences: [
              "Recorded in the audit trail with your reason.",
              "Runs in the ops job process (fiboki ops jobs), not in the API; it changes nothing on the bot.",
            ],
            phrase: null,
          },
        ]}
        requireReason
        reasonMinLength={8}
        reasonMaxLength={500}
        confirmLabel="Queue"
        busy={busy}
        errorMessage={error}
        onCancel={() => setOpen(false)}
        onConfirm={queueReconciliation}
      />
    </span>
  );
}
