"use client";

import { useApi } from "@/lib/api";
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
      <AsyncBoundary state={state} label="correlations" onRetry={state.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <CaveatList caveats={envelope.caveats} />
            <div className="card">
              <Heatmap
                title="Rolling correlation"
                provenance="backtest"
                labels={envelope.data.instruments}
                matrix={envelope.data.matrix}
              />
            </div>
          </>
        )}
      </AsyncBoundary>
    </>
  );
}
