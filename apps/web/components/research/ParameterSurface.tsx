"use client";

import Link from "next/link";
import { useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { GridHeatmap, HeatLegend, divergingBin, type HeatCell } from "@/components/charts/analytics/GridHeatmap";
import { CaveatList, SourceBadge } from "@/components/primitives";
import { formatNumber } from "@/lib/format";
import { useApi } from "@/lib/query";
import { researchHref, type ParameterSurfaceView, type SurfaceCellView } from "@/lib/research";
import type { Envelope } from "@/lib/types";
import "./research.css";

const METRIC_LABEL: Record<string, string> = {
  sharpe: "Sharpe (daily)",
  total_return_pct: "Total return %",
  cagr_pct: "CAGR %",
  profit_factor: "Profit factor",
  expectancy: "Expectancy",
  win_rate_pct: "Win rate %",
  max_drawdown_pct: "Max drawdown % (higher is shallower)",
  sortino: "Sortino",
  calmar: "Calmar",
};

/**
 * The parameter heatmap (feature 23): a 2D surface of stored runs over any
 * two numeric parameters, with the plateau region the server marked using the
 * ladder's own rung-4 plateau gates (GET /api/research/parameter-lab/{id}/surface).
 * Loaded lazily by the Parameter Lab.
 */
export default function ParameterSurface({ strategyId }: { strategyId: string }) {
  const [x, setX] = useState<string | null>(null);
  const [y, setY] = useState<string | null>(null);
  const [metric, setMetric] = useState("sharpe");
  const [fixed, setFixed] = useState<Record<string, string>>({});
  const q = new URLSearchParams({ metric });
  if (x) q.set("x", x);
  if (y) q.set("y", y);
  const fixedNums = Object.fromEntries(
    Object.entries(fixed)
      .filter(([, v]) => v.trim() !== "" && Number.isFinite(Number(v)))
      .map(([k, v]) => [k, Number(v)]),
  );
  if (Object.keys(fixedNums).length) q.set("fixed", JSON.stringify(fixedNums));
  const state = useApi<Envelope<ParameterSurfaceView>>(
    `/api/research/parameter-lab/${encodeURIComponent(strategyId)}/surface?${q.toString()}`,
  );
  return (
    <section className="rs-panel" aria-labelledby="surface-title" data-testid="parameter-surface">
      <h2 className="rs-panel__title" id="surface-title">
        Parameter surface
      </h2>
      <AsyncBoundary state={state} label="the parameter surface" onRetry={state.reload}>
        {(env) => (
          <Surface
            env={env}
            onAxes={(nx, ny) => {
              setX(nx);
              setY(ny);
            }}
            metric={metric}
            onMetric={setMetric}
            fixed={fixed}
            onFixed={(k, v) => setFixed((f) => ({ ...f, [k]: v }))}
          />
        )}
      </AsyncBoundary>
    </section>
  );
}

function Surface({
  env,
  onAxes,
  metric,
  onMetric,
  fixed,
  onFixed,
}: {
  env: Envelope<ParameterSurfaceView>;
  onAxes: (x: string, y: string) => void;
  metric: string;
  onMetric: (m: string) => void;
  fixed: Record<string, string>;
  onFixed: (k: string, v: string) => void;
}) {
  const v = env.data;
  const [selected, setSelected] = useState<SurfaceCellView | null>(null);
  const [full, setFull] = useState(false);
  const others = v.parameters.filter((p) => p !== v.x && p !== v.y);
  const key = (a: number, b: number) => `${a}|${b}`;
  const byKey = new Map(v.cells.map((c) => [key(c.x, c.y), c]));
  const scores = v.cells.map((c) => c.score.value).filter((s): s is number => s !== null);
  const span = Math.max(1e-12, ...scores.map((s) => Math.abs(s)));
  // Presentation only: by default the grid is cropped to the cells that were
  // run, plus one step on every side (the neighbourhood the plateau reads);
  // "full domain" shows every declared value.
  const ran = v.cells.filter((c) => c.n_runs > 0);
  const crop = (values: number[], pick: (c: SurfaceCellView) => number) => {
    if (full || ran.length === 0) return values;
    const idx = ran.map((c) => values.indexOf(pick(c))).filter((i) => i >= 0);
    const lo = Math.max(0, Math.min(...idx) - 1);
    const hi = Math.min(values.length - 1, Math.max(...idx) + 1);
    return values.slice(lo, hi + 1);
  };
  const xs = crop(v.x_values, (c) => c.x);
  const ys = [...crop(v.y_values, (c) => c.y)].reverse();
  const cells: (HeatCell | null)[][] = ys.map((yv) =>
    xs.map((xv) => {
      const c = byKey.get(key(xv, yv));
      if (!c) return null;
      const marks = [
        ...(c.on_plateau ? ["plateau"] : []),
        ...(c.isolated_peak ? ["peak"] : []),
        ...(selected && selected.x === c.x && selected.y === c.y ? ["selected"] : []),
      ];
      const score = c.score.value;
      return {
        text: score === null ? (c.n_runs ? "n/a" : "") : formatNumber(score, "", { decimals: 2 }),
        bin: score === null ? null : divergingBin(score, span),
        label: `${v.x} ${xv}, ${v.y} ${yv}: ${score === null ? "not run" : `${METRIC_LABEL[v.metric] ?? v.metric} ${formatNumber(score, "", { decimals: 3 })} over ${c.n_runs} run(s)`}${c.on_plateau ? ", on the plateau" : ""}${c.isolated_peak ? ", an isolated peak" : ""}`,
        marks,
        onSelect: c.n_runs ? () => setSelected(c) : undefined,
        testId: "surface-cell",
      };
    }),
  );
  const plateauCount = v.cells.filter((c) => c.on_plateau).length;
  return (
    <>
      <SourceBadge source={env.source} />
      <CaveatList caveats={env.caveats} />
      <div className="rs-controls">
        <label htmlFor="surface-x">X</label>
        <select id="surface-x" data-testid="surface-x" value={v.x} onChange={(e) => onAxes(e.target.value, e.target.value === v.y ? v.x : v.y)}>
          {v.parameters.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <label htmlFor="surface-y">Y</label>
        <select id="surface-y" data-testid="surface-y" value={v.y} onChange={(e) => onAxes(e.target.value === v.x ? v.y : v.x, e.target.value)}>
          {v.parameters.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <label htmlFor="surface-metric">Metric</label>
        <select id="surface-metric" data-testid="surface-metric" value={metric} onChange={(e) => onMetric(e.target.value)}>
          {v.metrics_available.map((m) => (
            <option key={m} value={m}>
              {METRIC_LABEL[m] ?? m}
            </option>
          ))}
        </select>
      </div>
      <div className="rs-controls rs-controls--fixed" role="group" aria-label="Other parameters, held fixed" hidden={others.length === 0}>
        <span className="rs-metric">Held fixed</span>
        {others.map((p) => (
          <span key={p} className="rs-field">
            <label htmlFor={`fixed-${p}`}>{p} =</label>
            <input
              id={`fixed-${p}`}
              type="text"
              className="rs-input mono"
              inputMode="decimal"
              value={fixed[p] ?? String(v.fixed[p] ?? "")}
              onChange={(e) => onFixed(p, e.target.value)}
            />
          </span>
        ))}
      </div>
      <div className="rs-split">
        <GridHeatmap
          title={`${METRIC_LABEL[v.metric] ?? v.metric} over ${v.x} and ${v.y}`}
          rowHeader={`${v.y} ↓ / ${v.x} →`}
          rowLabels={ys.map((y) => formatNumber(y, ""))}
          colLabels={xs.map((x) => formatNumber(x, ""))}
          cells={cells}
          dense={xs.length > 12}
          meta={
            <label className="rs-field">
              <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} data-testid="surface-full" />
              full declared domain
            </label>
          }
          testId="surface-heatmap"
          legend={
            <>
              <HeatLegend low={`−${formatNumber(span, "", { decimals: 2 })}`} high={`+${formatNumber(span, "", { decimals: 2 })}`} />
              <span className="ax__key">green outline: on the plateau ({plateauCount})</span>
              <span className="ax__key">▲ isolated peak</span>
              <span className="ax__key">hatched: not run</span>
            </>
          }
          note={v.plateau_definition}
        />
        <aside className="rs-split__side" data-testid="surface-detail">
          <dl className="rs-kv">
            <dt>Runs placed</dt>
            <dd data-testid="surface-runs-used">
              {v.runs_used} of {v.runs_considered}
            </dd>
            <dt>Other parameters fixed at</dt>
            <dd className="mono">
              {Object.entries(v.fixed)
                .map(([k, x]) => `${k}=${String(x)}`)
                .join(", ") || "none"}
            </dd>
            <dt>Neighbourhood</dt>
            <dd>
              {v.radius} grid step(s), gate set <span className="mono">{v.gate_set_version}</span>
            </dd>
          </dl>
          <div className="rs-stat__k">Plateau gates (rung 4)</div>
          <ul className="rs-plateau-gates">
            {v.plateau_gates.map((g) => (
              <li key={g.name}>
                {g.name}
                <code>
                  {g.metric} {g.comparison} {g.threshold}
                </code>
                <span className="muted">{g.rationale}</span>
              </li>
            ))}
          </ul>
          {selected ? (
            <div className="rs-panel" data-testid="surface-selected">
              <h3 className="rs-panel__title">
                {v.x} {selected.x} · {v.y} {selected.y}
              </h3>
              <dl className="rs-kv">
                <dt>Score</dt>
                <dd>
                  <FigureValue figure={selected.score} decimals={4} missingLabel="n/a" showChip={false} />
                </dd>
                <dt>Plateau ratio</dt>
                <dd>
                  <FigureValue figure={selected.point_plateau_ratio} missingLabel="n/a" showChip={false} />
                </dd>
                <dt>Neighbour median / point</dt>
                <dd>
                  <FigureValue figure={selected.neighbourhood_median_ratio} missingLabel="n/a" showChip={false} />
                </dd>
                <dt>Worst neighbour</dt>
                <dd>
                  <FigureValue figure={selected.neighbourhood_min} decimals={4} missingLabel="n/a" showChip={false} />
                </dd>
              </dl>
              <ul className="rs-notes">
                {Object.entries(selected.plateau_checks).map(([name, ok]) => (
                  <li key={name}>
                    <span className={`badge badge--${ok ? "ok" : "down"}`}>{ok ? "PASS" : "FAIL"}</span> {name}
                  </li>
                ))}
                {selected.run_ids.map((id) => (
                  <li key={id}>
                    <Link href={researchHref.run(id)} className="mono">
                      {id}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="muted">Select a cell that was run to see its plateau checks and its runs.</p>
          )}
          {v.notes.length ? (
            <ul className="rs-notes" data-testid="surface-notes">
              {v.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          ) : null}
        </aside>
      </div>
    </>
  );
}
