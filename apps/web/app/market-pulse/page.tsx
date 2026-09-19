"use client";

import { useApi } from "@/lib/api";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { BarChart } from "@/components/charts";
import { Card, PageHead, SourceBadge } from "@/components/primitives";
import type { InstrumentRow, Page, RegimeRow } from "@/lib/types";

/** COMMAND · Market Pulse: what the universe looks like right now. */
export default function MarketPulsePage() {
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments?limit=40");
  const regimes = useApi<Page<RegimeRow>>("/api/markets/regimes");

  return (
    <>
      <PageHead
        title="Market Pulse"
        intro="The traded universe and its regime standing. Where a regime cannot be computed the platform says so rather than guessing one."
      />
      <Card title="Spread cost across the universe">
        <AsyncBoundary state={instruments} label="instruments" onRetry={instruments.reload}>
          {(page) => (
            <>
              <SourceBadge source={page.source} />
              <BarChart
                title="Typical spread (pips)"
                provenance="backtest"
                unit="pips"
                bars={page.items.slice(0, 20).map((row) => ({
                  label: row.symbol,
                  value: row.typical_spread_pips.value,
                }))}
              />
            </>
          )}
        </AsyncBoundary>
      </Card>
      <Card title="Regime standing">
        <AsyncBoundary state={regimes} label="regimes" onRetry={regimes.reload}>
          {(page) => {
            const known = page.items.filter((row) => row.available).length;
            return (
              <>
                <SourceBadge source={page.source} />
                <p>
                  {known} of {page.items.length} instruments have a computed regime.
                </p>
                {known === 0 ? (
                  <div className="state state--error">
                    <div className="state__title">No regime is computable</div>
                    <div className="state__body">
                      Every regime-gated strategy is unevaluable in this
                      deployment. This is a missing input, not a calm market.
                    </div>
                  </div>
                ) : null}
              </>
            );
          }}
        </AsyncBoundary>
      </Card>
    </>
  );
}
