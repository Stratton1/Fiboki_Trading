"use client";

import { useApi } from "@/lib/query";
import { GridPage } from "@/components/GridPage";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import type { GridColumn } from "@/components/grid";
import { formatTimestamp } from "@/lib/format";
import type { AuditEntryRow, AuditIntegrityView, Envelope } from "@/lib/types";

function IntegrityBanner() {
  const state = useApi<Envelope<AuditIntegrityView>>(
    "/api/intelligence/audit/integrity",
  );
  return (
    <AsyncBoundary state={state} label="audit integrity" onRetry={state.reload}>
      {(envelope) => (
        <div
          className={`state state--${envelope.data.intact ? "empty" : "error"} mb-3.5`}
          data-testid="audit-integrity"
        >
          <div className="state__title">
            <span>Audit chain {envelope.data.intact ? "intact" : "BROKEN"}</span>
            <span className={`badge badge--${envelope.data.intact ? "ok" : "down"}`}>
              {envelope.data.intact ? "VERIFIED" : "TAMPERED"}
            </span>
          </div>
          <div className="state__body">{envelope.data.detail}</div>
        </div>
      )}
    </AsyncBoundary>
  );
}

const OUTCOME_TONE: Record<string, "ok" | "degraded" | "down"> = {
  allowed: "ok",
  refused: "degraded",
};

/**
 * SYSTEM · Logs: the operator audit trail.
 *
 * Every mutating call — including the refusals — lands here, hash-chained. The
 * correlation id column joins a row to the server log line that produced it.
 * Wave 3: a DataGrid; `?row=<sequence>` selects an entry.
 */
export default function LogsPage() {
  const columns: GridColumn<AuditEntryRow>[] = [
    { id: "seq", header: "#", kind: "number", value: (row) => row.sequence, width: 72, pin: true },
    {
      id: "at",
      header: "When (UTC)",
      kind: "time",
      value: (row) => row.at,
      // "19/09/2026, 10:00 UTC" was cut at 150 px (inventory F-9): the column
      // is sized for the whole stamp, and the stamp is in the title too.
      cell: (row) => <span title={formatTimestamp(row.at)}>{formatTimestamp(row.at)}</span>,
      width: 176,
    },
    {
      id: "action",
      header: "Action",
      value: (row) => row.action,
      cell: (row) => (
        <span className="mono" title={row.action}>
          {row.action}
        </span>
      ),
      width: 200,
    },
    {
      id: "actor",
      header: "Actor",
      value: (row) => `${row.actor} (${row.actor_role})`,
      cell: (row) => <span title={`${row.actor} (${row.actor_role})`}>{`${row.actor} (${row.actor_role})`}</span>,
      width: 140,
    },
    {
      id: "outcome",
      header: "Outcome",
      value: (row) => row.outcome,
      cell: (row) => (
        <span className={`badge badge--${OUTCOME_TONE[row.outcome] ?? "down"}`} title={row.outcome.toUpperCase()}>
          {row.outcome.toUpperCase()}
        </span>
      ),
      // ALLOWED / REFUSED plus the badge's padding: 104 px overflowed.
      width: 124,
    },
    { id: "mode", header: "Mode", value: (row) => row.execution_mode || null, width: 88 },
    {
      id: "target",
      header: "Target",
      value: (row) => row.target || null,
      cell: (row) => (row.target ? <span title={row.target}>{row.target}</span> : "—"),
      width: 160,
    },
    { id: "reason", header: "Reason", value: (row) => row.reason, wrap: true },
    {
      id: "cid",
      header: "Correlation",
      value: (row) => row.correlation_id || null,
      cell: (row) => (
        <span className="mono" title={row.correlation_id || undefined}>
          {row.correlation_id || "—"}
        </span>
      ),
      width: 160,
    },
    {
      id: "hash",
      header: "Entry hash",
      value: (row) => row.entry_hash,
      cell: (row) => <span className="mono">{row.entry_hash}</span>,
      hidden: true,
      width: 200,
    },
  ];
  return (
    <>
      <IntegrityBanner />
      <GridPage<AuditEntryRow>
        title="Logs"
        intro="The append-only operator audit trail. Refusals are recorded as loudly as successes; an attempted disarm by the wrong role is exactly the row an incident review needs."
        path="/api/intelligence/audit?limit=200"
        label="audit entries"
        gridId="audit"
        columns={columns}
        rowKey={(row) => `${row.sequence}`}
        emptyTitle="No operator actions yet"
        emptyBody="Nobody has performed a mutating action on this deployment since the trail was created."
      />
    </>
  );
}
