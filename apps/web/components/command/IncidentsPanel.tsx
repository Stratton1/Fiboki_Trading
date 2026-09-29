"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { capability } from "@/lib/auth";
import { awaitEcho } from "@/lib/echo";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { IncidentRow, Page } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { CaveatList, SourceBadge, TableWrap } from "../primitives";
import { useExecutionMode, useOperator } from "../shell/platform";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { SeverityBadge } from "./SeverityBadge";

/**
 * Incidents (plan §4, "System & Incidents"; v1 on the Overview).
 *
 * Read from GET /api/system/incidents and kept current by the `incidents`
 * stream topic. Acknowledging (POST .../{id}/ack, admin only) goes through the
 * one ConfirmDialog with a mandatory reason of at least 8 characters (the
 * backend's minimum; it is written to the audit trail), and, like every
 * mutation, shows no optimistic state: the row reads "acknowledged" only once
 * the platform says so. Acknowledging does not resolve an incident.
 */

export const INCIDENTS_PATH = "/api/system/incidents";
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
  const { mode: executionMode, mutationsAllowed } = useExecutionMode();
  const allowed = capability(useOperator(), "can_acknowledge");
  const [target, setTarget] = useState<IncidentRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  async function acknowledge(_choice: string, reason: string) {
    if (!target) return;
    const id = target.id;
    setBusy(true);
    setSent(false);
    setError(null);
    try {
      await apiFetch(`${INCIDENTS_PATH}/${encodeURIComponent(id)}/ack`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (${err.code})` : "The request failed.");
      setBusy(false);
      return;
    }
    setSent(true);
    const echoed = await awaitEcho<Page<IncidentRow>>(client, INCIDENTS_PATH, (page) => {
      const row = page.items.find((r) => r.id === id);
      return row !== undefined && row.status !== "open";
    });
    if (!echoed) setUnconfirmed((previous) => new Set([...previous, id]));
    setBusy(false);
    setSent(false);
    setTarget(null);
  }

  return (
    <>
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
              <table data-testid="incidents-table">
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
                    <tr
                      key={row.id}
                      data-testid="incident-row"
                      data-incident-id={row.id}
                      data-status={row.status}
                    >
                      <td>
                        <SeverityBadge severity={row.severity} />
                      </td>
                      <td className="wrap">
                        <strong>{row.title}</strong>
                        <br />
                        <span className="mono muted">{row.event}</span>
                        <span className="muted"> · first seen {formatTimestamp(row.first_seen)}</span>
                      </td>
                      <td>
                        <FigureValue figure={row.occurrences} />
                      </td>
                      <td>{formatTimestamp(row.last_seen)}</td>
                      <td className="wrap">
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
                      <td>
                        {row.status === "open" ? (
                          <Button
                            size="sm"
                            data-testid={`incident-ack-${row.id}`}
                            disabled={!mutationsAllowed || !allowed.allowed}
                            onClick={() => {
                              setError(null);
                              setTarget(row);
                            }}
                          >
                            Acknowledge
                          </Button>
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
      <ConfirmDialog
        open={target !== null}
        title={target ? `Acknowledge: ${target.title}` : "Acknowledge incident"}
        executionMode={executionMode}
        choices={[
          {
            id: "acknowledge",
            title: "ACKNOWLEDGE",
            body: "Record that you have seen this incident. It is not resolved by this, and it stays in the log.",
            consequences: [],
          },
        ]}
        confirmLabel="Acknowledge"
        busy={busy}
        errorMessage={error}
        notice={sent ? "Sent. Waiting for the platform to confirm the acknowledgement." : null}
        onCancel={() => setTarget(null)}
        onConfirm={acknowledge}
      />
    </>
  );
}
