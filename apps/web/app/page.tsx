"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { AttentionPanel } from "@/components/command/AttentionPanel";
import { FleetStrip } from "@/components/command/FleetStrip";
import { IncidentsPanel } from "@/components/command/IncidentsPanel";
import { FigureValue } from "@/components/FigureValue";
import { Card, PageHead } from "@/components/primitives";
import { Stat } from "@/components/Stat";
import { ViewStateTag } from "@/components/ui/ViewStateTag";
import { useApi } from "@/lib/query";
import type { Envelope, RiskStateView } from "@/lib/types";

/**
 * COMMAND (Wave 4a): "Is anything wrong, and what needs me now?"
 *
 * The hero is the server-ranked attention queue as triage rows, rendered in
 * the order given (components/command/AttentionPanel.tsx). Below it, the
 * three numbers an operator wants first and the fleet strip, then incidents.
 * V1's dashboard led with "Fleet PnL (live)", which was a backtest number;
 * this page leads with what needs a human, and every figure carries the
 * provenance the API gave it.
 *
 * The three numbers, from GET /api/trading/risk:
 *  - open risk: the API reports no open-risk (risk at stop) figure, so the
 *    tile says NOT REPORTED rather than borrowing a different number;
 *  - today's P&L: `daily_loss_pct`, the day's net P&L as a percentage of the
 *    starting balance, with its provenance;
 *  - drawdown throttle: the API does not report the step in force, so NOT
 *    REPORTED, with the drawdown the API does report beside it.
 */
export default function CommandPage() {
  const risk = useApi<Envelope<RiskStateView>>("/api/trading/risk");
  return (
    <>
      <PageHead
        title="Command"
        intro="What needs you now, in the platform's own order. Then the three numbers, the fleet and incidents."
      />

      <Card title="Needs attention">
        <AttentionPanel />
      </Card>

      <div className="command__grid">
        <section className="card" aria-labelledby="command-book">
          <h2 id="command-book" className="card__title">
            First numbers
          </h2>
          <AsyncBoundary state={risk} label="risk state" onRetry={risk.reload}>
            {(envelope) => (
              <div className="tiles command__tiles">
                <AbsentStat label="Open risk">
                  The API reports no open-risk (risk at stop) figure.{" "}
                  <Link href="/trading/risk">Risk &amp; Exposure</Link> shows every limit it does report.
                </AbsentStat>
                <Stat
                  label="Today's P&L"
                  figure={envelope.data.daily_loss_pct}
                  signed
                  help="Day net P&L as % of the starting balance. For a replayed journal, its last replayed day."
                />
                <AbsentStat label="Drawdown throttle">
                  The step in force is not reported. Drawdown:{" "}
                  <FigureValue figure={envelope.data.drawdown_pct} /> of a{" "}
                  <FigureValue figure={envelope.data.max_drawdown_limit_pct} showChip={false} /> limit.
                </AbsentStat>
              </div>
            )}
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
    </>
  );
}

/** A stat tile for a number the API does not report: the reason, never a zero. */
function AbsentStat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="tile stat" data-testid="tile" data-label={label} data-state="absent">
      <div className="tile__label">{label}</div>
      <div className="tile__value">
        <ViewStateTag state="absent">NOT REPORTED</ViewStateTag>
      </div>
      <div className="tile__help">{children}</div>
    </div>
  );
}
