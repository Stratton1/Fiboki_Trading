import type { components } from "./generated/openapi";
import { apiFetch } from "./api";
import type { Envelope } from "./types";

/**
 * The workspace contract (GET/PUT /api/workspace/...), as GENERATED types:
 * the panel kinds, link groups and the tiling tree are the API's own unions,
 * so a kind the API does not know cannot be tiled here, and the panel
 * registry (components/workspace/registry.tsx) is exhaustive by type.
 *
 * The tree operations below are display geometry (which box holds which
 * panel), never market logic. Every number a panel shows comes from its API.
 */
type S = components["schemas"];

export type PanelKind = S["PanelConfig"]["kind"];
export type LinkGroup = S["PanelLink"]["group"];
export type LinkRole = S["PanelLink"]["role"];
export type PanelConfig = S["PanelConfig"];
export type PanelLeaf = S["PanelLeaf"];
export type SplitNode = S["SplitNode-Output"];
export type LayoutNode = SplitNode | PanelLeaf;
export type LayoutDoc = S["LayoutDoc-Output"];
export type LayoutView = S["LayoutView"];
export type LayoutSummary = S["LayoutSummary"];
export type LayoutPreset = S["LayoutPreset"];
export type LayoutRevision = S["LayoutRevisionView"];
export type PanelKindSpec = S["PanelKindSpec"];
export type ColumnSet = S["ColumnSet"];
export type ColumnSetsView = S["ColumnSetsView"];

export const LINK_GROUPS: readonly LinkGroup[] = ["none", "amber", "cyan", "violet", "white"];
export const LINK_ROLES: readonly LinkRole[] = ["both", "source", "destination"];
export const MAX_PANELS = 16;
export const MAX_DEPTH = 5;

export const PATHS = {
  panels: "/api/workspace/panels",
  presets: "/api/workspace/presets",
  layouts: "/api/workspace/layouts",
  layout: (name: string) => `/api/workspace/layouts/${encodeURIComponent(name)}`,
  history: (name: string) => `/api/workspace/layouts/${encodeURIComponent(name)}/history`,
  columnSets: (grid: string) => `/api/workspace/column-sets/${encodeURIComponent(grid)}`,
} as const;

export function saveLayout(
  name: string,
  baseVersion: number,
  layout: LayoutDoc,
  action: "save" | "autosave",
): Promise<Envelope<LayoutView>> {
  return apiFetch<Envelope<LayoutView>>(PATHS.layout(name), {
    method: "PUT",
    body: JSON.stringify({ base_version: baseVersion, layout, action }),
  });
}

export function deleteLayout(name: string, baseVersion: number): Promise<void> {
  return apiFetch<void>(`${PATHS.layout(name)}?base_version=${baseVersion}`, { method: "DELETE" });
}

export function saveColumnSets(
  grid: string,
  baseVersion: number,
  sets: ColumnSet[],
  defaultSet: string | null,
): Promise<Envelope<ColumnSetsView>> {
  return apiFetch<Envelope<ColumnSetsView>>(PATHS.columnSets(grid), {
    method: "PUT",
    body: JSON.stringify({ base_version: baseVersion, sets, default: defaultSet }),
  });
}

// ------------------------------------------------------------ tree geometry

export function isSplit(node: LayoutNode): node is SplitNode {
  return node.type === "split";
}

/** Panel ids in reading order (depth first): Ctrl/⌘+1..9 and Tab follow it. */
export function panelOrder(node: LayoutNode): string[] {
  return isSplit(node) ? node.children.flatMap(panelOrder) : [node.panel];
}

function depthOf(node: LayoutNode): number {
  return isSplit(node) ? 1 + Math.max(...node.children.map(depthOf)) : 0;
}

function depthTo(node: LayoutNode, panel: string, depth = 0): number | null {
  if (!isSplit(node)) return node.panel === panel ? depth : null;
  for (const child of node.children) {
    const d = depthTo(child, panel, depth + 1);
    if (d !== null) return d;
  }
  return null;
}

let counter = 0;
/** A short unique id for a new panel or split (unique within the layout). */
export function newId(prefix: string, taken: ReadonlySet<string>): string {
  for (;;) {
    counter += 1;
    const id = `${prefix}${Date.now().toString(36).slice(-4)}${counter.toString(36)}`;
    if (!taken.has(id)) return id;
  }
}

function idsOf(doc: LayoutDoc): Set<string> {
  const out = new Set<string>(doc.panels.map((p) => p.id));
  const walk = (n: LayoutNode) => {
    if (isSplit(n)) {
      out.add(n.id);
      n.children.forEach(walk);
    }
  };
  walk(doc.root);
  return out;
}

/** Sizes that sum to exactly 100 (two decimals), so the API accepts them. */
export function normalise(sizes: number[]): number[] {
  const total = sizes.reduce((a, b) => a + b, 0) || 1;
  const scaled = sizes.map((s) => Math.round(((s / total) * 100) * 100) / 100);
  const drift = Math.round((100 - scaled.reduce((a, b) => a + b, 0)) * 100) / 100;
  const last = scaled.length - 1;
  scaled[last] = Math.round(((scaled[last] as number) + drift) * 100) / 100;
  return scaled;
}

function mapNode(node: LayoutNode, fn: (n: LayoutNode) => LayoutNode | null): LayoutNode | null {
  const mapped = fn(node);
  if (mapped !== node || !isSplit(node)) return mapped;
  const children: LayoutNode[] = [];
  const sizes: number[] = [];
  node.children.forEach((child, i) => {
    const next = mapNode(child, fn);
    if (next !== null) {
      children.push(next);
      sizes.push(node.sizes[i] as number);
    }
  });
  if (children.length === 0) return null;
  if (children.length === 1) return children[0] ?? null;
  return { ...node, children, sizes: normalise(sizes) };
}

export type TreeRefusal = { ok: false; reason: string };
export type TreeResult = { ok: true; doc: LayoutDoc } | TreeRefusal;

/** Split a panel's box in two, putting `panel` beside (horizontal) or below (vertical) it. */
export function splitPanel(
  doc: LayoutDoc,
  target: string,
  orientation: "horizontal" | "vertical",
  panel: Omit<PanelConfig, "id">,
): TreeResult {
  if (doc.panels.length >= MAX_PANELS) return { ok: false, reason: `A layout holds at most ${MAX_PANELS} panels.` };
  const depth = depthTo(doc.root, target);
  if (depth === null) return { ok: false, reason: "That panel is not in the layout." };
  const taken = idsOf(doc);
  const pid = newId("p", taken);
  taken.add(pid);
  const sid = newId("s", taken);
  const root = mapNode(doc.root, (n) => {
    if (isSplit(n) || n.panel !== target) return n;
    return { type: "split", id: sid, orientation, sizes: [50, 50], children: [n, { type: "panel", panel: pid }] };
  });
  if (root === null) return { ok: false, reason: "The layout would be empty." };
  if (depthOf(root) > MAX_DEPTH) return { ok: false, reason: `Splits nest at most ${MAX_DEPTH} deep.` };
  return { ok: true, doc: { ...doc, root, panels: [...doc.panels, { ...panel, id: pid }] } };
}

/** Add a panel beside everything (a new column at the right of the top split). */
export function addPanel(doc: LayoutDoc, panel: Omit<PanelConfig, "id">): TreeResult {
  if (doc.panels.length >= MAX_PANELS) return { ok: false, reason: `A layout holds at most ${MAX_PANELS} panels.` };
  const taken = idsOf(doc);
  const pid = newId("p", taken);
  taken.add(pid);
  const leaf: PanelLeaf = { type: "panel", panel: pid };
  let root: LayoutNode;
  if (isSplit(doc.root) && doc.root.orientation === "horizontal" && doc.root.children.length < 6) {
    const n = doc.root.children.length + 1;
    const share = 100 / n;
    root = {
      ...doc.root,
      children: [...doc.root.children, leaf],
      sizes: normalise([...doc.root.sizes.map((s) => s * (1 - share / 100)), share]),
    };
  } else {
    root = { type: "split", id: newId("s", taken), orientation: "horizontal", sizes: [70, 30], children: [doc.root, leaf] };
  }
  if (depthOf(root) > MAX_DEPTH) return { ok: false, reason: `Splits nest at most ${MAX_DEPTH} deep.` };
  return { ok: true, doc: { ...doc, root, panels: [...doc.panels, { ...panel, id: pid }] } };
}

/** Close a panel; its sibling takes the space. The last panel cannot be closed. */
export function closePanel(doc: LayoutDoc, target: string): TreeResult {
  if (doc.panels.length <= 1) return { ok: false, reason: "A layout keeps at least one panel." };
  const root = mapNode(doc.root, (n) => (!isSplit(n) && n.panel === target ? null : n));
  if (root === null) return { ok: false, reason: "The layout would be empty." };
  return { ok: true, doc: { ...doc, root, panels: doc.panels.filter((p) => p.id !== target) } };
}

/** New sizes for one split (from a drag or a keyboard resize). */
export function resizeSplit(doc: LayoutDoc, splitId: string, sizes: number[]): LayoutDoc {
  const root = mapNode(doc.root, (n) =>
    isSplit(n) && n.id === splitId && n.children.length === sizes.length ? { ...n, sizes: normalise(sizes) } : n,
  );
  return root === null ? doc : { ...doc, root };
}

export function updatePanel(doc: LayoutDoc, id: string, patch: Partial<Omit<PanelConfig, "id">>): LayoutDoc {
  return { ...doc, panels: doc.panels.map((p) => (p.id === id ? { ...p, ...patch } : p)) };
}

/**
 * TWS-style linking: a SOURCE (or both) panel in a colour group names a
 * symbol; every DESTINATION (or both) panel in that group that shows one
 * symbol follows it. A panel with no group links to nothing.
 */
export function retarget(
  doc: LayoutDoc,
  from: string,
  symbol: string,
  follows: (kind: PanelKind) => boolean,
): LayoutDoc {
  const source = doc.panels.find((p) => p.id === from);
  if (!source || source.link.group === "none" || source.link.role === "destination") return doc;
  const group = source.link.group;
  let changed = false;
  const panels = doc.panels.map((p) => {
    if (p.id === from || p.link.group !== group || p.link.role === "source" || !follows(p.kind)) return p;
    if (p.symbol === symbol) return p;
    changed = true;
    return { ...p, symbol };
  });
  return changed ? { ...doc, panels } : doc;
}

/** A structural fingerprint: changes when panels move, not when sizes do. */
export function structureKey(node: LayoutNode): string {
  return isSplit(node) ? `${node.id}:${node.orientation}(${node.children.map(structureKey).join(",")})` : node.panel;
}
