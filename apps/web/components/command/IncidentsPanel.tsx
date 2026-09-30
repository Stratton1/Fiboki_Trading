"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { capability } from "@/lib/auth";
import { awaitEcho } from "@/lib/echo";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { IncidentRow, Page } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { CaveatList, SourceBadge, TableWrap } from "../primitives";
import { AcknowledgeIncident, INCIDENTS_PATH } from "../incidents/AcknowledgeIncident";
import { useOperator } from "../shell/platform";
import { SeverityBadge } from "./SeverityBadge";

/**
 * Incidents (plan §4, "System & Incidents"; v1 on the Overview).
 *
 * Read from GET /api/system/incidents and kept current by the `incidents`
 * stream topic. Acknowledging goes through the shared AcknowledgeIncident
 * control (the same dialog and friction as the attention queue and the
 * incident page), and, like every mutation, shows no optimistic state: the row
 * reads "acknowledged" only once the platform says so. Acknowledging does not
 * resolve an incident. Each title opens the incident's own page.
 *
 * Below 640 px the table stacks one incident per block (`table--stack-sm`),
 * so Acknowledge is on screen rather than off to the right.
 */

export { INCIDENTS_PATH };
const INCIDENTS_REFRESH_MS = 30_000;

/** The reason recorded with the latest acknowledgement, from the incident's timeline. */
function latestAckReason(row: IncidentRow): string | null {
  const acks = row.timeline.filter((entry) => entry.kind === "ack");
  const last = acks[acks.length - 1];
  return last ? last.text : null;
}

export function IncidentsPanel() {
  const state = useApi<Page<IncidentRow>>(INCIDENTS_PATH, { refreshMs: INCIDENTS_REFRESH_MS });
  const client = useQueryClient();
  const allowed = capability(useOperator(), "can_acknowledge");
  const [unconfirmed, setUnconfirmed] = useState<ReadonlySet<string>>(new Set());

  // Adjusted during render: a row the platform now shows as acknowledged is
  // no longer "confirming…".
  const items = state.status === "success" ? state.data.items : null;
  if (items && unconfirmed.size > 0) {
    const still = new Set(
      [...unconfirmed].filter((id) => items.find((row) => row.id === id)?.status === "open"),
    );
    if (still.size !== unconfirmed.size) setUnconfirmed(still);
  }

  return (
    <AsyncBoundary
      state={state}
      label="incidents"
      onRetry={state.reload}
      isEmpty={(page) => page.items.length === 0}
      emptyTitle="No incidents"
      emptyBody="The platform reports no incidents. That is a real, empty answer, not a failure to load."
    >
      {(page) => (
        <>
          <SourceBadge source={page.source} />
          <CaveatList caveats={page.caveats} />
          {!allowed.allowed ? (
            <p className="muted" data-testid="incident-role-blocked" role="note">
              Acknowledging is disabled. {allowed.reason}
            </p>
          ) : null}
          <TableWrap>
            <table data-testid="incidents-table" className="table--stack-sm">
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Incident</th>
                  <th className="num">Occurrences</th>
                  <th>Last seen (UTC)</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} data-testid="incident-row" data-incident-id={row.id} data-status={row.status}>
                    <td data-label="Severity">
                      <SeverityBadge severity={row.severity} />
                    </td>
                    <td className="wrap" data-label="Incident">
                      <Link href={`/system/incidents/${encodeURIComponent(row.id)}`} data-testid={`incident-link-${row.id}`}>
                        <strong>{row.title}</strong>
                      </Link>
                      <br />
                      <span className="mono muted">{row.event}</span>
                      <span className="muted"> · first seen {formatTimestamp(row.first_seen)}</span>
                    </td>
                    <td data-label="Occurrences">
                      <FigureValue figure={row.occurrences} />
                    </td>
                    <td data-label="Last seen (UTC)">{formatTimestamp(row.last_seen)}</td>
                    <td className="wrap" data-label="Status">
                      {row.status === "open" ? (
                        unconfirmed.has(row.id) ? (
                          <span data-testid="incident-confirming">
                            open · <span className="badge badge--neutral">confirming…</span>
                          </span>
                        ) : (
                          "open"
                        )
                      ) : (
                        <>
                          {row.status}
                          {row.acknowledged_by
                            ? ` by ${row.acknowledged_by}${row.acknowledged_at ? ` at ${formatTimestamp(row.acknowledged_at)}` : ""}`
                            : ""}
                          {latestAckReason(row) ? `: ${latestAckReason(row)}` : ""}
                        </>
                      )}
                    </td>
                    <td data-label="Action">
                      {row.status === "open" ? (
                        <AcknowledgeIncident
                          incidentId={row.id}
                          title={row.title}
                          testId={`incident-ack-${row.id}`}
                          echo={() =>
                            awaitEcho<Page<IncidentRow>>(client, INCIDENTS_PATH, (latest) => {
                              const found = latest.items.find((r) => r.id === row.id);
                              return found !== undefined && found.status !== "open";
                            })
                          }
                          onUnconfirmed={() => setUnconfirmed((previous) => new Set([...previous, row.id]))}
                        />
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </>
      )}
    </AsyncBoundary>
  );
}
