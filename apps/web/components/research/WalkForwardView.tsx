"use client";

import Link from "next/link";
import { useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { AnalyticsFrame } from "@/components/charts/analytics/AnalyticsFrame";
import { extent, fmtDate, linear, niceTicks, timeTicks } from "@/components/charts/analytics/scale";
import { CaveatList, SourceBadge, ToneBadge } from "@/components/primitives";
import { formatNumber, parseUtc } from "@/lib/format";
import { useApi } from "@/lib/query";
import { researchHref, type FoldView, type WalkForwardView as WF } from "@/lib/research";
import type { Envelope } from "@/lib/types";
import { useWidth } from "@/lib/use-width";
import { GateCheck, gateName } from "./GateCheck";
import { MetricLabel } from "./MetricLabel";
import { rungTone } from "./tones";

/**
 * The ladder's walk-forward rung for one strategy (feature 25), as filed:
 * GET /api/research/strategies/{id}/walk-forward. In-sample and out-of-sample
 * windows per fold on one time axis, their profit rates side by side, and the
 * stability figures the server derived from them.
 */
export function WalkForwardPanel({ strategyId, source }: { strategyId: string; source: string | null }) {
  const state = useApi<Envelope<WF>>(
    `/api/research/strategies/${encodeURIComponent(strategyId)}/walk-forward${source ? `?source=${encodeURIComponent(source)}` : ""}`,
  );
  return (
    <AsyncBoundary state={state} label="the walk-forward folds" onRetry={state.reload}>
      {(env) => <Folds env={env} />}
    </AsyncBoundary>
  );
}

function Folds({ env }: { env: Envelope<WF> }) {
  const v = env.data;
  const wfeGate = v.gates.find((g) => g.metric === "walk_forward_efficiency" || g.name === "walk_forward_efficiency");
  const hitGate = v.gates.find((g) => g.metric === "oos_profitable_fraction");
  return (
    <>
      <SourceBadge source={env.source} />
      <CaveatList caveats={env.caveats} />
      <section className="rs-panel" aria-labelledby="wf-summary">
        <h2 className="rs-panel__title" id="wf-summary">
          Rung 2 walk-forward · {v.scheme || "scheme not recorded"} · {v.n_folds} folds
        </h2>
        <div className="rs-gate-head">
          <ToneBadge tone={rungTone(v.outcome)} testId="wf-outcome" value={v.outcome} />
          <span className="muted">{v.reason || "The rung recorded no reason."}</span>
          <span className="mono muted">{v.gate_set_version}</span>
        </div>
        <dl className="rs-stats" data-testid="wf-stats">
          <Stat label="Walk-forward efficiency" def={v.definitions.walk_forward_efficiency} figure={v.walk_forward_efficiency} check={wfeGate} />
          <Stat label="OOS profitable folds" figure={v.oos_profitable_fraction} check={hitGate} />
          <Stat label="Mean IS rate" figure={v.mean_is_rate} />
          <Stat label="Mean OOS rate" figure={v.mean_oos_rate} />
          <Stat label="OOS rate sd" figure={v.oos_rate_sd} />
          <Stat label="OOS dispersion" def={v.definitions.oos_rate_cv} figure={v.oos_rate_cv} />
          <div className="rs-stat">
            <dt className="rs-stat__k">Same parameters as previous fold</dt>
            <dd>
              {v.parameter_repeats} of {Math.max(0, v.n_folds - 1)}
            </dd>
          </div>
        </dl>
        <p className="ax__note">Rates are {v.rate_unit}.</p>
      </section>
      <div className="rs-two">
        <section className="rs-panel" aria-label="Fold windows">
          <FoldTimeline folds={v.folds} />
        </section>
        <section className="rs-panel" aria-label="In-sample against out-of-sample">
          <FoldBars folds={v.folds} />
        </section>
      </div>
      <section className="rs-panel" aria-labelledby="wf-table">
        <h2 className="rs-panel__title" id="wf-table">
          Folds
        </h2>
        <div className="table-wrap">
          <table className="rs-table" data-testid="wf-folds">
            <thead>
              <tr>
                <th scope="col">Fold</th>
                <th scope="col">Train (IS)</th>
                <th scope="col">Test (OOS)</th>
                <th scope="col" className="num">
                  IS rate
                </th>
                <th scope="col" className="num">
                  OOS rate
                </th>
                <th scope="col" className="num">
                  <MetricLabel label="Efficiency" definition={v.definitions.efficiency} />
                </th>
                <th scope="col" className="num">
                  OOS net
                </th>
                <th scope="col" className="num">
                  OOS trades
                </th>
                <th scope="col" className="num">
                  OOS Sharpe
                </th>
                <th scope="col" className="num">
                  Defaults OOS rate
                </th>
                <th scope="col">Selected parameters</th>
              </tr>
            </thead>
            <tbody>
              {v.folds.map((f) => (
                <tr key={f.fold} data-testid="wf-fold">
                  <th scope="row">{f.fold}</th>
                  <td>{span(f.train_start, f.train_end)}</td>
                  <td>{span(f.test_start, f.test_end)}</td>
                  <N v={f.is_rate.value} />
                  <N v={f.oos_rate.value} signed />
                  <N v={f.efficiency.value} />
                  <N v={f.oos_net_profit.value} signed />
                  <td className="num">{f.oos_trades.value ?? "—"}</td>
                  <N v={f.oos_sharpe.value} d={4} signed />
                  <N v={f.fixed_param_oos_rate.value} signed />
                  <td className="mono wrap">
                    {Object.entries(f.selected_params)
                      .map(([k, x]) => `${k}=${String(x)}`)
                      .join(", ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="ax__note">
          &quot;Defaults OOS rate&quot; re-runs the declared default parameters on the same test window: a diagnostic, not
          evidence (the ladder&apos;s CONTRAST figures).
        </p>
      </section>
      <div className="rs-two">
        <section className="rs-panel" aria-labelledby="wf-gates">
          <h2 className="rs-panel__title" id="wf-gates">
            Rung 2 gates
          </h2>
          {v.gates.length === 0 ? (
            <p className="muted">The report records no rung-2 gate results.</p>
          ) : (
            <table className="rs-table" data-testid="wf-gates">
              <tbody>
                {v.gates.map((g) => (
                  <tr key={g.name}>
                    <th scope="row" className="rs-gate-name">
                      {gateName(g.name)}
                    </th>
                    <td className="num">{g.value.value === null ? "not evaluated" : formatNumber(g.value.value, "")}</td>
                    <td>
                      <GateCheck check={g} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="rs-panel" aria-labelledby="wf-runs">
          <h2 className="rs-panel__title" id="wf-runs">
            Walk-forward fold runs in the ledger
          </h2>
          {v.fold_runs.length === 0 ? (
            <p className="muted">No walk-forward job filed fold backtests for this strategy in the research ledger.</p>
          ) : (
            <ul className="rs-notes">
              {v.fold_runs.map((r) => (
                <li key={r.run_id}>
                  <Link href={researchHref.run(r.run_id)} className="mono">
                    {r.run_id}
                  </Link>{" "}
                  {r.label} · {r.instruments.join(", ")} {r.timeframe}
                </li>
              ))}
            </ul>
          )}
          <p className="ax__note">
            Source: {v.source === "report_file" ? "report file" : "experiment ledger"} <span className="mono">{v.source_ref}</span>
            {v.available.length > 1 ? ` (${v.available.length} reports reached this rung; the newest is shown)` : null}
          </p>
        </section>
      </div>
    </>
  );
}

function num(v: number | null, d: number): string {
  return v === null ? "—" : formatNumber(v, "", { decimals: d });
}

function span(a: string | null, b: string | null) {
  if (!a || !b) return "—";
  return `${fmtDate.format(parseUtc(a))} to ${fmtDate.format(parseUtc(b))}`;
}

function N({ v, d, signed = false }: { v: number | null; d?: number; signed?: boolean }) {
  if (v === null) return <td className="num muted">—</td>;
  const text = formatNumber(v, "", { decimals: d ?? 3 });
  return <td className={`num ${signed ? (v > 0 ? "pos" : v < 0 ? "neg" : "") : ""}`}>{signed && v > 0 ? `+${text}` : text}</td>;
}

function Stat({ label, figure, def, check }: { label: string; figure: Parameters<typeof FigureValue>[0]["figure"]; def?: { label: string; definition: string; formula: string }; check?: Parameters<typeof GateCheck>[0]["check"] }) {
  return (
    <div className="rs-stat">
      <dt>{def ? <MetricLabel label={label} definition={def} /> : <span className="rs-metric">{label}</span>}</dt>
      <dd>
        <FigureValue figure={figure} showChip={false} />
        {check ? <GateCheck check={check} /> : null}
      </dd>
    </div>
  );
}

const ROW = 22;
const PADL = 54;

function FoldTimeline({ folds }: { folds: FoldView[] }) {
  const [ref, width] = useWidth(700);
  const times = folds.flatMap((f) => [f.train_start, f.train_end, f.test_start, f.test_end]).filter(Boolean) as string[];
  if (times.length === 0) return <p className="muted">No fold windows were recorded.</p>;
  const ms = times.map((t) => parseUtc(t).getTime());
  const t0 = Math.min(...ms);
  const t1 = Math.max(...ms);
  const x = linear([t0, t1], [PADL, width - 10]);
  const height = folds.length * ROW + 28;
  return (
    <AnalyticsFrame
      title="Fold windows"
      testId="wf-timeline"
      legend={
        <>
          <span className="ax__key">
            <svg width="14" height="9" aria-hidden="true">
              <rect width="14" height="9" className="rs-fold__train" />
            </svg>
            train (in-sample)
          </span>
          <span className="ax__key">
            <svg width="14" height="9" aria-hidden="true">
              <rect width="14" height="9" className="rs-fold__test" />
            </svg>
            test (out-of-sample)
          </span>
        </>
      }
    >
      <div ref={ref} className="ax__plot">
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Train and test windows per fold; dates are in the Folds table." className="ax__svg">
          {timeTicks(t0, t1, width > 600 ? 6 : 3).map((t, i, all) => (
            <g key={i}>
              <line className="ax__grid" x1={x(t.ms)} x2={x(t.ms)} y1={4} y2={height - 20} />
              <text className="ax__tick" x={x(t.ms)} y={height - 6} textAnchor={i === 0 ? "start" : i === all.length - 1 ? "end" : "middle"}>
                {t.label}
              </text>
            </g>
          ))}
          {folds.map((f, i) => {
            const y = 6 + i * ROW;
            const seg = (a: string | null, b: string | null, cls: string) =>
              a && b ? (
                <rect className={cls} x={x(parseUtc(a).getTime())} y={y} width={Math.max(1, x(parseUtc(b).getTime()) - x(parseUtc(a).getTime()))} height={ROW - 8} />
              ) : null;
            return (
              <g key={f.fold}>
                <text className="ax__tick" x={4} y={y + 10}>
                  fold {f.fold}
                </text>
                {seg(f.train_start, f.train_end, "rs-fold__train")}
                {seg(f.test_start, f.test_end, "rs-fold__test")}
              </g>
            );
          })}
        </svg>
      </div>
    </AnalyticsFrame>
  );
}

function FoldBars({ folds }: { folds: FoldView[] }) {
  const [ref, width] = useWidth(700);
  const [hover, setHover] = useState<number | null>(null);
  const values = folds.flatMap((f) => [f.is_rate.value, f.oos_rate.value]);
  const [y0, y1] = extent(values, 0);
  const height = 220;
  const PAD = { l: 48, r: 10, t: 10, b: 24 };
  const y = linear([y0, y1], [height - PAD.b, PAD.t]);
  const band = (width - PAD.l - PAD.r) / Math.max(1, folds.length);
  const bw = Math.min(26, band / 3);
  const h = hover === null ? null : folds[hover];
  return (
    <AnalyticsFrame
      title="In-sample against out-of-sample rate"
      testId="wf-bars"
      legend={
        <>
          <span className="ax__key">
            <svg width="10" height="10" aria-hidden="true">
              <rect width="10" height="10" className="rs-fold__bar" data-kind="is" />
            </svg>
            in-sample
          </span>
          <span className="ax__key">
            <svg width="10" height="10" aria-hidden="true">
              <rect width="10" height="10" className="rs-fold__bar" data-kind="oos" data-side="up" />
            </svg>
            out-of-sample (red when negative)
          </span>
        </>
      }
    >
      <div className="ax__readout" aria-live="polite">
        <span className="ax__readout-hint">
          {h
            ? `Fold ${h.fold}: IS ${num(h.is_rate.value, 3)}, OOS ${num(h.oos_rate.value, 3)}, efficiency ${num(h.efficiency.value, 2)}`
            : "Hover or focus a fold."}
        </span>
      </div>
      <div ref={ref} className="ax__plot">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="In-sample and out-of-sample rate per fold. Use the arrow keys to read each fold; values are in the Folds table."
          className="ax__svg"
          tabIndex={0}
          onKeyDown={(e) => {
            const n = folds.length;
            if (e.key === "ArrowRight") setHover((h) => (h === null ? 0 : Math.min(n - 1, h + 1)));
            else if (e.key === "ArrowLeft") setHover((h) => (h === null ? n - 1 : Math.max(0, h - 1)));
            else if (e.key === "Escape") setHover(null);
            else return;
            e.preventDefault();
          }}
          onBlur={() => setHover(null)}
        >
          {niceTicks(y0, y1, 4).map((v) => (
            <g key={v}>
              <line className="ax__grid" x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} />
              <text className="ax__tick" x={PAD.l - 5} y={y(v) + 3} textAnchor="end">
                {formatNumber(v, "", { decimals: 2 })}
              </text>
            </g>
          ))}
          <line className="ax__zero" x1={PAD.l} x2={width - PAD.r} y1={y(0)} y2={y(0)} />
          {folds.map((f, i) => {
            const cx = PAD.l + band * i + band / 2;
            const bar = (v: number | null, dx: number, kind: "is" | "oos") =>
              v === null ? null : (
                <rect
                  className="rs-fold__bar"
                  data-kind={kind}
                  data-side={v >= 0 ? "up" : "down"}
                  x={cx + dx}
                  y={Math.min(y(v), y(0))}
                  width={bw}
                  height={Math.max(1, Math.abs(y(v) - y(0)))}
                />
              );
            return (
              <g key={f.fold} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
                {bar(f.is_rate.value, -bw - 1, "is")}
                {bar(f.oos_rate.value, 1, "oos")}
                <text className="ax__tick" x={cx} y={height - 6} textAnchor="middle">
                  {f.fold}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </AnalyticsFrame>
  );
}
