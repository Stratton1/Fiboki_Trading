"use client";

import { LIMITS_PATH, LimitBoard } from "@/components/risk/LimitBoard";
import { useApi } from "@/lib/query";
import type { Envelope, LimitBoardView } from "@/lib/types";

/** RSK in a tile: the limit board. Utilisation, headroom and bands are the API's. */
export default function RiskPanel() {
  const limits = useApi<Envelope<LimitBoardView>>(LIMITS_PATH, { refreshMs: 15_000 });
  return <LimitBoard limits={limits} />;
}
