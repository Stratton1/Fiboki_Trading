"use client";

import Link from "next/link";
import { parseAsString, useQueryState } from "nuqs";
import { useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { UrlState } from "@/components/UrlState";
import { CaveatList, PageHead, SourceBadge, ToneBadge } from "@/components/primitives";
import { ResearchNav } from "@/components/research/ResearchNav";
import { gateVerdictTone } from "@/components/research/tones";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import { researchHref, type RunSummaryView } from "@/lib/research";
import type { Page, StrategyRow } from "@/lib/types";

const MAX_COMPARE = 6;
const strategyParser = parseAsString.withOptions({ history: "replace" });

/**
 * RESEARCH · Runs: every stored backtest in the research ledger, any symbol,
 * newest first (GET /api/research/runs). Each run opens its tearsheet; tick
 * two to six to compare them. `?strategy=<id>` filters.
 */
export default function RunsPage() {
  return (
    <UrlState fallback={<Runs strategy={null} onStrategy={() => undefined} />}>
      <RunsFromUrl />
    </UrlState>
  );
}

function RunsFromUrl() {
  const [strategy, setStrategy] = useQueryState("strategy", strategyParser);
  return <Runs strategy={strategy} onStrategy={(s) => void setStrategy(s || null)} />;
}

function Runs({ strategy, onStrategy }: { strategy: string | null; onStrategy: (s: string) => void }) {
  const strategies = useApi<Page<StrategyRow>>("/api/research/strategies");
  const runs = useApi<Page<RunSummaryView>>(
    `/api/research/runs?limit=300${strategy ? `&strategy_id=${encodeURIComponent(strategy)}` : ""}`,
  );
  const [picked, setPicked] = useState<string[]>([]);
  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= MAX_COMPARE ? p : [...p, id]));

  return (
    <div className="rs-page">
      <ResearchNav />
      <PageHead
        title="Runs"
        intro="Every stored backtest and walk-forward fold, newest first. Open one for its tearsheet; tick two to six to compare them on one rebased chart."
      />
      <div className="rs-toolbar">
        <label htmlFor="runs-strategy">Strategy</label>
        <select
          id="runs-strategy"
          data-testid="runs-strategy"
          value={strategy ?? ""}
          onChange={(e) => onStrategy(e.target.value)}
        >
          <option value="">All strategies</option>
          {strategies.status === "success"
            ? strategies.data.items.map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>
                  {s.name}
                </option>
              ))
            : null}
        </select>
        <span className="rs-toolbar__spacer" />
        <span className="muted" data-testid="compare-count">
          {picked.length} of {MAX_COMPARE} selected
        </span>
        {picked.length >= 2 ? (
          <Link href={researchHref.compare(picked)} className="rs-btn" data-testid="compare-selected">
            Compare {picked.length} runs
          </Link>
        ) : (
          <span className="rs-btn" aria-disabled="true" data-testid="compare-selected">
            Select two or more to compare
          </span>
        )}
      </div>
      <AsyncBoundary
        state={runs}
        label="stored runs"
        onRetry={runs.reload}
        isEmpty={(p) => p.items.length === 0}
        emptyTitle="No stored runs"
        emptyBody="The research ledger answered with no backtests for this filter. Check the source badge: ABSENT means no ledger is attached, which says nothing about what has been run."
      >
        {(page) => (
          <>
            <SourceBadge source={page.source} />
            <CaveatList caveats={page.caveats} />
            <div className="table-wrap">
              <table className="rs-table" data-testid="runs-table">
                <thead>
                  <tr>
                    <th scope="col">
                      <span className="sr-only">Select to compare</span>
                    </th>
                    <th scope="col">Run</th>
                    <th scope="col">Strategy</th>
                    <th scope="col">Kind</th>
                    <th scope="col">TF</th>
                    <th scope="col">Instruments</th>
                    <th scope="col">Filed</th>
                    <th scope="col" className="num">
                      Trades
                    </th>
                    <th scope="col" className="num">
                      Return
                    </th>
                    <th scope="col" className="num">
                      Sharpe
                    </th>
                    <th scope="col" className="num">
                      Max DD
                    </th>
                    <th scope="col" className="num">
                      PF
                    </th>
                    <th scope="col">Gates</th>
                    <th scope="col">Engine</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((r) => (
                    <tr key={r.run.run_id} data-testid="run-row" data-run={r.run.run_id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Compare ${r.run.run_id}`}
                          checked={picked.includes(r.run.run_id)}
                          disabled={!picked.includes(r.run.run_id) && picked.length >= MAX_COMPARE}
                          onChange={() => toggle(r.run.run_id)}
                          data-testid="run-pick"
                        />
                      </td>
                      <td className="mono">
                        <Link href={r.tearsheet}>{r.run.run_id}</Link>
                      </td>
                      <td>
                        <Link href={researchHref.strategy(r.run.strategy_id)}>{r.run.strategy_id}</Link>
                      </td>
                      <td>{r.run.kind === "walkforward_fold" ? r.run.label.replace("walkforward_", "WF ") : "backtest"}</td>
                      <td>{r.run.timeframe}</td>
                      <td className="mono">{r.run.instruments.join(", ")}</td>
                      <td>{formatTimestamp(r.run.created_at)}</td>
                      <td>
                        <FigureValue figure={r.run.trade_count} showChip={false} />
                      </td>
                      <td>
                        <FigureValue figure={r.total_return_pct} showChip={false} colourSign />
                      </td>
                      <td>
                        <FigureValue figure={r.sharpe} showChip={false} />
                      </td>
                      <td>
                        <FigureValue figure={r.max_drawdown_pct} showChip={false} />
                      </td>
                      <td>
                        <FigureValue figure={r.profit_factor} showChip={false} />
                      </td>
                      <td>
                        <ToneBadge tone={gateVerdictTone(r.run.gate_verdict)} testId="run-verdict" value={r.run.gate_verdict} />
                      </td>
                      <td>
                        {r.run.engine_current && !r.run.superseded ? (
                          <span className="muted">current</span>
                        ) : (
                          <span className="badge badge--degraded">{r.run.superseded ? "SUPERSEDED" : "OLDER"}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted">
              {page.items.length} of {page.total} run(s) shown.
            </p>
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}
