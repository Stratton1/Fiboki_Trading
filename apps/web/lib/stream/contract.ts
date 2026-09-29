import type { Figure, SourceNote } from "../types";

/**
 * The wire contract of GET /api/stream (plan §5, report E §6.2), as the
 * backend implements it in src/fiboki/api/routers/stream.py:
 *
 *   id:    <topic>:<seq>
 *   event: <topic>.<kind>
 *   data:  StreamEnvelope (JSON)
 *
 *   snapshot   data = { entities: { <id>: entity }, count: Figure }
 *              carries the topic's CURRENT seq, so its id equals the id of
 *              the last delta already folded into it: dedupe on (event, id).
 *   delta      data = { id, entity }   a full upsert of one entity
 *   tombstone  data = { id }
 *   heartbeat  every 5 s, topic "heartbeat" (HeartbeatData)
 *
 * Entities are the REST shapes (with named exceptions the router guards
 * against). Every number inside is still a Figure with provenance.
 *
 * SSE is not describable in OpenAPI, so this file is hand-written; the Wave 2
 * report lists each assumption for the lead to hold the backend to.
 */

export type StreamKind = "snapshot" | "delta" | "tombstone" | "heartbeat";

export interface StreamEnvelope<D = unknown> {
  topic: string;
  seq: number;
  kind: StreamKind;
  as_of: string | null;
  source: SourceNote;
  data: D;
}

export interface SnapshotData {
  entities: Record<string, unknown>;
  count?: Figure;
}

export interface DeltaData {
  id: string;
  entity: unknown;
}

export interface TombstoneData {
  id: string;
}

/** The heartbeat's `data`, every 5 s. */
export interface HeartbeatData {
  server_time: string;
  /** A Figure: `value` null means no worker has ever beaten (or it is unreadable). */
  worker_heartbeat_age_s: Figure | number | null;
  /** The backend's stale threshold, a Figure (default 120 s when absent). */
  worker_stale_after_s?: Figure | number | null;
  worker_state?: string;
  worker_reason?: string;
  mode: string;
  kill_switch: { active: boolean | null; mode?: string | null };
  /** The latest sequence number and as_of per topic. */
  topics: Record<
    string,
    { seq: number; as_of: string | null; source_kind?: string; error?: string | null }
  >;
}

/** The server's heartbeat cadence. */
export const HEARTBEAT_MS = 5_000;

export const STREAM_KINDS: readonly StreamKind[] = ["snapshot", "delta", "tombstone", "heartbeat"];

export function isStreamEnvelope(value: unknown): value is StreamEnvelope {
  if (value === null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.topic === "string" &&
    typeof v.seq === "number" &&
    typeof v.kind === "string" &&
    (STREAM_KINDS as readonly string[]).includes(v.kind) &&
    "data" in v
  );
}

export function isHeartbeatData(value: unknown): value is HeartbeatData {
  if (value === null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return typeof v.server_time === "string" && typeof v.topics === "object" && v.topics !== null;
}

/** A Figure's value, or a bare number; null for a missing value. Never zero by default. */
export function figureValue(value: unknown): number | null {
  if (typeof value === "number") return value;
  if (value !== null && typeof value === "object" && "value" in value) {
    const inner = (value as { value: unknown }).value;
    return typeof inner === "number" ? inner : null;
  }
  return null;
}
