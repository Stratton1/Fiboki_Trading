"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { Allocation } from "@/components/ops/Allocation";
import { UrlState } from "@/components/UrlState";

/**
 * FLEET · Allocation (Wave 3O): advisory strategy-portfolio weights.
 * Equal-risk, inverse-vol and minimum-correlation weights across the
 * strategies (backtest basis) or the paper bots (paper basis), with the
 * portfolio each would have been. Display only; `?basis=` is URL state.
 */
const basisParser = parseAsStringLiteral(["backtest", "paper"] as const).withDefault("backtest").withOptions({ history: "replace" });

export default function AllocationPage() {
  return (
    <UrlState fallback={<Allocation basis="backtest" onBasis={() => undefined} />}>
      <AllocationFromUrl />
    </UrlState>
  );
}

function AllocationFromUrl() {
  const [basis, setBasis] = useQueryState("basis", basisParser);
  return <Allocation basis={basis} onBasis={(b) => void setBasis(b)} />;
}
