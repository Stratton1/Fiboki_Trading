"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useClock } from "@/lib/clock";
import { formatTimestamp } from "@/lib/format";
import { untilWords, type CalendarEvent } from "@/lib/monitors";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { Envelope } from "@/lib/types";

/**
 * `?event=<id>` on the chart workstation (a link from ECO): one line naming
 * the event from the official calendar, its time, the API's status and the
 * time to it. The chart draws the same event among its overlays. Renders
 * nothing without the parameter.
 */
export function EventFocus({ symbol }: { symbol: string }) {
  const id = useSearchParams().get("event");
  return id ? <Focus id={id} symbol={symbol} /> : null;
}

function Focus({ id, symbol }: { id: string; symbol: string }) {
  const state = useApi<Envelope<CalendarEvent>>(`/api/markets/calendar/events/${encodeURIComponent(id)}?instrument=${encodeURIComponent(symbol)}`);
  const now = useClock();
  if (state.status === "loading") return <p className="eco-focus muted" role="status">Loading event {id}…</p>;
  if (state.status === "error") {
    return (
      <p className="eco-focus" role="status" data-testid="event-focus" data-state="error">
        Event <span className="mono">{id}</span>: {state.error.message}
      </p>
    );
  }
  const e = state.data.data;
  return (
    <p className="eco-focus" role="status" data-testid="event-focus" data-status={e.status}>
      <span className={`eco__impact eco__impact--${e.impact}`}>{e.impact.toUpperCase()}</span>{" "}
      <span className="mono">{e.currency}</span> <strong>{e.name}</strong>{" "}
      <span className="mono" title={formatTimestamp(e.event_time)}>
        {formatTimestamp(e.event_time)}
      </span>{" "}
      · <span className="mono">{e.status === "in_progress" ? "in progress" : untilWords(e.event_time, now)}</span> ·{" "}
      <Link href={route.calendar(symbol)} prefetch={false}>
        calendar
      </Link>
    </p>
  );
}
