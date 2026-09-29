import type { BrowserContext, Page, Route } from "@playwright/test";
import { API, figure, healthReport, killSwitchView, modeBanner, source } from "./fixtures";

/**
 * A scripted GET /api/stream for Playwright (report E §6.5), emitting the
 * backend's wire format (src/fiboki/api/routers/stream.py):
 *
 *   id: <topic>:<seq>
 *   event: <topic>.<kind>
 *   data: {"topic","seq","kind","as_of","source","data"}
 *
 *   snapshot   data = {entities: {id: entity}, count: Figure}, current seq
 *   delta      data = {id, entity}
 *   tombstone  data = {id}
 *   heartbeat  worker_heartbeat_age_s and worker_stale_after_s as Figures
 *
 * `route.fulfill` cannot hold a response open, so each connection is served
 * one complete text/event-stream body and closes. The body carries a short
 * `retry:` hint, so the browser's EventSource reconnects on its own (sending
 * Last-Event-ID, exactly as it would after a real drop) and the next body
 * carries whatever the test has pushed since. A connection with no
 * Last-Event-ID is a new subscription and receives a snapshot of every topic
 * first, as the backend does.
 *
 * One MockStream can serve several browser contexts (Joe's and Tom's): each
 * install is a subscriber with its own queue, and every push reaches all.
 * `mirrorRest` serves the REST reads from the same state: one truth, two
 * transports, including the backend's known shape difference (the kill
 * switch's `open_positions` is a Figure on the stream and an int over REST).
 */

export type Kind = "snapshot" | "delta" | "tombstone" | "heartbeat";
type Entities = Record<string, Record<string, unknown>>;

interface Subscriber {
  queue: string[];
  wake: (() => void) | null;
  connections: number;
  lastEventIds: string[];
}

export interface PushOptions {
  /** Force a sequence number (to script replays). */
  seq?: number;
  /** Force the SSE id. */
  id?: string;
  sourceKind?: string;
}

/** The kill-switch entity as the stream sends it. */
export function killSwitchEntity(overrides: Record<string, unknown> = {}) {
  const rest = killSwitchView().data;
  return {
    active: rest.active,
    mode: rest.mode,
    operator: rest.operator,
    reason: rest.reason,
    since: rest.since,
    blocks_new_risk: rest.blocks_new_risk,
    requires_flatten: rest.requires_flatten,
    open_positions: figure(rest.open_positions, "paper", "count"),
    ...overrides,
  };
}

export class MockStream {
  /** Current entities per topic: snapshots, the heartbeat and REST read these. */
  readonly entities: Record<string, Entities> = {
    mode: { mode: modeBanner().data },
    killswitch: { kill_switch: killSwitchEntity() },
    health: { health: healthReport() },
  };
  private readonly seq = new Map<string, number>();
  private readonly subscribers = new Set<Subscriber>();
  /** Every request served, including the browser's own reconnects. */
  requests = 0;
  /** When a number, every connection is refused with that HTTP status. */
  down: number | false = false;
  /** Add a heartbeat to every body. */
  autoHeartbeat = true;
  workerAgeS: number | null = 3;
  workerStaleAfterS = 120;
  /** Topics the heartbeat stops vouching for (the server has lost track of them). */
  heartbeatOmits: string[] = [];
  /** How long a reconnect waits for pushed events before answering. */
  holdMs = 300;
  retryMs = 50;
  /** When true, every mirrored REST read fails with a 503. */
  restDown = false;
  /** REST reads served per path, for resync and fallback assertions. */
  readonly restReads = new Map<string, number>();

  constructor(initial: Record<string, Entities> = {}) {
    Object.assign(this.entities, initial);
    // A per-process base, as the backend uses (epoch milliseconds).
    for (const topic of Object.keys(this.entities)) this.seq.set(topic, 1_790_000_000_000);
  }

  /** Install on a page or a whole browser context. */
  async install(target: Page | BrowserContext): Promise<Subscriber> {
    const sub: Subscriber = { queue: [], wake: null, connections: 0, lastEventIds: [] };
    this.subscribers.add(sub);
    await target.route(`${API}/api/stream*`, (route) => this.serve(route, sub));
    return sub;
  }

  /** Serve the REST reads of the stream-backed topics from the same state. */
  async mirrorRest(target: Page | BrowserContext) {
    const envelope = (data: unknown) => ({ data, source: source("live", "Mirrored."), caveats: [] });
    const paths: Record<string, () => unknown> = {
      "/api/system/execution-mode": () => envelope(this.entities.mode?.mode),
      "/api/system/kill-switch": () => {
        const entity = this.entities.killswitch?.kill_switch as Record<string, unknown>;
        const count = entity.open_positions as { value: number | null };
        return envelope({ ...killSwitchView().data, ...entity, open_positions: count.value });
      },
      "/api/health": () => this.entities.health?.health,
      "/api/trading/risk": () =>
        this.entities.risk?.state === undefined ? undefined : envelope(this.entities.risk.state),
    };
    for (const [path, body] of Object.entries(paths)) {
      await target.route(`${API}${path}`, (route) => {
        this.restReads.set(path, this.reads(path) + 1);
        const json = body();
        if (this.restDown || json === undefined) {
          return route.fulfill({
            status: 503,
            json: { code: "unavailable", detail: "Mock REST down.", correlation_id: "cid-rest", context: {} },
          });
        }
        return route.fulfill({ json });
      });
    }
  }

  reads(path: string): number {
    const count = this.restReads.get(path);
    return count === undefined ? 0 : count;
  }

  currentSeq(topic: string): number {
    const seq = this.seq.get(topic);
    return seq === undefined ? 0 : seq;
  }

  /** Advance a topic's sequence without sending anything (events lost in transit). */
  skip(topic: string, count: number) {
    this.seq.set(topic, this.currentSeq(topic) + count);
  }

  /** A full upsert of one entity: a delta to every subscriber. */
  upsert(topic: string, id: string, entity: Record<string, unknown>, options: PushOptions = {}) {
    this.entities[topic] = { ...(this.entities[topic] ?? {}), [id]: entity };
    return this.push(topic, "delta", { id, entity }, options);
  }

  /** Merge fields into one entity and publish it (the backend always sends the whole entity). */
  patch(topic: string, id: string, fields: Record<string, unknown>) {
    const current = this.entities[topic]?.[id] ?? {};
    return this.upsert(topic, id, { ...current, ...fields });
  }

  remove(topic: string, id: string) {
    const next = { ...(this.entities[topic] ?? {}) };
    delete next[id];
    this.entities[topic] = next;
    return this.push(topic, "tombstone", { id });
  }

  /** Arm the kill switch as another operator would: both topics change. */
  armKillSwitch(operator: string, mode: "pause" | "flatten", reason: string) {
    this.patch("killswitch", "kill_switch", {
      active: true,
      mode,
      operator,
      reason,
      blocks_new_risk: true,
      requires_flatten: mode === "flatten",
    });
    this.patch("mode", "mode", { kill_switch_active: true, kill_switch_mode: mode });
  }

  /** Push a raw event (sequence and id may be forced). */
  push(topic: string, kind: Kind, data: unknown, options: PushOptions = {}): number {
    const seq = options.seq ?? this.currentSeq(topic) + 1;
    if (options.seq === undefined) this.seq.set(topic, seq);
    this.broadcast(this.frame(topic, kind, seq, data, options));
    return seq;
  }

  /** Push a heartbeat now (one is also added to every body). */
  pushHeartbeat() {
    this.broadcast(this.heartbeatFrame());
  }

  private broadcast(frame: string) {
    for (const sub of this.subscribers) {
      sub.queue.push(frame);
      sub.wake?.();
    }
  }

  private frame(topic: string, kind: Kind, seq: number, data: unknown, options: PushOptions = {}) {
    const envelope = {
      topic,
      seq,
      kind,
      as_of: new Date().toISOString(),
      source: { kind: options.sourceKind ?? "live", detail: "Mock stream.", as_of: null },
      data,
    };
    return `id: ${options.id ?? `${topic}:${seq}`}\nevent: ${topic}.${kind}\ndata: ${JSON.stringify(envelope)}\n\n`;
  }

  heartbeatFrame(): string {
    const seq = this.currentSeq("heartbeat") + 1;
    this.seq.set("heartbeat", seq);
    const topics: Record<string, unknown> = {};
    for (const topic of Object.keys(this.entities)) {
      if (this.heartbeatOmits.includes(topic)) continue;
      topics[topic] = {
        seq: this.currentSeq(topic),
        as_of: new Date().toISOString(),
        source_kind: "live",
        error: null,
      };
    }
    const mode = this.entities.mode?.mode as { mode?: string } | undefined;
    const ks = this.entities.killswitch?.kill_switch as { active?: boolean; mode?: string | null };
    return this.frame("heartbeat", "heartbeat", seq, {
      server_time: new Date().toISOString(),
      worker_heartbeat_age_s: figure(this.workerAgeS, "paper", "s"),
      worker_state: this.workerAgeS === null ? "absent" : "running",
      worker_reason: "table",
      worker_stale_after_s: figure(this.workerStaleAfterS, "paper", "s"),
      mode: mode?.mode ?? "paper",
      kill_switch: { active: ks?.active === true, mode: ks?.mode ?? null },
      topics,
    });
  }

  private snapshotFrames(): string[] {
    return Object.entries(this.entities).map(([topic, entities]) =>
      this.frame(topic, "snapshot", this.currentSeq(topic), {
        entities,
        count: figure(Object.keys(entities).length, "paper", "count"),
      }),
    );
  }

  private take(sub: Subscriber): Promise<string[]> {
    if (sub.queue.length > 0 || this.holdMs <= 0) return Promise.resolve(sub.queue.splice(0));
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        sub.wake = null;
        resolve(sub.queue.splice(0));
      }, this.holdMs);
      sub.wake = () => {
        clearTimeout(timer);
        sub.wake = null;
        // Let a burst of pushes land in one body.
        setTimeout(() => resolve(sub.queue.splice(0)), 10);
      };
    });
  }

  private async serve(route: Route, sub: Subscriber) {
    this.requests += 1;
    sub.connections += 1;
    const headers = route.request().headers();
    const lastEventId = headers["last-event-id"];
    if (lastEventId) sub.lastEventIds.push(lastEventId);
    const cors = {
      "access-control-allow-origin": headers["origin"] ?? "http://127.0.0.1:3100",
      "access-control-allow-credentials": "true",
    };
    if (this.down !== false) {
      await route.fulfill({ status: this.down, headers: cors, body: "" }).catch(() => undefined);
      return;
    }
    let frames: string[];
    if (!lastEventId) {
      sub.queue.length = 0;
      frames = this.snapshotFrames();
    } else {
      frames = await this.take(sub);
    }
    if (this.autoHeartbeat) frames.push(this.heartbeatFrame());
    await route
      .fulfill({
        status: 200,
        headers: { ...cors, "content-type": "text/event-stream", "cache-control": "no-cache" },
        body: `retry: ${this.retryMs}\n\n${frames.join("")}`,
      })
      .catch(() => undefined);
  }
}

/** Switch on the page's test counters (lib/stream/control.ts) before it loads. */
export async function enableTestHooks(target: Page | BrowserContext) {
  await target.addInitScript(() => {
    (window as unknown as { __fibokiTestHooks: Record<string, number> }).__fibokiTestHooks = {};
  });
}

export async function testHook(page: Page, name: string): Promise<number> {
  return page.evaluate((key) => {
    const hooks = (window as unknown as { __fibokiTestHooks?: Record<string, number> })
      .__fibokiTestHooks;
    const value = hooks ? hooks[key] : undefined;
    return typeof value === "number" ? value : 0;
  }, name);
}
