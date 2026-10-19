"use client";

import { AsyncBoundary } from "@/components/AsyncBoundary";
import { ExposureMatrix } from "@/components/risk/ExposureMatrix";
import { LIMITS_PATH } from "@/components/risk/LimitBoard";
import { familyRows } from "@/lib/limits-core";
import { useApi } from "@/lib/query";
import type { Envelope, LimitBoardView } from "@/lib/types";
import { PanelStamp } from "../PanelStamp";

/** EXPO in a tile: the exposure family of the limit board, as a matrix. */
export default function ExposurePanel() {
  const limits = useApi<Envelope<LimitBoardView>>(LIMITS_PATH, { refreshMs: 15_000 });
  return (
    <AsyncBoundary
      state={limits}
      label="exposure"
      onRetry={limits.reload}
      isEmpty={(envelope) => !envelope.data.families.some((f) => f.id === "exposure" && f.rows.length > 0)}
      emptyTitle="No exposure"
      emptyBody="The platform answered with no exposure buckets: nothing is open. That is a real, empty answer."
    >
      {(envelope) => (
        <>
          <PanelStamp source={envelope.data.sources.exposure ?? envelope.source} />
          <ExposureMatrix
            rows={familyRows(envelope.data, envelope.data.families.find((f) => f.id === "exposure")?.rows ?? [])}
          />
        </>
      )}
    </AsyncBoundary>
  );
}
