"use client";

import Link from "next/link";
import { SELF_CORRELATION_PATH, type SelfCorrelationView } from "@/lib/ops";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { Envelope } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { Card, SourceBadge } from "../primitives";

/**
 * Self-correlation of each candidate with the paper fleet (Wave 3O), from
 * GET /api/trading/candidates/correlation. Each cell is the API's Pearson rho
 * on common days, on two bases (the bot strategy's backtest, and the bot's own
 * paper record); the API decides which cells are FLAGGED (|rho| above its
 * threshold, the bot's own strategy excluded). Advisory: nothing reads it.
 */
export function SelfCorrelation() {
  const state = useApi<Envelope<SelfCorrelationView>>(SELF_CORRELATION_PATH, { refreshMs: 120_000 });
  return (
    <Card title="Self-correlation with the paper fleet (advisory)">
      <AsyncBoundary
        state={state}
        label="self-correlation"
        onRetry={state.reload}
        isEmpty={(env) => env.data.rows.length === 0}
        emptyTitle="No candidates to compare"
        emptyBody="The platform lists no strategy document, so there is nothing to correlate."
      >
        {(env) => {
          const v = env.data;
          return (
            <div data-testid="self-correlation">
              <SourceBadge source={env.source} />
              <p className="ops-hint">{v.method}</p>
              {v.bots.length === 0 ? (
                <p className="muted" data-testid="self-correlation-no-fleet">
                  No paper bot is running, so there is no fleet to correlate with.
                </p>
              ) : (
                <div className="table-wrap">
                  <table className="dense" data-testid="self-correlation-table">
                    <thead>
                      <tr>
                        <th scope="col">Candidate</th>
                        {v.bots.map((b) => (
                          <th key={b} scope="col" className="num" colSpan={2}>
                            <Link href={route.sessionDetail(b)}>{b.replace(/^forward-/, "")}</Link>
                          </th>
                        ))}
                        <th scope="col" className="num">
                          Max |ρ|
                        </th>
                        <th scope="col">Flag</th>
                      </tr>
                      <tr>
                        <th scope="col" className="muted">
                          basis
                        </th>
                        {v.bots.flatMap((b) => [
                          <th key={`${b}-bt`} scope="col" className="num muted">
                            bt
                          </th>,
                          <th key={`${b}-pp`} scope="col" className="num muted">
                            paper
                          </th>,
                        ])}
                        <th scope="col" />
                        <th scope="col" />
                      </tr>
                    </thead>
                    <tbody>
                      {v.rows.map((r) => (
                        <tr key={r.strategy_id} data-testid="self-correlation-row" data-flagged={r.flagged} data-strategy={r.strategy_id}>
                          <th scope="row" className="mono" title={r.source}>
                            {r.strategy_id}
                          </th>
                          {r.cells.map((c) => (
                            <td
                              key={`${c.session_id}-${c.basis}`}
                              className="ops-heat-cell"
                              data-flag={c.flagged}
                              title={`${c.basis} basis · ${c.n_common} common day(s)${c.same_strategy ? " · the bot's own strategy" : ""}`}
                            >
                              {c.same_strategy ? <span className="muted">self</span> : <FigureValue figure={c.rho} showChip={false} missingLabel="n/a" />}
                            </td>
                          ))}
                          <td className="num">
                            <FigureValue figure={r.max_abs_rho} showChip={false} missingLabel="n/a" />
                          </td>
                          <td className="wrap">
                            {r.flagged ? (
                              <span className="badge badge--degraded" data-testid="self-correlation-flag">
                                ABOVE {v.threshold}
                              </span>
                            ) : null}{" "}
                            <span className="ops-hint">{r.flag_reason}</span>
                          </td>
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
