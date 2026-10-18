"use client";

import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { CaveatList, SourceBadge, ToneBadge } from "@/components/primitives";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import { researchHref, type StrategyOverviewView } from "@/lib/research";
import type { Envelope } from "@/lib/types";
import { ResearchNav } from "./ResearchNav";
import { gateVerdictTone, ladderTone, rungTone } from "./tones";

/**
 * RESEARCH · one strategy (`/research/strategies/<id>`): the document, its
 * stored runs, its lifecycle state, its ladder history and gate reports, and
 * links to every view that reads it. From
 * GET /api/research/strategies/{id}/overview.
 */
export function StrategyEntity({ strategyId }: { strategyId: string }) {
  const state = useApi<Envelope<StrategyOverviewView>>(
    `/api/research/strategies/${encodeURIComponent(strategyId)}/overview`,
  );
  return (
    <div className="rs-page" data-testid="strategy-entity">
      <ResearchNav />
      {state.status !== "success" ? (
        <header className="page-head">
          <h1>Strategy {strategyId}</h1>
          <p>Runs, lifecycle state, ladder history and links for one registered strategy.</p>
        </header>
      ) : null}
      <AsyncBoundary state={state} label="the strategy" onRetry={state.reload}>
        {(env) => <Overview env={env} />}
      </AsyncBoundary>
    </div>
  );
}

function Overview({ env }: { env: Envelope<StrategyOverviewView> }) {
  const v = env.data;
  const latest = v.ladder_history[0];
  return (
    <>
      <header className="rs-head">
        <div className="rs-head__main">
          <h1 className="rs-head__title">
            <span className="rs-head__kicker">Strategy</span>
            {v.name}
          </h1>
          <div className="rs-head__facts">
            <span className="mono">{v.strategy_id}</span>
            <span>{v.family}</span>
            <span>{v.timeframes.join(", ")}</span>
            <span>{v.universe.length} instrument(s)</span>
            <span className="mono" title={v.content_hash}>
              hash {v.content_hash.slice(0, 12)}
            </span>
          </div>
        </div>
        <div className="rs-head__badges">
          {latest ? (
            <ToneBadge tone={ladderTone(latest.verdict)} testId="ladder-verdict" value={latest.verdict} />
          ) : (
            <span className="badge badge--unknown" data-testid="ladder-verdict">
              NOT VALIDATED
            </span>
          )}
          {v.lifecycle.map((l) => (
            <span key={l.content_hash} className={`badge badge--${l.degraded ? "degraded" : "unknown"}`} data-testid="lifecycle-state">
              {l.state.toUpperCase()}
            </span>
          ))}
        </div>
        <div className="rs-head__actions">
          <Link href={`/research/runs?strategy=${encodeURIComponent(v.strategy_id)}`} className="rs-btn" data-testid="link-runs">
            Runs ({v.runs_total})
          </Link>
          <Link href={v.links.walk_forward ?? researchHref.walkForward(v.strategy_id)} className="rs-btn rs-btn--ghost" data-testid="link-walk-forward">
            Walk-forward
          </Link>
          <Link href={v.links.parameter_lab ?? "/research/parameter-lab"} className="rs-btn rs-btn--ghost" data-testid="link-parameter-lab">
            Parameter surface
          </Link>
          <Link href={v.links.lifecycle ?? "/trading/candidates"} className="rs-btn rs-btn--ghost" data-testid="link-lifecycle">
            Lifecycle
          </Link>
          <Link href={v.links.chart ?? "/markets"} className="rs-btn rs-btn--ghost" data-testid="link-chart">
            Chart
          </Link>
        </div>
      </header>
      <SourceBadge source={env.source} />
      <CaveatList caveats={env.caveats} />

      <div className="rs-split">
        <section className="rs-panel" aria-labelledby="st-doc">
          <h2 className="rs-panel__title" id="st-doc">
            Hypothesis and parameters
          </h2>
          <p>{v.hypothesis || "No hypothesis recorded."}</p>
          {Object.keys(v.parameters).length === 0 ? (
            <p className="muted">This strategy declares no parameters.</p>
          ) : (
            <table className="rs-table" data-testid="strategy-params">
              <thead>
                <tr>
                  <th scope="col">Parameter</th>
                  <th scope="col">Kind</th>
                  <th scope="col" className="num">
                    Default
                  </th>
                  <th scope="col" className="num">
                    Min
                  </th>
                  <th scope="col" className="num">
                    Max
                  </th>
                  <th scope="col" className="num">
                    Step
                  </th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(v.parameters).map(([name, p]) => (
                  <tr key={name}>
                    <th scope="row" className="mono">
                      {name}
                    </th>
                    <td>{p.kind}</td>
                    <td className="num">{String(p.default)}</td>
                    <td className="num">{p.min ?? "—"}</td>
                    <td className="num">{p.max ?? "—"}</td>
                    <td className="num">{p.step ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="rs-panel rs-split__side" aria-labelledby="st-life">
          <h2 className="rs-panel__title" id="st-life">
            Lifecycle
          </h2>
          {v.lifecycle.length === 0 ? (
            <p className="muted" data-testid="lifecycle-note">
              {v.lifecycle_note}
            </p>
          ) : (
            <ul className="rs-notes">
              {v.lifecycle.map((l) => (
                <li key={l.content_hash}>
                  <Link href={l.link}>{l.state}</Link> <span className="mono muted">{l.content_hash.slice(0, 12)}</span>
                  {l.degraded ? <span className="badge badge--degraded">DEGRADED</span> : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="rs-panel" aria-labelledby="st-ladder">
        <h2 className="rs-panel__title" id="st-ladder">
          Ladder history
        </h2>
        {v.ladder_history.length === 0 ? (
          <p className="muted" data-testid="ladder-empty">
            No validation ladder report exists for this strategy, in the report directory or the experiment ledger. It
            has not been through the ladder; that is not a pass.
          </p>
        ) : (
          <div className="table-wrap">
            <table className="rs-table" data-testid="ladder-history">
              <thead>
                <tr>
                  <th scope="col">Filed</th>
                  <th scope="col">Source</th>
                  <th scope="col">Verdict</th>
                  <th scope="col">Rungs</th>
                  <th scope="col">Binding constraint</th>
                  <th scope="col">Gate set</th>
                  <th scope="col" className="num">
                    Trials (N)
                  </th>
                  <th scope="col">Dataset</th>
                  <th scope="col">Walk-forward</th>
                </tr>
              </thead>
              <tbody>
                {v.ladder_history.map((e, i) => (
                  <tr key={`${e.source}-${e.experiment_id ?? i}`}>
                    <td>{formatTimestamp(e.created_at)}</td>
                    <td>{e.source === "report_file" ? "report file" : <span className="mono">{e.experiment_id}</span>}</td>
                    <td>
                      <ToneBadge tone={ladderTone(e.verdict)} testId="ladder-row-verdict" value={e.verdict} />
                    </td>
                    <td>
                      <span className="rs-rungs">
                        {e.rungs.map((r) => (
                          <span
                            key={r.index}
                            className="rs-rung"
                            data-outcome={r.outcome}
                            title={`${r.name}: ${rungTone(r.outcome).label}${r.reason ? `, ${r.reason}` : ""}`}
                          >
                            {r.index}
                            <span className="sr-only">
                              {" "}
                              {r.name} {rungTone(r.outcome).label}
                            </span>
                          </span>
                        ))}
                      </span>
                    </td>
                    <td className="wrap">{e.binding_constraint || "—"}</td>
                    <td className="mono">{e.gate_set_version}</td>
                    <td>
                      <FigureValue figure={e.trial_count} showChip={false} missingLabel="—" />
                    </td>
                    <td className="mono">{e.dataset_version_id.slice(0, 16)}</td>
                    <td>
                      {e.has_walk_forward ? (
                        <Link href={`${researchHref.walkForward(v.strategy_id)}${e.experiment_id ? `&source=${encodeURIComponent(e.experiment_id)}` : ""}`}>
                          folds
                        </Link>
                      ) : (
                        <span className="muted">not reached</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rs-panel" aria-labelledby="st-runs">
        <h2 className="rs-panel__title" id="st-runs">
          Stored runs ({v.runs_total})
        </h2>
        {v.runs.length === 0 ? (
          <p className="muted">No stored backtest for this strategy in the research ledger.</p>
        ) : (
          <div className="table-wrap">
            <table className="rs-table" data-testid="strategy-runs">
              <thead>
                <tr>
                  <th scope="col">Run</th>
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
                  <th scope="col">Gates</th>
                </tr>
              </thead>
              <tbody>
                {v.runs.slice(0, 50).map((r) => (
                  <tr key={r.run.run_id}>
                    <td className="mono">
                      <Link href={r.tearsheet}>{r.run.run_id}</Link>
                    </td>
                    <td>{r.run.kind === "walkforward_fold" ? r.run.label : "backtest"}</td>
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
                      <ToneBadge tone={gateVerdictTone(r.run.gate_verdict)} testId="strategy-run-verdict" value={r.run.gate_verdict} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {v.gate_reports.length ? (
        <section className="rs-panel" aria-labelledby="st-gates">
          <h2 className="rs-panel__title" id="st-gates">
            Gate reports
          </h2>
          <table className="rs-table" data-testid="strategy-gate-reports">
            <thead>
              <tr>
                <th scope="col">Report</th>
                <th scope="col">Run</th>
                <th scope="col">Filed</th>
                <th scope="col">Verdict</th>
                <th scope="col" className="num">
                  Checks failed
                </th>
                <th scope="col">Gate set</th>
              </tr>
            </thead>
            <tbody>
              {v.gate_reports.slice(0, 20).map((g) => (
                <tr key={g.report_id}>
                  <td className="mono">{g.report_id}</td>
                  <td className="mono">
                    <Link href={`${researchHref.run(g.backtest_id)}#gates`}>{g.backtest_id}</Link>
                  </td>
                  <td>{formatTimestamp(g.created_at)}</td>
                  <td>
                    <ToneBadge tone={gateVerdictTone(g.verdict)} testId="gate-report-row" value={g.verdict} />
                  </td>
                  <td className="num">
                    {g.checks.filter((c) => !c.passed).length} of {g.checks.length}
                  </td>
                  <td className="mono">{g.gate_set_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </>
  );
}
