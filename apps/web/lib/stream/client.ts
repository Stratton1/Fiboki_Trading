"use client";

import type { QueryClient } from "@tanstack/react-query";
import { API_BASE } from "../api";
import { ME_PATH } from "../auth";
import { noteAsOf } from "../as-of";
import { touchClock } from "../clock";
import {
  patchConnection,
  patchTopic,
  writeLive,
  type ConnectionInfo,
  type ConnectionState,
} from "../live-store";
import { apiKey, refetchPath } from "../query";
import {
  backoffDelay,
  DROP_GRACE_MS,
  FAILURES_BEFORE_DISCONNECTED,
  uniformRandom,
} from "./backoff";
import { STREAM_KINDS } from "./contract";
import { countTestHook, type StreamControl } from "./control";
import { StreamRouter, type RouterSink, type StreamFrame } from "./router";
import { samePathname, SUBSCRIBED_TOPICS } from "./topics";

/**
 * The SSE client (report E §6.3), loaded after first paint.
 *
 * ONE EventSource per browser: browsers allow six HTTP/1.1 connections per
 * origin across all tabs, so a stream per tab would starve both the stream and
 * ordinary fetches once an operator has several tabs open. The tab holding
 * the Web Lock "fiboki-stream" is the leader and owns the connection; it
 * forwards every raw frame over the BroadcastChannel "fiboki-stream", and each
 * tab (leader included) runs its own StreamRouter into its own query cache.
 * When the leader closes, the lock passes to a follower, which connects.
 * Without Web Locks or BroadcastChannel each tab connects on its own.
 *
 * Reconnection: a stream that drops after working is first left to the
 * browser's own retry (which honours the server's `retry:` hint and sends
 * Last-Event-ID, so the server replays the gap). If that fails, or the stream
 * never opened, the connection is closed and retried after 1 -> 2 -> 4 -> 8 ->
 * 15 s with jitter. A generation counter stops a superseded EventSource from
 * writing anything. After five consecutive failures the state is
 * DISCONNECTED: the banner offers Reconnect, stream-backed reads poll REST
 * every 10 s, and attempts continue at the 15 s cap.
 */

const CHANNEL = "fiboki-stream";
const LOCK = "fiboki-stream";

type ChannelMessage =
  | { type: "hello" }
  | { type: "reconnect" }
  | { type: "reset" }
  | { type: "frame"; frame: StreamFrame }
  | { type: "status"; info: Omit<ConnectionInfo, "role"> };

export function streamUrl(): string {
  return `${API_BASE}/api/stream?topics=${SUBSCRIBED_TOPICS.join(",")}`;
}

/** Count router outcomes for the Playwright suite (no-op unless it opted in). */
function counted(result: string) {
  if (result === "gap") countTestHook("streamGaps");
  else if (result === "duplicate") countTestHook("streamDuplicates");
  else if (result === "stale") countTestHook("streamStale");
}

function makeSink(client: QueryClient): RouterSink {
  return {
    now: () => {
      touchClock();
      return Date.now();
    },
    getQueryData: (path) => client.getQueryData(apiKey(path)),
    setQueryData: (path, value) => client.setQueryData(apiKey(path), value),
    invalidate: (path) => refetchPath(client, path),
    invalidateFiltered: (path) => {
      void client.invalidateQueries({
        predicate: (query) => {
          const key = String(query.queryKey[1]);
          return query.queryKey[0] === "api" && key !== path && samePathname(key, path);
        },
      });
    },
    patchTopic,
    setHeartbeat: (update) => writeLive(() => ({ heartbeat: update })),
    updateMarks: (update) => writeLive((state) => ({ marks: update(state.marks) })),
    noteAsOf,
  };
}

/** One EventSource and its retry policy. */
class Connection {
  private generation = 0;
  private source: EventSource | null = null;
  private failures = 0;
  private opened = false;
  private state: ConnectionState = "idle";
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private graceTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly url: string,
    private readonly onFrame: (frame: StreamFrame) => void,
    private readonly onStatus: (info: Omit<ConnectionInfo, "role">) => void,
    private readonly onNewConnection: () => void,
    /** The stream was refused (an EventSource cannot say why: it may be a 401). */
    private readonly onRefused: () => void = () => undefined,
  ) {}

  connect() {
    this.clearTimers();
    this.source?.close();
    const generation = ++this.generation;
    this.opened = false;
    this.onNewConnection();
    this.report(
      this.failures === 0
        ? "connecting"
        : this.failures >= FAILURES_BEFORE_DISCONNECTED
          ? "disconnected"
          : "reconnecting",
      null,
    );

    countTestHook("streamConnections");
    const source = new EventSource(this.url, { withCredentials: true });
    this.source = source;
    const current = () => generation === this.generation;

    source.onopen = () => {
      if (!current()) return;
      this.failures = 0;
      this.opened = true;
      this.clearGrace();
      this.report("live", null);
    };

    source.onerror = () => {
      if (!current()) return;
      if (source.readyState === EventSource.CONNECTING && this.opened) {
        // It worked, then dropped: let the browser's own retry try first.
        this.opened = false;
        this.clearGrace();
        this.graceTimer = setTimeout(() => {
          if (current() && this.state === "live") this.report("reconnecting", null);
        }, DROP_GRACE_MS);
        return;
      }
      // CLOSED before we close it: the browser refused the response (a
      // non-200 status or wrong type), rather than losing the network.
      const refused = source.readyState === EventSource.CLOSED;
      source.close();
      if (refused) this.onRefused();
      this.fail(generation);
    };

    const forward = (event: MessageEvent<string>) => {
      if (!current()) return;
      this.onFrame({ id: event.lastEventId, event: event.type, data: event.data });
    };
    source.onmessage = forward;
    for (const topic of [...SUBSCRIBED_TOPICS, "heartbeat"]) {
      for (const kind of STREAM_KINDS) {
        source.addEventListener(`${topic}.${kind}`, forward as EventListener);
      }
    }
  }

  /** Manual reconnect: the backoff starts again from the beginning. */
  reconnectNow() {
    this.failures = 0;
    this.connect();
  }

  stop() {
    this.generation += 1;
    this.clearTimers();
    this.source?.close();
    this.source = null;
  }

  private fail(generation: number) {
    this.failures += 1;
    this.clearGrace();
    const delay = backoffDelay(this.failures, uniformRandom());
    const state: ConnectionState =
      this.failures >= FAILURES_BEFORE_DISCONNECTED ? "disconnected" : "reconnecting";
    this.report(state, Date.now() + delay);
    this.retryTimer = setTimeout(() => {
      if (generation === this.generation) this.connect();
    }, delay);
  }

  private report(state: ConnectionState, nextRetryAt: number | null) {
    this.state = state;
    this.onStatus({ state, failures: this.failures, nextRetryAt });
  }

  private clearGrace() {
    if (this.graceTimer !== null) clearTimeout(this.graceTimer);
    this.graceTimer = null;
  }

  private clearTimers() {
    this.clearGrace();
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }
}

/** Start the stream for this tab. Returns its controls; `stop()` on unmount. */
export function startStream(client: QueryClient): StreamControl {
  const router = new StreamRouter(makeSink(client));
  // The backend ends the stream when the session is revoked or expires, and
  // refuses it with a 401 after that, which an EventSource cannot report. Ask
  // "who am I" again: its 401 sends the operator to sign in (lib/api.ts).
  const onRefused = () => void client.refetchQueries({ queryKey: apiKey(ME_PATH) });
  const url = streamUrl();
  const locks =
    typeof navigator !== "undefined" && "locks" in navigator ? navigator.locks : undefined;
  const canShare = locks !== undefined && typeof BroadcastChannel === "function";

  if (!canShare) {
    const connection = new Connection(
      url,
      (frame) => counted(router.handle(frame)),
      (info) => patchConnection({ ...info, role: "solo" }),
      () => router.reset(),
      onRefused,
    );
    connection.connect();
    return { reconnect: () => connection.reconnectNow(), stop: () => connection.stop() };
  }

  const channel = new BroadcastChannel(CHANNEL);
  let leader: Connection | null = null;
  let lastStatus: Omit<ConnectionInfo, "role"> | null = null;
  let release: (() => void) | null = null;
  let stopped = false;
  const abort = new AbortController();

  patchConnection({ role: "follower", state: "connecting", failures: 0, nextRetryAt: null });

  channel.onmessage = (message: MessageEvent<ChannelMessage>) => {
    const msg = message.data;
    if (leader !== null) {
      if (msg.type === "hello" && lastStatus) channel.postMessage({ type: "status", info: lastStatus });
      else if (msg.type === "reconnect") leader.reconnectNow();
      return;
    }
    if (msg.type === "frame") counted(router.handle(msg.frame));
    else if (msg.type === "reset") router.reset();
    else if (msg.type === "status") patchConnection({ ...msg.info, role: "follower" });
  };
  channel.postMessage({ type: "hello" } satisfies ChannelMessage);

  void locks
    .request(LOCK, { signal: abort.signal }, () => {
      if (stopped) return undefined;
      countTestHook("streamLeaderships");
      router.reset();
      leader = new Connection(
        url,
        (frame) => {
          counted(router.handle(frame));
          channel.postMessage({ type: "frame", frame } satisfies ChannelMessage);
        },
        (info) => {
          lastStatus = info;
          patchConnection({ ...info, role: "leader" });
          channel.postMessage({ type: "status", info } satisfies ChannelMessage);
        },
        () => {
          router.reset();
          channel.postMessage({ type: "reset" } satisfies ChannelMessage);
        },
        onRefused,
      );
      leader.connect();
      // Hold the lock for the life of this tab (released by stop() or unload).
      return new Promise<void>((resolve) => {
        release = resolve;
      });
    })
    .catch(() => undefined);

  return {
    reconnect: () => {
      if (leader !== null) leader.reconnectNow();
      else channel.postMessage({ type: "reconnect" } satisfies ChannelMessage);
    },
    stop: () => {
      stopped = true;
      abort.abort();
      leader?.stop();
      leader = null;
      release?.();
      channel.close();
    },
  };
}
