"use client";

import { useApi } from "@/lib/api";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { KillSwitchPanel } from "@/components/KillSwitch";
import { Card, PageHead, SourceBadge, Tile } from "@/components/primitives";
import type { Envelope, RiskStateView } from "@/lib/types";

/** TRADING · Risk: the gateway's view, and the kill switch beside it. */
export default function RiskPage() {
  const state = useApi<Envelope<RiskStateView>>("/api/trading/risk");
  return (
    <>
      <PageHead
        title="Risk"
        intro="What the risk gateway currently permits, against the versioned limit set. The kill switch lives here and on Overview — it is reachable in every mode."
      />
      <AsyncBoundary state={state} label="risk state" onRetry={state.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <div className="tiles">
              <Tile label="Daily P&L" figure={envelope.data.daily_loss_pct} colourSign />
              <Tile label="Daily loss limit" figure={envelope.data.max_daily_loss_pct} />
              <Tile label="Drawdown" figure={envelope.data.drawdown_pct} />
              <Tile label="Drawdown limit" figure={envelope.data.max_drawdown_limit_pct} />
              <Tile
                label="Margin utilisation"
                figure={envelope.data.margin_utilisation_pct}
                help="Requires a broker account snapshot."
              />
            </div>
            <Card title="Gateway">
              <p>
                New risk:{" "}
                <span
                  className={`badge badge--${envelope.data.new_risk_permitted ? "ok" : "down"}`}
                >
                  {envelope.data.new_risk_permitted ? "PERMITTED" : "BLOCKED"}
                </span>{" "}
                <span className="mono muted">{envelope.data.new_risk_reason}</span>
              </p>
              <p>
                Closing:{" "}
                <span
                  className={`badge badge--${envelope.data.closing_permitted ? "ok" : "down"}`}
                >
                  {envelope.data.closing_permitted ? "PERMITTED" : "BLOCKED"}
                </span>
              </p>
              {envelope.data.breaches.length > 0 ? (
                <div className="state state--error">
                  <div className="state__title">Limit breaches</div>
                  <ul>
                    {envelope.data.breaches.map((breach) => (
                      <li key={breach}>{breach}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </Card>
          </>
        )}
      </AsyncBoundary>
      <Card title="Kill switch">
        <KillSwitchPanel />
      </Card>
    </>
  );
}
