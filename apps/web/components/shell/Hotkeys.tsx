"use client";

import { useEffect } from "react";
import { uiPrefs } from "@/lib/ui-prefs";
import { useToast } from "../ui/Toast";

/**
 * Shell shortcuts. Display only, and never a mutation: no single keystroke
 * changes anything on the platform (plan §4).
 *
 *   ⌘⇧D (Ctrl+Shift+D)  cycle density
 *   ⌘⇧L (Ctrl+Shift+L)  toggle light / dark
 */
export function ShellHotkeys() {
  const notify = useToast();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || !event.shiftKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "d") {
        event.preventDefault();
        notify(`Density: ${uiPrefs.cycleDensity()}`);
      } else if (key === "l") {
        event.preventDefault();
        notify(`Theme: ${uiPrefs.toggleTheme()}`);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [notify]);
  return null;
}
