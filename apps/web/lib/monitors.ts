import type { components } from "./generated/openapi";
import { apiFetch } from "./api";
import type { Caveat, Figure, Page, Series, SeriesPoint, SourceNote } from "./types";

/**
 * Market monitors (routers/monitors.py) and price alerts
 * (routers/price_alerts.py), as GENERATED types. Every figure, verdict and
 * tone here was decided by the API; the workstation renders them.
 */
type S = components["schemas"];

/**
 * A generated schema with the authoritative LOCAL Figure, Series, Caveat and
 * SourceNote substituted (lib/types.ts; lib/api-contract.ts checks their keys
 * against the schema), so a monitor row renders with FigureValue like every
 * other screen.
 */
export type Local<T> = T extends S["Figure"]
  ? Figure
  : T extends S["Series"]
    ? Series
    : T extends S["SeriesPoint"]
      ? SeriesPoint
      : T extends S["Caveat"]
        ? Caveat
        : T extends S["SourceNote"]
          ? SourceNote
          : T extends (infer U)[]
            ? Local<U>[]
            : T extends object
              ? { [K in keyof T]: Local<T[K]> }
              : T;

export type QuoteRow = Local<S["QuoteRowView"]>;
export type Watchlist = S["WatchlistView"];
export type DataState = QuoteRow["data_state"];

export type NewsView = Local<S["NewsView"]>;
export type Headline = Local<S["HeadlineView"]>;
export const NEWS_HOURS = [1, 4, 12, 24, 72, 168] as const;
export const NEWS_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD"] as const;

export function newsPath(filter: { instrument?: string | null; currencies?: string[]; source?: string | null; hours: number }): string {
  const q = new URLSearchParams();
  if (filter.instrument) q.set("instrument", filter.instrument);
  for (const c of filter.currencies ?? []) q.append("currency", c);
  if (filter.source) q.set("source", filter.source);
  q.set("hours", String(filter.hours));
  return `/api/markets/news?${q.toString()}`;
}

export type CalendarView = Local<S["CalendarView"]>;
export type CalendarEvent = Local<S["CalendarEventView"]>;
export const CALENDAR_DAYS = [1, 3, 7, 14, 31] as const;
export const IMPACTS = ["high", "medium", "low"] as const;

export function calendarPath(filter: { impacts: string[]; currencies: string[]; instrument?: string | null; days: number }): string {
  const q = new URLSearchParams();
  for (const i of filter.impacts) q.append("impact", i);
  for (const c of filter.currencies) q.append("currency", c);
  if (filter.instrument) q.set("instrument", filter.instrument);
  q.set("days", String(filter.days));
  return `/api/markets/calendar?${q.toString()}`;
}

/** "in 2h 05m", "due", "3h ago": display of the time to an event (the status is the API's). */
export function untilWords(eventIso: string, now: number): string {
  const s = Math.round((Date.parse(eventIso) - now) / 1000);
  const abs = Math.abs(s);
  const h = Math.floor(abs / 3600);
  const m = Math.floor((abs % 3600) / 60);
  const d = Math.floor(h / 24);
  const text = d >= 2 ? `${d}d ${h % 24}h` : h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : abs >= 60 ? `${m}m` : `${abs}s`;
  if (abs < 30) return "due";
  return s > 0 ? `in ${text}` : `${text} ago`;
}

export type PulseView = Local<S["PulseView"]>;
export const pulsePath = (window: string) => `/api/markets/pulse?window=${window}`;

export type AlertRulesView = Local<S["AlertRulesView"]>;
export type PriceRule = Local<S["PriceRuleView"]>;
export type NotificationsView = Local<S["NotificationsView"]>;
export type AlertNotification = Local<S["NotificationView"]>;
export type RuleWrite = S["RuleWrite"];

export const ALERT_PATHS = {
  rules: "/api/alerts/rules",
  rule: (id: string) => `/api/alerts/rules/${encodeURIComponent(id)}`,
  notifications: "/api/alerts/notifications",
  read: "/api/alerts/notifications/read",
} as const;

export function createRule(rule: RuleWrite): Promise<{ data: AlertRulesView }> {
  return apiFetch(ALERT_PATHS.rules, { method: "POST", body: JSON.stringify(rule) });
}

export function updateRule(id: string, baseVersion: number, rule: RuleWrite): Promise<{ data: AlertRulesView }> {
  return apiFetch(ALERT_PATHS.rule(id), { method: "PUT", body: JSON.stringify({ ...rule, base_version: baseVersion }) });
}

export function deleteRule(id: string, baseVersion: number): Promise<void> {
  return apiFetch(`${ALERT_PATHS.rule(id)}?base_version=${baseVersion}`, { method: "DELETE" });
}

export function markRead(throughSeq: number): Promise<{ data: NotificationsView }> {
  return apiFetch(ALERT_PATHS.read, { method: "POST", body: JSON.stringify({ through_seq: throughSeq }) });
}

export const QUOTE_WINDOWS = ["1h", "4h", "24h"] as const;
export type QuoteWindow = (typeof QUOTE_WINDOWS)[number];

export const MONITOR_PATHS = {
  watchlists: "/api/markets/watchlists",
  watchlist: (name: string) => `/api/markets/watchlists/${encodeURIComponent(name)}`,
  watchlistQuotes: (name: string, window: QuoteWindow) =>
    `/api/markets/watchlists/${encodeURIComponent(name)}/quotes?window=${window}`,
  quotes: (symbols: string[], window: QuoteWindow) =>
    `/api/markets/quotes?symbols=${encodeURIComponent(symbols.join(","))}&window=${window}`,
} as const;

export function saveWatchlist(
  name: string,
  baseVersion: number,
  symbols: string[],
  columnSet: string | null = null,
): Promise<Page<Watchlist>> {
  return apiFetch<Page<Watchlist>>(MONITOR_PATHS.watchlist(name), {
    method: "PUT",
    body: JSON.stringify({ base_version: baseVersion, symbols, column_set: columnSet }),
  });
}

export function deleteWatchlist(name: string, baseVersion: number): Promise<Page<Watchlist>> {
  return apiFetch<Page<Watchlist>>(`${MONITOR_PATHS.watchlist(name)}?base_version=${baseVersion}`, {
    method: "DELETE",
  });
}

export const DATA_STATE_WORD: Record<DataState, string> = {
  live: "LIVE",
  stale: "STALE",
  closed: "CLOSED",
  none: "NO DATA",
};
