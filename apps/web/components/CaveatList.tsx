import type { Caveat } from "@/lib/types";

/**
 * Realism caveats. Always rendered from the payload: a caveat written into a
 * page is a caveat that goes stale the moment the model behind it changes, and
 * V1 shipped "Estimated realistic return: 190–230%" as page copy beside a live
 * computed value.
 */
export function CaveatList({ caveats }: { caveats: Caveat[] }) {
  if (caveats.length === 0) return null;
  return (
    <div className="caveats" data-testid="caveat-list">
      {caveats.map((caveat) => (
        <div
          key={caveat.code + caveat.affects}
          className={`caveat caveat--${caveat.severity}`}
          data-testid="caveat"
          data-code={caveat.code}
        >
          <span className="caveat__code">
            {caveat.code}
            {caveat.direction !== "unknown" ? ` · reads ${caveat.direction}` : ""}
          </span>
          {caveat.message}
        </div>
      ))}
    </div>
  );
}

/**
 * Caveats split into those an operator must see now (warning, critical) and
 * the rest, in their given order. A loop rather than two filters, so a panel
 * that must not filter its ITEMS (the attention queue) need not filter at all.
 */
export function partitionCaveats(caveats: readonly Caveat[]): { loud: Caveat[]; quiet: Caveat[] } {
  const loud: Caveat[] = [];
  const quiet: Caveat[] = [];
  for (const caveat of caveats) {
    (caveat.severity === "warning" || caveat.severity === "critical" ? loud : quiet).push(caveat);
  }
  return { loud, quiet };
}
