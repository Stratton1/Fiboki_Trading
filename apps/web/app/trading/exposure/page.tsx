"use client";

import { RiskExposureScreen } from "@/components/risk/RiskExposureScreen";

/**
 * TRADING · Risk & Exposure (v2). One screen with /trading/risk; this route
 * opens with the exposure matrix first, so links and bookmarks to it keep
 * landing on exposure (and nothing has to scroll to it once the board loads).
 */
export default function ExposurePage() {
  return <RiskExposureScreen focus="exposure" />;
}
