"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { capability } from "@/lib/auth";
import { useClock } from "@/lib/clock";
import { awaitEcho } from "@/lib/echo";
import { formatTimestamp, parseUtc } from "@/lib/format";
import { invalidatePath, useApi } from "@/lib/query";
import type { AttentionItem, Page } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { ProvenanceChip } from "../ProvenanceChip";
import { CaveatList, SourceBadge } from "../primitives";
import { useExecutionMode, useOperator } from "../shell/platform";
import { Button } from "../ui/Button";
import { SeverityBadge } from "./SeverityBadge";

/**
 * COMMAND · the attention queue as triage rows (plan §4, Wave 4a).
 *
 * "Is anything wrong, and what needs me now?" The ranking is the server's:
 * ranking is logic, and logic belongs in the backend (routers/command.py).
 * This panel renders the items in the order received; it never sorts, filters
 * or re-weights them. The position shown is the item's place in that order.
 *
 * Each row: a severity glyph and word, the title (a link to the item's deep
 * link), the reason, its age from the item's `as_of`, the provenance of the
 * deployment state it was derived from (the item's only provenance, carried by
 * its score), the score, and the actions the API supports for it. The API
 * supports one: acknowledging an INCIDENT (POST /api/system/incidents/{id}/ack,
 * admin only, a reason of 8 to 500 characters, sent with the CSRF header by
 * apiFetch). Nothing else on this panel mutates anything.
 *
 * An incident's attention id is `incident:<incident id>` (routers/command.py
 * `collect_attention`); that prefix is how a row knows it can be acknowledged.
 * No optimistic UI: after the POST, the row stays busy until the platform's
 * queue no longer lists the incident as needing attention (the server drops an
 * incident once it is acknowledged), exactly like the incidents panel.
 *
 * Keyboard: each title is a link, so Tab reaches it and Enter opens its deep
 * link; ArrowDown/ArrowUp (or j/k) move between rows. A deep link that is not
 * an in-app path is shown but not followed.
 */

export const ATTENTION_PATH = "/api/command/attention";
const INCIDENTS_PATH = "/api/system/incidents";
const ATTENTION_REFRESH_MS = 15_000;
const INCIDENT_PREFIX = "incident:";
/** The backend's IncidentAckRequest bounds (routers/incidents.py). */
const REASON_MIN = 8;
const REASON_MAX = 500;

/** Only an in-app path is followed; anything else could leave the workstation. */
function inAppLink(link: string | null): string | null {
  if (!link || !link.startsWith("/") || link.startsWith("//") || link.startsWith("/\\")) return null;
  return link;
}

/** The incident id behind an attention item, when the item is an incident. */
function incidentIdOf(item: AttentionItem): string | null {
  return item.category === "incident" && item.id.startsWith(INCIDENT_PREFIX)
    ? item.id.slice(INCIDENT_PREFIX.length)
    : null;
}

const SEVERITY_GLYPH: Record<string, string> = {
  critical: "◆",
  error: "✕",
  warning: "◐",
  info: "○",
};

function ageText(asOf: string | null, now: number): string {
  if (!asOf) return "age unknown: no as-of";
  const then = parseUtc(asOf).getTime();
  if (Number.isNaN(then)) return "age unknown";
  const s = Math.max(0, (now - then) / 1_000);
  if (s < 60) return `${Math.floor(s)}s old`;
  if (s < 3_600) return `${Math.floor(s / 60)}m old`;
  if (s < 86_400) return `${Math.floor(s / 3_600)}h old`;
  return `${Math.floor(s / 86_400)}d old`;
}

function moveFocus(event: KeyboardEvent<HTMLOListElement>) {
  const key = event.key;
  const down = key === "ArrowDown" || key === "j";
  const up = key === "ArrowUp" || key === "k";
  if (!down && !up) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  // Letters typed into a reason are text, not navigation.
  const target = event.target as HTMLElement;
  if (target.closest("input, textarea")) return;
  const links = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("[data-attention-focus]"));
  const index = links.indexOf(document.activeElement as HTMLElement);
  if (index === -1) return;
  const next = links[down ? Math.min(index + 1, links.length - 1) : Math.max(index - 1, 0)];
  event.preventDefault();
  next?.focus();
}

type Ack =
  | { phase: "editing"; reason: string; error: string | null }
  | { phase: "sending"; reason: string }
  | { phase: "confirming"; reason: string };

function AckForm({
  item,
  incidentId,
  ack,
  onChange,
  onCancel,
  onSubmit,
}: {
  item: AttentionItem;
  incidentId: string;
  ack: Ack;
  onChange: (reason: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const busy = ack.phase !== "editing";
  const length = ack.reason.trim().length;
  const valid = length >= REASON_MIN && ack.reason.length <= REASON_MAX;
  const fieldId = `ack-reason-${item.id}`;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (valid && !busy) onSubmit();
  };
  return (
    <form className="triage__ack" onSubmit={submit} data-testid={`attention-ack-form-${item.id}`}>
      <label htmlFor={fieldId} className="triage__ack-label">
        Acknowledge incident <span className="mono">{incidentId}</span>: what do you know or what have you done? It is
        audited, and does not resolve the incident.
      </label>
      <div className="triage__ack-row">
        <input
          id={fieldId}
          className="triage__ack-input"
          value={ack.reason}
          maxLength={REASON_MAX}
          disabled={busy}
          onChange={(event) => onChange(event.target.value)}
          data-testid={`attention-ack-reason-${item.id}`}
          aria-describedby={`${fieldId}-hint`}
          autoComplete="off"
        />
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={!valid || busy}
          data-testid={`attention-ack-submit-${item.id}`}
        >
          {busy ? "Working…" : "Acknowledge"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
      <p id={`${fieldId}-hint`} className="triage__ack-hint" role="status">
        {ack.phase === "editing" && ack.error ? (
          <span className="triage__ack-error" data-testid={`attention-ack-error-${item.id}`}>
            {ack.error}
          </span>
        ) : ack.phase === "sending" ? (
          "Sending…"
        ) : ack.phase === "confirming" ? (
          "Sent. Waiting for the platform to confirm the acknowledgement."
        ) : length < REASON_MIN ? (
          `At least ${REASON_MIN} characters.`
        ) : (
          `${ack.reason.length} / ${REASON_MAX}`
        )}
      </p>
    </form>
  );
}

function Row({
  item,
  position,
  now,
  ack,
  canAck,
  ackBlocked,
  onOpenAck,
  onChange,
  onCancel,
  onSubmit,
  unconfirmed,
}: {
  item: AttentionItem;
  position: number;
  now: number;
  ack: Ack | undefined;
  canAck: boolean;
  ackBlocked: string | null;
  onOpenAck: () => void;
  onChange: (reason: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
  unconfirmed: boolean;
}) {
  const href = inAppLink(item.deep_link);
  const incidentId = incidentIdOf(item);
  const glyph = SEVERITY_GLYPH[item.severity] ?? "?";
  return (
    <li
      className="triage"
      data-testid="attention-item"
      data-item-id={item.id}
      data-position={position}
      data-severity={item.severity}
      data-category={item.category}
      data-ack={ack ? ack.phase : unconfirmed ? "unconfirmed" : undefined}
    >
      <span className="triage__glyph" data-severity={item.severity} aria-hidden="true">
        {glyph}
      </span>
      <div className="triage__body">
        <div className="triage__line">
          <span className="mono muted triage__pos" aria-label={`position ${position}`}>
            #{position}
          </span>
          <SeverityBadge severity={item.severity} />
          {href ? (
            <Link
              href={href}
              className="triage__title"
              data-attention-focus=""
              data-testid={`attention-link-${item.id}`}
            >
              {item.title}
              <ArrowRight size={13} aria-hidden="true" className="triage__arrow" />
            </Link>
          ) : (
            <span
              className="triage__title"
              tabIndex={0}
              data-attention-focus=""
              data-testid={`attention-nolink-${item.id}`}
            >
              {item.title}
            </span>
          )}
        </div>
        <p className="triage__reason">{item.reason}</p>
        <div className="triage__meta">
          <span className="num" data-testid="attention-age" title={item.as_of ? formatTimestamp(item.as_of) : undefined}>
            {ageText(item.as_of, now)}
          </span>
          <span className="triage__chip" data-testid="attention-mode">
            <span className="muted">from</span>
            <ProvenanceChip provenance={item.score.provenance} />
          </span>
          <span className="muted">
            {item.category.replace(/_/g, " ")} · score <FigureValue figure={item.score} showChip={false} />
          </span>
          {href ? null : <span className="muted">No in-app link was supplied for this item.</span>}
          {unconfirmed ? (
            <span className="badge badge--neutral" data-testid="attention-ack-confirming">
              acknowledged · confirming…
            </span>
          ) : null}
        </div>
        {incidentId && ack ? (
          <AckForm
            item={item}
            incidentId={incidentId}
            ack={ack}
            onChange={onChange}
            onCancel={onCancel}
            onSubmit={onSubmit}
          />
        ) : null}
      </div>
      <div className="triage__actions">
        {incidentId && !ack && !unconfirmed ? (
          <Button
            size="sm"
            data-testid={`attention-ack-${item.id}`}
            disabled={!canAck}
            title={ackBlocked ?? undefined}
            onClick={onOpenAck}
          >
            Acknowledge…
          </Button>
        ) : null}
      </div>
    </li>
  );
}

export function AttentionPanel() {
  const state = useApi<Page<AttentionItem>>(ATTENTION_PATH, { refreshMs: ATTENTION_REFRESH_MS });
  const client = useQueryClient();
  const now = useClock();
  const { mutationsAllowed } = useExecutionMode();
  const allowed = capability(useOperator(), "can_acknowledge");
  const [acks, setAcks] = useState<ReadonlyMap<string, Ack>>(new Map());
  const [unconfirmed, setUnconfirmed] = useState<ReadonlySet<string>>(new Set());

  const set = (id: string, next: Ack | null) =>
    setAcks((previous) => {
      const map = new Map(previous);
      if (next === null) map.delete(id);
      else map.set(id, next);
      return map;
    });

  // Adjusted during render: an item the platform no longer lists is no
  // longer "confirming…".
  const items = state.status === "success" ? state.data.items : null;
  if (items && unconfirmed.size > 0) {
    const listed = new Set(items.map((item) => item.id));
    const still = new Set<string>();
    for (const id of unconfirmed) if (listed.has(id)) still.add(id);
    if (still.size !== unconfirmed.size) setUnconfirmed(still);
  }

  const ackBlocked = !mutationsAllowed
    ? "The execution mode is unknown or the workstation is disconnected; nothing can be confirmed."
    : allowed.reason;

  async function acknowledge(item: AttentionItem, reason: string) {
    const incidentId = incidentIdOf(item);
    if (!incidentId) return;
    set(item.id, { phase: "sending", reason });
    try {
      await apiFetch(`${INCIDENTS_PATH}/${encodeURIComponent(incidentId)}/ack`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() }),
      });
    } catch (err) {
      set(item.id, {
        phase: "editing",
        reason,
        error: err instanceof ApiError ? `${err.message} (${err.code})` : "The request failed.",
      });
      return;
    }
    set(item.id, { phase: "confirming", reason });
    void invalidatePath(client, INCIDENTS_PATH);
    // The echo: the platform's queue stops listing this incident.
    const echoed = await awaitEcho<Page<AttentionItem>>(
      client,
      ATTENTION_PATH,
      (page) => !page.items.some((candidate) => candidate.id === item.id),
    );
    if (!echoed) setUnconfirmed((previous) => new Set([...previous, item.id]));
    set(item.id, null);
  }

  return (
    <AsyncBoundary
      state={state}
      label="the attention queue"
      onRetry={state.reload}
      isEmpty={(page) => page.items.length === 0}
      emptyTitle="Nothing needs you"
      emptyBody="The platform's attention queue is empty: no kill switch armed, no breach, no open incident, no stale worker, no failing health check and no strategy awaiting review. That is the server's answer, not an absence of one."
    >
      {(page) => (
        <>
          <SourceBadge source={page.source} />
          <CaveatList caveats={page.caveats} />
          {!allowed.allowed ? (
            <p className="muted" data-testid="attention-role-blocked" role="note">
              Acknowledging is disabled. {allowed.reason}
            </p>
          ) : null}
          <ol className="triage-list" data-testid="attention-list" onKeyDown={moveFocus}>
            {page.items.map((item, index) => (
              <Row
                key={item.id}
                item={item}
                position={index + 1}
                now={now}
                ack={acks.get(item.id)}
                canAck={mutationsAllowed && allowed.allowed}
                ackBlocked={ackBlocked}
                unconfirmed={unconfirmed.has(item.id)}
                onOpenAck={() => set(item.id, { phase: "editing", reason: "", error: null })}
                onChange={(reason) => set(item.id, { phase: "editing", reason, error: null })}
                onCancel={() => set(item.id, null)}
                onSubmit={() => {
                  const current = acks.get(item.id);
                  if (current) void acknowledge(item, current.reason);
                }}
              />
            ))}
          </ol>
          <p className="muted mt-2">
            {page.items.length} of {page.total}, in the platform&apos;s order.
          </p>
        </>
      )}
    </AsyncBoundary>
  );
}
