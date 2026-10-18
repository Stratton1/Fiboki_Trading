"use client";

import Link from "next/link";
import { parseAsString, useQueryState } from "nuqs";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { UrlState } from "@/components/UrlState";
import { GridHeatmap, HeatLegend, divergingBin, type HeatCell } from "@/components/charts/analytics/GridHeatmap";
import { TimeSeriesChart } from "@/components/charts/analytics/TimeSeriesChart";
import { CaveatList, PageHead, SourceBadge, ToneBadge } from "@/components/primitives";
import { GateCheck } from "@/components/research/GateCheck";
import { MetricLabel } from "@/components/research/MetricLabel";
import { ResearchNav } from "@/components/research/ResearchNav";
import { gateVerdictTone } from "@/components/research/tones";
import { formatNumber } from "@/lib/format";
import { useApi } from "@/lib/query";
import { researchHref, type ComparisonView, type RunSummaryView } from "@/lib/research";
import type { Envelope, Page } from "@/lib/types";

const MAX = 6;
const runsParser = parseAsString.withOptions({ history: "replace" });
const INKS = ["s1", "s2", "s3", "s4", "s5", "s6"];

/**
 * RESEARCH · Compare: two to six stored runs side by side
 * (`/research/compare?runs=a,b`). Rebased equity on one chart, the same
 * statistics in columns, and the correlation of their daily returns, all
 * from GET /api/research/compare.
 */
export default function ComparePage() {
  return (
    <UrlState fallback={<Compare ids={[]} onIds={() => undefined} />}>
      <CompareFromUrl />
    </UrlState>
  );
}

function CompareFromUrl() {
  const [runs, setRuns] = useQueryState("runs", runsParser);
  const ids = (runs ?? "").split(",").map((s) => s.trim()).filter(Boolean).slice(0, MAX);
  return <Compare ids={ids} onIds={(next) => void setRuns(next.length ? next.join(",") : null)} />;
}

function Compare({ ids, onIds }: { ids: string[]; onIds: (ids: string[]) => void }) {
  const unique = [...new Set(ids)];
  const comparison = useApi<Envelope<ComparisonView>>(
    unique.length >= 2 ? `/api/research/compare?runs=${unique.map(encodeURIComponent).join(",")}` : null,
  );
  const runs = useApi<Page<RunSummaryView>>("/api/research/runs?limit=300");
  return (
    <div className="rs-page">
      <ResearchNav />
      <PageHead
        title="Compare runs"
        intro="Two to six stored runs on one rebased chart, the same statistics side by side, and how their daily returns move together. Every figure is the server's."
      />
      <div className="rs-toolbar" data-testid="compare-picker">
        {unique.map((id, i) => (
          <span key={id} className="rs-chip" data-ink={INKS[i]}>
            <span className="rs-swatch" aria-hidden="true" />
            <Link href={researchHref.run(id)}>{id}</Link>
            <button type="button" aria-label={`Remove ${id}`} onClick={() => onIds(unique.filter((x) => x !== id))}>
              ✕
            </button>
          </span>
        ))}
        {unique.length < MAX ? (
          <>
            <label htmlFor="compare-add" className="sr-only">
              Add a run
            </label>
            <select
              id="compare-add"
              data-testid="compare-add"
              value=""
              onChange={(e) => e.target.value && onIds([...unique, e.target.value])}
            >
              <option value="">Add a run…</option>
              {runs.status === "success"
                ? runs.data.items
                    .filter((r) => !unique.includes(r.run.run_id))
                    .map((r) => (
                      <option key={r.run.run_id} value={r.run.run_id}>
                        {r.run.run_id} · {r.run.strategy_id} · {r.run.instruments.join(",")} {r.run.timeframe}
                      </option>
                    ))
                : null}
            </select>
          </>
        ) : null}
      </div>
      {unique.length < 2 ? (
        <div className="state state--empty" data-testid="compare-needs-runs">
          <div className="state__title">Choose at least two runs</div>
          <div className="state__body">
            Add runs above, or tick them on <Link href="/research/runs">Runs</Link>. Nothing is compared until two are
            chosen.
          </div>
        </div>
      ) : (
        <AsyncBoundary state={comparison} label="the comparison" onRetry={comparison.reload}>
          {(env) => <Comparison env={env} />}
        </AsyncBoundary>
      )}
    </div>
  );
}

function Comparison({ env }: { env: Envelope<ComparisonView> }) {
  const view = env.data;
  const ids = view.runs.map((r) => r.run.run_id);
  const keys = view.runs[0]?.stats.map((s) => s.key) ?? [];
  const byPair = new Map(view.correlations.map((c) => [`${c.a}|${c.b}`, c]));
  const pair = (a: string, b: string) => byPair.get(`${a}|${b}`) ?? byPair.get(`${b}|${a}`);
  const cells: (HeatCell | null)[][] = ids.map((a) =>
    ids.map((b) => {
      const c = pair(a, b);
      if (!c) return null;
      const rho = c.rho.value;
      return {
        text: rho === null ? "n/a" : formatNumber(rho, "ratio"),
        bin: rho === null ? null : divergingBin(rho, 1),
        label: `${a} with ${b}: ${rho === null ? `not computed (${c.n_common} common days)` : formatNumber(rho, "ratio")} over ${c.n_common} common days`,
        marks: a === b ? ["diagonal"] : [],
        testId: "corr-cell",
      };
    }),
  );
  return (
    <>
      <SourceBadge source={env.source} />
      <CaveatList caveats={env.caveats} />
      <section className="rs-panel" aria-label="Rebased equity">
        <TimeSeriesChart
          title="Equity, rebased to 100"
          unit="index"
          height={300}
          baseline={100}
          testId="compare-equity"
          lines={view.runs.map((r, i) => ({
            id: r.run.run_id,
            label: `${r.run.run_id} (${r.run.strategy_id})`,
            points: r.rebased.points,
            ink: INKS[i] ?? "s1",
          }))}
          note="Each run's equity over its own initial equity, x 100. Runs on different windows overlap only where their dates do."
        />
      </section>
      <div className="rs-split">
        <section className="rs-panel" aria-labelledby="cmp-stats">
          <h2 className="rs-panel__title" id="cmp-stats">
            Statistics side by side
          </h2>
          <div className="table-wrap">
            <table className="rs-table" data-testid="compare-stats">
              <thead>
                <tr>
                  <th scope="col">Statistic</th>
                  {view.runs.map((r, i) => (
                    <th key={r.run.run_id} scope="col" className="num" data-ink={INKS[i]}>
                      <span className="rs-swatch" aria-hidden="true" />{" "}
                      <Link href={researchHref.run(r.run.run_id)}>{r.run.run_id}</Link>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Gates</th>
                  {view.runs.map((r) => (
                    <td key={r.run.run_id} className="num">
                      <ToneBadge tone={gateVerdictTone(r.run.gate_verdict)} testId="compare-verdict" value={r.run.gate_verdict} />
                    </td>
                  ))}
                </tr>
                {keys.map((k) => {
                  const first = view.runs[0]!.stats.find((s) => s.key === k)!;
                  return (
                    <tr key={k} data-stat={k}>
                      <th scope="row">
                        <MetricLabel
                          label={first.label}
                          definition={{ label: first.label, definition: first.definition, formula: first.formula }}
                        />
                      </th>
                      {view.runs.map((r) => {
                        const s = r.stats.find((x) => x.key === k);
                        return (
                          <td key={r.run.run_id} className="num">
                            {s ? <FigureValue figure={s.figure} showChip={false} /> : "—"}
                            {s?.check ? (
                              <>
                                {" "}
                                <GateCheck check={s.check} compact />
                              </>
                            ) : null}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
        <section className="rs-panel rs-split__side" aria-label="Return correlation">
          <GridHeatmap
            title="Daily return correlation"
            rowHeader="Run"
            rowLabels={ids.map((id) => (
              <span key={id} className="mono">
                {id}
              </span>
            ))}
            colLabels={ids.map((_, i) => `#${i + 1}`)}
            cells={cells}
            testId="compare-correlation"
            meta={<MetricLabel label="definition" definition={view.definitions.return_correlation} />}
            legend={<HeatLegend low="−1" high="+1" />}
            note={`Pearson, on the trading days both runs have (17:00 New York days). Fewer than ${view.min_common_days} common days: not computed. Column #n is the nth run in the row list.`}
          />
        </section>
      </div>
    </>
  );
}
