"use client";

import { RiskExposureScreen } from "@/components/risk/RiskExposureScreen";

/**
 * TRADING · Risk & Exposure (v2). One screen with /trading/risk; this route
 * opens scrolled to the exposure matrix, so links and bookmarks to it keep
 * landing on exposure.
 */
export default function ExposurePage() {
  return <RiskExposureScreen focus="exposure" />;
}
