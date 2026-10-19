"use client";

import { useCallback, useSyncExternalStore } from "react";
import { isTimeZoneId, type TimeZoneId } from "./time";

/**
 * The chart's DISPLAY time zone, remembered per browser (a convenience, like
 * the theme): it relabels axes, readouts and tables and nothing else. Every
 * request, every stored drawing and every cursor stays UTC. Storage can throw
 * or come back empty (private windows); the default, UTC, is always correct.
 */
const KEY = "fiboki.chart.tz";
const EVENT = "fiboki:chart-tz";

function read(): TimeZoneId {
  try {
    const value = window.localStorage.getItem(KEY);
    return isTimeZoneId(value) ? value : "UTC";
  } catch {
    return "UTC";
  }
}

function subscribe(listener: () => void): () => void {
  window.addEventListener(EVENT, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(EVENT, listener);
    window.removeEventListener("storage", listener);
  };
}

export function useTimeZone(): { tz: TimeZoneId; setTz: (tz: TimeZoneId) => void } {
  const tz = useSyncExternalStore(subscribe, read, () => "UTC" as TimeZoneId);
  const setTz = useCallback((next: TimeZoneId) => {
    try {
      if (next === "UTC") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, next);
    } catch {
      // Storage unavailable: the choice lasts for this page view only.
    }
    window.dispatchEvent(new Event(EVENT));
  }, []);
  return { tz, setTz };
}
