"use client";

import { useState } from "react";
import useSWR from "swr";
import { useAuth } from "@/lib/auth";
import { api, API_URL } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { PageHeader } from "@/components/PageHeader";
import { Copy, ExternalLink, Server, User, Shield, Flag, Zap, Users, Wifi, WifiOff, Loader2 } from "lucide-react";
import Link from "next/link";

const RISK_PARAMS: Array<{ label: string; key: string; suffix: string }> = [
  { label: "Risk per Trade", key: "max_risk_per_trade_pct", suffix: "%" },
  { label: "Max Portfolio Risk", key: "max_portfolio_risk_pct", suffix: "%" },
  { label: "Max Open Positions", key: "max_open_trades", suffix: "" },
  { label: "Max Per Instrument", key: "max_per_instrument", suffix: "" },
  { label: "Daily Soft Stop", key: "daily_soft_stop_pct", suffix: "%" },
  { label: "Daily Hard Stop", key: "daily_hard_stop_pct", suffix: "%" },
  { label: "Weekly Soft Stop", key: "weekly_soft_stop_pct", suffix: "%" },
  { label: "Weekly Hard Stop", key: "weekly_hard_stop_pct", suffix: "%" },
];

const FLEET_RISK_PARAMS: Array<{ label: string; key: string; suffix: string }> = [
  { label: "Max Bots / Instrument", key: "fleet_max_bots_per_instrument", suffix: "" },
  { label: "Max Total Positions", key: "fleet_max_total_positions", suffix: "" },
  { label: "Max Exposure / Instrument", key: "fleet_max_exposure_per_instrument", suffix: "" },
  { label: "Correlation Threshold", key: "fleet_correlation_threshold", suffix: "" },
  { label: "Auto-Cull Sigma", key: "fleet_cull_sigma", suffix: "σ" },
  { label: "Min Trades for Cull", key: "fleet_cull_min_trades", suffix: "" },
];

const ENDPOINTS = [
  { label: "Frontend", url: "https://fiboki.uk" },
  { label: "Backend API", url: API_URL },
  { label: "Health Check", url: `${API_URL}/api/v1/health` },
  { label: "API Docs", url: `${API_URL}/docs` },
];

export default function SettingsPage() {
  const { user } = useAuth();
  const [copied, setCopied] = useState<string | null>(null);

  const { data: execMode } = useSWR("/execution/mode", () => api.executionMode(), {
    refreshInterval: 30000,
  });
  const { data: systemStatus } = useSWR("/system/status", () => api.systemStatus());
  const { data: riskConfig, isLoading: riskLoading } = useSWR("/system/risk-config", () => api.riskConfig());
  const { data: igHealth, isLoading: igHealthLoading } = useSWR(
    execMode?.live_execution_enabled ? "/execution/ig-health" : null,
    () => api.igHealth(),
    { refreshInterval: 60000 }
  );

  function copyToClipboard(text: string, key: string) {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1500);
  }

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Settings"
        subtitle="Platform configuration and operator tools"
      />

      {/* User Info */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-4">
          <User size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">User</p>
        </div>
        <div className="flex items-center gap-6">
          <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-primary text-lg font-bold uppercase">
            {user?.username?.charAt(0) ?? "?"}
          </div>
          <div className="grid grid-cols-2 gap-x-8 gap-y-2">
            <div>
              <p className="text-xs text-foreground-muted">Username</p>
              <p className="text-sm font-semibold">{user?.username ?? "—"}</p>
            </div>
            <div>
              <p className="text-xs text-foreground-muted">Role</p>
              <p className="text-sm font-semibold">{user?.role ?? "—"}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Execution Mode */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-4">
          <Zap size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">Execution Mode</p>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="bg-background-muted rounded-lg px-3 py-2.5">
            <p className="text-xs text-foreground-muted mb-1">Mode</p>
            <StatusBadge variant={execMode?.mode === "ig_demo" ? "info" : "neutral"}>
              {execMode?.mode === "ig_demo" ? "IG Demo" : "Paper"}
            </StatusBadge>
          </div>
          <div className="bg-background-muted rounded-lg px-3 py-2.5">
            <p className="text-xs text-foreground-muted mb-1">Live Execution</p>
            <StatusBadge variant={execMode?.live_execution_enabled ? "info" : "ok"}>
              {execMode?.live_execution_enabled ? "Enabled" : "Disabled"}
            </StatusBadge>
          </div>
          <div className="bg-background-muted rounded-lg px-3 py-2.5">
            <p className="text-xs text-foreground-muted mb-1">Kill Switch</p>
            <StatusBadge variant={execMode?.kill_switch_active ? "error" : "ok"}>
              {execMode?.kill_switch_active ? "Active" : "Inactive"}
            </StatusBadge>
          </div>
          <div className="bg-background-muted rounded-lg px-3 py-2.5">
            <p className="text-xs text-foreground-muted mb-1">Strategies</p>
            <p className="text-lg font-bold tracking-tight">{systemStatus?.strategies_loaded ?? "—"}</p>
          </div>
        </div>
      </div>

      {/* Risk Parameters */}
      <div className="card mb-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Shield size={14} className="text-foreground-muted" />
            <p className="section-label !mb-0">Risk Parameters</p>
          </div>
          <StatusBadge variant="ok">Live from server</StatusBadge>
        </div>
        <p className="text-xs text-foreground-muted mb-3">
          Risk limits are configured via environment variables on the backend.
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {RISK_PARAMS.map(({ label, key, suffix }) => (
            <div key={key} className="bg-background-muted rounded-lg px-3 py-2.5">
              <p className="text-xs text-foreground-muted mb-0.5">{label}</p>
              <p className="text-lg font-bold tracking-tight">
                {riskLoading ? (
                  <Loader2 size={14} className="animate-spin inline text-foreground-muted" />
                ) : riskConfig ? (
                  <>{(riskConfig as Record<string, number>)[key]}{suffix}</>
                ) : "—"}
              </p>
              <p className="text-[10px] text-foreground-muted font-mono mt-0.5">{key}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Fleet Risk Limits */}
      <div className="card mb-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Users size={14} className="text-foreground-muted" />
            <p className="section-label !mb-0">Fleet Risk Limits</p>
          </div>
          <StatusBadge variant="ok">Live from server</StatusBadge>
        </div>
        <p className="text-xs text-foreground-muted mb-3">
          Fleet-level limits prevent overconcentration when running multiple bots. Configured via environment variables.
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {FLEET_RISK_PARAMS.map(({ label, key, suffix }) => (
            <div key={key} className="bg-background-muted rounded-lg px-3 py-2.5">
              <p className="text-xs text-foreground-muted mb-0.5">{label}</p>
              <p className="text-lg font-bold tracking-tight">
                {riskLoading ? (
                  <Loader2 size={14} className="animate-spin inline text-foreground-muted" />
                ) : riskConfig ? (
                  <>{(riskConfig as Record<string, number>)[key]}{suffix}</>
                ) : "—"}
              </p>
              <p className="text-[10px] text-foreground-muted font-mono mt-0.5">{key}</p>
            </div>
          ))}
        </div>
      </div>

      {/* IG Demo Connectivity — only shown when live execution is enabled */}
      {execMode?.live_execution_enabled && (
        <div className="card mb-6">
          <div className="flex items-center gap-2 mb-4">
            {igHealthLoading ? (
              <Loader2 size={14} className="text-foreground-muted animate-spin" />
            ) : igHealth?.reachable ? (
              <Wifi size={14} className="text-green-600" />
            ) : (
              <WifiOff size={14} className="text-red-500" />
            )}
            <p className="section-label !mb-0">IG Demo Connectivity</p>
          </div>
          {igHealthLoading ? (
            <p className="text-sm text-foreground-muted">Checking IG connection...</p>
          ) : igHealth ? (
            igHealth.reachable ? (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="bg-background-muted rounded-lg px-3 py-2.5">
                  <p className="text-xs text-foreground-muted mb-1">Status</p>
                  <StatusBadge variant="ok">Connected</StatusBadge>
                </div>
                <div className="bg-background-muted rounded-lg px-3 py-2.5">
                  <p className="text-xs text-foreground-muted mb-1">Account</p>
                  <p className="text-sm font-semibold">{igHealth.account_id ?? "—"}</p>
                </div>
                <div className="bg-background-muted rounded-lg px-3 py-2.5">
                  <p className="text-xs text-foreground-muted mb-1">Name</p>
                  <p className="text-sm font-semibold truncate">{igHealth.account_name ?? "—"}</p>
                </div>
                <div className="bg-background-muted rounded-lg px-3 py-2.5">
                  <p className="text-xs text-foreground-muted mb-1">Balance</p>
                  <p className="text-sm font-semibold">
                    {igHealth.balance != null ? `£${igHealth.balance.toFixed(2)}` : "—"}
                  </p>
                </div>
              </div>
            ) : (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                <strong>IG Demo unreachable</strong>
                {igHealth.error && <p className="text-xs mt-1 font-mono">{igHealth.error}</p>}
                <p className="text-xs mt-1">Check FIBOKEI_IG_API_KEY, FIBOKEI_IG_USERNAME, FIBOKEI_IG_PASSWORD on Railway.</p>
              </div>
            )
          ) : null}
        </div>
      )}

      {/* Feature Flags */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-4">
          <Flag size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">Feature Status</p>
        </div>
        <div className="space-y-1">
          {[
            { label: "Live Execution", enabled: execMode?.live_execution_enabled ?? false },
            { label: "IG Paper Mode", enabled: execMode?.ig_paper_mode ?? true },
            { label: "Paper Trading", enabled: true },
            { label: "Backtesting", enabled: true },
            { label: "Research Matrix", enabled: true },
          ].map(({ label, enabled }) => (
            <div key={label} className="flex items-center justify-between py-2.5 border-b border-border-muted last:border-0">
              <span className="text-sm">{label}</span>
              <StatusBadge variant={enabled ? "ok" : "neutral"}>
                {enabled ? "Enabled" : "Disabled"}
              </StatusBadge>
            </div>
          ))}
        </div>
      </div>

      {/* Operator Tools */}
      <div className="card">
        <div className="flex items-center gap-2 mb-4">
          <Server size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">Operator Tools</p>
        </div>

        {/* Deployment endpoints */}
        <div className="mb-5">
          <p className="text-xs text-foreground-muted mb-2">Deployment Endpoints</p>
          <div className="space-y-1">
            {ENDPOINTS.map(({ label, url }) => (
              <div key={label} className="flex items-center justify-between py-2 border-b border-border-muted last:border-0">
                <span className="text-sm font-medium">{label}</span>
                <div className="flex items-center gap-2">
                  <code className="text-xs font-mono text-foreground-muted bg-background-muted px-2 py-0.5 rounded max-w-[220px] truncate">
                    {url}
                  </code>
                  <button
                    onClick={() => copyToClipboard(url, label)}
                    className="text-foreground-muted hover:text-foreground transition p-1 rounded hover:bg-background-muted"
                    title={`Copy ${label} URL`}
                  >
                    {copied === label ? (
                      <span className="text-xs text-green-600">Copied</span>
                    ) : (
                      <Copy size={12} />
                    )}
                  </button>
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-foreground-muted hover:text-primary transition p-1 rounded hover:bg-background-muted"
                    title={`Open ${label}`}
                  >
                    <ExternalLink size={12} />
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Quick actions */}
        <div className="border-t border-border pt-4">
          <p className="text-xs text-foreground-muted mb-3">Quick Actions</p>
          <div className="flex flex-wrap gap-2">
            <Link href="/system" className="btn btn-secondary text-sm">
              System Diagnostics
            </Link>
            <a
              href={`${API_URL}/docs`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-secondary text-sm"
            >
              <ExternalLink size={13} />
              API Documentation
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
