"use client";

import { QueryClient, useQuery, type QueryClient as Client } from "@tanstack/react-query";
import { useCallback, useEffect, useId, useSyncExternalStore } from "react";
import { ApiError, apiFetch } from "./api";
import { payloadAsOf, registerAsOf, unregisterAsOf } from "./as-of";
import { clockNow, subscribeClock } from "./clock";
import { computeFreshness, type Freshness } from "./freshness";
import { EMPTY_TOPIC, liveStore, useLive, type LiveState } from "./live-store";
import { samePathname, topicForPath } from "./stream/topics";

export type { Freshness } from "./freshness";

/**
 * Server state (report E §3.3): TanStack Query under one shape for every read.
 *
 *   loading  first load only
 *   error    no data was ever received for this resource
 *   success  data, plus how current it is (freshness), when it was last known
 *            good (asOf) and why it may be old (refreshError)
 *
 * The invariant the V1 poll-blanking bug broke: once a resource has data, its
 * view never returns to loading or error. A background refetch keeps the data
 * and changes the freshness; a failed refetch keeps the data and sets
 * refreshError. Only a different resource (a different path) starts over.
 *
 * `apiFetch` stays the only way to reach the API: credentials: "include",
 * the double-submit CSRF header on every mutation, typed errors.
 */

export type ViewState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "error"; data: null; error: ApiError }
  | {
      status: "success";
      data: T;
      error: null;
      freshness: Freshness;
      /** When this data was last known good (ISO, UTC): received or confirmed by the stream. */
      asOf: string;
      /** The platform's own as_of for the stream topic, when the stream feeds this view. */
      sourceAsOf: string | null;
      /** A refetch is in flight; `data` is the last good payload. */
      refreshing: boolean;
      /** The latest refresh failed; `data` is last-known-good, NOT current. */
      refreshError: ApiError | null;
      /** A stream sequence gap was detected; the REST snapshot is being re-read. */
      resyncing: boolean;
    };

/** @deprecated name kept for the components written against the Wave 0 hook. */
export type AsyncState<T> = ViewState<T>;

export type ApiHandle<T> = ViewState<T> & {
  reload: () => void;
  /** The poll interval in force right now (undefined: not polling). */
  refreshMs: number | undefined;
};

/** Every REST read is keyed by its path, so two views of one resource share one fetch. */
export function apiKey(path: string) {
  return ["api", path] as const;
}

/** The REST fallback interval while the stream is disconnected (report E §6.3). */
export const FALLBACK_POLL_MS = 10_000;

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Polling is the retry: a failed first load shows its error at once,
        // and a failed refresh keeps the last good data and says so.
        retry: false,
        refetchOnWindowFocus: false,
        staleTime: 2_000,
        gcTime: 5 * 60_000,
        queryFn: async ({ queryKey, signal }) => {
          const path = String(queryKey[1]);
          return apiFetch<unknown>(path, { signal });
        },
      },
    },
  });
}

function readsOf(path: string) {
  return (query: { queryKey: readonly unknown[] }) =>
    query.queryKey[0] === "api" && samePathname(String(query.queryKey[1]), path);
}

/** Refetch every read of a resource, filtered or not. Resolves when they settle. */
export function invalidatePath(client: Client, path: string): Promise<void> {
  return client.invalidateQueries({ predicate: readsOf(path) });
}

/**
 * Refetch every read of a resource and say whether they all succeeded; a
 * resync that failed must not be reported as confirmed.
 */
export async function refetchPath(client: Client, path: string): Promise<boolean> {
  await invalidatePath(client, path);
  return client
    .getQueryCache()
    .findAll({ predicate: readsOf(path) })
    .every((query) => query.state.status === "success");
}

/**
 * The worker heartbeat age right now, in seconds, counting up since the last
 * report. The stream heartbeat when connected, else the last REST health
 * report. undefined: nothing has reported. null: the worker never beat.
 */
export function workerAgeNow(state: LiveState, now: number): number | null | undefined {
  const hb = state.connection.state === "live" ? state.heartbeat : null;
  const source = hb
    ? { ageS: hb.workerAgeS, receivedAt: hb.receivedAt }
    : state.restWorker;
  if (!source) return undefined;
  if (source.ageS === null) return null;
  return source.ageS + Math.max(0, now - source.receivedAt) / 1_000;
}

/** The platform's worker stale threshold, when the live stream has supplied it. */
export function workerStaleAfter(state: LiveState): number | null {
  return state.connection.state === "live" && state.heartbeat ? state.heartbeat.workerStaleAfterS : null;
}

/**
 * The platform's own verdict on the worker: the stream heartbeat's
 * `worker_state` when connected, else the REST health report's
 * `worker_heartbeat` check status. Null when neither has said.
 */
export function workerStateNow(state: LiveState): string | null {
  if (state.connection.state === "live" && state.heartbeat) return state.heartbeat.workerState;
  return state.restWorker?.state ?? null;
}

/**
 * A maximum age the platform supplies with a payload (`source.max_age_s`),
 * in ms. The API does not send one yet (report G §2.1 item 4 asks for it);
 * when it does, it wins over the client's default.
 */
function serverMaxAgeMs(payload: unknown): number | undefined {
  if (payload === null || typeof payload !== "object") return undefined;
  const source = (payload as { source?: { max_age_s?: unknown } }).source;
  const value = source?.max_age_s;
  return typeof value === "number" && value > 0 ? value * 1_000 : undefined;
}

function subscribeLiveAndClock(listener: () => void) {
  const stopClock = subscribeClock(listener);
  const stopLive = liveStore.subscribe(listener);
  return () => {
    stopClock();
    stopLive();
  };
}

const LOADING = { status: "loading", data: null, error: null } as const;

/**
 * One server read, as a ViewState. `refreshMs` is the poll interval while
 * the stream is not feeding this resource; when it is, polling stops, and when
 * the stream is disconnected every stream-backed read polls at 10 s.
 */
export function useApi<T>(
  path: string | null,
  options: { refreshMs?: number; maxAgeMs?: number } = {},
): ApiHandle<T> {
  const route = path ? topicForPath(path) : undefined;
  const spec = route?.spec;
  const topicName = spec?.topic ?? null;
  const connection = useLive((s) => s.connection.state);
  const topic = useLive((s) => (topicName ? (s.topics[topicName] ?? EMPTY_TOPIC) : EMPTY_TOPIC));

  const streamFeeds =
    route !== undefined && route.exact && connection === "live" && topic.streamFed;
  // Polling policy for a stream-backed read:
  //   the stream feeds it          -> no polling (the stream is the refresh);
  //   stream disconnected          -> REST fallback at 10 s (or faster);
  //   otherwise (connecting,
  //   reconnecting, topic absent)  -> its own interval, or 10 s if it has none,
  //                                   so it ages honestly instead of reading
  //                                   "fresh" forever with nothing re-reading it.
  let refetchInterval: number | undefined = options.refreshMs;
  if (route !== undefined) {
    if (streamFeeds) refetchInterval = undefined;
    else if (connection === "disconnected")
      refetchInterval = Math.min(options.refreshMs ?? FALLBACK_POLL_MS, FALLBACK_POLL_MS);
    else refetchInterval = options.refreshMs ?? FALLBACK_POLL_MS;
  }

  const query = useQuery<unknown, ApiError, T>({
    queryKey: apiKey(path ?? ""),
    enabled: path !== null,
    refetchInterval: refetchInterval ?? false,
  });

  const hasData = path !== null && query.data !== undefined;
  const refreshFailing =
    hasData && query.status === "error" && query.errorUpdatedAt >= query.dataUpdatedAt;
  const confirmedAt = streamFeeds ? topic.confirmedAt : null;
  const knownGoodAt =
    confirmedAt === null ? query.dataUpdatedAt : Math.max(query.dataUpdatedAt, confirmedAt);
  const maxAgeMs = serverMaxAgeMs(query.data) ?? options.maxAgeMs;

  const getFreshness = useCallback((): Freshness => {
    const state = liveStore.getState();
    const now = clockNow();
    return computeFreshness({
      now: Math.max(now, knownGoodAt),
      connection: state.connection.state,
      streamFed: streamFeeds,
      confirmedAt,
      topicCadenceMs: spec ? spec.cadenceMs : null,
      receivedAt: query.dataUpdatedAt,
      refreshMs: refetchInterval,
      refreshFailing,
      workerDependent: spec ? spec.workerDependent : false,
      workerAgeS: workerAgeNow(state, now),
      workerStaleAfterS: workerStaleAfter(state),
      workerState: workerStateNow(state),
      maxAgeMs,
    });
  }, [
    maxAgeMs,
    knownGoodAt,
    streamFeeds,
    confirmedAt,
    spec,
    query.dataUpdatedAt,
    refetchInterval,
    refreshFailing,
  ]);

  const freshness = useSyncExternalStore(
    hasData ? subscribeLiveAndClock : subscribeNothing,
    getFreshness,
    () => "fresh" as Freshness,
  );

  const { refetch } = query;
  const reload = useCallback(() => {
    void refetch();
  }, [refetch]);

  // "Data as of" in the status bar is the oldest platform as-of ON SCREEN
  // (lib/as-of.ts): register this view's while it is mounted.
  const viewId = useId();
  const platformAsOf = hasData
    ? streamFeeds && topic.asOf
      ? topic.asOf
      : payloadAsOf(query.data)
    : undefined;
  useEffect(() => {
    if (path === null || platformAsOf === undefined) unregisterAsOf(viewId);
    else registerAsOf(viewId, path, platformAsOf);
  }, [viewId, path, platformAsOf]);
  useEffect(() => () => unregisterAsOf(viewId), [viewId]);

  let state: ViewState<T>;
  if (path === null) {
    state = LOADING;
  } else if (hasData) {
    state = {
      status: "success",
      data: query.data as T,
      error: null,
      freshness,
      asOf: new Date(knownGoodAt).toISOString(),
      sourceAsOf: streamFeeds ? topic.asOf : null,
      refreshing: query.isFetching,
      refreshError: refreshFailing ? toApiError(query.error) : null,
      resyncing: topic.resyncing && route !== undefined,
    };
  } else if (query.status === "error" && !query.isFetching) {
    state = { status: "error", data: null, error: toApiError(query.error) };
  } else {
    // Never succeeded: a retry of a failed first load is a fresh load.
    state = LOADING;
  }
  return { ...state, reload, refreshMs: refetchInterval };
}

function subscribeNothing() {
  return () => undefined;
}

export function toApiError(error: unknown): ApiError {
  return error instanceof ApiError
    ? error
    : new ApiError(0, { code: "unexpected_error" }, String(error));
}
