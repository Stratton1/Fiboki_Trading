/**
 * Search ranking for the command line and the palette: exact match first.
 *
 * The SAME tiers as the API (src/fiboki/api/routers/search.py), applied to
 * the workstation's own registry (screens and functions), so API hits and
 * local entries merge into one honest order:
 *
 *   0  exact match of the code, id or label
 *   1  prefix of the code, id or label
 *   2  prefix of a word in the label
 *   3  substring
 *
 * Ties break on kind (instrument before a screen, so EURUSD can never be
 * outranked by "Exposure"), then on the label. This orders search results;
 * it decides nothing about trading.
 */

export type HitKind =
  | "function"
  | "instrument"
  | "strategy"
  | "session"
  | "candidate"
  | "run"
  | "trade"
  | "incident"
  | "screen";

export interface Hit {
  kind: HitKind;
  /** What the operator types to get exactly this (a code, a symbol, an id). */
  id: string;
  label: string;
  detail: string;
  href: string;
  tier: number;
}

const KIND_ORDER: Record<HitKind, number> = {
  function: 0,
  instrument: 1,
  strategy: 2,
  session: 3,
  candidate: 4,
  run: 5,
  trade: 6,
  incident: 7,
  screen: 8,
};

const WORD = /[\s_\-/.:&]+/;

export function tierOf(query: string, id: string, label: string): number | null {
  const q = query.trim().toLowerCase();
  if (q === "") return null;
  const i = id.toLowerCase();
  const l = label.toLowerCase();
  if (q === i || q === l) return 0;
  if (i.startsWith(q) || l.startsWith(q)) return 1;
  if ([...l.split(WORD), ...i.split(WORD)].some((word) => word !== "" && word.startsWith(q))) return 2;
  if (i.includes(q) || l.includes(q)) return 3;
  return null;
}

export function rankHits(hits: readonly Hit[]): Hit[] {
  const seen = new Set<string>();
  const unique: Hit[] = [];
  for (const hit of hits) {
    const key = `${hit.kind}:${hit.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(hit);
  }
  return unique.sort(
    (a, b) => a.tier - b.tier || KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.label.localeCompare(b.label),
  );
}
