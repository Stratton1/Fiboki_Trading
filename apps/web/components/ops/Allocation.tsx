"use client";

import { allocationPath, type AllocationView } from "@/lib/ops";
import { useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { Card, PageHead, SourceBadge } from "../primitives";

/**
 * Advisory portfolio weights (Wave 3O), from GET /api/trading/allocation.
 * Every weight and statistic is the API's (ops/analytics.py). DISPLAY ONLY:
 * the paper runtime's portfolio construction decides sizes and reads none of it.
 */
export function Allocation({ basis, onBasis }: { basis: "backtest" | "paper"; onBasis: (b: "backtest" | "paper") => void }) {
  const state = useApi<Envelope<AllocationView>>(allocationPath(basis), { refreshMs: 300_000 });
  return (
    <>
      <PageHead
        title="Allocation"
        intro="Advisory strategy-portfolio weights: equal risk contribution, inverse volatility and minimum correlation, with the portfolio each would have been over the members' common days. Display only: the paper runtime's portfolio construction decides every size and reads none of this."
      />
      <div className="row mb-3" role="group" aria-label="Basis">
        <label htmlFor="alloc-basis" className="m-0">
          Basis
        </label>
        <select id="alloc-basis" data-testid="allocation-basis" value={basis} onChange={(e) => onBasis(e.target.value as "backtest" | "paper")}>
          <option value="backtest">Strategies (stored backtests)</option>
          <option value="paper">Paper bots (paper records)</option>
        </select>
      </div>
      <Card title="Weights and portfolio statistics">
        <AsyncBoundary state={state} label="the allocation" onRetry={state.reload}>
          {(env) => {
            const v = env.data;
            return (
              <div data-testid="allocation" data-basis={v.basis}>
                <SourceBadge source={env.source} />
                <p className="state board__advisory" data-testid="allocation-advisory">
                  <strong>Display only.</strong> No weight here sizes anything.
                </p>
                {v.schemes.length === 0 ? (
                  <p className="muted" data-testid="allocation-absent">
                    No weights: {v.absent_reason ?? "nothing to weight"}.
                  </p>
                ) : (
                  <div className="table-wrap">
                    <table className="dense" data-testid="allocation-table">
                      <thead>
                        <tr>
                          <th scope="col">Member</th>
                          <th scope="col" className="num">
                            Ann. vol
                          </th>
                          {v.schemes.map((s) => (
                            <th key={s.scheme} scope="col" className="num">
                              {s.label}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {v.members.map((m) => (
                          <tr key={m.key} data-testid="allocation-member" data-key={m.key}>
                            <th scope="row" className="mono" title={m.source}>
                              {m.key}
                              {m.in_fleet ? <span className="badge badge--neutral"> IN FLEET</span> : null}
                            </th>
                            <td className="num">
                              <FigureValue figure={m.annual_vol_pct} showChip={false} />
                            </td>
                            {v.schemes.map((s) => (
                              <td key={s.scheme} className="num">
                                {s.weights[m.key] ? <FigureValue figure={s.weights[m.key]!} showChip={false} /> : "—"}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        {(
                          [
                            ["Ann. return", "annual_return_pct"],
                            ["Ann. vol", "annual_vol_pct"],
                            ["Sharpe", "sharpe"],
                            ["Max DD", "max_drawdown_pct"],
                            ["Effective N", "effective_n"],
                          ] as const
                        ).map(([label, key]) => (
                          <tr key={key} data-testid="allocation-stat" data-stat={key}>
                            <th scope="row">{label}</th>
                            <td />
                            {v.schemes.map((s) => (
                              <td key={s.scheme} className="num">
                                <FigureValue figure={s[key]} showChip={false} colourSign={key === "annual_return_pct"} />
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tfoot>
                    </table>
                  </div>
                )}
                <p className="ops-hint">
                  {v.common_days} common day(s). {v.method}
                </p>
              </div>
            );
          }}
        </AsyncBoundary>
      </Card>
    </>
  );
}
