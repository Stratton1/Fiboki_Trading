/**
 * Which stream topic feeds which REST read, and how fresh each must be.
 *
 * Low-frequency topics update the TanStack Query cache entry of their REST
 * endpoint (the stream is a faster way of receiving the same payload). The
 * high-frequency topic (`marks`) goes to the Zustand live store instead, so a
 * tick re-renders one cell, not a page.
 *
 * Two kinds of topic, matching the backend's entity model:
 *  - `object`: one entity (`entityId`) is the whole REST payload's `data`
 *    (or, for a `bare` payload, the payload itself);
 *  - `collection`: each entity is one row of a REST Page, keyed by `key`.
 *
 * `cadenceMs` is the expected interval between confirmations (report E §6.4):
 * lagging after 1.5x, stale after 3x. A topic is confirmed by an event on it or
 * by a heartbeat whose `topics[t].seq` equals the last sequence applied here.
 *
 * `workerDependent`: the data is produced by the worker, so a stale worker
 * heartbeat makes it stale however healthy the stream is. Mode, kill switch,
 * health and incidents are produced by the API itself and stay live while the
 * API does; the status bar shows the dead worker.
 */

export type TopicShape = "envelope" | "bare" | "page";

export interface TopicSpec {
  topic: string;
  path: string;
  shape: TopicShape;
  /** `object` topics: the entity that is the payload. */
  entityId?: string;
  /** `collection` topics: the row field that is the entity id. */
  key?: string;
  /** Other entity ids on this topic feed these REST paths (re-read on change). */
  related?: { prefix: string; path: string }[];
  cadenceMs: number;
  workerDependent: boolean;
}

export const LOW_FREQUENCY_TOPICS: readonly TopicSpec[] = [
  {
    topic: "mode",
    path: "/api/system/execution-mode",
    shape: "envelope",
    entityId: "mode",
    cadenceMs: 5_000,
    workerDependent: false,
  },
  {
    topic: "killswitch",
    path: "/api/system/kill-switch",
    shape: "envelope",
    entityId: "kill_switch",
    cadenceMs: 5_000,
    workerDependent: false,
  },
  {
    topic: "health",
    path: "/api/health",
    shape: "bare",
    entityId: "health",
    cadenceMs: 20_000,
    workerDependent: false,
  },
  {
    topic: "risk",
    path: "/api/trading/risk",
    shape: "envelope",
    entityId: "state",
    related: [{ prefix: "exposure:", path: "/api/trading/exposure" }],
    cadenceMs: 5_000,
    workerDependent: true,
  },
  {
    topic: "positions",
    path: "/api/trading/positions",
    shape: "page",
    key: "position_id",
    cadenceMs: 5_000,
    workerDependent: true,
  },
  {
    topic: "incidents",
    path: "/api/system/incidents",
    shape: "page",
    key: "id",
    cadenceMs: 30_000,
    workerDependent: false,
  },
];

export const HIGH_FREQUENCY_TOPICS = ["marks"] as const;

/** Every topic the workstation subscribes to, in one leader connection. */
export const SUBSCRIBED_TOPICS = [
  ...LOW_FREQUENCY_TOPICS.map((t) => t.topic),
  ...HIGH_FREQUENCY_TOPICS,
];

/** Queries re-read whenever a topic changes, because the server derives them from it. */
export const DERIVED_PATHS: Record<string, string[]> = {
  incidents: ["/api/command/attention"],
  killswitch: ["/api/command/attention"],
  risk: ["/api/command/attention"],
};

const BY_TOPIC = new Map(LOW_FREQUENCY_TOPICS.map((t) => [t.topic, t]));
const BY_PATH = new Map(LOW_FREQUENCY_TOPICS.map((t) => [t.path, t]));

export function topicSpec(topic: string): TopicSpec | undefined {
  return BY_TOPIC.get(topic);
}

function pathname(path: string): string {
  const q = path.indexOf("?");
  return q === -1 ? path : path.slice(0, q);
}

/**
 * The topic whose stream carries this REST path. Only the exact, unfiltered
 * path is fed by events; a filtered read of the same resource (a query
 * string) is re-read instead, because an entity may not match its filter.
 */
export function topicForPath(path: string): { spec: TopicSpec; exact: boolean } | undefined {
  const spec = BY_PATH.get(pathname(path));
  return spec ? { spec, exact: spec.path === path } : undefined;
}

export function samePathname(a: string, b: string): boolean {
  return pathname(a) === pathname(b);
}
