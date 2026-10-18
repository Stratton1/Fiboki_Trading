"use client";

import { useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { FanChart } from "@/components/charts/analytics/FanChart";
import { formatNumber } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { MonteCarloView } from "@/lib/research";
import type { Envelope } from "@/lib/types";

const DEFAULT_SEED = 20261018;
const PATHS = [500, 1000, 2000, 5000];

/**
 * Monte Carlo of the trade sequence (feature 28), computed by the backend
 * (GET /api/research/runs/{id}/monte-carlo: stats/bootstrap.py
 * resample_with_compounding). The seed is shown and editable, so the same
 * request always draws the same fan. Loaded lazily with the tearsheet.
 */
export default function MonteCarloPanel({ runId }: { runId: string }) {
  const [seed, setSeed] = useState(DEFAULT_SEED);
  const [draft, setDraft] = useState(String(DEFAULT_SEED));
  const [paths, setPaths] = useState(1000);
  const state = useApi<Envelope<MonteCarloView>>(
    `/api/research/runs/${encodeURIComponent(runId)}/monte-carlo?paths=${paths}&seed=${seed}`,
  );
  return (
    <div data-testid="monte-carlo">
      <form
        className="rs-controls"
        onSubmit={(e) => {
          e.preventDefault();
          const n = Number(draft);
          if (Number.isInteger(n) && n >= 0) setSeed(n);
        }}
      >
        <label htmlFor="mc-seed">Seed</label>
        <input
          id="mc-seed"
          type="text"
          className="rs-input mono"
          inputMode="numeric"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          data-testid="mc-seed"
        />
        <label htmlFor="mc-paths">Paths</label>
        <select id="mc-paths" value={paths} onChange={(e) => setPaths(Number(e.target.value))} data-testid="mc-paths">
          {PATHS.map((p) => (
            <option key={p} value={p}>
              {p.toLocaleString("en-GB")}
            </option>
          ))}
        </select>
        <button type="submit" className="rs-btn">
          Re-draw
        </button>
      </form>
      <AsyncBoundary state={state} label="the simulation" onRetry={state.reload}>
        {(env) => <Result view={env.data} />}
      </AsyncBoundary>
    </div>
  );
}

/** A drawdown depth (stored positive) shown as a fall. */
function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "n/a" : formatNumber(-v, "pct");
}

function money(v: number | null | undefined, ccy: string): string {
  return v === null || v === undefined ? "n/a" : formatNumber(v, ccy);
}

function Result({ view }: { view: MonteCarloView }) {
  const ccy = view.account_ccy;
  const dd = ["p50", "p75", "p90", "p95", "p99"];
  const fin = ["p5", "p25", "p50", "p75", "p95"];
  return (
    <div className="rs-mc">
      <FanChart
        title={`Equity fan, ${view.n_paths.toLocaleString("en-GB")} resampled paths`}
        unit={ccy}
        steps={view.fan_steps}
        fan={view.fan}
        initial={view.initial_equity.value ?? Number.NaN}
        testId="mc-fan"
        meta={
          <span className="mono muted" data-testid="mc-seed-shown">
            seed {view.seed} · block {view.block_length.value === null ? "?" : formatNumber(view.block_length.value, "ratio")} trades · basis{" "}
            {view.basis === "r_multiple" ? "R, re-sized" : "trade return"}
          </span>
        }
        note={view.method}
      />
      <div className="rs-mc__tables">
        <table className="rs-table" data-testid="mc-drawdown-table">
          <caption>Max drawdown by percentile of paths</caption>
          <thead>
            <tr>
              <th scope="col">Percentile</th>
              <th scope="col" className="num">
                Max drawdown
              </th>
            </tr>
          </thead>
          <tbody>
            {dd.map((k) => (
              <tr key={k}>
                <th scope="row">{k.replace("p", "")}th</th>
                <td className="num neg">{pct(view.drawdown_percentiles[k]?.value)}</td>
              </tr>
            ))}
            <tr className="rs-table__ref">
              <th scope="row">This run, in order</th>
              <td className="num">
                {pct(view.realised_max_drawdown_pct.value)}
              </td>
            </tr>
          </tbody>
        </table>
        <table className="rs-table" data-testid="mc-final-table">
          <caption>Final equity by percentile</caption>
          <thead>
            <tr>
              <th scope="col">Percentile</th>
              <th scope="col" className="num">
                Final equity
              </th>
            </tr>
          </thead>
          <tbody>
            {fin.map((k) => (
              <tr key={k}>
                <th scope="row">{k.replace("p", "")}th</th>
                <td className="num">{money(view.final_equity_percentiles[k]?.value, ccy)}</td>
              </tr>
            ))}
            <tr className="rs-table__ref">
              <th scope="row">This run, in order</th>
              <td className="num">
                {money(view.realised_final_equity.value, ccy)}
              </td>
            </tr>
          </tbody>
        </table>
        <div className="rs-mc__ruin">
          <div className="rs-stat__k">Probability of ruin</div>
          <FigureValue figure={view.ruin_probability} showChip={false} />
          <div className="muted">
            a {formatNumber((1 - view.ruin_fraction) * 100, "pct")} fall from a running peak, over {view.n_trades} trades
          </div>
          <ul className="rs-notes">
            {view.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
