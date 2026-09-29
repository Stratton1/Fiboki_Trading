"use client";

import type { QueryClient } from "@tanstack/react-query";
import { liveStore } from "./live-store";
import { apiKey, invalidatePath } from "./query";
import { topicForPath } from "./stream/topics";

/**
 * No optimistic UI for kill switch, promote-like or pause actions (report E
 * §6.3). After the POST returns, the dialog stays busy until the platform
 * ECHOES the change: the stream event lands in the query cache, or, when the
 * stream is not feeding this resource, a REST re-read shows it. The POST's own
 * response is not the echo; the echo is the shared state every other operator
 * sees, which is the point: Tom's arm is on Joe's screen within a heartbeat,
 * and Joe's own screen shows what Tom's shows.
 *
 * Resolves true when `confirmed(cached)` holds, or false after `timeoutMs`
 * (the caller then closes the dialog and shows "confirming…" until it holds).
 */

export const ECHO_TIMEOUT_MS = 5_000;
const REST_ECHO_EVERY_MS = 1_000;

function streamFeeds(path: string): boolean {
  const route = topicForPath(path);
  if (!route) return false;
  const state = liveStore.getState();
  return state.connection.state === "live" && state.topics[route.spec.topic]?.streamFed === true;
}

export function awaitEcho<T>(
  client: QueryClient,
  path: string,
  confirmed: (data: T) => boolean,
  timeoutMs = ECHO_TIMEOUT_MS,
): Promise<boolean> {
  const holds = () => {
    const data = client.getQueryData<T>(apiKey(path));
    return data !== undefined && confirmed(data);
  };
  if (holds()) return Promise.resolve(true);

  return new Promise<boolean>((resolve) => {
    let settled = false;
    const restEcho = () => {
      if (!streamFeeds(path)) void invalidatePath(client, path);
    };
    const unsubscribe = client.getQueryCache().subscribe((event) => {
      if (event.query.queryKey[0] === "api" && event.query.queryKey[1] === path && holds()) {
        finish(true);
      }
    });
    const poller = setInterval(restEcho, REST_ECHO_EVERY_MS);
    const timer = setTimeout(() => finish(false), timeoutMs);
    function finish(ok: boolean) {
      if (settled) return;
      settled = true;
      unsubscribe();
      clearInterval(poller);
      clearTimeout(timer);
      resolve(ok);
    }
    restEcho();
  });
}
