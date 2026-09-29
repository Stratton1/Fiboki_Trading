"use client";

import { useSyncExternalStore } from "react";
import { parseUtc } from "./format";

/**
 * The newest `as_of` the workstation has received from the platform, across
 * every payload, for the status bar. It is the platform's own timestamp
 * (`source.as_of`, `data.as_of`, a health report's `checked_at`), never the
 * browser's receipt time, and it stays null until one has actually arrived.
 */

let latest: { iso: string; ms: number } | null = null;
const listeners = new Set<() => void>();

export function noteAsOf(iso: string | null | undefined) {
  if (!iso) return;
  const ms = parseUtc(iso).getTime();
  if (Number.isNaN(ms)) return;
  if (latest !== null && ms <= latest.ms) return;
  latest = { iso, ms };
  for (const listener of listeners) listener();
}

type Maybe = { as_of?: unknown; checked_at?: unknown } | null | undefined;

/** Pull the platform timestamps a payload carries, wherever the API puts them. */
export function asOfCandidates(payload: unknown): string[] {
  if (payload === null || typeof payload !== "object") return [];
  const p = payload as { source?: Maybe; data?: Maybe; checked_at?: unknown };
  return [p.source?.as_of, p.data?.as_of, p.checked_at].filter(
    (v): v is string => typeof v === "string" && v.length > 0,
  );
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLatestAsOf(): string | null {
  return useSyncExternalStore(
    subscribe,
    () => latest?.iso ?? null,
    () => null,
  );
}
