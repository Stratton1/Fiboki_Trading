"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { SeverityBadge } from "@/components/command/SeverityBadge";
import { FigureValue } from "@/components/FigureValue";
import {
  AcknowledgeIncident,
  INCIDENTS_PATH,
  useAckBlocked,
} from "@/components/incidents/AcknowledgeIncident";
import { CaveatList, PageHead, SourceBadge } from "@/components/primitives";
import { useExecutionMode } from "@/components/shell/platform";
import { Button } from "@/components/ui/Button";
import { ApiError, apiFetch } from "@/lib/api";
import { awaitEcho } from "@/lib/echo";
import { formatTimestamp } from "@/lib/format";
import { invalidatePath, useApi } from "@/lib/query";
import type { Envelope, IncidentRow, IncidentTimelineEntry } from "@/lib/types";

/**
 * One incident (GET /api/system/incidents/{id}, routers/incidents.py
 * IncidentView): the header, the actions the API supports for it, and its
 * whole timeline as a vertical list, oldest first, exactly as the read model
 * returns it (alert-log occurrences, acknowledgements, notes, resolution).
 *
 * Two actions, both admin only and both audited by the backend:
 *  - Acknowledge: the shared AcknowledgeIncident control, the same dialog
 *    and friction as on Command (mode stated, REAL MONEY in LIVE, reason 8
 *    to 500 characters);
 *  - Note: POST /api/system/incidents/{id}/note `{text}` (1 to 2000
 *    characters). A note never changes the incident's status. No optimistic
 *    UI: the note is shown when the platform's timeline carries it.
 *
 * The page links back to Command and to the screen that shows the incident's
 * source, where one exists; where none does, it says so.
 */

const INCIDENT_REFRESH_MS = 15_000;
const NOTE_MAX = 2_000;

/** The workstation screen that shows an incident's source, from the event the platform named. */
export function sourceScreen(incident: Pick<IncidentRow, "event" | "source">): { href: string; label: string } | null {
  const event = incident.event;
  if (event.startsWith("kill_switch") || incident.source.startsWith("kill_switch_journal")) {
    return { href: "/trading/risk", label: "Risk & Exposure (kill switch)" };
  }
  const byEvent: Record<string, { href: string; label: string }> = {
    worker_down: { href: "/system/workers", label: "Workers" },
    heartbeat_stale: { href: "/system/workers", label: "Workers" },
    worker_lease_contended: { href: "/system/workers", label: "Workers" },
    data_stale: { href: "/markets/data-quality", label: "Data Quality" },
    data_quality_defect: { href: "/markets/data-quality", label: "Data Quality" },
    sweep_no_data_exceeded: { href: "/markets/data-quality", label: "Data Quality" },
    broker_unhealthy: { href: "/system/broker-health", label: "Broker Health" },
    risk_limit_breach: { href: "/trading/risk", label: "Risk & Exposure" },
    strategy_degraded: { href: "/trading/candidates", label: "Candidates" },
    strategy_halted: { href: "/trading/candidates", label: "Candidates" },
    strategy_quarantined: { href: "/trading/candidates", label: "Candidates" },
    reconciliation_divergence: { href: "/trading/execution", label: "Trades" },
    order_rejected_repeatedly: { href: "/trading/execution", label: "Trades" },
    spread_model_divergence: { href: "/trading/execution", label: "Trades" },
    queue_backed_up: { href: "/system/services", label: "Services" },
    job_dead_lettered: { href: "/system/services", label: "Services" },
    migration_drift: { href: "/system/services", label: "Services" },
  };
  return byEvent[event] ?? null;
}

const KIND_META: Record<IncidentTimelineEntry["kind"], { glyph: string; word: string }> = {
  occurrence: { glyph: "●", word: "OCCURRED" },
  ack: { glyph: "✓", word: "ACKNOWLEDGED" },
  note: { glyph: "✎", word: "NOTE" },
  resolved: { glyph: "◇", word: "RESOLVED" },
};

export function IncidentDetail({ id }: { id: string }) {
  const path = `${INCIDENTS_PATH}/${encodeURIComponent(id)}`;
  const state = useApi<Envelope<IncidentRow>>(path, { refreshMs: INCIDENT_REFRESH_MS });
  const client = useQueryClient();
  const [unconfirmed, setUnconfirmed] = useState(false);

  const current = state.status === "success" ? state.data.data : null;
  if (unconfirmed && current && current.status !== "open") setUnconfirmed(false);

  return (
    <>
      <nav className="entity-nav" aria-label="Back">
        <Link href="/" data-testid="incident-back-command">
          <ArrowLeft size={13} aria-hidden="true" /> Command
        </Link>
      </nav>
      <PageHead
        title="Incident"
        intro="One incident as the platform's read model derives it: the header, what you can do about it, and every entry on its timeline, oldest first."
      />
      <AsyncBoundary state={state} label={`incident ${id}`} onRetry={state.reload}>
        {(envelope) => {
          const incident = envelope.data;
          const source = sourceScreen(incident);
          return (
            <div data-testid="incident-detail" data-incident-id={incident.id} data-status={incident.status}>
              <SourceBadge source={envelope.source} />
              <CaveatList caveats={envelope.caveats} />
              <section className="card" aria-labelledby="incident-title">
                <div className="entity-head">
                  <SeverityBadge severity={incident.severity} />
                  <h2 id="incident-title" className="entity-head__title" data-testid="incident-title">
                    {incident.title}
                  </h2>
                  <span className="badge badge--neutral" data-testid="incident-status">
                    {incident.status.toUpperCase()}
                  </span>
                  {unconfirmed ? (
                    <span className="badge badge--neutral" data-testid="incident-confirming">
                      acknowledged · confirming…
                    </span>
                  ) : null}
                </div>
                <dl className="entity-facts">
                  <div>
                    <dt>Incident</dt>
                    <dd className="mono">{incident.id}</dd>
                  </div>
                  <div>
                    <dt>Event</dt>
                    <dd className="mono">{incident.event}</dd>
                  </div>
                  <div>
                    <dt>Dedupe key</dt>
                    <dd className="mono">{incident.key}</dd>
                  </div>
                  <div>
                    <dt>Source</dt>
                    <dd className="mono">{incident.source}</dd>
                  </div>
                  <div>
                    <dt>First seen</dt>
                    <dd>{formatTimestamp(incident.first_seen)}</dd>
                  </div>
                  <div>
                    <dt>Last seen</dt>
                    <dd>{formatTimestamp(incident.last_seen)}</dd>
                  </div>
                  <div>
                    <dt>Occurrences</dt>
                    <dd data-testid="incident-occurrences">
                      <FigureValue figure={incident.occurrences} />
                    </dd>
                  </div>
                  <div>
                    <dt>Acknowledged</dt>
                    <dd data-testid="incident-acknowledged">
                      {incident.acknowledged_by
                        ? `by ${incident.acknowledged_by}${incident.acknowledged_at ? ` at ${formatTimestamp(incident.acknowledged_at)}` : ""}`
                        : "not acknowledged"}
                    </dd>
                  </div>
                  <div>
                    <dt>Resolved</dt>
                    <dd>{incident.resolved_at ? formatTimestamp(incident.resolved_at) : "not resolved"}</dd>
                  </div>
                </dl>
                <p className="entity-links" data-testid="incident-links">
                  <Link href="/">Back to Command</Link>
                  {source ? (
                    <Link href={source.href} data-testid="incident-source-link">
                      Open the source: {source.label}
                    </Link>
                  ) : (
                    <span className="muted" data-testid="incident-source-none">
                      No workstation screen shows the source <span className="mono">{incident.source}</span> of a{" "}
                      <span className="mono">{incident.event}</span> event; its entries are below.
                    </span>
                  )}
                </p>
              </section>

              <section className="card" aria-labelledby="incident-actions">
                <h2 id="incident-actions" className="card__title">
                  Actions
                </h2>
                <div className="entity-actions">
                  {incident.status === "open" && !unconfirmed ? (
                    <AcknowledgeIncident
                      incidentId={incident.id}
                      title={incident.title}
                      testId="incident-detail-ack"
                      echo={() =>
                        awaitEcho<Envelope<IncidentRow>>(client, path, (latest) => latest.data.status !== "open")
                      }
                      onUnconfirmed={() => setUnconfirmed(true)}
                    />
                  ) : (
                    <p className="muted" data-testid="incident-ack-not-applicable">
                      {incident.status === "open"
                        ? "Acknowledgement sent; waiting for the platform to show it."
                        : `Nothing to acknowledge: the incident is ${incident.status}. A recurrence re-opens it.`}
                    </p>
                  )}
                </div>
                <NoteForm
                  incidentId={incident.id}
                  onSent={(text) =>
                    awaitEcho<Envelope<IncidentRow>>(client, path, (latest) =>
                      latest.data.timeline.some((entry) => entry.kind === "note" && entry.text === text),
                    )
                  }
                  afterSend={() => void invalidatePath(client, INCIDENTS_PATH)}
                />
              </section>

              <section className="card" aria-labelledby="incident-timeline-title">
                <h2 id="incident-timeline-title" className="card__title">
                  Timeline ({incident.timeline.length})
                </h2>
                {incident.timeline.length === 0 ? (
                  <p className="muted" data-testid="incident-timeline-empty">
                    The platform returned no timeline entries for this incident.
                  </p>
                ) : (
                  <ol className="vtimeline" data-testid="incident-timeline">
                    {incident.timeline.map((entry, index) => {
                      const meta = KIND_META[entry.kind] ?? { glyph: "?", word: entry.kind.toUpperCase() };
                      return (
                        <li
                          key={`${entry.at}:${entry.kind}:${index}`}
                          className="vtimeline__item"
                          data-testid="incident-timeline-entry"
                          data-kind={entry.kind}
                        >
                          <span className="vtimeline__glyph" data-kind={entry.kind} aria-hidden="true">
                            {meta.glyph}
                          </span>
                          <div className="vtimeline__body">
                            <div className="vtimeline__line">
                              <span className="vtimeline__kind">{meta.word}</span>
                              {entry.severity ? <SeverityBadge severity={entry.severity} /> : null}
                              <time className="num muted" dateTime={entry.at}>
                                {formatTimestamp(entry.at)}
                              </time>
                              {entry.actor ? <span className="muted">by {entry.actor}</span> : null}
                            </div>
                            <p className="vtimeline__text">{entry.text || "(no text)"}</p>
                            {entry.correlation_id ? (
                              <p className="mono muted vtimeline__cid">correlation {entry.correlation_id}</p>
                            ) : null}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </section>
            </div>
          );
        }}
      </AsyncBoundary>
    </>
  );
}

type NoteState =
  | { phase: "editing"; error: string | null }
  | { phase: "sending" }
  | { phase: "confirming" }
  | { phase: "unconfirmed" };

function NoteForm({
  incidentId,
  onSent,
  afterSend,
}: {
  incidentId: string;
  onSent: (text: string) => Promise<boolean>;
  afterSend: () => void;
}) {
  const { mode } = useExecutionMode();
  const blocked = useAckBlocked("note");
  const fieldId = useId();
  const [text, setText] = useState("");
  const [state, setState] = useState<NoteState>({ phase: "editing", error: null });
  const busy = state.phase === "sending" || state.phase === "confirming";
  const trimmed = text.trim();
  const valid = trimmed.length >= 1 && text.length <= NOTE_MAX;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid || busy || blocked) return;
    setState({ phase: "sending" });
    try {
      await apiFetch(`${INCIDENTS_PATH}/${encodeURIComponent(incidentId)}/note`, {
        method: "POST",
        body: JSON.stringify({ text: trimmed }),
      });
    } catch (err) {
      setState({
        phase: "editing",
        error: err instanceof ApiError ? `${err.message} (${err.code})` : "The request failed.",
      });
      return;
    }
    setState({ phase: "confirming" });
    afterSend();
    const echoed = await onSent(trimmed);
    setText("");
    setState(echoed ? { phase: "editing", error: null } : { phase: "unconfirmed" });
  }

  return (
    <form className="note-form" onSubmit={submit} data-testid="incident-note-form">
      <label htmlFor={fieldId} className="note-form__label">
        Add a note to the timeline. It is audited, is recorded in{" "}
        <strong data-testid="incident-note-mode">{mode.toUpperCase()}</strong> mode, and does not change the
        incident&apos;s status.
      </label>
      <textarea
        id={fieldId}
        rows={3}
        value={text}
        maxLength={NOTE_MAX}
        disabled={busy || blocked !== null}
        onChange={(event) => {
          setText(event.target.value);
          if (state.phase !== "editing") setState({ phase: "editing", error: null });
        }}
        data-testid="incident-note-text"
        aria-describedby={`${fieldId}-hint`}
      />
      <div className="note-form__row">
        <Button type="submit" size="sm" disabled={!valid || busy || blocked !== null} data-testid="incident-note-submit">
          {busy ? "Working…" : "Add note"}
        </Button>
        <span id={`${fieldId}-hint`} className="muted" role="status" data-testid="incident-note-status">
          {blocked
            ? blocked.full
            : state.phase === "editing" && state.error
              ? null
              : state.phase === "sending"
                ? "Sending…"
                : state.phase === "confirming"
                  ? "Sent. Waiting for the platform to show it on the timeline."
                  : state.phase === "unconfirmed"
                    ? "Sent, but the platform has not shown it yet · confirming…"
                    : `${text.length} / ${NOTE_MAX}`}
        </span>
      </div>
      {state.phase === "editing" && state.error ? (
        <p className="state state--error" role="alert" data-testid="incident-note-error">
          {state.error}
        </p>
      ) : null}
    </form>
  );
}
