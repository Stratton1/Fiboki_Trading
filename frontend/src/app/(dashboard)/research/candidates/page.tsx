"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";

import { PageHeader } from "@/components/PageHeader";
import { InfoTip } from "@/components/InfoTip";
import { api } from "@/lib/api";
import { strategyShortName } from "@/lib/strategy-names";

const TIERS = ["traditional_gen1", "hybrid_gen1", "triple_hybrid_gen1",
  "canonical", "experimental"];
const STATES = ["paper_candidate", "research_watchlist"];

function num(v: number | null, d = 2) {
  return v === null || v === undefined ? "—" : v.toFixed(d);
}

export default function CandidatesPage() {
  const [state, setState] = useState("");
  const [tier, setTier] = useState("");
  const qs = useMemo(() => {
    const p = new URLSearchParams();
    if (state) p.set("state", state);
    if (tier) p.set("tier", tier);
    return p.toString();
  }, [state, tier]);

  const { data: candidates, error, isLoading } = useSWR(
    `candidates?${qs}`, () => api.researchCandidates(qs), { refreshInterval: 30000 });
  const { data: funnel } = useSWR("research-funnel", () => api.researchFunnel(),
    { refreshInterval: 30000 });

  const rungOrder = ["min_trades", "below_screen", "walk_forward", "oos",
    "monte_carlo", "param_sensitivity", "cost_stress"];
  const rejections = funnel?.rejections ?? {};
  const rejectionRows = Object.entries(rejections).sort(
    (a, b) => (rungOrder.indexOf(a[0]) - rungOrder.indexOf(b[0])) || b[1] - a[1]);

  return (
    <div>
      <PageHeader
        title="Candidates"
        subtitle="Strategies that survived the full robustness ladder — review-only, awaiting human decision."
      />

      {/* Funnel */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-medium text-foreground">Validation funnel</h3>
          <InfoTip text="How the sweep's combos progressed through the robustness ladder (walk-forward → OOS → Monte Carlo → param sensitivity → cost stress). Only combos that pass every rung become candidates." />
        </div>
        {funnel ? (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
            <Stat label="Validated" value={funnel.validated} accent />
            <Stat label="Paper candidate" value={funnel.paper_candidate} />
            <Stat label="Research watchlist" value={funnel.research_watchlist} />
            <Stat label="Ranking-only (sub-hourly)" value={funnel.ranking_only} />
            <Stat label="Total ledgered" value={funnel.total_ledgered} />
          </div>
        ) : (
          <p className="text-sm text-foreground-muted">Loading funnel…</p>
        )}
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

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-end mb-4">
        <div>
          <label className="block text-xs text-foreground-muted mb-1">Recommended state</label>
          <select value={state} onChange={(e) => setState(e.target.value)}
            className="px-2 py-1 rounded text-sm border border-gray-300 bg-background text-foreground">
            <option value="">All</option>
            {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-foreground-muted mb-1">Tier</label>
          <select value={tier} onChange={(e) => setTier(e.target.value)}
            className="px-2 py-1 rounded text-sm border border-gray-300 bg-background text-foreground">
            <option value="">All</option>
            {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <span className="text-xs text-foreground-muted ml-auto">
          {candidates ? `${candidates.length} candidate${candidates.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>

      {/* Table */}
      <div className="card overflow-x-auto">
        {error ? (
          <p className="text-danger text-sm">Failed to load candidates.</p>
        ) : isLoading ? (
          <p className="text-sm text-foreground-muted">Loading candidates…</p>
        ) : !candidates || candidates.length === 0 ? (
          <p className="text-sm text-foreground-muted py-4">
            No candidates yet. As the research pipeline runs, strategies that pass every
            robustness rung will appear here for review.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-foreground-muted border-b border-gray-300">
                <th className="px-2 py-2">Strategy</th>
                <th className="px-2 py-2">Tier</th>
                <th className="px-2 py-2">Instrument</th>
                <th className="px-2 py-2">TF</th>
                <th className="px-2 py-2 text-right">Sharpe</th>
                <th className="px-2 py-2 text-right">Composite</th>
                <th className="px-2 py-2 text-right">OOS</th>
                <th className="px-2 py-2 text-right">MC P(profit)</th>
                <th className="px-2 py-2">Recommended</th>
                <th className="px-2 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.event_id} className="border-b border-gray-200 hover:bg-background-subtle">
                  <td className="px-2 py-2 font-mono text-xs" title={c.strategy_id ?? ""}>
                    {c.strategy_id}
                    <span className="block text-[10px] text-foreground-muted">
                      {c.strategy_id ? strategyShortName(c.strategy_id) : ""}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-xs">{c.tier}</td>
                  <td className="px-2 py-2">{c.instrument}</td>
                  <td className="px-2 py-2">{c.timeframe}</td>
                  <td className="px-2 py-2 text-right font-medium">{num(c.sharpe, 2)}</td>
                  <td className="px-2 py-2 text-right">{num(c.composite, 3)}</td>
                  <td className="px-2 py-2 text-right">{num(c.oos_score, 3)}</td>
                  <td className="px-2 py-2 text-right">{num(c.mc_profit_prob, 2)}</td>
                  <td className="px-2 py-2">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] border ${
                      c.recommended_state === "paper_candidate"
                        ? "bg-primary/10 border-primary text-primary"
                        : "bg-background-subtle border-gray-300 text-foreground-muted"
                    }`}>
                      {c.recommended_state ?? "—"}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      disabled
                      title="Approve-to-paper is not yet enabled — review only. Paper promotion is a separate, operator-gated step."
                      className="px-2 py-1 rounded text-xs border border-gray-300 text-foreground-muted opacity-60 cursor-not-allowed"
                    >
                      Approve → paper
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p className="text-xs text-foreground-muted mt-3">
        Review-only. Nothing here creates a bot or trades. The “Approve → paper” action is
        intentionally gated until the operator-approval path is built.
      </p>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="rounded border border-gray-300 bg-background px-3 py-2">
      <div className={`text-lg font-semibold ${accent ? "text-primary" : "text-foreground"}`}>
        {value}
      </div>
      <div className="text-[11px] text-foreground-muted">{label}</div>
    </div>
  );
}
