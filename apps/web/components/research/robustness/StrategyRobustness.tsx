"use client";

import Link from "next/link";
import { useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList, SourceBadge } from "@/components/primitives";
import { useApi } from "@/lib/query";
import { researchHref } from "@/lib/research";
import { robustnessPath, type OptimisationProfileView, type RankingView } from "@/lib/robustness";
import type { Envelope } from "@/lib/types";
import { Fig } from "./parts";

/**
 * RESEARCH · strategy-level robustness (wave 3R): the optimisation profile
 * (System Parameter Permutation: median versus peak over the stored grid) and
 * the runs ranked by a selectable objective with its explicit below-80-trades
 * penalty. GET /api/research/strategies/{id}/optimisation-profile and
 * GET /api/research/rankings. Display and ranking only: the ladder keeps its
 * own metric, and no number here is computed in the browser.
 */
const OBJECTIVES: [string, string][] = [
  ["net_profit", "Net profit"],
  ["sharpe", "Sharpe"],
  ["sortino", "Sortino"],
  ["calmar", "Calmar"],
  ["sqn", "SQN"],
  ["expectancy", "Expectancy"],
  ["profit_factor", "Profit factor"],
];

export default function StrategyRobustness({ strategyId }: { strategyId: string }) {
  const [objective, setObjective] = useState("sharpe");
  return (
    <div className="rb" data-testid="strategy-robustness">
      <div className="rs-controls">
        <span className="rs-seg" role="group" aria-label="Objective" data-testid="objective">
          {OBJECTIVES.map(([k, label]) => (
            <button key={k} type="button" className="rs-seg__btn" aria-pressed={objective === k}
              onClick={() => setObjective(k)} data-testid={`objective-${k}`}>
              {label}
            </button>
          ))}
        </span>
        <span className="muted">advisory: the ladder keeps its own metric</span>
      </div>
      <div className="rs-two">
        <Profile strategyId={strategyId} objective={objective} />
        <Ranking strategyId={strategyId} objective={objective} />
      </div>
    </div>
  );
}

function Profile({ strategyId, objective }: { strategyId: string; objective: string }) {
  const state = useApi<Envelope<OptimisationProfileView>>(robustnessPath.profile(strategyId, objective));
  return (
    <section className="rs-panel" aria-labelledby="rb-spp" data-testid="optimisation-profile">
      <h3 className="rs-panel__title" id="rb-spp">Optimisation profile (median, not best)</h3>
      <AsyncBoundary state={state} label="the optimisation profile" onRetry={state.reload}
        isEmpty={(env) => env.data.points.length === 0}
        emptyTitle="No stored run with known parameters"
        emptyBody="The grid needs runs whose job is in the job ledger, so their parameters can be read back.">
        {(env) => {
          const v = env.data;
          return (
            <>
              <SourceBadge source={env.source} />
              <dl className="rs-kv" data-testid="spp-stats">
                <div><dt>Median</dt><dd><Fig f={v.median} sign /></dd></div>
                <div><dt>Peak</dt><dd><Fig f={v.peak} sign /></dd></div>
                <div><dt>Median / peak</dt><dd><Fig f={v.median_over_peak} /></dd></div>
                <div><dt>25% – 75%</dt><dd><Fig f={v.p25} sign /> – <Fig f={v.p75} sign /></dd></div>
                <div><dt>Profitable share</dt><dd><Fig f={v.profitable_share} /></dd></div>
                <div><dt>Grid points</dt><dd><Fig f={v.n_points} /> <span className="muted">(undefined <Fig f={v.n_undefined} />, unplaceable <Fig f={v.runs_unknown_params} />)</span></dd></div>
              </dl>
              <div className="table-wrap">
                <table className="rs-table rb-table" data-testid="spp-points">
                  <thead><tr><th scope="col">Parameters</th><th scope="col" className="num">Objective</th><th scope="col" className="num">Trades</th><th scope="col">Run</th></tr></thead>
                  <tbody>
                    {v.points.map((p) => (
                      <tr key={p.run_id}>
                        <td className="mono">{Object.entries(p.params).map(([k, val]) => `${k}=${String(val)}`).join(", ")}</td>
                        <td><Fig f={p.objective} sign /></td>
                        <td><Fig f={p.trades} /></td>
                        <td><Link href={researchHref.run(p.run_id)} className="mono">{p.run_id}</Link></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="rb-method">{v.method}</p>
              <CaveatList caveats={env.caveats} />
            </>
          );
        }}
      </AsyncBoundary>
    </section>
  );
}

function Ranking({ strategyId, objective }: { strategyId: string; objective: string }) {
  const state = useApi<Envelope<RankingView>>(robustnessPath.rankings(strategyId, objective));
  return (
    <section className="rs-panel" aria-labelledby="rb-rank" data-testid="ranking">
      <h3 className="rs-panel__title" id="rb-rank">Runs ranked by objective</h3>
      <AsyncBoundary state={state} label="the ranking" onRetry={state.reload}
        isEmpty={(env) => env.data.rows.length === 0 && env.data.unranked.length === 0}
        emptyTitle="No stored run" emptyBody="This strategy has no stored backtest to rank.">
        {(env) => {
          const v = env.data;
          const def = v.objectives.find((o) => o.key === v.objective);
          return (
            <>
              <SourceBadge source={env.source} />
              {def ? <p className="muted"><b>{def.label}</b>: {def.definition}</p> : null}
              <div className="table-wrap">
                <table className="rs-table rb-table" data-testid="ranking-table">
                  <thead><tr><th scope="col" className="num">#</th><th scope="col">Run</th><th scope="col" className="num">Trades</th><th scope="col" className="num">Raw</th><th scope="col" className="num">Score</th><th scope="col" className="num">Penalty</th></tr></thead>
                  <tbody>
                    {v.rows.map((r) => (
                      <tr key={r.run.run_id} data-penalised={r.penalised}>
                        <td className="num">{r.rank}</td>
                        <td><Link href={researchHref.run(r.run.run_id)} className="mono">{r.run.run_id}</Link></td>
                        <td><Fig f={r.trades} />{r.penalised ? <span className="rb-tag rb-tag--warn" title={v.penalty}>&lt;{v.min_trades}</span> : null}</td>
                        <td><Fig f={r.raw} /></td>
                        <td><Fig f={r.score} /></td>
                        <td><Fig f={r.penalty_factor} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {v.unranked.length ? <p className="muted">{v.unranked.length} run(s) unranked: the objective is undefined for them.</p> : null}
              <p className="rb-method">{v.penalty} {v.note}</p>
              <CaveatList caveats={env.caveats} />
            </>
          );
        }}
      </AsyncBoundary>
    </section>
  );
}
