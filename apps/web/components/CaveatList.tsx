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
