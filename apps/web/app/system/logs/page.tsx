"use client";

import { useApi } from "@/lib/api";
import { ListPage, type Column } from "@/components/ListPage";
import { AsyncBoundary } from "@/components/AsyncBoundary";
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

/**
 * SYSTEM · Logs: the operator audit trail.
 *
 * Every mutating call — including the refusals — lands here, hash-chained. The
 * correlation id column joins a row to the server log line that produced it.
 */
export default function LogsPage() {
  const columns: Column<AuditEntryRow>[] = [
    { key: "seq", header: "#", cell: (row) => row.sequence },
    { key: "at", header: "When", cell: (row) => formatTimestamp(row.at) },
    { key: "action", header: "Action", cell: (row) => <span className="mono">{row.action}</span> },
    { key: "actor", header: "Actor", cell: (row) => `${row.actor} (${row.actor_role})` },
    {
      key: "outcome",
      header: "Outcome",
      cell: (row) => (
        <span
          className={`badge badge--${row.outcome === "allowed" ? "ok" : row.outcome === "refused" ? "degraded" : "down"}`}
        >
          {row.outcome.toUpperCase()}
        </span>
      ),
    },
    { key: "mode", header: "Mode", cell: (row) => row.execution_mode || "—" },
    { key: "target", header: "Target", cell: (row) => row.target || "—" },
    { key: "reason", header: "Reason", cell: (row) => row.reason, wrap: true },
    { key: "cid", header: "Correlation", cell: (row) => <span className="mono">{row.correlation_id || "—"}</span> },
  ];
  return (
    <>
      <IntegrityBanner />
      <ListPage<AuditEntryRow>
        title="Logs"
        intro="The append-only operator audit trail. Refusals are recorded as loudly as successes; an attempted disarm by the wrong role is exactly the row an incident review needs."
        path="/api/intelligence/audit?limit=200"
        label="audit entries"
        columns={columns}
        rowKey={(row) => `${row.sequence}`}
        emptyTitle="No operator actions yet"
        emptyBody="Nobody has performed a mutating action on this deployment since the trail was created."
      />
    </>
  );
}
