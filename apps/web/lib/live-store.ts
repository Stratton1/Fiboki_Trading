"use client";

import { createStore, useStore } from "zustand";

/**
 * The live store: high-frequency, per-tab state fed by the stream.
 *
 * Holds what changes too often, or is too global, for the query cache: the
 * connection, the last heartbeat, per-topic confirmation times and marks.
 * Components subscribe with selectors, so a heartbeat re-renders the status
 * bar and nothing else.
 *
 * Writes are batched to one per animation frame (report E §6.3). A hidden tab
 * gets no animation frames, so it flushes on a short timer instead; nobody is
 * looking at it, but its state must be current the moment it is shown.
 */

export type ConnectionState =
  /** No stream started yet (first paint, or the sign-in page). */
  | "idle"
  | "connecting"
  | "live"
  /** Dropped, retrying with backoff; fewer than five consecutive failures. */
  | "reconnecting"
  /** Five or more consecutive failures: REST polling fallback, manual reconnect. */
  | "disconnected";

/** How this tab receives the stream. */
export type StreamRole = "none" | "leader" | "follower" | "solo";

export interface ConnectionInfo {
  state: ConnectionState;
  role: StreamRole;
  /** Consecutive failed attempts since the last successful open. */
  failures: number;
  /** Client time (ms) the next automatic attempt is due, when one is scheduled. */
  nextRetryAt: number | null;
}

export interface HeartbeatInfo {
  serverTime: string;
  /** Client time (ms) the heartbeat arrived. Ages count up from here. */
  receivedAt: number;
  /** Delivery lag: receipt time minus the server's send time, in ms. */
  lagMs: number;
  workerAgeS: number | null;
  /** The backend's stale threshold for the worker heartbeat, when supplied. */
  workerStaleAfterS: number | null;
  /**
   * The platform's own judgement of the worker (`ok`, `stale`, `absent`), from
   * the same reading as the age. The status bar's tone comes from this, never
   * from a client-side threshold.
   */
  workerState: string | null;
  mode: string;
  killSwitchActive: boolean;
}

export interface TopicInfo {
  /** Client time (ms) the stream last confirmed this topic is current. */
  confirmedAt: number | null;
  /** The platform's as_of for the topic's latest state. */
  asOf: string | null;
  /** A sequence gap was seen; the REST snapshot is being re-read. */
  resyncing: boolean;
  /** The stream is feeding this topic (false when it says the source is absent). */
  streamFed: boolean;
}

export interface LiveState {
  connection: ConnectionInfo;
  heartbeat: HeartbeatInfo | null;
  topics: Record<string, TopicInfo>;
  /** Per-symbol marks as the stream sent them (each value is a Figure payload). */
  marks: Record<string, unknown>;
  /**
   * The worker heartbeat from the last REST health report, for when the stream
   * is not connected. `ageS: null` means the platform says it never beat.
   * `state` is the report's own `worker_heartbeat` check status (`ok`,
   * `degraded`, `down`), or null when the report carries no such check.
   */
  restWorker: { ageS: number | null; receivedAt: number; state: string | null } | null;
}

export const EMPTY_TOPIC: TopicInfo = {
  confirmedAt: null,
  asOf: null,
  resyncing: false,
  streamFed: false,
};

function initial(): LiveState {
  return {
    connection: { state: "idle", role: "none", failures: 0, nextRetryAt: null },
    heartbeat: null,
    topics: {},
    marks: {},
    restWorker: null,
  };
}

export const liveStore = createStore<LiveState>()(() => initial());

export function useLive<T>(selector: (state: LiveState) => T): T {
  return useStore(liveStore, selector);
}

type Updater = (state: LiveState) => Partial<LiveState>;

let pending: Updater[] = [];
let scheduled = false;

function flush() {
  scheduled = false;
  const batch = pending;
  pending = [];
  if (batch.length === 0) return;
  liveStore.setState((state) => {
    let next = state;
    for (const update of batch) next = { ...next, ...update(next) };
    return next;
  });
}

/** Queue a write; all writes queued in one frame land in one store update. */
export function writeLive(update: Updater) {
  pending.push(update);
  if (scheduled) return;
  scheduled = true;
  const hidden = typeof document !== "undefined" && document.visibilityState === "hidden";
  if (!hidden && typeof requestAnimationFrame === "function") requestAnimationFrame(flush);
  else setTimeout(flush, 100);
}

/** Apply every queued write now (connection changes that gate mutations). */
export function flushLive() {
  flush();
}

export function patchTopic(topic: string, patch: Partial<TopicInfo>) {
  writeLive((state) => ({
    topics: {
      ...state.topics,
      [topic]: { ...(state.topics[topic] ?? EMPTY_TOPIC), ...patch },
    },
  }));
}

/**
 * Connection changes are rare and gate what the operator may do (mutations
 * are disabled while disconnected), so they apply at once, with anything
 * queued before them, rather than waiting for a frame.
 */
export function patchConnection(patch: Partial<ConnectionInfo>) {
  writeLive((state) => ({ connection: { ...state.connection, ...patch } }));
  flush();
}

/** For tests of the store itself and for sign-out. */
export function resetLive() {
  pending = [];
  liveStore.setState(initial(), true);
}
