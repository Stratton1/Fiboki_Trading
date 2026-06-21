"use client";

import { Fragment, useMemo, useState } from "react";
import useSWR from "swr";

import { PageHeader } from "@/components/PageHeader";
import { InfoTip } from "@/components/InfoTip";
import { api } from "@/lib/api";
import { strategyShortName } from "@/lib/strategy-names";

const TIERS = ["traditional_gen1", "hybrid_gen1", "triple_hybrid_gen1",
  "canonical", "experimental"];
const STATES = ["paper_candidate", "research_watchlist"];

function num(v: number | null | undefined, d = 2) {
  return v === null || v === undefined ? "—" : v.toFixed(d);
}

export default function CandidatesPage() {
  const [view, setView] = useState<"combos" | "by_strategy">("combos");
  const [state, setState] = useState("");
  const [tier, setTier] = useState("");
  const [demoOnly, setDemoOnly] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    if (state) p.set("state", state);
    if (tier) p.set("tier", tier);
    return p.toString();
  }, [state, tier]);

  const { data: candidates, isLoading } = useSWR(
    `candidates?${qs}`, () => api.researchCandidates(qs), { refreshInterval: 30000 });
  const { data: byStrategy } = useSWR(
    "candidates-by-strategy", () => api.researchCandidatesByStrategy(),
    { refreshInterval: 30000 });
  const { data: funnel } = useSWR("research-funnel", () => api.researchFunnel(),
    { refreshInterval: 30000 });

  const rungOrder = ["min_trades", "below_screen", "walk_forward", "oos",
    "monte_carlo", "param_sensitivity", "cost_stress"];
  const rejections = funnel?.rejections ?? {};
  const rejectionRows = Object.entries(rejections).sort(
    (a, b) => (rungOrder.indexOf(a[0]) - rungOrder.indexOf(b[0])) || b[1] - a[1]);

  const shownCandidates = (candidates ?? []).filter((c) => !demoOnly || c.demo_ready);

  return (
    <div>
      <PageHeader
        title="Candidates"
        subtitle="Strategies that survived the full robustness ladder — review-only, for demo-promotion decisions."
      />

      {/* Funnel */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-medium text-foreground">Validation funnel</h3>
          <InfoTip text="How the sweep's combos progressed through the ladder (walk-forward → OOS → Monte Carlo → param sensitivity → cost stress). Only combos passing every rung become candidates; 'demo-ready' clears a stronger bar on top." />
        </div>
        {funnel ? (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
            <Stat label="Validated" value={funnel.validated} accent />
            <Stat label="Paper candidate" value={funnel.paper_candidate} />
            <Stat label="Research watchlist" value={funnel.research_watchlist} />
            <Stat label="Ranking-only (sub-hourly)" value={funnel.ranking_only} />
            <Stat label="Total ledgered" value={funnel.total_ledgered} />
          </div>
        ) : <p className="text-sm text-foreground-muted">Loading funnel…</p>}
        {rejectionRows.length > 0 && (
          <div>
            <p className="text-xs text-foreground-muted mb-1">Rejections by rung:</p>
            <div className="flex flex-wrap gap-2">
              {rejectionRows.map(([rung, n]) => (
                <span key={rung} className="px-2 py-1 rounded text-xs bg-background-subtle border border-gray-300 text-foreground">
                  {rung}: <span className="font-medium">{n}</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* View toggle */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex rounded border border-gray-300 overflow-hidden">
          <button type="button" onClick={() => setView("combos")}
            className={`px-3 py-1 text-sm ${view === "combos" ? "bg-primary text-white" : "bg-background text-foreground"}`}>
            All combos
          </button>
          <button type="button" onClick={() => setView("by_strategy")}
            className={`px-3 py-1 text-sm ${view === "by_strategy" ? "bg-primary text-white" : "bg-background text-foreground"}`}>
            By strategy (best combo)
          </button>
        </div>
        {view === "combos" && (
          <>
            <select value={state} onChange={(e) => setState(e.target.value)}
              className="px-2 py-1 rounded text-sm border border-gray-300 bg-background text-foreground">
              <option value="">All states</option>
              {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={tier} onChange={(e) => setTier(e.target.value)}
              className="px-2 py-1 rounded text-sm border border-gray-300 bg-background text-foreground">
              <option value="">All tiers</option>
              {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <label className="flex items-center gap-1 text-sm text-foreground">
              <input type="checkbox" checked={demoOnly} onChange={(e) => setDemoOnly(e.target.checked)} />
              Demo-ready only
            </label>
          </>
        )}
      </div>

      {view === "combos" ? (
        <div className="card overflow-x-auto">
          {isLoading ? (
            <p className="text-sm text-foreground-muted">Loading candidates…</p>
          ) : shownCandidates.length === 0 ? (
            <p className="text-sm text-foreground-muted py-4">
              No candidates yet. As the research pipeline runs, strategies that pass every
              robustness rung appear here.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-foreground-muted border-b border-gray-300">
                  <th className="px-2 py-2">Strategy</th>
                  <th className="px-2 py-2">Inst</th>
                  <th className="px-2 py-2">TF</th>
                  <th className="px-2 py-2 text-right">Sharpe</th>
                  <th className="px-2 py-2 text-right">PF</th>
                  <th className="px-2 py-2 text-right">MaxDD%</th>
                  <th className="px-2 py-2 text-right">Trades</th>
                  <th className="px-2 py-2 text-right">OOS</th>
                  <th className="px-2 py-2 text-right">MC P(win)</th>
                  <th className="px-2 py-2">State</th>
                  <th className="px-2 py-2">Demo</th>
                  <th className="px-2 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {shownCandidates.map((c) => (
                  <Fragment key={c.event_id}>
                    <tr className="border-b border-gray-200 hover:bg-background-subtle cursor-pointer"
                      onClick={() => setExpanded(expanded === c.event_id ? null : c.event_id)}>
                      <td className="px-2 py-2 font-mono text-xs">{c.strategy_id}</td>
                      <td className="px-2 py-2">{c.instrument}</td>
                      <td className="px-2 py-2">{c.timeframe}</td>
                      <td className="px-2 py-2 text-right font-medium">{num(c.sharpe, 2)}</td>
                      <td className="px-2 py-2 text-right">{num(c.profit_factor, 2)}</td>
                      <td className="px-2 py-2 text-right">{num(c.max_dd, 1)}</td>
                      <td className="px-2 py-2 text-right">{c.trades ?? "—"}</td>
                      <td className="px-2 py-2 text-right">{num(c.oos_score, 3)}</td>
                      <td className="px-2 py-2 text-right">{num(c.mc_profit_prob, 2)}</td>
                      <td className="px-2 py-2">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] border ${
                          c.recommended_state === "paper_candidate"
                            ? "bg-primary/10 border-primary text-primary"
                            : "bg-background-subtle border-gray-300 text-foreground-muted"}`}>
                          {c.recommended_state ?? "—"}
                        </span>
                      </td>
                      <td className="px-2 py-2">
                        {c.demo_ready
                          ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-success/15 border border-success text-success font-medium">READY</span>
                          : <span className="text-[10px] text-foreground-muted">—</span>}
                      </td>
                      <td className="px-2 py-2">
                        <button type="button" disabled
                          title="Approve-to-paper is not yet enabled — review only."
                          className="px-2 py-1 rounded text-xs border border-gray-300 text-foreground-muted opacity-60 cursor-not-allowed">
                          Approve
                        </button>
                      </td>
                    </tr>
                    {expanded === c.event_id && (
                      <tr className="bg-background-subtle">
                        <td colSpan={12} className="px-4 py-3 text-xs text-foreground">
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-1">
                            <Detail k="Composite" v={num(c.composite, 3)} />
                            <Detail k="Net profit" v={num(c.net_profit, 0)} />
                            <Detail k="Walk-forward score" v={num(c.wf_test_score, 3)} />
                            <Detail k="OOS robust" v={c.oos_robust === null ? "—" : (c.oos_robust ? "yes" : "no")} />
                            <Detail k="MC ruin prob" v={num(c.mc_ruin_prob, 3)} />
                            <Detail k="Cost-stress net" v={num(c.cost_net, 0)} />
                            <Detail k="Run" v={c.research_run_id ?? "—"} />
                            <Detail k="Found" v={new Date(c.created_at).toLocaleString()} />
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="card overflow-x-auto">
          {!byStrategy || byStrategy.length === 0 ? (
            <p className="text-sm text-foreground-muted py-4">No strategy roll-ups yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-foreground-muted border-b border-gray-300">
                  <th className="px-2 py-2">Strategy</th>
                  <th className="px-2 py-2">Tier</th>
                  <th className="px-2 py-2 text-right">Passing combos</th>
                  <th className="px-2 py-2 text-right">Demo-ready</th>
                  <th className="px-2 py-2">Best combo</th>
                  <th className="px-2 py-2 text-right">Best Sharpe</th>
                  <th className="px-2 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {byStrategy.map((s) => (
                  <Fragment key={s.strategy_id}>
                    <tr className="border-b border-gray-200 hover:bg-background-subtle cursor-pointer"
                      onClick={() => setExpanded(expanded === s.strategy_id ? null : s.strategy_id)}>
                      <td className="px-2 py-2 font-mono text-xs">{s.strategy_id}
                        <span className="block text-[10px] text-foreground-muted">{strategyShortName(s.strategy_id)}</span>
                      </td>
                      <td className="px-2 py-2 text-xs">{s.tier}</td>
                      <td className="px-2 py-2 text-right">{s.combos}</td>
                      <td className="px-2 py-2 text-right">
                        {s.demo_ready_combos > 0
                          ? <span className="text-success font-medium">{s.demo_ready_combos}</span>
                          : <span className="text-foreground-muted">0</span>}
                      </td>
                      <td className="px-2 py-2">
                        {s.best_combo ? `${s.best_combo.instrument} ${s.best_combo.timeframe}` : "—"}
                      </td>
                      <td className="px-2 py-2 text-right font-medium">{num(s.best_sharpe, 2)}</td>
                      <td className="px-2 py-2 text-xs text-primary">{expanded === s.strategy_id ? "hide" : "all combos"}</td>
                    </tr>
                    {expanded === s.strategy_id && (
                      <tr className="bg-background-subtle">
                        <td colSpan={7} className="px-4 py-3">
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-left text-foreground-muted">
                                <th className="px-2 py-1">Instrument</th>
                                <th className="px-2 py-1">TF</th>
                                <th className="px-2 py-1 text-right">Sharpe</th>
                                <th className="px-2 py-1 text-right">PF</th>
                                <th className="px-2 py-1 text-right">MaxDD%</th>
                                <th className="px-2 py-1">State</th>
                                <th className="px-2 py-1">Demo</th>
                              </tr>
                            </thead>
                            <tbody>
                              {s.all_combos.map((cc, i) => (
                                <tr key={i} className="border-t border-gray-200">
                                  <td className="px-2 py-1">{cc.instrument}</td>
                                  <td className="px-2 py-1">{cc.timeframe}</td>
                                  <td className="px-2 py-1 text-right font-medium">{num(cc.sharpe, 2)}</td>
                                  <td className="px-2 py-1 text-right">{num(cc.profit_factor, 2)}</td>
                                  <td className="px-2 py-1 text-right">{num(cc.max_dd, 1)}</td>
                                  <td className="px-2 py-1">{cc.recommended_state ?? "—"}</td>
                                  <td className="px-2 py-1">{cc.demo_ready ? "✓" : ""}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <p className="text-xs text-foreground-muted mt-3">
        Review-only. Nothing here creates a bot or trades. “Demo-ready” = passed every
        robustness rung AND cleared the stronger demo bar (≥80 trades, OOS-robust,
        PF ≥ 1.2, max DD ≤ 20%, Sharpe ≥ 1.0, MC ruin ≤ 5%). The approve action is
        gated until the operator-approval path is built.
      </p>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="rounded border border-gray-300 bg-background px-3 py-2">
      <div className={`text-lg font-semibold ${accent ? "text-primary" : "text-foreground"}`}>{value}</div>
      <div className="text-[11px] text-foreground-muted">{label}</div>
    </div>
  );
}

function Detail({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-2 border-b border-gray-200 py-0.5">
      <span className="text-foreground-muted">{k}</span>
      <span className="font-medium">{v}</span>
    </div>
  );
}
