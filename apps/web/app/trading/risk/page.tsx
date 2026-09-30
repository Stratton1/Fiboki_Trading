"use client";

import { RiskExposureScreen } from "@/components/risk/RiskExposureScreen";

/**
 * TRADING · Risk & Exposure (v2). One screen with /trading/exposure: "how
 * close are we to any limit?" This route opens at the limit board.
 */
export default function RiskPage() {
  return <RiskExposureScreen focus="risk" />;
}
