"use client";

import { useApi } from "@/lib/api";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { PageHead, SourceBadge, Tile } from "@/components/primitives";
import type { Envelope, ResearchMemoryView } from "@/lib/types";

/** INTELLIGENCE · Research Memory: "have we tried this already?" */
export default function ResearchMemoryPage() {
  const state = useApi<Envelope<ResearchMemoryView>>(
    "/api/intelligence/research-memory",
  );
  return (
    <>
      <PageHead
        title="Research Memory"
        intro="Answered before the work, not after it. An automated search that only remembers its successes keeps rediscovering its failures."
      />
      <AsyncBoundary state={state} label="research memory" onRetry={state.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <div
              className={`state state--${envelope.data.available ? "empty" : "error"}`}
            >
              <div className="state__title">
                {envelope.data.available ? "Available" : "Unavailable"}
              </div>
              <div className="state__body">{envelope.data.detail}</div>
            </div>
            <div className="tiles">
              <Tile label="Structures recorded" figure={envelope.data.structures_recorded} />
              <Tile label="Rediscovery rate" figure={envelope.data.rediscovery_rate} />
            </div>
          </>
        )}
      </AsyncBoundary>
    </>
  );
}
