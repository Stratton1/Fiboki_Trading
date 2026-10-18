"use client";

import { useSyncExternalStore } from "react";
import { isProbe } from "./as-of";
import type { Freshness } from "./freshness";

/**
 * Which views on screen are stale right now (programme P1 11): every `useApi`
 * read registers its freshness here while mounted, and the shell's page-level
 * banner says how many panels are showing last-known-good data. Each of those
 * panels is also dimmed in place (AsyncBoundary's `data-stale`).
 *
 * Freshness here is about THIS TAB's copy of the data (a failed or overdue
 * re-read, a dropped stream). Whether a platform component is stale is the
 * platform's verdict, shown on the System board.
 */

interface Entry {
  path: string;
  freshness: Freshness;
}

const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();
let snapshot: { stale: string[]; total: number } = { stale: [], total: 0 };

function publish() {
  const stale: string[] = [];
  for (const entry of entries.values()) {
    if (entry.freshness === "stale" || entry.freshness === "disconnected") stale.push(entry.path);
  }
  stale.sort();
  const same = stale.length === snapshot.stale.length && stale.every((p, i) => p === snapshot.stale[i]);
  if (same && entries.size === snapshot.total) return;
  snapshot = { stale, total: entries.size };
  for (const listener of listeners) listener();
}

export function registerFreshness(id: string, path: string, freshness: Freshness) {
  if (isProbe(path)) return;
  const prev = entries.get(id);
  if (prev && prev.path === path && prev.freshness === freshness) return;
  entries.set(id, { path, freshness });
  publish();
}

export function unregisterFreshness(id: string) {
  if (entries.delete(id)) publish();
}

const EMPTY = { stale: [] as string[], total: 0 };

export function useStaleViews(): { stale: string[]; total: number } {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => snapshot,
    () => EMPTY,
  );
}
