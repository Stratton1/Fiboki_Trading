import { PROVENANCES, type Provenance, type SourceNote } from "./types";

/**
 * What a chart (or any aggregate over many rows) can honestly say about where
 * its numbers came from. It is derived from the data, never chosen by a page.
 *
 *  - `single`: every contributing value carries the same provenance;
 *  - `mixed`: values carry more than one, with a count per provenance;
 *  - `source`: the payload is not a backtest/paper result and carries no
 *    per-value provenance, only the API's SourceNote for the whole payload;
 *  - `unlabelled`: the API supplied nothing to label it with. Rendered as
 *    exactly that, never as a guess.
 */
export type ProvenanceLabel =
  | { kind: "single"; provenance: Provenance; count: number }
  | { kind: "mixed"; counts: { provenance: Provenance; count: number }[] }
  | { kind: "source"; source: SourceNote }
  | { kind: "unlabelled"; reason: string };

/**
 * Derive the label for an aggregate from the provenances of the values that
 * actually went into it. Pass one entry per contributing value.
 */
export function deriveProvenance(
  provenances: readonly Provenance[],
  emptyReason = "No value in this view carries a provenance.",
): ProvenanceLabel {
  const counts = new Map<Provenance, number>();
  for (const p of provenances) {
    // A tally of rows we HAVE, so starting from one is a count, not a default.
    const seen = counts.get(p);
    counts.set(p, seen === undefined ? 1 : seen + 1);
  }
  if (counts.size === 0) return { kind: "unlabelled", reason: emptyReason };
  // Stable order: the canonical provenance order, not arrival order.
  const ordered = PROVENANCES.filter((p) => counts.has(p)).map((p) => ({
    provenance: p,
    count: counts.get(p) as number,
  }));
  const first = ordered[0];
  if (ordered.length === 1 && first) {
    return { kind: "single", provenance: first.provenance, count: first.count };
  }
  return { kind: "mixed", counts: ordered };
}

export function singleProvenance(provenance: Provenance): ProvenanceLabel {
  return { kind: "single", provenance, count: 1 };
}
