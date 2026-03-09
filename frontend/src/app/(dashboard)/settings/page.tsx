"use client";

import { useState } from "react";
import useSWR from "swr";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { PageHeader } from "@/components/PageHeader";
import { Copy, ExternalLink, Server, User, Shield, Flag, Zap } from "lucide-react";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const RISK_PARAMS = [
  { label: "Risk per Trade", key: "max_risk_per_trade_pct", suffix: "%", default: "1.0" },
  { label: "Max Portfolio Risk", key: "max_portfolio_risk_pct", suffix: "%", default: "5.0" },
  { label: "Max Open Positions", key: "max_open_trades", suffix: "", default: "8" },
  { label: "Max Per Instrument", key: "max_per_instrument", suffix: "", default: "2" },
  { label: "Daily Soft Stop", key: "daily_soft_stop_pct", suffix: "%", default: "3.0" },
  { label: "Daily Hard Stop", key: "daily_hard_stop_pct", suffix: "%", default: "4.0" },
  { label: "Weekly Soft Stop", key: "weekly_soft_stop_pct", suffix: "%", default: "6.0" },
  { label: "Weekly Hard Stop", key: "weekly_hard_stop_pct", suffix: "%", default: "8.0" },
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
            <StatusBadge variant={execMode?.mode === "ig_demo" ? "warn" : "info"}>
              {execMode?.mode === "ig_demo" ? "IG Demo" : "Paper"}
            </StatusBadge>
          </div>
          <div className="bg-background-muted rounded-lg px-3 py-2.5">
            <p className="text-xs text-foreground-muted mb-1">Live Execution</p>
            <StatusBadge variant={execMode?.live_execution_enabled ? "warn" : "ok"}>
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
          <StatusBadge variant="neutral">Server-configured</StatusBadge>
        </div>
        <p className="text-xs text-foreground-muted mb-3">
          Risk limits are configured via environment variables on the backend. Values shown are defaults.
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {RISK_PARAMS.map(({ label, key, suffix, default: defaultVal }) => (
            <div key={key} className="bg-background-muted rounded-lg px-3 py-2.5">
              <p className="text-xs text-foreground-muted mb-0.5">{label}</p>
              <p className="text-lg font-bold tracking-tight">
                {defaultVal}{suffix}
              </p>
              <p className="text-[10px] text-foreground-muted font-mono mt-0.5">{key}</p>
            </div>
          ))}
        </div>
      </div>

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
