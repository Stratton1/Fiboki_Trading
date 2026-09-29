"use client";

import { useSyncExternalStore } from "react";
import { parseUtc } from "./format";

/**
 * "Data as of", for the status bar (report G W-04).
 *
 * It used to be the NEWEST timestamp on any payload, and `/api/health`'s
 * `checked_at` (polled every 20 s) was one of them, so it always read roughly
 * "now" and masked a dataset that was hours old. It is now the OLDEST platform
 * as-of among the views on screen, with probes excluded:
 *
 *  - each mounted view registers the platform's own as-of for its data (the
 *    stream topic's as_of when the stream feeds it, else the payload's
 *    `source.as_of`, else `data.as_of`), and unregisters on unmount;
 *  - probes (health, the execution mode, the operator) are not data, so they
 *    never register: a health check that ran a second ago says nothing about
 *    how old the positions are;
 *  - a view whose payload carries no as-of registers nothing and claims
 *    nothing; the inspector lists which views those are.
 *
 * `noteAsOf` still records the newest as-of received (stream events), shown in
 * the inspector as "newest received", never as the headline.
 */

/** Reads that are probes of the platform, not data an operator reads. */
export const PROBE_PATHS: ReadonlySet<string> = new Set([
  "/api/health",
  "/api/system/execution-mode",
  "/api/auth/me",
]);

export function isProbe(path: string): boolean {
  const q = path.indexOf("?");
  return PROBE_PATHS.has(q === -1 ? path : path.slice(0, q));
}

export interface OnScreenAsOf {
  path: string;
  iso: string | null;
  ms: number | null;
}

const views = new Map<string, OnScreenAsOf>();
let newest: { iso: string; ms: number } | null = null;
const listeners = new Set<() => void>();
let snapshot: { oldest: OnScreenAsOf | null; views: OnScreenAsOf[]; newest: string | null } = {
  oldest: null,
  views: [],
  newest: null,
};

function publish() {
  const list = [...views.values()].sort((a, b) => a.path.localeCompare(b.path));
  let oldest: OnScreenAsOf | null = null;
  for (const view of list) {
    if (view.ms === null) continue;
    if (oldest === null || (oldest.ms !== null && view.ms < oldest.ms)) oldest = view;
  }
  snapshot = { oldest, views: list, newest: newest?.iso ?? null };
  for (const listener of listeners) listener();
}

function toMs(iso: string | null): number | null {
  if (!iso) return null;
  const ms = parseUtc(iso).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/** A mounted view says what its data's platform as-of is (null: none supplied). */
export function registerAsOf(id: string, path: string, iso: string | null) {
  if (isProbe(path)) return;
  const prev = views.get(id);
  if (prev && prev.path === path && prev.iso === iso) return;
  views.set(id, { path, iso, ms: toMs(iso) });
  publish();
}

export function unregisterAsOf(id: string) {
  if (views.delete(id)) publish();
}

/** The newest platform as-of received on the stream (diagnostic only). */
export function noteAsOf(iso: string | null | undefined) {
  const ms = toMs(iso ?? null);
  if (ms === null || !iso) return;
  if (newest !== null && ms <= newest.ms) return;
  newest = { iso, ms };
  publish();
}

type Maybe = { as_of?: unknown } | null | undefined;

/**
 * The platform's as-of for one payload: `source.as_of`, else `data.as_of`.
 * A probe's `checked_at` is deliberately not a candidate.
 */
export function payloadAsOf(payload: unknown): string | null {
  if (payload === null || typeof payload !== "object") return null;
  const p = payload as { source?: Maybe; data?: Maybe };
  for (const candidate of [p.source?.as_of, p.data?.as_of]) {
    if (typeof candidate === "string" && candidate.length > 0) return candidate;
  }
  return null;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const EMPTY = { oldest: null, views: [], newest: null };

export function useOnScreenAsOf(): typeof snapshot {
  return useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => EMPTY,
  );
}

/** For tests and sign-out. */
export function resetAsOf() {
  views.clear();
  newest = null;
  publish();
}
