"use client";

import { CHECK_LABEL, CHECK_TONE, sessionMaturityPath, type MaturityView } from "@/lib/ops";
import { useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { SourceBadge } from "../primitives";
import { Stat } from "../Stat";

/**
 * The paper maturity report (Wave 3O): ADVISORY. A "ready to consider for IG
 * demo" checklist, item by item, from GET /api/trading/sessions/{id}/maturity.
 * Every status, value and threshold is the API's (ops/maturity.py); this page
 * enables nothing and says so above the checklist, not in a footnote.
 */
const VERDICT: Record<string, { label: string; tone: string }> = {
  ready_to_consider: { label: "READY TO CONSIDER", tone: "ok" },
  not_ready: { label: "NOT READY", tone: "down" },
  not_evaluated: { label: "NOT EVALUATED", tone: "unknown" },
};

export function Maturity({ sessionId }: { sessionId: string }) {
  const state = useApi<Envelope<MaturityView>>(sessionMaturityPath(sessionId), { refreshMs: 60_000 });
  return (
    <AsyncBoundary state={state} label="the maturity report" onRetry={state.reload}>
      {(env) => {
        const m = env.data;
        const verdict = VERDICT[m.verdict] ?? { label: m.verdict.toUpperCase(), tone: "unknown" };
        return (
          <div data-testid="maturity" data-verdict={m.verdict}>
            <SourceBadge source={env.source} />
            <p className="state board__advisory" data-testid="maturity-advisory">
              <strong>Advisory.</strong> {m.note}
            </p>
            <div className="ops-row">
              <span className={`badge badge--${verdict.tone}`} data-testid="maturity-verdict">
                {verdict.label}
              </span>
              <span className="ops-meaning">for an IG demo account · criteria {m.criteria_version}</span>
            </div>
            <div className="table-wrap">
              <table className="dense" data-testid="maturity-items">
                <thead>
                  <tr>
                    <th scope="col">Check</th>
                    <th scope="col">Item</th>
                    <th scope="col" className="num">
                      Value
                    </th>
                    <th scope="col" className="num">
                      Threshold
                    </th>
                    <th scope="col">Why</th>
                  </tr>
                </thead>
                <tbody>
                  {m.items.map((i) => (
                    <tr key={i.key} data-testid="maturity-item" data-key={i.key} data-status={i.status}>
                      <td>
                        <span className={`badge badge--${CHECK_TONE[i.status] ?? "unknown"} ops-check`}>
                          {CHECK_LABEL[i.status] ?? i.status}
                        </span>
                      </td>
                      <th scope="row">{i.label}</th>
                      <td className="num">
                        <FigureValue figure={i.value} showChip={false} missingLabel="—" />
                      </td>
                      <td className="num muted">
                        <FigureValue figure={i.threshold} showChip={false} missingLabel="—" />
                      </td>
                      <td className="wrap">{i.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {m.band ? (
              <>
                <h3 className="ops-subhead">Backtest drawdown band at {m.band.horizon_trades} trades</h3>
                <div className="tiles" data-testid="maturity-band">
                  <Stat label="Paper max DD" figure={m.paper_max_drawdown_pct} />
                  <Stat label="Band p5" figure={m.band.p5} />
                  <Stat label="Band p50" figure={m.band.p50} />
                  <Stat label="Band p95" figure={m.band.p95} />
                </div>
                <p className="ops-hint">
                  {m.band.n_paths} seeded paths (seed {m.band.seed}) resampled from {m.band.source_trades} trades of{" "}
                  {m.band.source}, re-sized at risk fraction {m.band.risk_fraction}.
                </p>
              </>
            ) : null}
            {m.notes.map((n) => (
              <p key={n} className="ops-hint">
                {n}
              </p>
            ))}
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
