"use client";

import { useApi } from "@/lib/query";
import { AbsentNotice, UNWIRED_CODES } from "@/components/AbsentNotice";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Heatmap } from "@/components/charts";
import { CaveatList, PageHead, SourceBadge } from "@/components/primitives";
import type { CorrelationView, Envelope } from "@/lib/types";

/** MARKETS · Correlations: an empty matrix is NOT an uncorrelated book. */
export default function CorrelationsPage() {
  const state = useApi<Envelope<CorrelationView>>("/api/markets/correlations");
  return (
    <>
      <PageHead
        title="Correlations"
        intro="Windowed correlation across the traded universe. Correlated-exposure limits cannot be enforced meaningfully without this."
      />
      {state.status === "error" && UNWIRED_CODES.has(state.error.code) ? (
        <AbsentNotice title="Correlations are not wired" detail={state.error.message} />
      ) : (
      <AsyncBoundary state={state} label="correlations" onRetry={state.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <CaveatList caveats={envelope.caveats} />
            <div className="card">
              <Heatmap
                title="Rolling correlation"
                // TODO(backend): CorrelationView carries no provenance and no
                // as_of for the window it was estimated over. Add
                // `provenance: Provenance` and `as_of: datetime` to the
                // /api/markets/correlations payload; until then the chip
                // shows the envelope's SourceNote, which is what the API
                // actually supplies, instead of a guessed provenance.
                provenance={{ kind: "source", source: envelope.source }}
                labels={envelope.data.instruments}
                matrix={envelope.data.matrix}
              />
            </div>
          </>
        )}
      </AsyncBoundary>
      )}
    </>
  );
}
