"use client";

import { useSyncExternalStore } from "react";

/**
 * Whether a CSS media query matches, kept current. The server snapshot (and
 * the first client render, for hydration) is `false`, so a layout chosen by
 * this hook renders the wide form first and switches after mount.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (listener) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", listener);
      return () => list.removeEventListener("change", listener);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Below 640 px: the phone layout (stacked cards instead of wide grids). */
export const PHONE_QUERY = "(max-width: 639.98px)";
