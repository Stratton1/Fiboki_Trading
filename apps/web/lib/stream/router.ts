import {
  figureValue,
  HEARTBEAT_MS,
  isHeartbeatData,
  isStreamEnvelope,
  type HeartbeatData,
  type StreamEnvelope,
} from "./contract";
import { DERIVED_PATHS, HIGH_FREQUENCY_TOPICS, topicSpec, type TopicSpec } from "./topics";

/**
 * StreamRouter: every frame from the stream goes through here, in every tab
 * (the leader's own frames and the ones followers receive over the
 * BroadcastChannel). It holds no React and no network, so it is tested
 * directly (tests/e2e/stream-router.spec.ts).
 *
 *  1. Dedupe by (event, id) in a 1,000-entry LRU (a replay after reconnect
 *     re-sends events already applied; a snapshot reuses the id of the last
 *     delta folded into it, so the id alone is not a key).
 *  2. Per-topic sequence check. A snapshot resets the topic. A delta whose
 *     seq is not last+1 is NOT applied: the topic is marked resyncing and its
 *     REST snapshot re-read. A heartbeat whose topics[t].seq is ahead of the
 *     last applied seq is a gap too (events were lost in transit).
 *  3. Low-frequency topics update the query cache entry of their REST path
 *     (applyDelta). `marks` goes to the live store.
 *
 * It never invents a value or an order: a delta that would insert a row it has
 * no position for triggers a REST re-read instead of an append.
 */

export interface StreamFrame {
  /** SSE `id:` (the EventSource's lastEventId). */
  id: string;
  /** SSE `event:` name. */
  event: string;
  /** SSE `data:` (JSON text). */
  data: string;
}

export interface HeartbeatUpdate {
  serverTime: string;
  receivedAt: number;
  lagMs: number;
  workerAgeS: number | null;
  /** The backend's worker stale threshold, when the heartbeat carries it. */
  workerStaleAfterS: number | null;
  mode: string;
  killSwitchActive: boolean;
}

export interface TopicPatch {
  confirmedAt?: number | null;
  asOf?: string | null;
  resyncing?: boolean;
  streamFed?: boolean;
}

/** Everything the router may touch, injected so it can run without a browser. */
export interface RouterSink {
  now(): number;
  getQueryData(path: string): unknown;
  setQueryData(path: string, value: unknown): void;
  /**
   * Re-read a resource by REST (all its filtered variants). Resolves when
   * settled: true if every re-read succeeded.
   */
  invalidate(path: string): Promise<boolean>;
  /** Re-read only the filtered variants (query strings) of a resource. */
  invalidateFiltered(path: string): void;
  patchTopic(topic: string, patch: TopicPatch): void;
  setHeartbeat(update: HeartbeatUpdate): void;
  updateMarks(update: (previous: Record<string, unknown>) => Record<string, unknown>): void;
  noteAsOf(iso: string): void;
}

export type RouteResult = "applied" | "duplicate" | "stale" | "gap" | "ignored" | "invalid";

export const DEDUPE_CAPACITY = 1_000;

/** A set that forgets its oldest entry beyond `capacity`. */
export class LruSet {
  private readonly items = new Map<string, true>();
  constructor(private readonly capacity: number) {}

  has(key: string): boolean {
    return this.items.has(key);
  }

  add(key: string) {
    this.items.delete(key);
    this.items.set(key, true);
    if (this.items.size > this.capacity) {
      const oldest = this.items.keys().next().value;
      if (oldest !== undefined) this.items.delete(oldest);
    }
  }

  get size(): number {
    return this.items.size;
  }

  clear() {
    this.items.clear();
  }
}

type Row = Record<string, unknown>;
interface PageLike {
  items: Row[];
  total: number;
  source?: unknown;
}
interface EnvelopeLike {
  data: Row;
  source?: unknown;
}

function isRecord(value: unknown): value is Row {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Same JSON kind (null is compatible with anything: nullable fields). */
function compatible(a: unknown, b: unknown): boolean {
  if (a === null || b === null || a === undefined) return true;
  if (typeof a !== typeof b) return false;
  if (typeof a === "object") return Array.isArray(a) === Array.isArray(b);
  return true;
}

/**
 * Merge a stream entity into the REST payload it mirrors. Only fields the
 * REST shape has, and only with the same JSON kind: a field the stream sends
 * in another shape (the kill switch's `open_positions` is a Figure on the
 * stream and an integer over REST) is NOT written; it is reported so the
 * caller re-reads REST, which stays the authority on its own shape.
 */
export function mergeCompatible(
  previous: Row,
  entity: Row,
  asOf: string | null,
): { merged: Row; mismatched: string[] } {
  const merged: Row = { ...previous };
  const mismatched: string[] = [];
  for (const [key, value] of Object.entries(entity)) {
    if (!(key in previous)) continue;
    if (compatible(previous[key], value)) merged[key] = value;
    else mismatched.push(key);
  }
  // The backend strips the banner's own as_of from the stream entity; the
  // event's as_of is when the stream read it.
  if ("as_of" in previous && !("as_of" in entity) && asOf) merged.as_of = asOf;
  return { merged, mismatched };
}

/**
 * The outcome of applying one event to a cached REST payload:
 *  - `value`: the new payload to store (absent: store nothing);
 *  - `refetch`: re-read REST, because the event could not be applied honestly
 *    (no base; a field in another shape; a new row whose position only the
 *    server knows).
 */
export interface ApplyOutcome {
  value?: unknown;
  refetch: boolean;
}

const REFETCH: ApplyOutcome = { refetch: true };
const NOTHING: ApplyOutcome = { refetch: false };

/** Pure: apply a snapshot, delta or tombstone to a cached payload. */
export function applyDelta(spec: TopicSpec, previous: unknown, env: StreamEnvelope): ApplyOutcome {
  const data = env.data;
  if (!isRecord(data)) return REFETCH;
  return spec.shape === "page"
    ? applyToPage(spec, previous, env.kind, data, env.source)
    : applyToObject(spec, previous, env, data);
}

function applyToObject(
  spec: TopicSpec,
  previous: unknown,
  env: StreamEnvelope,
  data: Row,
): ApplyOutcome {
  const id = spec.entityId;
  if (!id) return REFETCH;
  let entity: unknown;
  if (env.kind === "snapshot") {
    if (!isRecord(data.entities)) return REFETCH;
    entity = data.entities[id];
    if (entity === undefined) return REFETCH;
  } else if (env.kind === "delta") {
    if (data.id !== id) return NOTHING; // another entity on this topic (see `related`)
    entity = data.entity;
  } else if (env.kind === "tombstone") {
    return data.id === id ? REFETCH : NOTHING;
  } else {
    return NOTHING;
  }
  if (!isRecord(entity)) return REFETCH;

  if (spec.shape === "bare") {
    // The entity IS the REST body (the health report).
    return { value: entity, refetch: false };
  }
  const prev = previous as EnvelopeLike | undefined;
  if (!isRecord(prev) || !isRecord(prev.data)) return REFETCH;
  const { merged, mismatched } = mergeCompatible(prev.data, entity, env.as_of);
  return {
    value: { ...prev, data: merged, source: env.source },
    refetch: mismatched.length > 0,
  };
}

function applyToPage(
  spec: TopicSpec,
  previous: unknown,
  kind: StreamEnvelope["kind"],
  data: Row,
  source: unknown,
): ApplyOutcome {
  const key = spec.key;
  const prev = previous as PageLike | undefined;
  if (!key || !isRecord(prev) || !Array.isArray(prev.items)) return REFETCH;
  const idOf = (row: Row) => String(row[key]);

  if (kind === "snapshot") {
    if (!isRecord(data.entities)) return REFETCH;
    const entities = data.entities;
    const ids = Object.keys(entities);
    // Only when the set of rows is unchanged can the snapshot be laid onto
    // the REST order; otherwise the server knows the order (and the page).
    const same =
      ids.length === prev.items.length && prev.items.every((row) => idOf(row) in entities);
    if (!same) return REFETCH;
    const items: Row[] = [];
    for (const row of prev.items) {
      const next = entities[idOf(row)];
      if (!isRecord(next)) return REFETCH;
      items.push(next);
    }
    return { value: { ...prev, items, source }, refetch: false };
  }

  if (kind === "delta") {
    const id = typeof data.id === "string" ? data.id : String(data.id);
    if (!isRecord(data.entity)) return REFETCH;
    const index = prev.items.findIndex((row) => idOf(row) === id);
    // A new entity: only the server knows where it belongs in this list.
    if (index === -1) return REFETCH;
    const items = [...prev.items];
    items[index] = data.entity;
    return { value: { ...prev, items, source }, refetch: false };
  }

  if (kind === "tombstone") {
    const id = String(data.id);
    const items = prev.items.filter((row) => idOf(row) !== id);
    if (items.length === prev.items.length) return NOTHING;
    return {
      value: { ...prev, items, total: prev.total - (prev.items.length - items.length), source },
      refetch: false,
    };
  }
  return NOTHING;
}

export class StreamRouter {
  private readonly seen = new LruSet(DEDUPE_CAPACITY);
  /** Last applied seq per topic on the current connection; absent: none yet. */
  private readonly lastSeq = new Map<string, number>();

  constructor(private readonly sink: RouterSink) {}

  /**
   * A new connection: the server sends a fresh snapshot per topic, and its
   * sequence base may differ (it restarts from epoch milliseconds), so
   * nothing held carries over.
   */
  reset() {
    this.lastSeq.clear();
    this.seen.clear();
  }

  handle(frame: StreamFrame): RouteResult {
    // A snapshot carries the id of the last delta folded into it, so the
    // dedupe key is (event, id), not the id alone.
    if (frame.id) {
      const key = `${frame.event}|${frame.id}`;
      if (this.seen.has(key)) return "duplicate";
      this.seen.add(key);
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(frame.data);
    } catch {
      return "invalid";
    }
    if (!isStreamEnvelope(parsed)) return "invalid";
    const env = parsed;
    if (typeof env.as_of === "string" && env.as_of) this.sink.noteAsOf(env.as_of);

    if (env.kind === "heartbeat") return this.heartbeat(env);

    const highFrequency = (HIGH_FREQUENCY_TOPICS as readonly string[]).includes(env.topic);
    const spec = topicSpec(env.topic);
    if (!highFrequency && !spec) return "ignored";

    const last = this.lastSeq.get(env.topic);
    if (env.kind !== "snapshot") {
      if (last === undefined) {
        // A delta with no snapshot on this connection: its base is unknown.
        this.lastSeq.set(env.topic, env.seq);
        if (spec) this.resync(spec);
        return "gap";
      }
      if (env.seq <= last) return "stale";
      if (env.seq > last + 1) {
        this.lastSeq.set(env.topic, env.seq);
        if (spec) this.resync(spec);
        return "gap";
      }
    }
    this.lastSeq.set(env.topic, env.seq);

    if (env.source?.kind === "absent") {
      // The platform says this source does not exist here; REST says so too.
      this.sink.patchTopic(env.topic, { streamFed: false, asOf: env.as_of ?? null });
      return "applied";
    }

    if (highFrequency) return this.marks(env);
    if (!spec) return "ignored";

    const outcome = applyDelta(spec, this.sink.getQueryData(spec.path), env);
    if (outcome.value !== undefined) {
      this.sink.setQueryData(spec.path, outcome.value);
      this.sink.invalidateFiltered(spec.path);
    }
    if (outcome.refetch) void this.sink.invalidate(spec.path);
    const id = isRecord(env.data) && typeof env.data.id === "string" ? env.data.id : null;
    if (id !== null && env.kind !== "snapshot") {
      for (const related of spec.related ?? []) {
        if (id.startsWith(related.prefix)) void this.sink.invalidate(related.path);
      }
    }
    this.sink.patchTopic(env.topic, {
      streamFed: true,
      confirmedAt: this.sink.now(),
      asOf: env.as_of ?? null,
      resyncing: false,
    });
    for (const derived of DERIVED_PATHS[env.topic] ?? []) void this.sink.invalidate(derived);
    return "applied";
  }

  private resync(spec: TopicSpec) {
    this.sink.patchTopic(spec.topic, { resyncing: true });
    void this.sink.invalidate(spec.path).then((ok) => {
      // Confirmed only if the REST snapshot was actually read; otherwise the
      // view keeps its failed re-read (stale) until the stream or a retry
      // brings current data.
      this.sink.patchTopic(
        spec.topic,
        ok ? { resyncing: false, confirmedAt: this.sink.now() } : { resyncing: false },
      );
    });
  }

  private marks(env: StreamEnvelope): RouteResult {
    const data = env.data;
    if (!isRecord(data)) return "invalid";
    if (env.kind === "snapshot") {
      if (!isRecord(data.entities)) return "invalid";
      const entities = data.entities;
      this.sink.updateMarks(() => ({ ...entities }));
    } else if (env.kind === "delta" && typeof data.id === "string") {
      const id = data.id;
      this.sink.updateMarks((prev) => ({ ...prev, [id]: data.entity }));
    } else if (env.kind === "tombstone" && typeof data.id === "string") {
      const id = data.id;
      this.sink.updateMarks((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
    this.sink.patchTopic(env.topic, {
      streamFed: true,
      confirmedAt: this.sink.now(),
      asOf: env.as_of ?? null,
    });
    return "applied";
  }

  private heartbeat(env: StreamEnvelope): RouteResult {
    if (!isHeartbeatData(env.data)) return "invalid";
    const hb: HeartbeatData = env.data;
    const now = this.sink.now();
    const sent = Date.parse(hb.server_time);
    this.sink.setHeartbeat({
      serverTime: hb.server_time,
      receivedAt: now,
      lagMs: Number.isNaN(sent) ? 0 : Math.max(0, now - sent),
      workerAgeS: figureValue(hb.worker_heartbeat_age_s),
      workerStaleAfterS: figureValue(hb.worker_stale_after_s),
      mode: hb.mode,
      killSwitchActive: hb.kill_switch?.active === true,
    });

    for (const [topic, info] of Object.entries(hb.topics)) {
      if (!info || typeof info.seq !== "number") continue;
      if (info.source_kind === "absent") {
        this.sink.patchTopic(topic, { streamFed: false });
        continue;
      }
      const last = this.lastSeq.get(topic);
      if (last === undefined) continue;
      const spec = topicSpec(topic);
      if (info.seq > last) {
        // Events were lost between the last one applied and this heartbeat.
        this.lastSeq.set(topic, info.seq);
        if (spec) this.resync(spec);
        continue;
      }
      if (info.seq === last) {
        this.sink.patchTopic(topic, { confirmedAt: now, asOf: info.as_of ?? null });
      }
    }

    this.reconcile(hb);
    return "applied";
  }

  /**
   * The heartbeat carries mode and kill-switch state every 5 s. If the cached
   * REST payloads disagree (an event was missed, or the topic is not streamed),
   * re-read them: another operator's arm is visible within one heartbeat.
   */
  private reconcile(hb: HeartbeatData) {
    const active = hb.kill_switch?.active;
    const mode = this.sink.getQueryData("/api/system/execution-mode") as EnvelopeLike | undefined;
    if (
      isRecord(mode) &&
      isRecord(mode.data) &&
      (mode.data.mode !== hb.mode ||
        (typeof active === "boolean" && mode.data.kill_switch_active !== active))
    ) {
      void this.sink.invalidate("/api/system/execution-mode");
    }
    const ks = this.sink.getQueryData("/api/system/kill-switch") as EnvelopeLike | undefined;
    if (typeof active === "boolean" && isRecord(ks) && isRecord(ks.data) && ks.data.active !== active) {
      void this.sink.invalidate("/api/system/kill-switch");
    }
  }
}

/** Exposed for the status bar: the heartbeat is overdue after this long. */
export const HEARTBEAT_OVERDUE_MS = HEARTBEAT_MS * 2;
