import { expect, test } from "@playwright/test";
import { backoffDelay, BACKOFF_STEPS_MS, FAILURES_BEFORE_DISCONNECTED } from "../../lib/stream/backoff";
import { computeFreshness, type FreshnessInput } from "../../lib/freshness";
import {
  applyDelta,
  DEDUPE_CAPACITY,
  LruSet,
  StreamRouter,
  type RouterSink,
  type TopicPatch,
} from "../../lib/stream/router";
import { topicSpec } from "../../lib/stream/topics";

/**
 * Unit tests for the stream's pure parts, run in Node (no browser): the
 * router's dedupe and sequence rules, applyDelta, the backoff schedule and
 * the freshness table of report E §6.4. The browser-level behaviour is in
 * stream.spec.ts and freshness.spec.ts.
 */

test.describe.configure({ mode: "parallel" });
test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "pure Node tests; run once");
});

function frame(topic: string, kind: string, seq: number, data: unknown, id = `${topic}:${seq}`) {
  return {
    id,
    event: `${topic}.${kind}`,
    data: JSON.stringify({
      topic,
      seq,
      kind,
      as_of: "2026-09-29T10:00:00Z",
      source: { kind: "live", detail: "test", as_of: null },
      data,
    }),
  };
}

function fakeSink(initial: Record<string, unknown> = {}) {
  const cache = new Map<string, unknown>(Object.entries(initial));
  const invalidated: string[] = [];
  const patches: { topic: string; patch: TopicPatch }[] = [];
  const sink: RouterSink = {
    now: () => 1_000,
    getQueryData: (path) => cache.get(path),
    setQueryData: (path, value) => void cache.set(path, value),
    invalidate: (path) => {
      invalidated.push(path);
      return Promise.resolve(true);
    },
    invalidateFiltered: () => undefined,
    patchTopic: (topic, patch) => void patches.push({ topic, patch }),
    setHeartbeat: () => undefined,
    updateMarks: () => undefined,
    noteAsOf: () => undefined,
  };
  return { sink, cache, invalidated, patches };
}

const KS = "/api/system/kill-switch";
const ksEnvelope = (data: Record<string, unknown>) => ({
  data: {
    active: false,
    mode: null,
    operator: null,
    reason: null,
    open_positions: 3,
    consequences: { pause: ["x"] },
    ...data,
  },
  source: { kind: "live", detail: "rest", as_of: null },
  caveats: [{ code: "kept", severity: "info", message: "", affects: "", direction: "unknown" }],
});
const ksEntity = (data: Record<string, unknown>) => ({ active: false, mode: null, operator: null, reason: null, ...data });
const snap = (entities: Record<string, unknown>) => ({ entities, count: { value: 1 } });
const delta = (id: string, entity: unknown) => ({ id, entity });
type KsEnv = ReturnType<typeof ksEnvelope>;

test.describe("StreamRouter", () => {
  test("snapshot then delta update the REST cache entry, keeping its caveats and REST-only fields", () => {
    const { sink, cache } = fakeSink({ [KS]: ksEnvelope({}) });
    const router = new StreamRouter(sink);
    expect(router.handle(frame("killswitch", "snapshot", 5, snap({ kill_switch: ksEntity({}) })))).toBe("applied");
    expect(
      router.handle(frame("killswitch", "delta", 6, delta("kill_switch", ksEntity({ active: true, mode: "pause" })))),
    ).toBe("applied");
    const env = cache.get(KS) as KsEnv;
    expect(env.data).toMatchObject({ active: true, mode: "pause", open_positions: 3 });
    expect(env.data.consequences).toEqual({ pause: ["x"] });
    expect(env.caveats[0]?.code).toBe("kept");
  });

  test("a field the stream sends in another shape is not written; REST is re-read", () => {
    const { sink, cache, invalidated } = fakeSink({ [KS]: ksEnvelope({}) });
    const router = new StreamRouter(sink);
    router.handle(frame("killswitch", "snapshot", 1, snap({ kill_switch: ksEntity({}) })));
    const entity = ksEntity({ active: true, open_positions: { value: 4, provenance: "paper" } });
    router.handle(frame("killswitch", "delta", 2, delta("kill_switch", entity)));
    const env = cache.get(KS) as KsEnv;
    expect(env.data.active).toBe(true);
    expect(env.data.open_positions).toBe(3);
    expect(invalidated).toContain(KS);
  });

  test("dedupe is on (event, id): a snapshot reusing a delta's id still applies", () => {
    const { sink } = fakeSink({ [KS]: ksEnvelope({}) });
    const router = new StreamRouter(sink);
    router.handle(frame("killswitch", "snapshot", 1, snap({ kill_switch: ksEntity({}) })));
    expect(router.handle(frame("killswitch", "delta", 2, delta("kill_switch", ksEntity({ active: true }))))).toBe(
      "applied",
    );
    expect(router.handle(frame("killswitch", "delta", 2, delta("kill_switch", ksEntity({ active: true }))))).toBe(
      "duplicate",
    );
    expect(router.handle(frame("killswitch", "snapshot", 2, snap({ kill_switch: ksEntity({ active: true }) })))).toBe(
      "applied",
    );
  });

  test("an old seq under a new id is stale, not applied", () => {
    const { sink } = fakeSink({ [KS]: ksEnvelope({}) });
    const router = new StreamRouter(sink);
    router.handle(frame("killswitch", "snapshot", 4, snap({ kill_switch: ksEntity({}) })));
    expect(
      router.handle(frame("killswitch", "delta", 3, delta("kill_switch", ksEntity({ active: true })), "other-id")),
    ).toBe("stale");
  });

  test("a sequence gap is not applied: the topic resyncs from REST", async () => {
    const { sink, cache, invalidated, patches } = fakeSink({ [KS]: ksEnvelope({}) });
    const router = new StreamRouter(sink);
    router.handle(frame("killswitch", "snapshot", 1, snap({ kill_switch: ksEntity({}) })));
    expect(
      router.handle(frame("killswitch", "delta", 4, delta("kill_switch", ksEntity({ active: true, reason: "NO" })))),
    ).toBe("gap");
    expect((cache.get(KS) as KsEnv).data.active).toBe(false);
    expect(invalidated).toContain(KS);
    expect(patches).toContainEqual({ topic: "killswitch", patch: { resyncing: true } });
    await Promise.resolve();
    expect(router.handle(frame("killswitch", "delta", 5, delta("kill_switch", ksEntity({ active: true }))))).toBe(
      "applied",
    );
  });

  test("a delta with no snapshot on this connection resyncs", () => {
    const { sink, invalidated } = fakeSink({ [KS]: ksEnvelope({}) });
    const router = new StreamRouter(sink);
    expect(router.handle(frame("killswitch", "delta", 9, delta("kill_switch", ksEntity({}))))).toBe("gap");
    expect(invalidated).toEqual([KS]);
  });

  let beat = 0;
  const heartbeat = (topics: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
    frame("heartbeat", "heartbeat", (beat += 1), {
      server_time: "2026-09-29T10:00:00Z",
      worker_heartbeat_age_s: { value: 3, provenance: "paper", unit: "s" },
      worker_stale_after_s: { value: 90, provenance: "paper", unit: "s" },
      mode: "paper",
      kill_switch: { active: false, mode: null },
      topics,
      ...extra,
    });

  test("a heartbeat ahead of the last applied seq is a gap", () => {
    const { sink, invalidated } = fakeSink({ [KS]: ksEnvelope({}) });
    const router = new StreamRouter(sink);
    router.handle(frame("killswitch", "snapshot", 2, snap({ kill_switch: ksEntity({}) })));
    router.handle(heartbeat({ killswitch: { seq: 5, as_of: null, source_kind: "live" } }));
    expect(invalidated).toContain(KS);
  });

  test("the heartbeat's worker age and stale threshold are read from Figures", () => {
    const { sink } = fakeSink();
    let got: unknown = null;
    sink.setHeartbeat = (update) => {
      got = update;
    };
    const router = new StreamRouter(sink);
    router.handle(heartbeat({}));
    expect(got).toMatchObject({ workerAgeS: 3, workerStaleAfterS: 90 });
    router.handle(
      heartbeat({}, { worker_heartbeat_age_s: { value: null, provenance: "paper", unit: "s" } }),
    );
    expect(got).toMatchObject({ workerAgeS: null });
  });

  test("a heartbeat whose kill-switch state disagrees with the cache re-reads it", () => {
    const { sink, invalidated } = fakeSink({ [KS]: ksEnvelope({ active: false }) });
    const router = new StreamRouter(sink);
    router.handle(frame("killswitch", "snapshot", 2, snap({ kill_switch: ksEntity({}) })));
    router.handle(heartbeat({ killswitch: { seq: 2, as_of: null } }, { kill_switch: { active: true } }));
    expect(invalidated).toContain(KS);
  });

  test("an absent source does not overwrite the REST payload", () => {
    const { sink, cache, patches } = fakeSink({ "/api/trading/risk": { data: { x: 1 }, source: {}, caveats: [] } });
    const router = new StreamRouter(sink);
    const absent = frame("risk", "snapshot", 1, snap({}));
    const parsed = JSON.parse(absent.data);
    parsed.source.kind = "absent";
    expect(router.handle({ ...absent, data: JSON.stringify(parsed) })).toBe("applied");
    expect(cache.get("/api/trading/risk")).toEqual({ data: { x: 1 }, source: {}, caveats: [] });
    expect(patches.at(-1)?.patch.streamFed).toBe(false);
  });

  test("another entity on an object topic re-reads its own REST path", () => {
    const risk = "/api/trading/risk";
    const { sink, cache, invalidated } = fakeSink({ [risk]: { data: { a: 1 }, source: {}, caveats: [] } });
    const router = new StreamRouter(sink);
    router.handle(frame("risk", "snapshot", 1, snap({ state: { a: 1 }, "exposure:eur": { k: 1 } })));
    router.handle(frame("risk", "delta", 2, delta("exposure:eur", { k: 2 })));
    expect(invalidated).toContain("/api/trading/exposure");
    expect(cache.get(risk)).toMatchObject({ data: { a: 1 } });
  });

  test("reset forgets sequence numbers (a restarted server may reuse them)", () => {
    const { sink } = fakeSink({ [KS]: ksEnvelope({}) });
    const router = new StreamRouter(sink);
    router.handle(frame("killswitch", "snapshot", 40, snap({ kill_switch: ksEntity({}) })));
    router.reset();
    expect(router.handle(frame("killswitch", "snapshot", 1, snap({ kill_switch: ksEntity({}) })))).toBe("applied");
    expect(router.handle(frame("killswitch", "delta", 2, delta("kill_switch", ksEntity({ active: true }))))).toBe(
      "applied",
    );
  });

  test("malformed frames are rejected", () => {
    const { sink } = fakeSink();
    const router = new StreamRouter(sink);
    expect(router.handle({ id: "x:1", event: "x.delta", data: "{not json" })).toBe("invalid");
    expect(router.handle({ id: "x:2", event: "x.delta", data: '{"topic":"x"}' })).toBe("invalid");
  });
});

test.describe("LruSet", () => {
  test("keeps at most its capacity, evicting the oldest", () => {
    const lru = new LruSet(DEDUPE_CAPACITY);
    for (let i = 0; i < DEDUPE_CAPACITY + 5; i += 1) lru.add(`id:${i}`);
    expect(lru.size).toBe(DEDUPE_CAPACITY);
    expect(lru.has("id:0")).toBe(false);
    expect(lru.has(`id:${DEDUPE_CAPACITY + 4}`)).toBe(true);
  });
});

test.describe("applyDelta for list topics", () => {
  const spec = topicSpec("positions");
  if (!spec) throw new Error("positions topic missing");
  const page = {
    items: [
      { position_id: "a", v: 1 },
      { position_id: "b", v: 2 },
    ],
    total: 2,
    offset: 0,
    limit: 100,
    source: {},
    caveats: [],
  };
  const env = (kind: "delta" | "tombstone" | "snapshot", data: unknown) => ({
    topic: "positions",
    seq: 2,
    kind,
    as_of: "t",
    source: { kind: "live" as const, detail: "", as_of: null },
    data,
  });
  const itemsOf = (out: { value?: unknown }) => (out.value as typeof page).items;

  test("an update replaces the row in place", () => {
    const out = applyDelta(spec, page, env("delta", { id: "b", entity: { position_id: "b", v: 3 } }));
    expect(out.refetch).toBe(false);
    expect(itemsOf(out).map((r) => r.v)).toEqual([1, 3]);
  });

  test("a new row is never appended at a guessed position: REST re-read", () => {
    const out = applyDelta(spec, page, env("delta", { id: "c", entity: { position_id: "c", v: 9 } }));
    expect(out).toEqual({ refetch: true });
  });

  test("a tombstone removes the row and the total", () => {
    const out = applyDelta(spec, page, env("tombstone", { id: "a" }));
    expect(out.value).toMatchObject({ items: [{ position_id: "b" }], total: 1 });
  });

  test("a snapshot of the same rows is laid onto the REST order; a different set is re-read", () => {
    const same = applyDelta(
      spec,
      page,
      env("snapshot", { entities: { b: { position_id: "b", v: 5 }, a: { position_id: "a", v: 4 } } }),
    );
    expect(itemsOf(same).map((r) => r.position_id)).toEqual(["a", "b"]);
    expect(itemsOf(same).map((r) => r.v)).toEqual([4, 5]);
    const different = applyDelta(spec, page, env("snapshot", { entities: { a: { position_id: "a" } } }));
    expect(different).toEqual({ refetch: true });
  });

  test("no cached base means a REST read, never a fabricated page", () => {
    expect(applyDelta(spec, undefined, env("snapshot", { entities: {} }))).toEqual({ refetch: true });
  });
});

test.describe("backoff", () => {
  test("1, 2, 4, 8 then 15 s, capped, with at most 20% jitter", () => {
    for (let failures = 1; failures <= 8; failures += 1) {
      const base = BACKOFF_STEPS_MS[Math.min(failures, 5) - 1] as number;
      expect(backoffDelay(failures, 0)).toBe(Math.round(base * 0.8));
      expect(backoffDelay(failures, 0.5)).toBe(base);
      expect(backoffDelay(failures, 0.999999)).toBeLessThanOrEqual(Math.round(base * 1.2));
    }
    expect(FAILURES_BEFORE_DISCONNECTED).toBe(5);
  });
});

test.describe("freshness (report E §6.4)", () => {
  const base: FreshnessInput = {
    now: 100_000,
    connection: "live",
    streamFed: true,
    confirmedAt: 99_000,
    topicCadenceMs: 5_000,
    receivedAt: 50_000,
    refreshMs: undefined,
    refreshFailing: false,
    workerDependent: true,
    workerAgeS: 10,
  };

  test("live, lagging and stale by stream confirmation age", () => {
    expect(computeFreshness(base)).toBe("live");
    expect(computeFreshness({ ...base, confirmedAt: 100_000 - 8_000 })).toBe("lagging");
    expect(computeFreshness({ ...base, confirmedAt: 100_000 - 16_000 })).toBe("stale");
  });

  test("a worker heartbeat at the stale threshold or none makes worker data stale, however live the stream", () => {
    expect(computeFreshness({ ...base, workerAgeS: 120 })).toBe("stale");
    expect(computeFreshness({ ...base, workerAgeS: 95, workerStaleAfterS: 90 })).toBe("stale");
    expect(computeFreshness({ ...base, workerAgeS: 95 })).toBe("live");
    expect(computeFreshness({ ...base, workerAgeS: null })).toBe("stale");
    expect(computeFreshness({ ...base, workerAgeS: undefined })).toBe("live");
    expect(computeFreshness({ ...base, workerAgeS: 500, workerDependent: false })).toBe("live");
  });

  test("REST-read data is fresh, lagging or stale by its poll interval", () => {
    const rest = { ...base, connection: "connecting" as const, streamFed: false, confirmedAt: null, refreshMs: 10_000 };
    expect(computeFreshness({ ...rest, receivedAt: 95_000 })).toBe("fresh");
    expect(computeFreshness({ ...rest, receivedAt: 100_000 - 16_000 })).toBe("lagging");
    expect(computeFreshness({ ...rest, receivedAt: 100_000 - 31_000 })).toBe("stale");
    expect(computeFreshness({ ...rest, receivedAt: 99_000, refreshFailing: true })).toBe("stale");
  });

  test("a failed re-read of stream-fed data is stale, however live the stream", () => {
    expect(computeFreshness({ ...base, workerDependent: false, refreshFailing: true })).toBe("stale");
  });

  test("disconnected only when the stream has given up AND REST is failing", () => {
    const down = { ...base, connection: "disconnected" as const, refreshMs: 10_000, receivedAt: 99_000 };
    expect(computeFreshness({ ...down, refreshFailing: false })).toBe("fresh");
    expect(computeFreshness({ ...down, refreshFailing: true })).toBe("disconnected");
    expect(computeFreshness({ ...down, connection: "reconnecting", refreshFailing: true })).toBe("stale");
  });
});
