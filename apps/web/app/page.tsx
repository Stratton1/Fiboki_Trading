"use client";

import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { AttentionPanel } from "@/components/command/AttentionPanel";
import { FleetStrip } from "@/components/command/FleetStrip";
import { IncidentsPanel } from "@/components/command/IncidentsPanel";
import { Card, PageHead } from "@/components/primitives";
import { LazyKillSwitchTimeline } from "@/components/LazyChart";
import { DerivedNote, LimitRowItem } from "@/components/risk/LimitRow";
import { ViewStateTag } from "@/components/ui/ViewStateTag";
import { lossRows } from "@/lib/limits-core";
import { useApi } from "@/lib/query";
import type { Envelope, RiskStateView } from "@/lib/types";

/**
 * COMMAND (Wave 4a): "Is anything wrong, and what needs me now?"
 *
 * The hero is the server-ranked attention queue as triage rows, rendered in
 * the order given (components/command/AttentionPanel.tsx). Below it, how close
 * the book is to its two loss limits and the fleet strip, then incidents and
 * the kill-switch timeline. V1's dashboard led with "Fleet PnL (live)", which
 * was a backtest number; this page leads with what needs a human, and every
 * figure carries the provenance the API gave it.
 *
 * The limits, from GET /api/trading/risk (V-1): the Risk page's own daily-loss
 * and drawdown bars (LimitRowItem over lib/limits-core.ts `lossRows`, which
 * the Risk page's `riskRows` is built from), so the two
 * screens cannot draw the same limit differently: an OK bar in the figure's
 * provenance ink, NEAR and CRITICAL in the health tokens, BREACHED filled,
 * the utilisation marked † because the workstation divides two API figures.
 * Open risk and the drawdown throttle are not reported by the API; one line
 * says so rather than two tiles leading the page with absences.
 */
export default function CommandPage() {
  const risk = useApi<Envelope<RiskStateView>>("/api/trading/risk");
  return (
    <>
      <PageHead
        title="Command"
        intro="What needs you now, in the platform's own order. Then how close the book is to its loss limits, the fleet, incidents and the kill switch's month."
      />

      <Card title="Needs attention">
        <AttentionPanel />
      </Card>

      <div className="command__grid">
        <section className="card" aria-labelledby="command-book">
          <h2 id="command-book" className="card__title">
            Loss limits
          </h2>
          <AsyncBoundary state={risk} label="risk state" onRetry={risk.reload}>
            {(envelope) => {
              const rows = lossRows(envelope.data);
              return (
                <div data-testid="command-limits">
                  <ul className="limit-family__rows">
                    {rows.map((row) => (
                      <LimitRowItem key={row.key} row={row} signed={row.family === "loss"} showChip />
                    ))}
                  </ul>
                  <DerivedNote exposure={false} />
                  <p className="command__absent" data-testid="command-absent" data-state="absent">
                    <ViewStateTag state="absent">NOT REPORTED</ViewStateTag> Open risk (risk at stop) and the drawdown
                    throttle step are not reported by the API, so neither is shown.{" "}
                    <Link href="/trading/risk">Risk &amp; Exposure</Link> lists every limit it does report.
                  </p>
                </div>
              );
            }}
          </AsyncBoundary>
        </section>

        <section className="card" aria-labelledby="command-fleet">
          <h2 id="command-fleet" className="card__title">
            Fleet
          </h2>
          <FleetStrip />
        </section>
      </div>

      <Card title="Incidents">
        <IncidentsPanel />
      </Card>

      <LazyKillSwitchTimeline />
    </>
  );
}
