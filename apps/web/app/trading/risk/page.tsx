"use client";

import { lazy, Suspense } from "react";
import { RiskExposureScreen } from "@/components/risk/RiskExposureScreen";

// wave ops: the risk-of-ruin and position-size calculator, loaded after the board.
const RiskCalculator = lazy(() => import("@/components/ops/RiskCalculator"));

/**
 * TRADING · Risk & Exposure (v2). One screen with /trading/exposure: "how
 * close are we to any limit?" This route opens at the limit board. Below it,
 * the risk-of-ruin and position-size calculator (Wave 3O), computed by the API.
 */
export default function RiskPage() {
  return (
    <>
      <RiskExposureScreen focus="risk" />
      <Suspense fallback={null}>
        <RiskCalculator />
      </Suspense>
    </>
  );
}
