"use client";

import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList } from "@/components/CaveatList";
import { PanelStamp } from "@/components/workspace/PanelStamp";
import { useClock } from "@/lib/clock";
import { formatTimestamp } from "@/lib/format";
import { CALENDAR_DAYS, IMPACTS, NEWS_CURRENCIES, calendarPath, untilWords, type CalendarView } from "@/lib/monitors";
import { useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";

export interface CalendarFilter {
  impacts: string[];
  currencies: string[];
  instrument: string | null;
  days: number;
}

const STATUS_WORD = { upcoming: "UPCOMING", in_progress: "IN PROGRESS", released: "RELEASED" } as const;

function utcDayTime(iso: string): { day: string; time: string } {
  const d = new Date(iso);
  const day = d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", timeZone: "UTC" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
  return { day, time };
}

/**
 * ECO: scheduled official releases from the NEWEST official calendar version
 * (the paper path; research pins an older one and is unaffected), filtered
 * on the server by impact, currency or instrument. The status (upcoming, in
 * progress, released) is the API's; the countdown is this screen's clock
 * display of the time to the event. Each event opens the chart at it
 * (`?event=<id>`), where the same events are drawn.
 */
export function CalendarMonitor({
  filter,
  onFilter,
  onSymbol,
  compact = false,
}: {
  filter: CalendarFilter;
  onFilter: (patch: Partial<CalendarFilter>) => void;
  onSymbol?: (symbol: string) => void;
  compact?: boolean;
}) {
  const state = useApi<Envelope<CalendarView>>(calendarPath(filter), { refreshMs: 60_000 });
  const now = useClock();
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <div className="eco" data-testid="calendar">
      <div className="ws-panel__bar" role="group" aria-label="Calendar filters">
        {filter.instrument ? <span className="mono ws-chart__sym" data-testid="eco-instrument">{filter.instrument}</span> : null}
        <span className="ws-inline" role="group" aria-label="Impact">
          {IMPACTS.map((i) => (
            <button
              key={i}
              type="button"
              className="ws-btn ws-btn--ghost"
              aria-pressed={filter.impacts.includes(i)}
              onClick={() => onFilter({ impacts: toggle(filter.impacts, i) })}
              data-testid={`eco-impact-${i}`}
            >
              {i.toUpperCase()}
            </button>
          ))}
        </span>
        {!filter.instrument ? (
          <span className="ws-inline" role="group" aria-label="Currencies">
            {NEWS_CURRENCIES.map((c) => (
              <button
                key={c}
                type="button"
                className="ws-btn ws-btn--ghost"
                aria-pressed={filter.currencies.includes(c)}
                onClick={() => onFilter({ currencies: toggle(filter.currencies, c) })}
                data-testid={`eco-ccy-${c}`}
              >
                {c}
              </button>
            ))}
          </span>
        ) : null}
        <label className="ws-inline">
          <span className="sr-only">Days ahead</span>
          <select value={filter.days} onChange={(e) => onFilter({ days: Number(e.target.value) })} data-testid="eco-days">
            {CALENDAR_DAYS.map((d) => (
              <option key={d} value={d}>
                next {d}d
              </option>
            ))}
          </select>
        </label>
      </div>
      <AsyncBoundary
        state={state}
        label="the economic calendar"
        onRetry={state.reload}
        isEmpty={(e) => e.data.events.length === 0}
        emptyTitle="No scheduled releases"
        emptyBody="The official calendar has no event in this window for these filters. Check the caveats: a currency it does not cover is a gap, not a quiet week."
      >
        {(envelope) => {
          const view = envelope.data;
          return (
            <>
              <PanelStamp
                source={envelope.source}
                extra={`official ${view.calendar_version} · research pins ${view.research_pinned_version} · ${view.events.length} event(s)`}
              />
              <CaveatList caveats={envelope.caveats} />
              <div className="table-wrap">
                <table className="dense ws-table eco__table" data-testid="eco-table">
                  <thead>
                    <tr>
                      <th scope="col">Day (UTC)</th>
                      <th scope="col">Time</th>
                      <th scope="col">When</th>
                      <th scope="col">Ccy</th>
                      <th scope="col">Impact</th>
                      <th scope="col">Event</th>
                      {!compact ? <th scope="col">Status</th> : null}
                      <th scope="col">Chart</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.events.map((e) => {
                      const { day, time } = utcDayTime(e.event_time);
                      return (
                        <tr
                          key={e.event_id}
                          data-testid="eco-row"
                          data-status={e.status}
                          data-impact={e.impact}
                          data-next={e.event_id === view.next_event_id || undefined}
                        >
                          <td className="mono">{day}</td>
                          <td className="mono" title={formatTimestamp(e.event_time)}>
                            {e.time_known ? time : "window"}
                          </td>
                          <td className="mono eco__until" data-testid="eco-until">
                            {e.status === "in_progress" ? "now" : untilWords(e.event_time, now)}
                          </td>
                          <td className="mono">{e.currency}</td>
                          <td>
                            <span className={`eco__impact eco__impact--${e.impact}`}>{e.impact.toUpperCase()}</span>
                          </td>
                          <td className="eco__name">
                            <a href={e.source_url} target="_blank" rel="noopener noreferrer" title={`Source: ${e.source}`}>
                              {e.name}
                            </a>
                          </td>
                          {!compact ? <td className="mono">{STATUS_WORD[e.status]}</td> : null}
                          <td className="eco__links">
                            {e.chart_links.map((l) =>
                              onSymbol ? (
                                <button key={l.symbol} type="button" className="ws-sym" onClick={() => onSymbol(l.symbol)} data-testid="eco-chart">
                                  {l.symbol}
                                </button>
                              ) : (
                                <Link key={l.symbol} href={l.href} className="mono" prefetch={false} data-testid="eco-chart">
                                  {l.symbol}
                                </Link>
                              ),
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          );
        }}
      </AsyncBoundary>
    </div>
  );
}
