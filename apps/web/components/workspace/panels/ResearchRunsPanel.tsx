"use client";

import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { formatUtcTime } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { RunSummaryView } from "@/lib/research";
import { route } from "@/lib/routes";
import type { Page } from "@/lib/types";
import { PanelStamp } from "../PanelStamp";
import type { PanelProps } from "../types";

/** RUNS in a tile: stored backtest runs with their gate verdict and headline stats (all the API's). */
export default function ResearchRunsPanel({ maximised }: PanelProps) {
  const runs = useApi<Page<RunSummaryView>>(`/api/research/runs?limit=${maximised ? 200 : 50}`, { refreshMs: 60_000 });
  return (
    <AsyncBoundary
      state={runs}
      label="research runs"
      onRetry={runs.reload}
      isEmpty={(p) => p.items.length === 0}
      emptyTitle="No stored runs"
      emptyBody="The experiment ledger holds no backtest run on this deployment."
    >
      {(page) => (
        <>
          <PanelStamp source={page.source} extra={`${page.items.length} of ${page.total}`} />
          <div className="table-wrap">
            <table className="dense ws-table" data-testid="ws-runs">
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Strategy</th>
                  <th scope="col">TF</th>
                  <th scope="col">Gate</th>
                  <th scope="col" className="num">Return</th>
                  <th scope="col" className="num">Sharpe</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((r) => (
                  <tr key={r.run.run_id} data-superseded={r.run.superseded || undefined}>
                    <td className="mono">
                      <Link href={route.run(r.run.run_id)} prefetch={false}>
                        {r.run.run_id}
                      </Link>
                    </td>
                    <td>
                      <Link href={route.strategy(r.run.strategy_id)} prefetch={false}>
                        {r.run.strategy_id}
                      </Link>
                    </td>
                    <td className="mono">{r.run.timeframe}</td>
                    <td className="mono" data-verdict={r.run.gate_verdict}>
                      {r.run.gate_verdict.toUpperCase()}
                    </td>
                    <td className="num">
                      <FigureValue figure={r.total_return_pct} showChip={false} colourSign />
                    </td>
                    <td className="num">
                      <FigureValue figure={r.sharpe} showChip={false} />
                    </td>
                    <td className="mono">{r.run.created_at ? formatUtcTime(r.run.created_at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </AsyncBoundary>
  );
}
