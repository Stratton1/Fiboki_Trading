"use client";

import { useSyncExternalStore } from "react";

/**
 * One shared 1 Hz wall clock for everything that ages on screen (freshness,
 * "last good 42 s ago", the worker heartbeat counting up, the UTC clock).
 * One interval for the whole tab, running only while something subscribes.
 */

let now = typeof Date === "undefined" ? 0 : Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function tick() {
  now = Date.now();
  for (const listener of listeners) listener();
}

export function subscribeClock(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    now = Date.now();
    timer = setInterval(tick, 1_000);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

/** The clock's current reading: stable between ticks, as useSyncExternalStore needs. */
export function clockNow(): number {
  return now;
}

/** Re-read the wall clock now (after an event that must age from its true receipt time). */
export function touchClock() {
  now = Date.now();
}

const noop = () => () => undefined;

/** The shared clock in ms, ticking only while `active`. */
export function useClock(active = true): number {
  return useSyncExternalStore(active ? subscribeClock : noop, clockNow, () => 0);
}
