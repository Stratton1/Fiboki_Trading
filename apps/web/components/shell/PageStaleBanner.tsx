"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useStaleViews } from "@/lib/stale-views";

/**
 * The page-level stale banner (programme P1 11): when any panel on this page
 * is showing last-known-good data, one line under the page header says how
 * many, and offers to re-read them. The panels themselves are dimmed in place.
 * It reserves no space when nothing is stale, and it sits above the page, so
 * appearing never moves a panel the operator is reading sideways.
 */
export function PageStaleBanner() {
  const { stale } = useStaleViews();
  const client = useQueryClient();
  if (stale.length === 0) return null;
  return (
    <div className="page-stale" role="status" data-testid="page-stale-banner" data-count={stale.length}>
      <span className="page-stale__tag">STALE</span>
      <span>
        {stale.length} panel{stale.length === 1 ? "" : "s"} on this page {stale.length === 1 ? "is" : "are"} showing
        last-known-good data, dimmed below. Do not act on {stale.length === 1 ? "it" : "them"} as current.
      </span>
      <button
        type="button"
        className="page-stale__retry"
        data-testid="page-stale-retry"
        onClick={() => void client.invalidateQueries({ predicate: (q) => q.queryKey[0] === "api" })}
      >
        Re-read
      </button>
      <span className="sr-only">Stale reads: {stale.join(", ")}</span>
    </div>
  );
}
