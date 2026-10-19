"use client";

import Link from "next/link";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { sessionControlPath, type BotControlView, type ControlAction } from "@/lib/ops";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { Envelope } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { Card, PageHead, SourceBadge } from "../primitives";
import { UrlState } from "../UrlState";
import { BotControlButton, ControlStateTag } from "./BotControl";

/**
 * One paper bot: control, reconciliation, maturity and audit (Wave 3O).
 * `?act=pause|resume|drain` (from the command line's `PAUSE <bot>`) opens the
 * control dialog with that act chosen; it never acts by itself.
 */
const actParser = parseAsStringLiteral(["pause", "resume", "drain"] as const).withOptions({ history: "replace" });

export function SessionView({ sessionId }: { sessionId: string }) {
  return (
    <>
      <PageHead
        title={`Bot ${sessionId}`}
        intro="One paper bot: its operator control state and history, the trade-for-trade reconciliation of its paper record against the backtest engine over the same closed bars, the advisory maturity report, and its hash-chained audit log. Every state and verdict here is the platform's."
      >
        <span className="ops-head-links">
          <Link href={route.session(sessionId)} data-testid="session-fleet-link">
            fleet row
          </Link>
        </span>
      </PageHead>
      <UrlState fallback={<ControlCard sessionId={sessionId} act={null} />}>
        <ControlFromUrl sessionId={sessionId} />
      </UrlState>
    </>
  );
}

function ControlFromUrl({ sessionId }: { sessionId: string }) {
  const [act] = useQueryState("act", actParser);
  return <ControlCard sessionId={sessionId} act={act} />;
}

function ControlCard({ sessionId, act }: { sessionId: string; act: ControlAction | null }) {
  const state = useApi<Envelope<BotControlView>>(sessionControlPath(sessionId), { refreshMs: 15_000 });
  return (
    <Card title="Control">
      <AsyncBoundary state={state} label="the bot's control state" onRetry={state.reload}>
        {(env) => {
          const view = env.data;
          return (
            <div data-testid="session-control" data-state={view.state}>
              <SourceBadge source={env.source} />
              <div className="ops-row">
                <ControlStateTag state={view.state} />
                <span className="ops-meaning">{view.meaning}</span>
                {view.controllable ? (
                  <BotControlButton sessionId={sessionId} request={act} />
                ) : (
                  <span className="muted" data-testid="session-not-controllable">
                    Not controllable: this is not a running forward paper bot.
                  </span>
                )}
              </div>
              {view.since ? (
                <p className="muted ops-since">
                  since {formatTimestamp(view.since)} · {view.actor} · {view.reason}
                </p>
              ) : null}
              <h3 className="ops-subhead">History</h3>
              {view.history.length === 0 ? (
                <p className="muted" data-testid="session-control-history-empty">
                  Never paused, drained or resumed.
                </p>
              ) : (
                <div className="table-wrap">
                  <table className="dense" data-testid="session-control-history">
                    <thead>
                      <tr>
                        <th scope="col">At (UTC)</th>
                        <th scope="col">Act</th>
                        <th scope="col">From → to</th>
                        <th scope="col">By</th>
                        <th scope="col">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.history.map((r) => (
                        <tr key={`${r.at}-${r.action}`} data-testid="control-history-row">
                          <td className="num">{formatTimestamp(r.at)}</td>
                          <td className="mono">{r.action.toUpperCase()}</td>
                          <td className="mono">
                            {r.state_before} → {r.state_after}
                          </td>
                          <td>
                            {r.actor} <span className="muted">({r.actor_role})</span>
                          </td>
                          <td className="wrap">{r.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        }}
      </AsyncBoundary>
    </Card>
  );
}
