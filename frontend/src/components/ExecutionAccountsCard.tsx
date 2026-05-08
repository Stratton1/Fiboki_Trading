"use client";

import useSWR from "swr";
import { Briefcase } from "lucide-react";

import { api } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { InfoTip } from "@/components/InfoTip";

const BROKER_LABELS: Record<string, string> = {
  paper: "Paper",
  ig: "IG",
  tradovate: "Tradovate",
};

const ENV_LABELS: Record<string, string> = {
  paper: "Paper",
  demo: "Demo",
  live: "Live",
};

function brokerVariant(broker: string): "ok" | "warn" | "error" | "neutral" | "info" {
  if (broker === "paper") return "neutral";
  if (broker === "ig") return "info";
  if (broker === "tradovate") return "warn";
  return "neutral";
}

function envVariant(env: string): "ok" | "warn" | "error" | "neutral" | "info" {
  if (env === "live") return "error";
  if (env === "demo") return "warn";
  return "neutral";
}

/**
 * Phase-2 execution-accounts panel for the System page.
 *
 * Read-only view that lists every configured ExecutionAccount with its
 * broker, environment, enabled state, allocated capital, and risk %.
 * Operators manage these via the API in Phase 2; an inline editor lives
 * in a later phase.
 */
export function ExecutionAccountsCard() {
  const { data: accounts } = useSWR(
    "/execution/accounts",
    () => api.listExecutionAccounts(),
    { refreshInterval: 30000 },
  );
  const { data: routerState } = useSWR(
    "/execution/router",
    () => api.routerState(),
    { refreshInterval: 30000 },
  );

  return (
    <div className="card mb-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Briefcase size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">
            Execution Accounts
            <InfoTip text="Phase 2 multi-broker fan-out. Each enabled account receives bot signals routed to it. Sizing uses each account's allocated capital." />
          </p>
        </div>
        {routerState ? (
          <StatusBadge variant={routerState.router_mode === "db_targets" ? "ok" : "warn"}>
            {routerState.router_mode}
          </StatusBadge>
        ) : null}
      </div>

      {routerState?.warning ? (
        <div className="mb-4 px-3 py-2 text-xs rounded-md bg-yellow-50 text-yellow-800 border border-yellow-200">
          {routerState.warning}
        </div>
      ) : null}

      {!accounts || accounts.length === 0 ? (
        <p className="text-foreground-muted text-sm">
          No execution accounts configured. The default Paper account is seeded on first
          startup; create more via <code>POST /api/v1/execution/accounts</code>.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Name</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Broker</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Env</th>
                <th className="text-right px-3 py-2 text-xs text-foreground-muted">Capital</th>
                <th className="text-right px-3 py-2 text-xs text-foreground-muted">Risk %</th>
                <th className="text-center px-3 py-2 text-xs text-foreground-muted">Default</th>
                <th className="text-center px-3 py-2 text-xs text-foreground-muted">Status</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((acct) => (
                <tr key={acct.id} className="border-b border-gray-100">
                  <td className="px-3 py-1.5 font-medium">{acct.name}</td>
                  <td className="px-3 py-1.5">
                    <StatusBadge variant={brokerVariant(acct.broker)}>
                      {BROKER_LABELS[acct.broker] ?? acct.broker}
                    </StatusBadge>
                  </td>
                  <td className="px-3 py-1.5">
                    <StatusBadge variant={envVariant(acct.environment)}>
                      {ENV_LABELS[acct.environment] ?? acct.environment}
                    </StatusBadge>
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {acct.allocated_capital.toLocaleString(undefined, {
                      style: "currency",
                      currency: acct.base_currency || "GBP",
                      maximumFractionDigits: 0,
                    })}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {acct.risk_per_trade_pct}%
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    {acct.is_default ? "★" : ""}
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    <StatusBadge variant={acct.is_enabled ? "ok" : "neutral"}>
                      {acct.is_enabled ? "Enabled" : "Disabled"}
                    </StatusBadge>
                    {acct.environment === "live" && !acct.live_allowed ? (
                      <span className="block text-[10px] text-amber-700 mt-0.5">
                        live blocked
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
