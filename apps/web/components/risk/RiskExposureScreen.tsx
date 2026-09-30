"use client";

import { useEffect, useRef } from "react";
import { useApi } from "@/lib/query";
import type { Envelope, ExposureRow, Page, RiskStateView } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { KillSwitchPanel } from "../KillSwitch";
import { CaveatList, PageHead, SourceBadge, Tile } from "../primitives";
import { PageActions } from "../shell/PageHeader";
import { ViewStateTag } from "../ui/ViewStateTag";
import { ExposureMatrix } from "./ExposureMatrix";
import { LimitBoard } from "./LimitBoard";
import { PositionsDrawer } from "./PositionsDrawer";
import { ThrottleMeter } from "./ThrottleMeter";

export const RISK_PATH = "/api/trading/risk";
export const EXPOSURE_PATH = "/api/trading/exposure";

/**
 * RISK & EXPOSURE v2: one screen answering "how close are we to any limit?"
 * (plan §4), served at /trading/risk and /trading/exposure alike; the second
 * opens scrolled to the exposure matrix.
 *
 * Reads: GET /api/trading/risk (RiskStateView: gateway state, daily loss,
 * drawdown, margin, breaches, kill switch) and GET /api/trading/exposure
 * (per-bucket exposure against its limit, with the API's utilisation), both
 * kept current by the `risk` stream topic; GET /api/trading/positions in the
 * drawer. The kill switch is the shell's own control (KillSwitchPanel over
 * useKillSwitchControl); ⇧K opens the same dialog from anywhere and never arms.
 *
 * Each read keeps its own view state. The gateway panel and the exposure
 * matrix are AsyncBoundaries; the limit board and the throttle draw from both
 * reads and state per family what is loading, failed or stale.
 */
export function RiskExposureScreen({ focus }: { focus: "risk" | "exposure" }) {
  const risk = useApi<Envelope<RiskStateView>>(RISK_PATH);
  const exposure = useApi<Page<ExposureRow>>(EXPOSURE_PATH);

  // The exposure route opens at the matrix, once, when the board above it has
  // its first answer (before that, its height is still changing).
  const scrolled = useRef(false);
  const settled = exposure.status !== "loading" && risk.status !== "loading";
  useEffect(() => {
    if (focus !== "exposure" || scrolled.current || !settled) return;
    scrolled.current = true;
    document.getElementById("exposure")?.scrollIntoView({ block: "start" });
  }, [focus, settled]);

  const drawdown = risk.status === "success" ? risk.data.data.drawdown_pct : null;

  return (
    <div className="rx" data-testid="risk-exposure" data-focus={focus}>
      <PageActions>
        <PositionsDrawer />
      </PageActions>
      <PageHead
        title="Risk & Exposure"
        intro="How close the book is to every limit the platform reports, against the limit set in force. Limits the platform enforces but does not report are listed as such, never drawn as zero."
      />
      <div className="rx__grid">
        <div className="rx__main">
          <LimitBoard risk={risk} exposure={exposure} />
        </div>
        <aside className="rx__side" aria-label="Kill switch, gateway and throttle">
          <section className="card" aria-labelledby="rx-kill-switch">
            <h2 id="rx-kill-switch" className="card__title">
              Kill switch
            </h2>
            <KillSwitchPanel />
          </section>
          <section className="card" aria-labelledby="rx-gateway">
            <h2 id="rx-gateway" className="card__title">
              Gateway
            </h2>
            <AsyncBoundary state={risk} label="risk state" onRetry={risk.reload}>
              {(envelope) => <Gateway envelope={envelope} />}
            </AsyncBoundary>
          </section>
          <section className="card">
            <ThrottleMeter drawdown={drawdown} />
          </section>
        </aside>
      </div>
      <section className="card rx__exposure" id="exposure" aria-labelledby="rx-exposure">
        <h2 id="rx-exposure" className="card__title">
          Exposure by instrument, currency and strategy
        </h2>
        <AsyncBoundary
          state={exposure}
          label="exposure"
          onRetry={exposure.reload}
          isEmpty={(page) => page.items.length === 0}
          emptyTitle="No exposure"
          emptyBody="The platform answered with no exposure buckets: nothing is open. That is a real, empty answer."
        >
          {(page) => (
            <>
              <SourceBadge source={page.source} />
              <CaveatList caveats={page.caveats} />
              <ExposureMatrix items={page.items} />
            </>
          )}
        </AsyncBoundary>
        <div className="rx__absent" data-testid="correlated-risk" data-state="absent">
          <span className="rx__absent-title">Correlated open risk</span>
          <ViewStateTag state="absent">NOT REPORTED</ViewStateTag>
          <span className="muted">
            The gateway checks correlated exposure for each order, but the API exposes no correlated-open-risk figure,
            so none is shown here.
          </span>
        </div>
      </section>
    </div>
  );
}

function Gateway({ envelope }: { envelope: Envelope<RiskStateView> }) {
  const view = envelope.data;
  return (
    <div data-testid="gateway-panel">
      <SourceBadge source={envelope.source} />
      <CaveatList caveats={envelope.caveats} />
      <p className="rx__gate" data-testid="gateway-new-risk" data-permitted={view.new_risk_permitted}>
        New risk{" "}
        <span className={`badge badge--${view.new_risk_permitted ? "ok" : "down"}`}>
          {view.new_risk_permitted ? "PERMITTED" : "BLOCKED"}
        </span>{" "}
        <span className="mono muted">{view.new_risk_reason}</span>
      </p>
      <p className="rx__gate" data-testid="gateway-closing" data-permitted={view.closing_permitted}>
        Closing{" "}
        <span className={`badge badge--${view.closing_permitted ? "ok" : "down"}`}>
          {view.closing_permitted ? "PERMITTED" : "BLOCKED"}
        </span>
      </p>
      <p className="rx__gate muted">
        Limit set <span className="mono">{view.limits_version}</span>
      </p>
      {view.breaches.length > 0 ? (
        <div className="state state--error" data-testid="gateway-breaches" role="alert">
          <div className="state__title">Limit breaches, as the API lists them</div>
          <ul>
            {view.breaches.map((breach) => (
              <li key={breach}>{breach}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="tiles rx__tiles">
        <Tile label="Daily P&L" figure={view.daily_loss_pct} colourSign />
        <Tile label="Daily loss limit" figure={view.max_daily_loss_pct} />
        <Tile label="Drawdown" figure={view.drawdown_pct} />
        <Tile label="Drawdown limit" figure={view.max_drawdown_limit_pct} />
        <Tile
          label="Margin utilisation"
          figure={view.margin_utilisation_pct}
          help="Requires a broker account snapshot."
        />
      </div>
    </div>
  );
}
