"use client";

import { parseAsInteger, parseAsString, useQueryState } from "nuqs";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { UrlState } from "@/components/UrlState";
import { GridHeatmap, HeatLegend, divergingBin, type HeatCell } from "@/components/charts/analytics/GridHeatmap";
import { CaveatList, PageHead, SourceBadge } from "@/components/primitives";
import { ResearchNav } from "@/components/research/ResearchNav";
import { formatNumber, formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { CorrelationMatrixView } from "@/lib/research";
import type { Envelope } from "@/lib/types";

const TIMEFRAMES = ["H1", "H4", "D1"];
const WINDOWS = [20, 50, 100, 250, 500];
const tfParser = parseAsString.withDefault("D1").withOptions({ history: "replace" });
const winParser = parseAsInteger.withDefault(100).withOptions({ history: "replace" });

/**
 * RESEARCH · Correlations (feature 27): the universe's trailing-window return
 * correlation, from GET /api/research/correlations (marketstate/cross_asset.py
 * alignment, no forward fill). `?tf=D1&window=100`.
 */
export default function CorrelationsPage() {
  return (
    <UrlState fallback={<Correlations tf="D1" window={100} onTf={() => undefined} onWindow={() => undefined} />}>
      <FromUrl />
    </UrlState>
  );
}

function FromUrl() {
  const [tf, setTf] = useQueryState("tf", tfParser);
  const [win, setWin] = useQueryState("window", winParser);
  return <Correlations tf={tf} window={win} onTf={(v) => void setTf(v)} onWindow={(v) => void setWin(v)} />;
}

function Correlations({
  tf,
  window,
  onTf,
  onWindow,
}: {
  tf: string;
  window: number;
  onTf: (v: string) => void;
  onWindow: (v: number) => void;
}) {
  const state = useApi<Envelope<CorrelationMatrixView>>(`/api/research/correlations?timeframe=${tf}&window=${window}`);
  return (
    <div className="rs-page">
      <ResearchNav />
      <PageHead
        title="Universe correlation"
        intro="How the instruments' returns move together over a trailing window you choose, on the bars they share. A correlated book is one position wearing several names."
      />
      <div className="rs-toolbar" data-testid="corr-controls">
        <span className="rs-metric">Timeframe</span>
        <span className="rs-seg" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((t) => (
            <button key={t} type="button" className="rs-seg__btn" aria-pressed={tf === t} onClick={() => onTf(t)} data-testid={`corr-tf-${t}`}>
              {t}
            </button>
          ))}
        </span>
        <span className="rs-metric">Window</span>
        <span className="rs-seg" role="group" aria-label="Window in bars">
          {WINDOWS.map((w) => (
            <button key={w} type="button" className="rs-seg__btn" aria-pressed={window === w} onClick={() => onWindow(w)} data-testid={`corr-window-${w}`}>
              {w}
            </button>
          ))}
        </span>
        <span className="muted">bars</span>
      </div>
      <AsyncBoundary state={state} label="the correlation matrix" onRetry={state.reload}>
        {(env) => <Matrix env={env} />}
      </AsyncBoundary>
    </div>
  );
}

function Matrix({ env }: { env: Envelope<CorrelationMatrixView> }) {
  const v = env.data;
  const cells: (HeatCell | null)[][] = v.instruments.map((a, r) =>
    v.instruments.map((b, c) => {
      const rho = v.matrix[r]?.[c] ?? null;
      return {
        text: rho === null ? "n/a" : formatNumber(rho, "", { decimals: 2 }),
        bin: rho === null ? null : divergingBin(rho, 1),
        label: `${a} with ${b}: ${rho === null ? "not computable" : formatNumber(rho, "", { decimals: 2 })}`,
        marks: r === c ? ["diagonal"] : [],
        testId: "universe-corr-cell",
      };
    }),
  );
  const excluded = Object.entries(v.excluded);
  return (
    <>
      <SourceBadge source={env.source} />
      <CaveatList caveats={env.caveats} />
      <section className="rs-panel">
        <GridHeatmap
          title={`${v.timeframe} log-return correlation, last ${v.bars_used} common bars`}
          rowHeader=""
          rowLabels={v.instruments}
          colLabels={v.instruments}
          cells={cells}
          dense
          rotateCols
          testId="universe-correlation"
          legend={<HeatLegend low="−1" high="+1" />}
          note={`${v.method} Window ${formatTimestamp(v.first_common)} to ${formatTimestamp(v.last_common)}.`}
        />
      </section>
      <div className="rs-two">
        <section className="rs-panel" aria-labelledby="corr-excluded">
          <h2 className="rs-panel__title" id="corr-excluded">
            Not in the matrix ({excluded.length})
          </h2>
          {excluded.length === 0 ? (
            <p className="muted">Every instrument with a validated {v.timeframe} dataset is in the matrix.</p>
          ) : (
            <table className="rs-table" data-testid="corr-excluded">
              <tbody>
                {excluded.map(([sym, why]) => (
                  <tr key={sym}>
                    <th scope="row" className="mono">
                      {sym}
                    </th>
                    <td>{why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="rs-panel" aria-labelledby="corr-datasets">
          <h2 className="rs-panel__title" id="corr-datasets">
            Datasets read
          </h2>
          <table className="rs-table">
            <tbody>
              {Object.entries(v.dataset_version_ids).map(([sym, id]) => (
                <tr key={sym}>
                  <th scope="row" className="mono">
                    {sym}
                  </th>
                  <td className="mono">{id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </>
  );
}
