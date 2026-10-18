"use client";

import { useQuery } from "@tanstack/react-query";
import { formatTimestamp } from "@/lib/format";
import { apiKey } from "@/lib/query";
import type { ClockView, Envelope } from "@/lib/types";

/**
 * Market session clocks in the top bar: Sydney, Tokyo, London and New York,
 * open or closed, with each city's local time. All from GET /api/system/clock
 * (the API decides open or closed, DST and the FX weekend); re-read every
 * minute. Loaded with the command-line engine, after hydration, into a slot
 * of fixed width, so nothing moves when it arrives.
 */

const CLOCK_PATH = "/api/system/clock";
const SHORT: Record<string, string> = { sydney: "SYD", tokyo: "TOK", london: "LDN", new_york: "NY" };

export function SessionClocks() {
  const clock = useQuery<Envelope<ClockView>>({
    queryKey: apiKey(CLOCK_PATH),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
  const sessions = clock.data?.data.sessions ?? null;
  return (
    <ul className="sessions" data-testid="session-clocks" aria-label="Market sessions">
      {sessions === null ? (
        <li className="sessions__item muted">{clock.isError ? "sessions unavailable" : "sessions …"}</li>
      ) : (
        sessions.map((s) => (
          <li
            key={s.key}
            className="sessions__item"
            data-open={s.open}
            data-testid={`session-${s.key}`}
            title={`${s.label} ${s.local_time} local, ${s.open ? "open" : "closed"}; ${s.open ? "closes" : "opens"} ${formatTimestamp(s.next_change_at)}. ${s.detail}`}
          >
            <span className="sessions__dot" aria-hidden="true">
              {s.open ? "●" : "○"}
            </span>
            <span className="sessions__name">{SHORT[s.key] ?? s.label}</span>
            <span className="sessions__time num">{s.local_time}</span>
            <span className="sr-only">{s.open ? "open" : "closed"}</span>
          </li>
        ))
      )}
    </ul>
  );
}
