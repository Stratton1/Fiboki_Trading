"use client";

import Link from "next/link";
import { lazy, Suspense, useMemo, useState, type ReactNode } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { ProvenanceChip } from "@/components/ProvenanceChip";
import { CaveatList, SourceBadge, ToneBadge } from "@/components/primitives";
import { GridHeatmap, HeatLegend, divergingBin, type HeatCell } from "@/components/charts/analytics/GridHeatmap";
import { HistogramChart } from "@/components/charts/analytics/HistogramChart";
import { ScatterChart, type ScatterPoint } from "@/components/charts/analytics/ScatterChart";
import { TimeSeriesChart } from "@/components/charts/analytics/TimeSeriesChart";
import { fmtDate } from "@/components/charts/analytics/scale";
import { formatNumber, formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import { researchHref, type TearsheetView } from "@/lib/research";
import type { Envelope } from "@/lib/types";
import { GateCheck, gateName } from "../GateCheck";
import { MetricLabel } from "../MetricLabel";
import { ResearchNav } from "../ResearchNav";
import { gateVerdictTone } from "../tones";

const MonteCarloPanel = lazy(() => import("./MonteCarloPanel"));
const RobustnessPanel = lazy(() => import("../robustness/RobustnessPanel"));

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * RESEARCH · one stored run, as a tearsheet (`/research/runs/<run_id>`).
 *
 * Every number, series, bin and verdict is the API's
 * (GET /api/research/runs/{id}/tearsheet, computed in research/tearsheet.py
 * from the stored record). This component lays them out and reads them back;
 * it computes no statistic.
 */
export function RunTearsheet({ runId }: { runId: string }) {
  const path = `/api/research/runs/${encodeURIComponent(runId)}/tearsheet`;
  const state = useApi<Envelope<TearsheetView>>(path);
  return (
    <div className="rs-page" data-testid="tearsheet">
      <ResearchNav />
      {state.status !== "success" ? (
        <header className="page-head">
          <h1>Run {runId}</h1>
          <p>The run&apos;s tearsheet: statistics, curves, distributions, gate verdicts and provenance.</p>
        </header>
      ) : null}
      <AsyncBoundary state={state} label="the tearsheet" onRetry={state.reload}>
        {(env) => <Sheet env={env} />}
      </AsyncBoundary>
    </div>
  );
}

function Sheet({ env }: { env: Envelope<TearsheetView> }) {
  const view = env.data;
  const { run } = view;
  const [cursor, setCursor] = useState<number | null>(null);
  const verdict = gateVerdictTone(run.gate_verdict);
  const defs = view.definitions;
  const ccy = view.account_ccy;

  return (
    <>
      <header className="rs-head">
        <div className="rs-head__main">
          <h1 className="rs-head__title">
            <span className="rs-head__kicker">Tearsheet</span>
            <span className="mono">{run.run_id}</span>
          </h1>
          <div className="rs-head__facts">
            <Link href={view.strategy_link} data-testid="strategy-link">
              {run.strategy_id}
            </Link>
            <span>{run.timeframe || "timeframe not recorded"}</span>
            <span>{run.instruments.join(", ") || "no instruments recorded"}</span>
            <span>{run.kind === "walkforward_fold" ? `walk-forward ${run.label}` : "backtest"}</span>
            <span>filed {formatTimestamp(run.created_at)}</span>
          </div>
        </div>
        <div className="rs-head__badges">
          <ProvenanceChip provenance={run.provenance} />
          <ToneBadge tone={verdict} testId="run-gate-verdict" value={run.gate_verdict} />
          {run.engine_current && !run.superseded ? (
            <span className="badge badge--ok" data-testid="engine-current">
              ENGINE CURRENT
            </span>
          ) : (
            <span className="badge badge--degraded" data-testid="engine-current">
              {run.superseded ? "SUPERSEDED" : "OLDER ENGINE"}
            </span>
          )}
        </div>
        <div className="rs-head__actions">
          {Object.entries(view.chart_links).map(([sym, href]) => (
            <Link key={sym} href={href} className="rs-btn" data-testid="chart-link">
              Chart {sym} with trades
            </Link>
          ))}
          <Link href={researchHref.compare([run.run_id])} className="rs-btn rs-btn--ghost">
            Compare…
          </Link>
          <a href="#monte-carlo" className="rs-btn rs-btn--ghost">
            Monte Carlo
          </a>
          <a href="#robustness" className="rs-btn rs-btn--ghost">
            Robustness
          </a>
          <a href="#provenance" className="rs-btn rs-btn--ghost">
            Provenance
          </a>
        </div>
      </header>
      <SourceBadge source={env.source} />
      <CaveatList caveats={env.caveats} />

      <section className="rs-panel" aria-labelledby="ts-stats">
        <h2 className="rs-panel__title" id="ts-stats">
          Key statistics
        </h2>
        <dl className="rs-stats" data-testid="stat-grid">
          {view.stats.map((s) => (
            <div key={s.key} className="rs-stat" data-testid={`stat-${s.key}`} data-key={s.key}>
              <dt>
                <MetricLabel
                  label={s.label}
                  definition={{ label: s.label, definition: s.definition, formula: s.formula }}
                  source={s.source}
                  testId={`def-${s.key}`}
                />
              </dt>
              <dd>
                <FigureValue
                  figure={s.figure}
                  showChip={false}
                  colourSign={["net_profit", "total_return_pct", "cagr_pct", "expectancy", "expectancy_r"].includes(s.key)}
                  decimals={s.figure.unit === "bars" ? 1 : undefined}
                />
                {s.check ? <GateCheck check={s.check} showName={s.check.metric !== s.key} /> : null}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <div className="rs-split">
        <section className="rs-panel rs-split__main" aria-label="Equity and drawdown">
          <TimeSeriesChart
            title="Equity"
            unit={ccy}
            testId="equity-chart"
            cursor={cursor}
            onCursor={setCursor}
            height={250}
            baseline={view.provenance.initial_balance}
            baselineLabel="initial"
            lines={[
              { id: "equity", label: "Equity", points: view.equity.points, ink: "equity" },
              ...(view.benchmark
                ? [{ id: "benchmark", label: view.benchmark.name, points: view.benchmark.points, ink: "benchmark", dashed: true }]
                : []),
            ]}
            note={view.benchmark_note}
          />
          <TimeSeriesChart
            title="Underwater"
            unit="pct"
            testId="underwater-chart"
            cursor={cursor}
            onCursor={setCursor}
            height={140}
            zeroLine
            lines={[{ id: "underwater", label: "Below peak", points: view.underwater.points, ink: "loss", area: true }]}
            bands={view.drawdowns.map((d) => ({
              from: d.peak_at,
              to: d.recovered_at,
              label: "five deepest drawdowns, peak to recovery",
            }))}
            meta={<MetricLabel label="definition" definition={defs.underwater} testId="def-underwater" />}
          />
          <TimeSeriesChart
            title={`Rolling Sharpe, ${view.rolling_window_days} trading days`}
            unit="ratio"
            testId="rolling-sharpe-chart"
            cursor={cursor}
            onCursor={setCursor}
            height={130}
            zeroLine
            lines={[{ id: "rolling", label: "Rolling Sharpe", points: view.rolling_sharpe.points, ink: "s1" }]}
            meta={<MetricLabel label="definition" definition={defs.rolling_sharpe} testId="def-rolling" />}
            note={
              view.rolling_sharpe.points.length === 0
                ? `Fewer than ${view.rolling_window_days} trading days of returns: no rolling window fits.`
                : undefined
            }
          />
        </section>
        <aside className="rs-split__side">
          <DrawdownTable view={view} />
          <GatePanel view={view} />
        </aside>
      </div>

      <section className="rs-panel" aria-label="Monthly returns">
        <MonthlyReturns view={view} />
      </section>

      <div className="rs-two">
        <section className="rs-panel" aria-label="Trade return distribution">
          <HistogramChart
            title={view.trade_histogram.basis === "r_multiple" ? "Trade returns (R)" : `Trade net P&L (${ccy})`}
            unit={view.trade_histogram.unit}
            edges={view.trade_histogram.edges}
            counts={view.trade_histogram.counts}
            mean={view.trade_histogram.mean.value}
            median={view.trade_histogram.median.value}
            testId="trade-histogram"
            note={
              view.trade_histogram.basis === "r_multiple"
                ? "R: net P&L over the risk planned at entry (stop distance x size x contract x FX)."
                : "Stops were not recorded for every trade, so P&L is shown in the account currency, not R."
            }
          />
        </section>
        <section className="rs-panel" aria-label="MAE and MFE">
          <Excursions view={view} />
        </section>
      </div>

      <section className="rs-panel" aria-labelledby="ts-instruments">
        <h2 className="rs-panel__title" id="ts-instruments">
          {view.pooled ? "Per-instrument breakdown (pooled run)" : "By instrument"}
        </h2>
        <InstrumentTable view={view} />
      </section>

      <section className="rs-panel" id="monte-carlo" aria-labelledby="ts-mc">
        <h2 className="rs-panel__title" id="ts-mc">
          Monte Carlo of the trade sequence
        </h2>
        <Suspense fallback={<div className="state state--loading">Loading the simulation…</div>}>
          <MonteCarloPanel runId={run.run_id} />
        </Suspense>
      </section>

      <section className="rs-panel" id="robustness" aria-labelledby="ts-rb">
        <h2 className="rs-panel__title" id="ts-rb">
          Robustness and integrity
        </h2>
        <Suspense fallback={<div className="state state--loading">Loading the diagnostics…</div>}>
          <RobustnessPanel runId={run.run_id} />
        </Suspense>
      </section>

      <section className="rs-panel" id="provenance" aria-labelledby="ts-prov">
        <h2 className="rs-panel__title" id="ts-prov">
          Provenance
        </h2>
        <ProvenanceBlock view={view} />
      </section>
    </>
  );
}

function DrawdownTable({ view }: { view: TearsheetView }) {
  return (
    <section className="rs-panel" aria-labelledby="ts-dd">
      <h2 className="rs-panel__title" id="ts-dd">
        Deepest drawdowns
      </h2>
      {view.drawdowns.length === 0 ? (
        <p className="muted">The equity curve never fell below a previous peak.</p>
      ) : (
        <table className="rs-table" data-testid="drawdown-table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col" className="num">
                Depth
              </th>
              <th scope="col">Peak</th>
              <th scope="col" className="num">
                To trough
              </th>
              <th scope="col" className="num">
                Recovery
              </th>
            </tr>
          </thead>
          <tbody>
            {view.drawdowns.map((d) => (
              <tr key={d.rank}>
                <td>{d.rank}</td>
                <td className="num neg">{d.depth_pct.value === null ? "n/a" : formatNumber(d.depth_pct.value, "pct")}</td>
                <td>{fmtDate.format(new Date(d.peak_at))}</td>
                <td className="num">{days(d.decline_days.value)}</td>
                <td className="num">
                  {d.recovery_days.value === null ? (
                    <span className="rs-open">not recovered</span>
                  ) : (
                    days(d.recovery_days.value)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function GatePanel({ view }: { view: TearsheetView }) {
  const report = view.gate_report;
  return (
    <section className="rs-panel" id="gates" aria-labelledby="ts-gates" data-testid="gate-panel">
      <h2 className="rs-panel__title" id="ts-gates">
        Gate verdicts
      </h2>
      {report === null ? (
        <p className="muted" data-testid="no-gate-report">
          No deterministic gate report is filed against this run. That is not a pass: run the validation job to
          evaluate the {view.provenance.gate_set_version.replace("not evaluated ", "")}.
        </p>
      ) : (
        <>
          <div className="rs-gate-head">
            <ToneBadge tone={gateVerdictTone(report.verdict)} testId="gate-report-verdict" value={report.verdict} />
            <span className="mono muted">
              {report.gate_set_version} · {report.report_id}
            </span>
          </div>
          <table className="rs-table" data-testid="gate-table">
            <thead>
              <tr>
                <th scope="col">Gate</th>
                <th scope="col" className="num">
                  Value
                </th>
                <th scope="col">Check</th>
              </tr>
            </thead>
            <tbody>
              {report.checks.map((c) => (
                <tr key={c.name} data-status={c.status}>
                  <th scope="row" className="rs-gate-name">
                    {gateName(c.name)}
                    {c.rung !== null ? <span className="muted"> r{c.rung}</span> : null}
                  </th>
                  <td className="num">{c.value.value === null ? "not evaluated" : formatNumber(c.value.value, "")}</td>
                  <td>
                    <GateCheck check={c} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.caveats.length ? (
            <details className="rs-details">
              <summary>{report.caveats.length} note(s) from the gate job</summary>
              <ul>
                {report.caveats.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

function MonthlyReturns({ view }: { view: TearsheetView }) {
  const years = [...new Set(view.monthly.map((m) => m.year))].sort();
  const byKey = new Map(view.monthly.map((m) => [`${m.year}-${m.month}`, m.return_pct.value]));
  const span = Math.max(1, ...view.monthly.map((m) => Math.abs(m.return_pct.value ?? 1)));
  const cells: (HeatCell | null)[][] = years.map((y) =>
    MONTHS.map((name, i) => {
      const key = `${y}-${i + 1}`;
      if (!byKey.has(key)) return null;
      const v = byKey.get(key) ?? null;
      return {
        text: v === null ? "n/a" : formatNumber(v, "pct", { bare: true }),
        bin: v === null ? null : divergingBin(v, span),
        label: `${name} ${y}: ${v === null ? "not computable" : formatNumber(v, "pct")}`,
        testId: "month-cell",
      };
    }),
  );
  const yearly = new Map(view.yearly.map((y) => [y.year, y.return_pct.value]));
  return (
    <GridHeatmap
      title="Monthly returns"
      unit="% of equity, UTC calendar months"
      rowHeader="Year"
      rowLabels={years.map(String)}
      colLabels={MONTHS}
      cells={cells}
      testId="monthly-heatmap"
      extraCols={[
        {
          header: "Year",
          cells: years.map((y) => {
            const v = yearly.get(y);
            return v === null || v === undefined ? "n/a" : (
              <span className={v >= 0 ? "pos" : "neg"}>{formatNumber(v, "pct")}</span>
            );
          }),
        },
      ]}
      meta={<MetricLabel label="definition" definition={view.definitions.monthly_return} testId="def-monthly" />}
      legend={<HeatLegend low={`−${formatNumber(span, "pct")}`} high={`+${formatNumber(span, "pct")}`} />}
      note="Hatched: no equity point in that month (before the run, after it, or a gap). Blue is a loss, orange a gain; every cell prints its value."
    />
  );
}

function Excursions({ view }: { view: TearsheetView }) {
  const [axis, setAxis] = useState<"mae" | "mfe">("mae");
  const t = view.excursions;
  const n = t.trade_id.length;
  const inR = n > 0 && t.r_multiple.every((r) => r !== null) && t.mae_r.every((r) => r !== null);
  const unit = inR ? t.r_unit : t.pnl_unit;
  const points: ScatterPoint[] = useMemo(() => {
    const out: ScatterPoint[] = [];
    for (let i = 0; i < n; i += 1) {
      const xv = inR ? (axis === "mae" ? t.mae_r[i] : t.mfe_r[i]) : axis === "mae" ? t.mae[i] : t.mfe[i];
      const yv = inR ? t.r_multiple[i] : t.net_pnl[i];
      if (xv === null || xv === undefined || yv === null || yv === undefined) continue;
      out.push({
        id: t.trade_id[i]!,
        x: xv,
        y: yv,
        outcome: yv > 0 ? "win" : yv < 0 ? "loss" : "flat",
        label: `${t.instrument[i]} ${t.direction[i]} ${formatTimestamp(t.entry_time[i] ?? null)}`,
      });
    }
    return out;
  }, [t, n, axis, inR]);
  return (
    <ScatterChart
      title={axis === "mae" ? "MAE against result" : "MFE against result"}
      xLabel={axis === "mae" ? "MAE" : "MFE"}
      yLabel="result"
      unit={unit}
      points={points}
      testId="excursion-scatter"
      diagonal={axis === "mfe"}
      meta={
        <span className="rs-seg" role="group" aria-label="Excursion">
          {(["mae", "mfe"] as const).map((k) => (
            <button
              key={k}
              type="button"
              className="rs-seg__btn"
              aria-pressed={axis === k}
              onClick={() => setAxis(k)}
              data-testid={`excursion-${k}`}
            >
              {k.toUpperCase()}
            </button>
          ))}
          <MetricLabel label="?" definition={view.definitions[axis]} testId="def-excursion" />
        </span>
      }
      note={view.excursions_note}
      dataTable={() => (
        <table className="ax__table" data-testid="excursion-table">
          <thead>
            <tr>
              <th scope="col">Trade</th>
              <th scope="col">Exit</th>
              <th scope="col" className="num">
                Net
              </th>
              <th scope="col" className="num">
                R
              </th>
              <th scope="col" className="num">
                MAE
              </th>
              <th scope="col" className="num">
                MFE
              </th>
              <th scope="col" className="num">
                MAE R
              </th>
              <th scope="col" className="num">
                MFE R
              </th>
            </tr>
          </thead>
          <tbody>
            {t.trade_id.map((id, i) => (
              <tr key={id}>
                <td className="mono">
                  {t.instrument[i]} {t.direction[i]} {formatTimestamp(t.entry_time[i] ?? null)}
                </td>
                <td>{t.exit_reason[i]}</td>
                <Num v={t.net_pnl[i]} unit={t.pnl_unit} />
                <Num v={t.r_multiple[i]} unit="R" />
                <Num v={t.mae[i]} unit={t.pnl_unit} />
                <Num v={t.mfe[i]} unit={t.pnl_unit} />
                <Num v={t.mae_r[i]} unit="R" />
                <Num v={t.mfe_r[i]} unit="R" />
              </tr>
            ))}
          </tbody>
        </table>
      )}
    />
  );
}

function days(v: number | null): string {
  return v === null ? "n/a" : `${formatNumber(v, "", { decimals: v < 10 ? 1 : 0 })}d`;
}

function Num({ v, unit }: { v: number | null | undefined; unit: string }) {
  return <td className="num">{v === null || v === undefined ? "no data" : formatNumber(v, unit)}</td>;
}

function InstrumentTable({ view }: { view: TearsheetView }) {
  if (view.by_instrument.length === 0) return <p className="muted">No closed trades.</p>;
  return (
    <div className="table-wrap">
      <table className="rs-table" data-testid="instrument-table">
        <thead>
          <tr>
            <th scope="col">Instrument</th>
            <th scope="col" className="num">
              Trades
            </th>
            <th scope="col" className="num">
              Net P&amp;L
            </th>
            <th scope="col" className="num">
              Win rate
            </th>
            <th scope="col" className="num">
              Profit factor
            </th>
            <th scope="col" className="num">
              Expectancy
            </th>
            <th scope="col" className="num">
              Expectancy (R)
            </th>
            <th scope="col">Chart</th>
          </tr>
        </thead>
        <tbody>
          {view.by_instrument.map((row) => (
            <tr key={row.instrument}>
              <th scope="row" className="mono">
                {row.instrument}
              </th>
              <td>
                <FigureValue figure={row.trades} showChip={false} />
              </td>
              <td>
                <FigureValue figure={row.net_pnl} showChip={false} colourSign />
              </td>
              <td>
                <FigureValue figure={row.win_rate_pct} showChip={false} />
              </td>
              <td>
                <FigureValue figure={row.profit_factor} showChip={false} />
              </td>
              <td>
                <FigureValue figure={row.expectancy} showChip={false} colourSign />
              </td>
              <td>
                <FigureValue figure={row.expectancy_r} showChip={false} colourSign />
              </td>
              <td>
                <Link href={researchHref.chart(row.instrument, view.run.run_id)}>open</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProvenanceBlock({ view }: { view: TearsheetView }) {
  const p = view.provenance;
  const rows: [string, ReactNode][] = [
    ["Run", <span className="mono" key="r">{p.run_id}</span>],
    ["Strategy", <Link key="s" href={view.strategy_link}>{p.strategy_id}</Link>],
    ["Strategy hash", <span className="mono" key="h">{p.strategy_hash || "not recorded"}</span>],
    [
      "Parameters",
      <span key="p">
        <span className="mono">
          {Object.keys(p.parameters).length
            ? Object.entries(p.parameters)
                .map(([k, v]) => `${k}=${String(v)}`)
                .join(", ")
            : "none"}
        </span>
        <span className="muted"> ({p.parameters_source})</span>
      </span>,
    ],
    ["Datasets", <span className="mono" key="d">{p.dataset_version_ids.join(", ") || "not recorded"}</span>],
    ["Bars", <span className="mono" key="b">{Object.entries(p.data_fingerprint).map(([k, v]) => `${k}: ${fingerprint(v)}`).join("; ") || "not recorded"}</span>],
    ["Timeframe and instruments", `${p.timeframe} · ${p.instruments.join(", ")}`],
    ["Account", `${p.account_ccy || "?"} · initial ${p.initial_balance === null ? "not recorded" : formatNumber(p.initial_balance, p.account_ccy || "")}`],
    ["Cost profile", <span className="mono" key="c">{p.cost_profile || "not recorded"}</span>],
    ["Spread", <span className="mono" key="sp">{p.spread_model || "not recorded"}</span>],
    ["Slippage", <span className="mono" key="sl">{p.slippage_model || "not recorded"}</span>],
    ["Commission", <span className="mono" key="cm">{p.commission_model || "not recorded"}</span>],
    ["Financing", <span className="mono" key="f">{p.financing_model || "not recorded"}</span>],
    ["Sizing", <span className="mono" key="z">{Object.entries(p.sizing).map(([k, v]) => `${k}=${String(v)}`).join(", ") || "not recorded"}</span>],
    ["Exit policy", <span className="mono" key="e">{Object.keys(p.exit_policy).length ? summarise(p.exit_policy) : "not recorded (predates the stamp)"}</span>],
    ["Intrabar policy", p.intrabar_policy || "not recorded"],
    ["Calendar", <span key="cal">{p.calendar_version} <span className="muted">({p.calendar_note})</span></span>],
    ["Gate set", <span key="g" className="mono">{p.gate_set_version}{p.gate_set_fingerprint ? ` · ${p.gate_set_fingerprint.slice(0, 12)}` : ""}</span>],
    ["Engine", <span key="en"><span className="mono">{p.engine_version}</span> {p.engine_current ? "(current)" : <b className="neg">(not the current engine)</b>}</span>],
    ["Ledger hash", <span className="mono" key="l">{p.ledger_sha256 || "not recorded"}</span>],
    ["Job", <span className="mono" key="j">{p.job_id || "not recorded"}</span>],
    ["Filed", formatTimestamp(p.run_created_at)],
    ["Tearsheet analytics", <span className="mono" key="t">research/tearsheet.py {p.tearsheet_version}</span>],
  ];
  return (
    <dl className="rs-prov" data-testid="provenance-block">
      {rows.map(([k, v]) => (
        <div key={k} className="rs-prov__row">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function fingerprint(v: unknown): string {
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    const parts = ["bars", "first", "last", "sha256"].filter((k) => k in o).map((k) => {
      const s = String(o[k]);
      return k === "sha256" ? `sha ${s.slice(0, 12)}` : `${k} ${s}`;
    });
    return parts.join(", ");
  }
  return String(v);
}

function summarise(o: Record<string, unknown>): string {
  return Object.entries(o)
    .filter(([k]) => k !== "key_version")
    .map(([k, v]) => `${k}=${typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}`)
    .join(", ");
}
