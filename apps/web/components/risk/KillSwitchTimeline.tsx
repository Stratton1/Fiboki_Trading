"use client";

import { useState } from "react";
import { useClock } from "@/lib/clock";
import { formatTimestamp, parseUtc } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { IncidentRow, KillSwitchEvent, Page } from "@/lib/types";
import { useWidth } from "@/lib/use-width";
import { AsyncBoundary } from "../AsyncBoundary";
import { CaveatList, SourceBadge } from "../primitives";

/**
 * Kill-switch history as a horizontal UTC timeline over the last 30 days
 * (V-2): "what happened while I was away, and who did it?"
 *
 * Reads GET /api/system/kill-switch/history (the append-only journal, newest
 * first; unused by any screen before this) and GET /api/system/incidents,
 * whose first-seen times are drawn as ticks on a second lane. Owned SVG, no
 * library. Every event is a glyph by kind, never by colour alone:
 *
 *   ◐ PAUSE armed    ◆ FLATTEN armed    ○ halt lifted (disarm)    ? other
 *
 * and the span between an arm and the next lift is a band, so "was trading
 * halted on Tuesday night?" reads at a glance. Hovering a glyph gives the
 * operator, the time and the reason (SVG <title>); every event is also in
 * "View data" as text, for the keyboard and screen readers.
 *
 * Positions on the axis are timestamps placed on a time scale; nothing about
 * the switch is computed here. Where the journal page is full (the route
 * returns at most `limit` events), older events inside the window may be
 * missing and the timeline says so rather than drawing a quiet month.
 */

export const KILL_SWITCH_HISTORY_PATH = "/api/system/kill-switch/history?limit=500";
const HISTORY_LIMIT = 500;
const INCIDENTS_PATH = "/api/system/incidents";
const WINDOW_DAYS = 30;
const DAY_MS = 86_400_000;
const PAD_L = 84;
const PAD_R = 14;
const LANE_H = 30;
const AXIS_H = 22;

type Kind = "pause" | "flatten" | "lift" | "other";

function kindOf(event: KillSwitchEvent): Kind {
  if (event.action === "activate") return event.mode === "flatten" ? "flatten" : event.mode === "pause" ? "pause" : "other";
  if (event.action === "deactivate") return "lift";
  return "other";
}

const KIND_META: Record<Kind, { glyph: string; word: string }> = {
  pause: { glyph: "◐", word: "PAUSE armed" },
  flatten: { glyph: "◆", word: "FLATTEN armed" },
  lift: { glyph: "○", word: "Halt lifted" },
  other: { glyph: "?", word: "Unrecognised event" },
};

interface Placed {
  event: KillSwitchEvent;
  kind: Kind;
  ms: number;
}

/** The armed spans, from each arm to the next lift (or to now, if none). */
function armedSpans(sorted: readonly Placed[], start: number, now: number): { from: number; to: number; open: boolean }[] {
  const spans: { from: number; to: number; open: boolean }[] = [];
  let armedAt: number | null = null;
  // The oldest event in the window is a lift: the switch was armed when the
  // window opened (the arm itself is older than the window).
  if (sorted[0]?.kind === "lift") armedAt = start;
  for (const p of sorted) {
    if ((p.kind === "pause" || p.kind === "flatten") && armedAt === null) armedAt = p.ms;
    else if (p.kind === "lift" && armedAt !== null) {
      spans.push({ from: armedAt, to: p.ms, open: false });
      armedAt = null;
    }
  }
  if (armedAt !== null) spans.push({ from: armedAt, to: now, open: true });
  return spans;
}

export function KillSwitchTimelineCard() {
  const history = useApi<Page<KillSwitchEvent>>(KILL_SWITCH_HISTORY_PATH, { refreshMs: 30_000 });
  const incidents = useApi<Page<IncidentRow>>(INCIDENTS_PATH, { refreshMs: 30_000 });
  return (
    <section className="card" aria-labelledby="ks-timeline-title" data-testid="ks-timeline-card">
      <h2 id="ks-timeline-title" className="card__title">
        Kill switch and incidents, last {WINDOW_DAYS} days (UTC)
      </h2>
      <AsyncBoundary state={history} label="kill-switch history" onRetry={history.reload}>
        {(page) => (
          <>
            <SourceBadge source={page.source} />
            <CaveatList caveats={page.caveats} />
            {incidents.status === "success" ? (
              <SourceBadge source={incidents.data.source} />
            ) : (
              <p className="muted" data-testid="ks-timeline-incidents-missing" role="status">
                {incidents.status === "loading"
                  ? "Reading incidents…"
                  : "Incidents could not be read, so no incident is drawn. That is not the same as none."}
              </p>
            )}
            <KillSwitchTimeline
              events={page.items}
              total={page.total}
              incidents={incidents.status === "success" ? incidents.data.items : null}
            />
          </>
        )}
      </AsyncBoundary>
    </section>
  );
}

export function KillSwitchTimeline({
  events,
  total,
  incidents,
}: {
  events: readonly KillSwitchEvent[];
  total: number;
  incidents: readonly IncidentRow[] | null;
}) {
  // The window's right edge is the shared wall clock (at 30 days across a few
  // hundred pixels, a second moves nothing visibly).
  const now = useClock();
  const start = now - WINDOW_DAYS * DAY_MS;
  const [box, width] = useWidth();

  const placed: Placed[] = events
    .map((event) => ({ event, kind: kindOf(event), ms: parseUtc(event.at).getTime() }))
    .filter((p) => !Number.isNaN(p.ms))
    .sort((a, b) => a.ms - b.ms);
  const inWindow = placed.filter((p) => p.ms >= start && p.ms <= now);
  const older = placed.length - inWindow.length;
  const oldest = placed[0];
  const maybeTruncated = events.length >= HISTORY_LIMIT && oldest !== undefined && oldest.ms > start;
  const ticks = (incidents ?? [])
    .map((incident) => ({ incident, ms: parseUtc(incident.first_seen).getTime() }))
    .filter((t) => !Number.isNaN(t.ms) && t.ms >= start && t.ms <= now)
    .sort((a, b) => a.ms - b.ms);

  const innerW = Math.max(1, width - PAD_L - PAD_R);
  const x = (ms: number) => PAD_L + ((ms - start) / (now - start)) * innerW;
  const height = LANE_H * 2 + AXIS_H;
  const laneY = (lane: number) => lane * LANE_H + LANE_H / 2;
  const spans = armedSpans(inWindow, start, now);
  const days = Array.from({ length: Math.floor(WINDOW_DAYS / 7) + 1 }, (_, i) => now - i * 7 * DAY_MS).filter(
    (ms) => ms >= start,
  );

  return (
    <figure className="ks-timeline" data-testid="ks-timeline" data-events={inWindow.length} data-incidents={ticks.length}>
      <div ref={box} className="ks-timeline__plot">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          role="img"
          aria-label={`${inWindow.length} kill-switch event(s) and ${incidents === null ? "unknown" : ticks.length} incident(s) in the last ${WINDOW_DAYS} days. Every event is listed in View data.`}
        >
          <text className="ks-timeline__lane" x={0} y={laneY(0) + 4}>
            Kill switch
          </text>
          <text className="ks-timeline__lane" x={0} y={laneY(1) + 4}>
            Incidents
          </text>
          <line className="ks-timeline__rule" x1={PAD_L} x2={width - PAD_R} y1={laneY(0)} y2={laneY(0)} />
          <line className="ks-timeline__rule" x1={PAD_L} x2={width - PAD_R} y1={laneY(1)} y2={laneY(1)} />
          {spans.map((span, i) => (
            <rect
              key={`span-${i}`}
              className="ks-timeline__armed"
              data-testid="ks-armed-span"
              data-open={span.open}
              x={x(span.from)}
              y={laneY(0) - 9}
              width={Math.max(2, x(span.to) - x(span.from))}
              height={18}
            />
          ))}
          {inWindow.map((p, i) => (
            <g
              key={`${p.event.at}:${i}`}
              data-testid="ks-event"
              data-kind={p.kind}
              data-operator={p.event.operator}
              className="ks-timeline__event"
            >
              <title>{`${KIND_META[p.kind].word} by ${p.event.operator || "unknown operator"} at ${formatTimestamp(p.event.at)}: ${p.event.reason || "no reason recorded"}`}</title>
              <circle className="ks-timeline__hit" cx={x(p.ms)} cy={laneY(0)} r={9} />
              <text className="ks-timeline__glyph" data-kind={p.kind} x={x(p.ms)} y={laneY(0) + 4.5} textAnchor="middle">
                {KIND_META[p.kind].glyph}
              </text>
            </g>
          ))}
          {ticks.map(({ incident, ms }) => (
            <g key={incident.id} data-testid="ks-incident-tick" data-severity={incident.severity}>
              <title>{`Incident ${incident.severity.toUpperCase()}: ${incident.title}, first seen ${formatTimestamp(incident.first_seen)} (${incident.status})`}</title>
              <line
                className="ks-timeline__tick"
                data-severity={incident.severity}
                x1={x(ms)}
                x2={x(ms)}
                y1={laneY(1) - 8}
                y2={laneY(1) + 8}
              />
            </g>
          ))}
          <line
            className="ks-timeline__axis"
            x1={PAD_L}
            x2={width - PAD_R}
            y1={LANE_H * 2 + 2}
            y2={LANE_H * 2 + 2}
          />
          {days.map((ms, i) => (
            <text
              key={ms}
              className="chart__tick"
              x={x(ms)}
              y={LANE_H * 2 + 16}
              textAnchor={i === 0 ? "end" : "middle"}
            >
              {i === 0 ? "now" : new Date(ms).toISOString().slice(5, 10)}
            </text>
          ))}
        </svg>
      </div>
      <figcaption className="ks-timeline__legend">
        <span>◐ PAUSE armed</span>
        <span>◆ FLATTEN armed</span>
        <span>○ halt lifted</span>
        <span>
          <span className="ks-timeline__swatch" aria-hidden="true" /> halted
        </span>
        <span>| incident first seen</span>
      </figcaption>
      {inWindow.length === 0 ? (
        <p className="muted" data-testid="ks-timeline-quiet">
          No kill-switch event in the last {WINDOW_DAYS} days: the journal answered, and it holds none in this window.
        </p>
      ) : null}
      {older > 0 ? (
        <p className="muted" data-testid="ks-timeline-older">
          {older} older event{older === 1 ? "" : "s"} in the journal, before this window.
        </p>
      ) : null}
      {maybeTruncated ? (
        <p className="state state--error" role="status" data-testid="ks-timeline-truncated">
          The journal returned its limit of {HISTORY_LIMIT} events (of {total}), all inside the window: older events in the
          window may be missing from this timeline.
        </p>
      ) : null}
      <TimelineData events={inWindow} ticks={ticks.map((t) => t.incident)} />
    </figure>
  );
}

/** "View data": every event and incident the timeline draws, as text. */
function TimelineData({ events, ticks }: { events: readonly Placed[]; ticks: readonly IncidentRow[] }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="chart__data" data-testid="ks-timeline-data" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>View data</summary>
      {open ? (
        <div className="table-wrap chart__data-table" tabIndex={0} role="region" aria-label="Kill-switch timeline data">
          <table>
            <caption className="sr-only">Kill-switch events and incidents, newest first</caption>
            <thead>
              <tr>
                <th scope="col">When (UTC)</th>
                <th scope="col">What</th>
                <th scope="col">Who</th>
                <th scope="col">Reason or title</th>
              </tr>
            </thead>
            <tbody>
              {[
                ...events.map((p) => ({
                  ms: p.ms,
                  key: `e:${p.event.at}:${p.kind}`,
                  at: p.event.at,
                  what: `${KIND_META[p.kind].glyph} ${KIND_META[p.kind].word}`,
                  who: p.event.operator || "unknown operator",
                  text: p.event.reason || "no reason recorded",
                })),
                ...ticks.map((incident) => ({
                  ms: parseUtc(incident.first_seen).getTime(),
                  key: `i:${incident.id}`,
                  at: incident.first_seen,
                  what: `| Incident ${incident.severity.toUpperCase()} (${incident.status})`,
                  who: incident.source,
                  text: incident.title,
                })),
              ]
                .sort((a, b) => b.ms - a.ms)
                .map((row) => (
                  <tr key={row.key} data-testid="ks-timeline-row">
                    <td>{formatTimestamp(row.at)}</td>
                    <td>{row.what}</td>
                    <td>{row.who}</td>
                    <td className="wrap">{row.text}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </details>
  );
}
