"use client";

import { useSyncExternalStore } from "react";
import { COMFORTABLE_BELOW_PX, PREF_KEY } from "./prepaint";

/**
 * Display preferences: theme, density and the P&L colour preset.
 *
 * They live as `data-*` attributes on <html> (the stylesheet keys off them)
 * and are remembered per browser in localStorage. localStorage is a
 * convenience only: it can throw or come back empty (private windows, blocked
 * storage), every access is guarded, and the defaults are always correct
 * without it. Nothing here is operator state the platform needs to know.
 *
 * The pre-paint script in app/layout.tsx (lib/prepaint.ts) applies the
 * stored values before first paint, so a light-theme operator never sees a
 * dark flash. This module then keeps the attributes, storage and React in step.
 */

export type ThemePref = "system" | "dark" | "light";
export type Theme = "dark" | "light";
export type DensityPref = "auto" | Density;
export type Density = "compact" | "regular" | "comfortable";
export type PnlPalette = "standard" | "cvd";

export const DENSITIES: readonly Density[] = ["compact", "regular", "comfortable"];

const KEY = PREF_KEY;

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Storage unavailable: the preference lasts for this page view only.
  }
}

export interface UiPrefs {
  themePref: ThemePref;
  theme: Theme;
  densityPref: DensityPref;
  density: Density;
  pnl: PnlPalette;
  railExpanded: boolean;
}

const SERVER: UiPrefs = {
  themePref: "system",
  theme: "dark",
  densityPref: "auto",
  density: "regular",
  pnl: "standard",
  railExpanded: false,
};

function systemTheme(): Theme {
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function autoDensity(): Density {
  return window.innerWidth < COMFORTABLE_BELOW_PX ? "comfortable" : "regular";
}

function readFromDom(): UiPrefs {
  const d = document.documentElement;
  const themePref = (d.getAttribute("data-theme-pref") as ThemePref | null) ?? "system";
  const densityPref = (d.getAttribute("data-density-pref") as DensityPref | null) ?? "auto";
  return {
    themePref,
    theme: d.getAttribute("data-theme") === "light" ? "light" : "dark",
    densityPref,
    density: (d.getAttribute("data-density") as Density | null) ?? autoDensity(),
    pnl: d.getAttribute("data-pnl") === "cvd" ? "cvd" : "standard",
    railExpanded: readStorage(KEY.rail) === "expanded",
  };
}

let snapshot: UiPrefs | null = null;
const listeners = new Set<() => void>();

function emit() {
  snapshot = readFromDom();
  for (const listener of listeners) listener();
}

function apply(prefs: Partial<UiPrefs>) {
  const d = document.documentElement;
  if (prefs.themePref !== undefined) {
    d.setAttribute("data-theme-pref", prefs.themePref);
    d.setAttribute("data-theme", prefs.themePref === "system" ? systemTheme() : prefs.themePref);
    writeStorage(KEY.theme, prefs.themePref === "system" ? null : prefs.themePref);
  }
  if (prefs.densityPref !== undefined) {
    d.setAttribute("data-density-pref", prefs.densityPref);
    d.setAttribute(
      "data-density",
      prefs.densityPref === "auto" ? autoDensity() : prefs.densityPref,
    );
    writeStorage(KEY.density, prefs.densityPref === "auto" ? null : prefs.densityPref);
  }
  if (prefs.pnl !== undefined) {
    if (prefs.pnl === "cvd") d.setAttribute("data-pnl", "cvd");
    else d.removeAttribute("data-pnl");
    writeStorage(KEY.pnl, prefs.pnl === "cvd" ? "cvd" : null);
  }
  if (prefs.railExpanded !== undefined) {
    writeStorage(KEY.rail, prefs.railExpanded ? "expanded" : null);
  }
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    // Follow the OS theme while the operator's choice is "system", and the
    // viewport while density is automatic.
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onMedia = () => {
      if (document.documentElement.getAttribute("data-theme-pref") === "system") {
        document.documentElement.setAttribute("data-theme", systemTheme());
        emit();
      }
    };
    const onResize = () => {
      if (document.documentElement.getAttribute("data-density-pref") === "auto") {
        const next = autoDensity();
        if (document.documentElement.getAttribute("data-density") !== next) {
          document.documentElement.setAttribute("data-density", next);
          emit();
        }
      }
    };
    media.addEventListener("change", onMedia);
    window.addEventListener("resize", onResize);
    teardown = () => {
      media.removeEventListener("change", onMedia);
      window.removeEventListener("resize", onResize);
    };
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) teardown?.();
  };
}
let teardown: (() => void) | null = null;

function getSnapshot(): UiPrefs {
  if (snapshot === null) snapshot = readFromDom();
  return snapshot;
}

export function useUiPrefs(): UiPrefs {
  return useSyncExternalStore(subscribe, getSnapshot, () => SERVER);
}

export const uiPrefs = {
  setTheme(themePref: ThemePref) {
    apply({ themePref });
  },
  /** ⌘⇧L: flip between dark and light from whatever is showing now. */
  toggleTheme(): Theme {
    const next: Theme = getSnapshot().theme === "dark" ? "light" : "dark";
    apply({ themePref: next });
    return next;
  },
  setDensity(densityPref: DensityPref) {
    apply({ densityPref });
  },
  /** ⌘⇧D: compact → regular → comfortable → compact. */
  cycleDensity(): Density {
    const current = getSnapshot().density;
    const next = DENSITIES[(DENSITIES.indexOf(current) + 1) % DENSITIES.length] as Density;
    apply({ densityPref: next });
    return next;
  },
  setPnl(pnl: PnlPalette) {
    apply({ pnl });
  },
  setRailExpanded(railExpanded: boolean) {
    apply({ railExpanded });
  },
};
