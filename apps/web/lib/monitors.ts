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
