"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList, SourceBadge, ToneBadge } from "@/components/primitives";
import { GridHeatmap, HeatLegend, divergingBin, type HeatCell } from "@/components/charts/analytics/GridHeatmap";
import { ApiError, apiFetch } from "@/lib/api";
import { formatNumber, formatTimestamp } from "@/lib/format";
import { invalidatePath, useApi } from "@/lib/query";
import {
  robustnessPath,
  type BreakdownsView,
  type ConfidenceBandsView,
  type ConsistencyView,
  type FillDetailView,
  type RerunKind,
  type RerunResultView,
  type RerunStatusView,
  type RobustnessIndexView,
  type SplitView,
} from "@/lib/robustness";
import type { Envelope } from "@/lib/types";
import { BoundaryStamp, Dist, Fig, MetricsStrip, Notes, RangeGlyph, findingTone, rerunTone } from "./parts";

/**
 * RESEARCH · robustness and integrity diagnostics of one stored run (wave 3R),
 * a tabbed panel on the tearsheet. Loaded lazily with the tearsheet.
 *
 * Ledger tabs read GET /api/research/runs/{id}/robustness/{bands|breakdowns|
 * consistency|fills}; re-run tabs read the cached result of a research job
 * (GET …/robustness/{kind}) and queue one (POST, admin, audited). Every number
 * is the backend's; the panel lays them out. ADVISORY: nothing here is a gate.
 */
type TabKey = "bands" | "breakdowns" | "consistency" | "fills" | RerunKind;

const TABS: [TabKey, string][] = [
  ["bands", "Bands"],
  ["breakdowns", "Breakdowns"],
  ["consistency", "Consistency"],
  ["fills", "Fills"],
  ["warmup", "Warm-up"],
  ["lookahead", "Look-ahead"],
  ["execution", "Execution"],
  ["perturbation", "Perturbation"],
  ["random", "Vs random"],
  ["noise", "Noise"],
];

export default function RobustnessPanel({ runId }: { runId: string }) {
  const [tab, setTab] = useState<TabKey>("bands");
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const index = useApi<Envelope<RobustnessIndexView>>(robustnessPath.index(runId), { refreshMs: 15_000 });
  const statusOf = (k: string): RerunStatusView | undefined =>
    index.status === "success" ? index.data.data.kinds.find((x) => x.kind === k) : undefined;

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const i = TABS.findIndex(([k]) => k === tab);
    let next = i;
    if (e.key === "ArrowRight") next = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    else return;
    e.preventDefault();
    setTab(TABS[next]![0]);
    refs.current[next]?.focus();
  }

  return (
    <div className="rb" data-testid="robustness">
      <p className="rb-advisory" data-testid="robustness-advisory">
        Advisory diagnostics, reported beside the verdict. None of them is read by a gate, a threshold or a
        pre-registration.
      </p>
      <div className="rb-tabs" role="tablist" aria-label="Robustness diagnostics" onKeyDown={onKey}>
        {TABS.map(([k, label], i) => {
          const st = statusOf(k);
          return (
            <button
              key={k}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`${base}-tab-${k}`}
              aria-selected={tab === k}
              aria-controls={`${base}-panel`}
              tabIndex={tab === k ? 0 : -1}
              className="rb-tab"
              data-testid={`rb-tab-${k}`}
              onClick={() => setTab(k)}
            >
              {label}
              {st ? <span className={`rb-dot rb-dot--${rerunTone(st.status).tone}`} aria-label={rerunTone(st.status).label} /> : null}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={`${base}-panel`} aria-labelledby={`${base}-tab-${tab}`} className="rb-body" data-testid={`rb-panel-${tab}`}>
        {tab === "bands" ? <Bands runId={runId} /> : null}
        {tab === "breakdowns" ? <Breakdowns runId={runId} /> : null}
        {tab === "consistency" ? <Consistency runId={runId} /> : null}
        {tab === "fills" ? <Fills runId={runId} /> : null}
        {tab !== "bands" && tab !== "breakdowns" && tab !== "consistency" && tab !== "fills" ? (
          <Rerun runId={runId} kind={tab} index={index.status === "success" ? index.data.data : null} />
        ) : null}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 3. bands

function Bands({ runId }: { runId: string }) {
  const state = useApi<Envelope<ConfidenceBandsView>>(robustnessPath.ledger(runId, "bands"));
  return (
    <AsyncBoundary state={state} label="the confidence bands" onRetry={state.reload}>
      {(env) => {
        const v = env.data;
        return (
          <>
            <SourceBadge source={env.source} />
            <BoundaryStamp b={v.boundary} />
            <div className="table-wrap">
              <table className="rs-table rb-table" data-testid="bands-table">
                <thead>
                  <tr>
                    <th scope="col">Metric</th>
                    <th scope="col" className="num">Point</th>
                    <th scope="col" className="num">5%</th>
                    <th scope="col" className="num">50%</th>
                    <th scope="col" className="num">95%</th>
                    <th scope="col">Band</th>
                    <th scope="col">Resampled</th>
                    <th scope="col" className="num">n</th>
                    <th scope="col" className="num">Block</th>
                  </tr>
                </thead>
                <tbody>
                  {v.bands.map((b) => {
                    const vals = [b.p5.value, b.p95.value, b.point.value].filter((x): x is number => x !== null);
                    return (
                      <tr key={b.key} data-testid={`band-${b.key}`}>
                        <th scope="row">{b.label}</th>
                        <td><Fig f={b.point} /></td>
                        <td><Fig f={b.p5} /></td>
                        <td><Fig f={b.p50} /></td>
                        <td><Fig f={b.p95} /></td>
                        <td>
                          {vals.length ? (
                            <RangeGlyph lo={b.p5.value} mid={b.p50.value} hi={b.p95.value} point={b.point.value}
                              min={Math.min(...vals, 0)} max={Math.max(...vals, 0)}
                              label={`${b.label}: 5 to 95 percent band, median tick, point dot`} />
                          ) : <span className="muted">n/a</span>}
                        </td>
                        <td className="muted">{b.basis}</td>
                        <td><Fig f={b.observations} /></td>
                        <td><Fig f={b.block_length} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="rb-method">{v.method}</p>
            <CaveatList caveats={env.caveats} />
          </>
        );
      }}
    </AsyncBoundary>
  );
}

// ------------------------------------------------------------------ 5. breakdowns

const GROUPS: [string, string][] = [
  ["exit_reason", "Exit reason"],
  ["direction", "Direction"],
  ["year", "Year"],
  ["month", "Month"],
  ["weekday", "Weekday"],
  ["hour", "Hour"],
  ["session", "Session"],
];

function Seg<T extends string | number>({ value, options, onChange, label, testId }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; label: string; testId: string;
}) {
  return (
    <span className="rs-seg" role="group" aria-label={label} data-testid={testId}>
      {options.map(([k, name]) => (
        <button key={String(k)} type="button" className="rs-seg__btn" aria-pressed={value === k}
          onClick={() => onChange(k)} data-testid={`${testId}-${k}`}>
          {name}
        </button>
      ))}
    </span>
  );
}

function Breakdowns({ runId }: { runId: string }) {
  const [group, setGroup] = useState("exit_reason");
  const state = useApi<Envelope<BreakdownsView>>(robustnessPath.ledger(runId, "breakdowns"));
  return (
    <AsyncBoundary state={state} label="the breakdowns" onRetry={state.reload}>
      {(env) => {
        const v = env.data;
        const rows = v.groups[group] ?? [];
        const span = Math.max(1, ...rows.flatMap((r) => (r.net_pnl.value === null ? [] : [Math.abs(r.net_pnl.value)])));
        return (
          <>
            <div className="rs-controls">
              <Seg value={group} options={GROUPS} onChange={setGroup} label="Group trades by" testId="bd-group" />
            </div>
            <SourceBadge source={env.source} />
            <BoundaryStamp b={v.boundary} />
            {rows.length === 0 ? (
              <p className="muted" data-testid="bd-empty">No trade in the research slice.</p>
            ) : (
              <div className="table-wrap">
                <table className="rs-table rb-table" data-testid="bd-table">
                  <thead>
                    <tr>
                      <th scope="col">{GROUPS.find(([k]) => k === group)?.[1]}</th>
                      <th scope="col" className="num">Trades</th>
                      <th scope="col" className="num">Net ({v.account_ccy})</th>
                      <th scope="col" aria-label="Net P&L bar" />
                      <th scope="col" className="num">Win</th>
                      <th scope="col" className="num">Expectancy</th>
                      <th scope="col" className="num">PF</th>
                      <th scope="col" className="num">Avg bars</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.key}>
                        <th scope="row" className="mono">{r.key}</th>
                        <td><Fig f={r.trades} /></td>
                        <td><Fig f={r.net_pnl} sign /></td>
                        <td className="rb-barcell"><PnlBar v={r.net_pnl.value} span={span} /></td>
                        <td><Fig f={r.win_rate_pct} /></td>
                        <td><Fig f={r.expectancy} sign /></td>
                        <td><Fig f={r.profit_factor} /></td>
                        <td><Fig f={r.avg_bars_held} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Notes items={[v.entry_reason_note, v.calendar_note]} />
            <CaveatList caveats={env.caveats} />
          </>
        );
      }}
    </AsyncBoundary>
  );
}

function PnlBar({ v, span }: { v: number | null; span: number }) {
  if (v === null) return null;
  const w = Math.min(50, (Math.abs(v) / span) * 50);
  return (
    <svg className="rb-pnlbar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
      <line x1="50" x2="50" y1="0" y2="8" className="rb-range__zero" />
      <rect x={v >= 0 ? 50 : 50 - w} width={w} y="1" height="6" className={v >= 0 ? "rb-pnlbar--pos" : "rb-pnlbar--neg"} />
    </svg>
  );
}

// ------------------------------------------------------------------ 10. consistency

function SplitTable({ rows, head, testId, ccy }: { rows: SplitView[]; head: string; testId: string; ccy: string }) {
  return (
    <div className="table-wrap">
      <table className="rs-table rb-table" data-testid={testId}>
        <thead>
          <tr>
            <th scope="col">{head}</th>
            <th scope="col">Span</th>
            <th scope="col" className="num">Trades</th>
            <th scope="col" className="num">Net ({ccy})</th>
            <th scope="col" className="num">Return</th>
            <th scope="col" className="num">Sharpe</th>
            <th scope="col" className="num">Win</th>
            <th scope="col" className="num">PF</th>
            <th scope="col">Sign</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} data-positive={r.positive === null ? "none" : String(r.positive)}>
              <th scope="row" className="mono">{r.key}</th>
              <td className="mono muted">{r.start ? `${formatTimestamp(r.start).slice(0, 10)} → ${formatTimestamp(r.end).slice(0, 10)}` : "whole slice"}</td>
              <td><Fig f={r.trades} /></td>
              <td><Fig f={r.net_profit} sign /></td>
              <td><Fig f={r.total_return_pct} sign /></td>
              <td><Fig f={r.sharpe} /></td>
              <td><Fig f={r.win_rate_pct} /></td>
              <td><Fig f={r.profit_factor} /></td>
              <td>{r.positive === null ? <span className="muted">no trade</span> : r.positive ? <span className="pos">+ positive</span> : <span className="neg">− negative</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Consistency({ runId }: { runId: string }) {
  const [k, setK] = useState(4);
  const state = useApi<Envelope<ConsistencyView>>(robustnessPath.ledger(runId, "consistency", `k=${k}`));
  return (
    <>
      <div className="rs-controls">
        <Seg value={k} options={[[2, "2"], [3, "3"], [4, "4"], [6, "6"], [8, "8"], [12, "12"]]} onChange={setK}
          label="Sub-periods" testId="cons-k" />
        <span className="muted">equal spans of the research window</span>
      </div>
      <AsyncBoundary state={state} label="the consistency splits" onRetry={state.reload}>
        {(env) => {
          const v = env.data;
          return (
            <>
              <SourceBadge source={env.source} />
              <BoundaryStamp b={v.boundary} />
              <dl className="rs-kv" data-testid="cons-shares">
                <div><dt>Positive sub-periods</dt><dd><Fig f={v.period_positive_share} /> <span className="muted">(empty: <Fig f={v.period_empty} />)</span></dd></div>
                <div><dt>Positive instruments</dt><dd><Fig f={v.instrument_positive_share} /></dd></div>
              </dl>
              <SplitTable rows={v.periods} head="Period" testId="cons-periods" ccy={v.periods[0]?.net_profit.unit ?? ""} />
              <h3 className="rb-h3">{v.pooled ? "By instrument (pooled run)" : "By instrument"}</h3>
              <SplitTable rows={v.instruments} head="Instrument" testId="cons-instruments" ccy={v.instruments[0]?.net_profit.unit ?? ""} />
              <p className="rb-method">{v.method}</p>
              <CaveatList caveats={env.caveats} />
            </>
          );
        }}
      </AsyncBoundary>
    </>
  );
}

// ------------------------------------------------------------------ 12. fills

const FILL_PAGE = 100;

function Fills({ runId }: { runId: string }) {
  const [rule, setRule] = useState<string>("all");
  const [shown, setShown] = useState(FILL_PAGE);
  const state = useApi<Envelope<FillDetailView>>(robustnessPath.ledger(runId, "fills"));
  return (
    <AsyncBoundary state={state} label="the fill detail" onRetry={state.reload}>
      {(env) => {
        const v = env.data;
        const t = v.table;
        const idx = t.trade_id.map((_, i) => i).filter((i) => rule === "all" || (t.rules[i] ?? []).includes(rule));
        const money = (x: number | null | undefined) => (x === null || x === undefined ? "no data" : formatNumber(x, t.unit));
        return (
          <>
            <SourceBadge source={env.source} />
            <BoundaryStamp b={v.boundary} />
            <div className="rs-controls">
              <Seg value={rule} label="Filter by fill rule" testId="fill-rule"
                options={[["all", "All"], ...Object.keys(v.rule_counts).map((k) => [k, k.replace("_", " ")] as [string, string])]}
                onChange={(r) => { setRule(r); setShown(FILL_PAGE); }} />
              <span className="muted">intrabar policy <b className="mono">{v.intrabar_policy || "not recorded"}</b></span>
            </div>
            <dl className="rs-kv" data-testid="fill-counts">
              {Object.entries(v.rule_counts).map(([k, f]) => (
                <div key={k} title={v.rule_definitions[k]}>
                  <dt>{k.replace("_", " ")}</dt>
                  <dd><Fig f={f} /> <span className="muted">{v.rule_definitions[k]}</span></dd>
                </div>
              ))}
              <div><dt>Trades with flags</dt><dd><Fig f={v.trades_recorded} /> of <Fig f={v.trades_total} /></dd></div>
            </dl>
            <div className="table-wrap">
              <table className="rs-table rb-table" data-testid="fill-table">
                <thead>
                  <tr>
                    <th scope="col">Trade</th>
                    <th scope="col">Exit</th>
                    <th scope="col">Fill rule</th>
                    <th scope="col" className="num">Gross</th>
                    <th scope="col" className="num">Spread</th>
                    <th scope="col" className="num">Slippage</th>
                    <th scope="col" className="num">Commission</th>
                    <th scope="col" className="num">Financing</th>
                    <th scope="col" className="num">Net</th>
                  </tr>
                </thead>
                <tbody>
                  {idx.slice(0, shown).map((i) => (
                    <tr key={t.trade_id[i]}>
                      <td className="mono">{t.instrument[i]} {t.direction[i]} {formatTimestamp(t.entry_time[i] ?? null)}</td>
                      <td>{t.exit_reason[i]}</td>
                      <td>{t.recorded[i] ? (t.rules[i] ?? []).map((r) => <span key={r} className={`rb-tag rb-tag--${r === "plain" ? "ok" : "warn"}`}>{r.replace("_", " ")}</span>) : <span className="muted">not recorded</span>}</td>
                      <td className="num">{money(t.gross_pnl[i])}</td>
                      <td className="num">{money(t.spread_cost[i])}</td>
                      <td className="num">{money(t.slippage_cost[i])}</td>
                      <td className="num">{money(t.commission[i])}</td>
                      <td className="num">{money(t.financing_cost[i])}</td>
                      <td className={`num ${netClass(t.net_pnl[i])}`}>{money(t.net_pnl[i])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {idx.length > shown ? (
              <button type="button" className="rs-btn rs-btn--ghost" onClick={() => setShown((s) => s + FILL_PAGE * 5)} data-testid="fill-more">
                Show more ({formatNumber(idx.length - shown, "count")} hidden)
              </button>
            ) : null}
            <p className="rb-method">{v.note}</p>
            <CaveatList caveats={env.caveats} />
          </>
        );
      }}
    </AsyncBoundary>
  );
}

function netClass(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  return v > 0 ? "pos" : v < 0 ? "neg" : "";
}

// ------------------------------------------------------------------ re-run tabs

function QueueControl({ runId, kind, status }: { runId: string; kind: RerunKind; status: RerunStatusView }) {
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const active = status.status === "pending" || status.status === "running";
  async function queue() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(robustnessPath.rerun(runId, kind), { method: "POST", body: JSON.stringify({ reason }) });
      void invalidatePath(client, robustnessPath.rerun(runId, kind));
      void invalidatePath(client, robustnessPath.index(runId));
      setReason("");
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (${err.code})` : "The request failed.");
    }
    setBusy(false);
  }
  return (
    <form className="rs-controls rb-queue" onSubmit={(e) => { e.preventDefault(); void queue(); }} data-testid="rb-queue">
      <label htmlFor={id}>Reason</label>
      <input id={id} className="rs-input" value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="why run it (audited)" minLength={4} maxLength={500} data-testid="rb-reason" disabled={active || busy} />
      <button type="submit" className="rs-btn" disabled={reason.trim().length < 4 || busy || active} data-testid="rb-queue-btn">
        {status.status === "done" ? "Re-queue" : "Queue job"}
      </button>
      <span className="muted">admin only; the research worker runs it</span>
      {error ? <span className="neg" role="alert" data-testid="rb-queue-error">{error}</span> : null}
    </form>
  );
}

function StatusLine({ s }: { s: RerunStatusView }) {
  const tone = rerunTone(s.status);
  return (
    <div className="rb-status" data-testid="rb-status" data-status={s.status}>
      <ToneBadge tone={tone} testId="rb-status-badge" value={s.status} />
      {s.status === "running" || s.status === "pending" ? (
        <span className="rb-progress" data-testid="rb-progress">
          <svg viewBox="0 0 100 6" preserveAspectRatio="none" className="rs-progress" aria-hidden="true">
            <rect x="0" y="0" width="100" height="6" className="rs-progress__track" />
            {s.progress.value === null ? null : (
              <rect x="0" y="0" width={Math.round(s.progress.value * 100)} height="6" className="rb-progress__fill" />
            )}
          </svg>
          <Fig f={s.progress} /> <span className="muted">{s.stage}</span>
        </span>
      ) : null}
      {s.job_id ? <span className="mono muted">job {s.job_id}</span> : null}
      {s.computed_at ? <span className="muted">computed {formatTimestamp(s.computed_at)}</span> : null}
      {s.updated_at && s.status !== "done" ? <span className="muted">updated {formatTimestamp(s.updated_at)}</span> : null}
      {s.error ? <span className="neg" data-testid="rb-error">{s.error}</span> : null}
    </div>
  );
}

function Rerun({ runId, kind, index }: { runId: string; kind: RerunKind; index: RobustnessIndexView | null }) {
  const [poll, setPoll] = useState(false);
  const state = useApi<Envelope<RerunResultView>>(robustnessPath.rerun(runId, kind), poll ? { refreshMs: 3_000 } : {});
  const st = state.status === "success" ? state.data.data.status.status : "";
  const shouldPoll = st === "pending" || st === "running";
  if (shouldPoll !== poll) setPoll(shouldPoll);
  return (
    <AsyncBoundary state={state} label="the diagnostic" onRetry={state.reload}>
      {(env) => {
        const v = env.data;
        return (
          <>
            <h3 className="rb-h3">{v.status.title}</h3>
            <StatusLine s={v.status} />
            {index && !index.rerunnable ? (
              <p className="rb-refusal" data-testid="rb-not-rerunnable">{index.reason}</p>
            ) : (
              <QueueControl runId={runId} kind={kind} status={v.status} />
            )}
            <SourceBadge source={env.source} />
            {v.provenance ? <RerunProvenance v={v} /> : (
              <p className="muted" data-testid="rb-not-computed">
                Not computed yet. {index?.can_queue_note ?? ""}
              </p>
            )}
            {v.warmup ? <WarmupResult v={v} /> : null}
            {v.lookahead ? <LookaheadResult v={v} /> : null}
            {v.execution ? <ExecutionResult v={v} /> : null}
            {v.perturbation ? <PerturbationResult v={v} /> : null}
            {v.random ? <RandomResult v={v} /> : null}
            {v.noise ? <NoiseResult v={v} /> : null}
            <p className="rb-method">{v.method}</p>
            <CaveatList caveats={env.caveats} />
          </>
        );
      }}
    </AsyncBoundary>
  );
}

function RerunProvenance({ v }: { v: RerunResultView }) {
  const p = v.provenance!;
  const engine = p.engine as { engine_version?: string; profile?: { name?: string }; construction?: string };
  const repro = findingTone(p.reproduces_stored, ["REPRODUCES STORED", "DIFFERS FROM STORED"], true);
  return (
    <dl className="rb-prov" data-testid="rb-provenance">
      <div><dt>Window</dt><dd className="mono">{formatTimestamp(p.window_start)} → {formatTimestamp(p.window_end)} (<Fig f={p.window_bars} />)</dd></div>
      <div><dt>Holdout fence</dt><dd className="mono">{String(p.boundary.start ?? "")} · {String(p.boundary.basis ?? "")}</dd></div>
      <div><dt>Engine</dt><dd className="mono">{engine.engine_version} · {engine.profile?.name} · construction {engine.construction}</dd></div>
      <div><dt>Dataset</dt><dd className="mono">{p.dataset_version_id}</dd></div>
      <div><dt>Engine runs</dt><dd><Fig f={p.engine_runs} /></dd></div>
      <div><dt>Baseline vs stored</dt><dd><ToneBadge tone={repro} testId="rb-repro" value={String(p.reproduces_stored)} />{" "}
        <span className="muted">{p.covers_stored_run ? "" : "the window stops at the holdout fence, so the stored ledger is not comparable"}</span></dd></div>
    </dl>
  );
}

function WarmupResult({ v }: { v: RerunResultView }) {
  const w = v.warmup!;
  return (
    <div data-testid="rb-warmup">
      <div className="rb-verdict">
        <ToneBadge tone={findingTone(w.stable, ["STABLE", "UNSTABLE"], true)} testId="rb-warmup-stable" value={String(w.stable)} />
        <span>declared warm-up <Fig f={w.warmup_period} /> · history available before the window <Fig f={w.prologue_available} /></span>
      </div>
      <MetricsStrip m={w.baseline.metrics} label="Baseline (cold start, as stored)" />
      {w.runs.map((r, i) => (
        <section key={i} className="rb-sub" aria-label={`plus ${r.extra_bars.value} bars`}>
          <h4 className="rb-h4">
            {r.mode === "late_start" ? "Start " : "+"}<Fig f={r.extra_bars} /> bars {r.mode === "late_start" ? "later (less history)" : "of earlier history"}{" "}
            <ToneBadge tone={findingTone(r.stable, ["SIGNALS STABLE", "SIGNALS OR TRADES CHANGE"], true)} testId="rb-warmup-run" value={String(r.stable)} />
            <ToneBadge tone={findingTone(r.indicators_converged, ["INDICATORS CONVERGED", "INDICATORS STILL DRIFT"], true)} testId="rb-warmup-indicators" value={String(r.indicators_converged)} />
          </h4>
          <dl className="rs-kv">
            <div><dt>Signals compared</dt><dd><Fig f={r.signals_compared} /> {r.compared_from ? <span className="mono muted">from {formatTimestamp(r.compared_from)}</span> : null}</dd></div>
            <div><dt>Changed after warm-up</dt><dd><Fig f={r.signals_changed_after_warmup} /> {r.first_changed_signal ? <span className="mono muted">first {formatTimestamp(r.first_changed_signal)}</span> : null}</dd></div>
            <div><dt>Changed in cold region</dt><dd><Fig f={r.signals_changed_in_cold_region} /> <span className="muted">(expected)</span></dd></div>
            <div><dt>Trades short / long run</dt><dd><Fig f={r.trades_short} /> / <Fig f={r.trades_long} /></dd></div>
            <div><dt>Matched · changed</dt><dd><Fig f={r.trades_matched} /> · <Fig f={r.trades_changed} /></dd></div>
            <div><dt>Only short · only long</dt><dd><Fig f={r.trades_only_short} /> · <Fig f={r.trades_only_long} /></dd></div>
          </dl>
          <MetricsStrip m={r.metrics} label={r.mode === "late_start" ? "Late-start run" : "Extended run"} />
          <div className="table-wrap">
            <table className="rs-table rb-table" data-testid="rb-drift">
              <thead><tr><th scope="col">Indicator column</th><th scope="col" className="num">Max |Δ|</th><th scope="col" className="num">Max rel Δ</th><th scope="col" className="num">Bars differing</th><th scope="col">Last differing</th></tr></thead>
              <tbody>
                {r.indicator_columns.map((c) => (
                  <tr key={c.column}>
                    <th scope="row" className="mono">{c.column}</th>
                    <td><Fig f={c.max_abs_diff} /></td><td><Fig f={c.max_rel_diff} /></td><td><Fig f={c.bars_differing} /></td>
                    <td className="mono muted">{c.last_differing_bar ? formatTimestamp(c.last_differing_bar) : "none"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

function LookaheadResult({ v }: { v: RerunResultView }) {
  const l = v.lookahead!;
  return (
    <div data-testid="rb-lookahead">
      <div className="rb-verdict">
        <ToneBadge tone={findingTone(l.biased, ["LOOK-AHEAD FOUND", "NO LOOK-AHEAD FOUND"], false)} testId="rb-lookahead-verdict" value={String(l.biased)} />
        <span><Fig f={l.samples} /> sampled bars (seed <span className="mono">{l.seed}</span>), <Fig f={l.signals_at_samples} /> with a signal, <Fig f={l.signal_flags} /> signal change(s)</span>
      </div>
      <p className="muted">Columns checked: <span className="mono">{l.columns_checked.join(", ") || "none"}</span></p>
      {l.flags.length ? (
        <div className="table-wrap">
          <table className="rs-table rb-table" data-testid="rb-lookahead-flags">
            <thead><tr><th scope="col">Bar</th><th scope="col">Full data</th><th scope="col">Truncated</th><th scope="col">Columns changed</th></tr></thead>
            <tbody>
              {l.flags.map((f) => (
                <tr key={f.bar}>
                  <td className="mono">{formatTimestamp(f.bar)}</td>
                  <td>{f.signal_full ?? "no signal"}</td>
                  <td className={f.signal_changed ? "neg" : ""}>{f.signal_truncated ?? "no signal"}</td>
                  <td className="mono">{f.columns_changed.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function ExecutionResult({ v }: { v: RerunResultView }) {
  const x = v.execution!;
  const mults = x.multipliers;
  const byKey = new Map(x.grid.map((c) => [`${c.spread_x}|${c.slippage_x}`, c]));
  const cells: (HeatCell | null)[][] = mults.map((s) => mults.map((l) => {
    const c = byKey.get(`${s}|${l}`);
    if (!c) return null;
    const r = c.retention.value;
    return {
      text: r === null ? "n/a" : formatNumber(r * 100, "pct", { bare: true }),
      bin: r === null ? null : divergingBin(r - 1, 1),
      label: `spread ${s}, slippage ${l}: net profit ${c.metrics.net_profit.value === null ? "undefined" : formatNumber(c.metrics.net_profit.value, c.metrics.net_profit.unit)}, retention ${r === null ? "undefined" : formatNumber(r * 100, "pct")}`,
      testId: "rb-grid-cell",
    };
  }));
  return (
    <div data-testid="rb-execution">
      <MetricsStrip m={x.baseline.metrics} label={`Baseline · ${x.baseline.profile}`} />
      <div className="table-wrap">
        <table className="rs-table rb-table" data-testid="rb-stress-rows">
          <thead><tr><th scope="col">Stress</th><th scope="col">Level</th><th scope="col" className="num">Runs</th><th scope="col" className="num">Net p50 [p5 – p95]</th><th scope="col" className="num">Sharpe p50</th><th scope="col" className="num">Max DD p50</th><th scope="col" className="num">Trades p50</th><th scope="col" className="num">Retention</th><th scope="col" className="num">Profitable</th></tr></thead>
          <tbody>
            {x.rows.map((r) => (
              <tr key={`${r.stress}-${r.level}`}>
                <th scope="row">{r.stress === "trade_skip" ? "Random trade skip" : "Entry delay"}</th>
                <td className="mono">{r.level}</td>
                <td><Fig f={r.runs} /></td>
                <td><Dist d={r.net_profit} sign /></td>
                <td><Fig f={r.sharpe.p50} /></td>
                <td><Fig f={r.max_drawdown_pct.p50} /></td>
                <td><Fig f={r.trades.p50} /></td>
                <td><Fig f={r.retention} /></td>
                <td><Fig f={r.profitable_share} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <GridHeatmap
        title="Spread × slippage: net-profit retention"
        unit="% of the baseline's net profit"
        rowHeader="Spread"
        rowLabels={mults}
        colLabels={mults.map((m) => `slip ${m}`)}
        cells={cells}
        testId="rb-grid"
        legend={<HeatLegend low="0%" high="200%" />}
        note="Rows scale every spread parameter, columns every slippage parameter, of the run's own profile; each cell is an engine run."
      />
      <Notes items={x.notes} />
    </div>
  );
}

function PerturbationResult({ v }: { v: RerunResultView }) {
  const p = v.perturbation!;
  return (
    <div data-testid="rb-perturbation">
      <MetricsStrip m={p.baseline.metrics} label="Baseline (stored binding)" />
      <dl className="rs-kv">
        <div><dt>Jitter</dt><dd>± <Fig f={p.jitter_pct} /> on <span className="mono">{p.parameters.join(", ")}</span> · seed <span className="mono">{p.seed}</span></dd></div>
        <div><dt>Net profit p50 [p5 – p95]</dt><dd><Dist d={p.net_profit} sign /></dd></div>
        <div><dt>Sharpe p50 [p5 – p95]</dt><dd><Dist d={p.sharpe} /></dd></div>
        <div><dt>Profitable perturbations</dt><dd><Fig f={p.profitable_share} /></dd></div>
        <div><dt>Baseline percentile</dt><dd><Fig f={p.baseline_percentile} /></dd></div>
        <div><dt>Invalid draws</dt><dd><Fig f={p.invalid} /></dd></div>
      </dl>
      <details className="rs-details">
        <summary>{p.draws.length} draws</summary>
        <div className="table-wrap">
          <table className="rs-table rb-table" data-testid="rb-draws">
            <thead><tr><th scope="col">Parameters</th><th scope="col" className="num">Net</th><th scope="col" className="num">Sharpe</th><th scope="col" className="num">Trades</th></tr></thead>
            <tbody>
              {p.draws.map((d, i) => (
                <tr key={i}>
                  <td className="mono">{Object.entries(d.params).map(([k, val]) => `${k}=${String(val)}`).join(", ")}{d.valid ? "" : ` (invalid: ${d.error})`}</td>
                  <td><Fig f={d.net_profit} sign /></td><td><Fig f={d.sharpe} /></td><td><Fig f={d.trades} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function RandomResult({ v }: { v: RerunResultView }) {
  const r = v.random!;
  return (
    <div data-testid="rb-random">
      <div className="rb-verdict">
        <span className="rb-big"><Fig f={r.percentile} /></span>
        <span>percentile of the strategy among <Fig f={r.runs} /> random-entry systems (seed <span className="mono">{r.seed}</span>)</span>
      </div>
      <dl className="rs-kv">
        <div><dt>Strategy total (fraction)</dt><dd><Fig f={r.strategy_total_return} sign /></dd></div>
        <div><dt>Random total p50 [p5 – p95]</dt><dd><Dist d={r.random_total_return} sign /></dd></div>
        <div><dt>Per trade</dt><dd><Fig f={r.strategy_mean_return} sign /></dd></div>
        <div><dt>Trades · long share</dt><dd><Fig f={r.trades} /> · <Fig f={r.long_share} /></dd></div>
        <div><dt>Holding bars p50 [p5 – p95]</dt><dd><Dist d={r.holding_bars} /></dd></div>
      </dl>
      <MetricsStrip m={r.baseline.metrics} label="Baseline re-run" />
    </div>
  );
}

function NoiseResult({ v }: { v: RerunResultView }) {
  const n = v.noise!;
  return (
    <div data-testid="rb-noise">
      <MetricsStrip m={n.baseline.metrics} label="Baseline (clean bars)" />
      <div className="table-wrap">
        <table className="rs-table rb-table" data-testid="rb-noise-rows">
          <thead><tr><th scope="col">Noise</th><th scope="col" className="num">Runs</th><th scope="col" className="num">Net p50 [p5 – p95]</th><th scope="col" className="num">Sharpe p50</th><th scope="col" className="num">Trades p50</th><th scope="col" className="num">Degradation</th><th scope="col" className="num">Profitable</th></tr></thead>
          <tbody>
            {n.rows.map((r) => (
              <tr key={r.atr_fraction}>
                <th scope="row" className="mono">± {r.atr_fraction}</th>
                <td><Fig f={r.runs} /></td><td><Dist d={r.net_profit} sign /></td><td><Fig f={r.sharpe.p50} /></td>
                <td><Fig f={r.trades.p50} /></td><td><Fig f={r.degradation} /></td><td><Fig f={r.profitable_share} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">ATR period <Fig f={n.atr_period} /> · seed <span className="mono">{n.seed}</span></p>
    </div>
  );
}

