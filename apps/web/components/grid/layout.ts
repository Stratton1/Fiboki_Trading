/**
 * Column layouts per grid, in localStorage: the current one (restored on the
 * next visit) and named sets the operator saved. Display state only: it never
 * leaves the browser and never reaches the platform.
 */

export interface GridLayout {
  visibility: Record<string, boolean>;
  pinned: string[];
}

export type SavedSets = Record<string, GridLayout>;

const key = (grid: string, part: "layout" | "sets") => `fiboki.grid.${grid}.${part}`;

function read<T>(k: string): T | null {
  try {
    const raw = window.localStorage.getItem(k);
    return raw === null ? null : (JSON.parse(raw) as T);
  } catch {
    return null;
  }
}

function write(k: string, value: unknown) {
  try {
    window.localStorage.setItem(k, JSON.stringify(value));
  } catch {
    // Storage full or disabled: the layout simply is not remembered.
  }
}

function valid(layout: unknown): layout is GridLayout {
  if (layout === null || typeof layout !== "object") return false;
  const l = layout as GridLayout;
  return typeof l.visibility === "object" && l.visibility !== null && Array.isArray(l.pinned);
}

export function loadLayout(grid: string): GridLayout | null {
  const layout = read<unknown>(key(grid, "layout"));
  return valid(layout) ? layout : null;
}

export function saveLayout(grid: string, layout: GridLayout) {
  write(key(grid, "layout"), layout);
}

export function loadSets(grid: string): SavedSets {
  const sets = read<Record<string, unknown>>(key(grid, "sets")) ?? {};
  const out: SavedSets = {};
  for (const [name, layout] of Object.entries(sets)) if (valid(layout)) out[name] = layout;
  return out;
}

export function saveSets(grid: string, sets: SavedSets) {
  write(key(grid, "sets"), sets);
}
