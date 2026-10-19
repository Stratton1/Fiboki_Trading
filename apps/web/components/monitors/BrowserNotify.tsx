"use client";

import { useEffect, useState } from "react";
import type { AlertNotification } from "@/lib/monitors";

const OPT_KEY = "fiboki.alerts.browser";
const SEEN_KEY = "fiboki.alerts.browser.seen";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // A per-browser convenience only.
  }
}

/**
 * Opt-in browser notifications for fired price alerts. OFF by default; turning
 * it on asks the browser's permission. Only notifications the API reports as
 * UNREAD and NEWER than the last one this browser announced are shown, so a
 * reload never replays history. Works while a screen showing the
 * notifications (NTF, or an alerts tile) is open; it is not a background push.
 */
export function useBrowserNotify(items: AlertNotification[] | null) {
  const [enabled, setEnabled] = useState(false);
  const [permission, setPermission] = useState<string>("default");
  const supported = typeof window !== "undefined" && "Notification" in window;
  useEffect(() => {
    if (!supported) return;
    const id = window.setTimeout(() => {
      setPermission(Notification.permission);
      setEnabled(read(OPT_KEY) === "on" && Notification.permission === "granted");
    }, 0);
    return () => window.clearTimeout(id);
  }, [supported]);

  useEffect(() => {
    if (!enabled || items === null || items.length === 0) return;
    const newest = Math.max(...items.map((n) => n.seq));
    const seenRaw = read(SEEN_KEY);
    if (seenRaw === null) {
      // First run after opting in: start from now, never replay the backlog.
      write(SEEN_KEY, String(newest));
      return;
    }
    const seen = Number(seenRaw);
    const fresh = items.filter((n) => n.seq > seen && !n.read).sort((a, b) => a.seq - b.seq);
    for (const n of fresh.slice(-5)) {
      try {
        new Notification(`Fiboki alert · ${n.instrument}`, { body: n.message, tag: `fiboki-alert-${n.seq}` });
      } catch {
        // Some browsers allow notifications only from a service worker.
      }
    }
    if (newest > seen) write(SEEN_KEY, String(newest));
  }, [enabled, items]);

  const toggle = async () => {
    if (!supported) return;
    if (enabled) {
      write(OPT_KEY, null);
      setEnabled(false);
      return;
    }
    const result = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
    setPermission(result);
    if (result === "granted") {
      write(OPT_KEY, "on");
      write(SEEN_KEY, null);
      setEnabled(true);
    }
  };
  return { supported, enabled, permission, toggle };
}
