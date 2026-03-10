"use client";

import { useCallback, useState } from "react";
import useSWR from "swr";
import { api, API_URL, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/StatusBadge";
import { PageHeader } from "@/components/PageHeader";
import {
  Activity,
  AlertTriangle,
  CheckCircle,
  Copy,
  ExternalLink,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  XCircle,
} from "lucide-react";

type DiagResult = { status: "idle" | "running" | "pass" | "fail"; detail: string };

export default function SystemPage() {
  const { user } = useAuth();
  const {
    data: health,
    isLoading: healthLoading,
    mutate: refreshHealth,
  } = useSWR("/system/health", () => api.systemHealth(), {
    refreshInterval: 15000,
  });
  const {
    data: status,
    isLoading: statusLoading,
    mutate: refreshStatus,
  } = useSWR("/system/status", () => api.systemStatus(), {
    refreshInterval: 15000,
  });
  const { data: instruments } = useSWR("/instruments", () => api.instruments());
  const { data: strategies } = useSWR("/strategies", () => api.strategies());
  const { data: execMode, mutate: refreshExecMode } = useSWR(
    "/execution/mode",
    () => api.executionMode(),
    { refreshInterval: 15000 }
  );

  const isHealthy = health?.status === "ok";

  const [diag, setDiag] = useState<Record<string, DiagResult>>({});
  const [copied, setCopied] = useState(false);

  const runDiagnostic = useCallback(
    async (key: string, _label: string, fn: () => Promise<string>) => {
      setDiag((prev) => ({ ...prev, [key]: { status: "running", detail: "" } }));
      try {
        const detail = await fn();
        setDiag((prev) => ({ ...prev, [key]: { status: "pass", detail } }));
      } catch (err) {
        const msg = err instanceof ApiError ? `${err.status}: ${err.message}` : String(err);
        setDiag((prev) => ({ ...prev, [key]: { status: "fail", detail: msg } }));
      }
    },
    []
  );

  async function runAllDiagnostics() {
    await Promise.all([
      runDiagnostic("health", "Backend Health", async () => {
        const h = await api.systemHealth();
        return `${h.status} — v${h.version}`;
      }),
      runDiagnostic("session", "Session", async () => {
        const me = await api.me();
        return `Authenticated as ${me.username} (${me.role})`;
      }),
      runDiagnostic("instruments", "Instruments", async () => {
        const list = await api.instruments();
        const canonical = list.filter((i) => i.has_canonical_data).length;
        return `${list.length} instruments (${canonical} canonical)`;
      }),
      runDiagnostic("strategies", "Strategies", async () => {
        const list = await api.strategies();
        return `${list.length} strategies loaded`;
      }),
    ]);
  }

  function copyApiUrl() {
    navigator.clipboard.writeText(API_URL);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function DiagIcon({ state }: { state: DiagResult["status"] }) {
    if (state === "running")
      return <RefreshCw size={14} className="animate-spin text-blue-500" />;
    if (state === "pass") return <CheckCircle size={14} className="text-green-600" />;
    if (state === "fail") return <XCircle size={14} className="text-red-600" />;
    return <Activity size={14} className="text-foreground-muted/40" />;
  }

  const diagEntries: { key: string; label: string }[] = [
    { key: "health", label: "Backend Health" },
    { key: "session", label: "Session State" },
    { key: "instruments", label: "Instrument Registry" },
    { key: "strategies", label: "Strategy Registry" },
  ];

  return (
    <div className="max-w-5xl">
      <PageHeader
        title="System"
        subtitle="Monitor platform health, diagnostics, and environment"
        actions={
          <button
            onClick={() => { refreshHealth(); refreshStatus(); refreshExecMode(); }}
            className="btn btn-secondary"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
        }
      />

      {/* Overview cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        <div className="stat-card">
          <p className="text-xs font-medium uppercase tracking-wide text-foreground-muted mb-2">Backend Health</p>
          {healthLoading ? (
            <p className="text-sm text-foreground-muted">Checking...</p>
          ) : (
            <div className="flex items-center gap-2">
              <span className={`inline-block w-2.5 h-2.5 rounded-full ${isHealthy ? "bg-green-500 animate-pulse" : "bg-red-500"}`} />
              <span className="text-sm font-semibold">{isHealthy ? "Healthy" : "Unhealthy"}</span>
            </div>
          )}
        </div>
        <div className="stat-card">
          <p className="text-xs font-medium uppercase tracking-wide text-foreground-muted mb-2">API Version</p>
          <p className="text-sm font-semibold">{health?.version ?? "—"}</p>
        </div>
        <div className="stat-card">
          <p className="text-xs font-medium uppercase tracking-wide text-foreground-muted mb-2">Instruments</p>
          <p className="text-sm font-semibold">
            {instruments ? (
              <>
                {instruments.length}{" "}
                <span className="text-xs text-foreground-muted font-normal">
                  ({instruments.filter((i) => i.has_canonical_data).length} canonical)
                </span>
              </>
            ) : "—"}
          </p>
        </div>
        <div className="stat-card">
          <p className="text-xs font-medium uppercase tracking-wide text-foreground-muted mb-2">Strategies</p>
          <p className="text-sm font-semibold">{strategies?.length ?? "—"}</p>
        </div>
      </div>

      {/* Engine Status */}
      <div className="card mb-6">
        <p className="section-label">Engine Status</p>
        {statusLoading ? (
          <p className="text-foreground-muted text-sm">Loading...</p>
        ) : status ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            {Object.entries(status).map(([key, value]) => (
              <div key={key}>
                <p className="text-xs text-foreground-muted mb-1">{key.replace(/_/g, " ")}</p>
                <p className="text-sm font-medium">
                  {typeof value === "boolean" ? (
                    <StatusBadge variant={value ? "ok" : "error"}>
                      {value ? "Active" : "Inactive"}
                    </StatusBadge>
                  ) : String(value ?? "—")}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-foreground-muted text-sm">Unable to fetch status.</p>
        )}
      </div>

      {/* Environment */}
      <div className="card mb-6">
        <p className="section-label">Environment</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-foreground-muted mb-1">API Base URL</p>
            <div className="flex items-center gap-2">
              <code className="text-sm font-mono bg-background-muted px-2 py-0.5 rounded">{API_URL}</code>
              <button onClick={copyApiUrl} className="text-foreground-muted hover:text-foreground transition" title="Copy API URL">
                <Copy size={13} />
              </button>
              {copied && <span className="text-xs text-green-600">Copied</span>}
            </div>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Execution Mode</p>
            <StatusBadge variant={execMode?.mode === "ig_demo" ? "warn" : "info"}>
              {execMode?.mode === "ig_demo" ? "IG Demo" : "Paper Trading"}
            </StatusBadge>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Kill Switch</p>
            <div className="flex items-center gap-2">
              {execMode?.kill_switch_active ? (
                <>
                  <StatusBadge variant="error">Active</StatusBadge>
                  <button
                    onClick={async () => { await api.deactivateKillSwitch(); refreshExecMode(); }}
                    className="text-xs text-foreground-muted hover:text-foreground transition"
                    title="Deactivate kill switch"
                  >
                    <ShieldOff size={14} />
                  </button>
                </>
              ) : (
                <>
                  <StatusBadge variant="ok">Inactive</StatusBadge>
                  {execMode?.mode === "ig_demo" && (
                    <button
                      onClick={async () => { await api.activateKillSwitch("Manual activation"); refreshExecMode(); }}
                      className="text-xs text-red-500 hover:text-red-700 transition"
                      title="Activate kill switch"
                    >
                      <AlertTriangle size={14} />
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Session</p>
            <p className="text-sm font-medium">
              {user ? (
                <>
                  {user.username}{" "}
                  <span className="text-xs text-foreground-muted font-normal">({user.role})</span>
                </>
              ) : (
                <StatusBadge variant="warn">Not authenticated</StatusBadge>
              )}
            </p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Frontend</p>
            <p className="text-sm font-medium">{typeof window !== "undefined" ? window.location.origin : "—"}</p>
          </div>
        </div>
      </div>

      {/* Diagnostics */}
      <div className="card mb-6">
        <div className="flex items-center justify-between mb-4">
          <p className="section-label !mb-0">Diagnostics</p>
          <button onClick={runAllDiagnostics} className="btn btn-ghost text-xs">
            <ShieldCheck size={14} />
            Run All Checks
          </button>
        </div>
        <div className="space-y-1">
          {diagEntries.map(({ key, label }) => {
            const d = diag[key];
            return (
              <div key={key} className="flex items-center justify-between py-2.5 border-b border-border-muted last:border-0">
                <div className="flex items-center gap-2.5">
                  <DiagIcon state={d?.status ?? "idle"} />
                  <span className="text-sm">{label}</span>
                </div>
                <span className="text-xs text-foreground-muted max-w-[50%] text-right truncate">
                  {d?.detail || (d?.status === "running" ? "Checking..." : "—")}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Quick Links */}
      <div className="card">
        <p className="section-label">Quick Links</p>
        <div className="flex flex-wrap gap-4">
          {[
            { label: "Health Endpoint", url: `${API_URL}/api/v1/health` },
            { label: "API Docs (Swagger)", url: `${API_URL}/docs` },
            { label: "Instruments API", url: `${API_URL}/api/v1/instruments/` },
          ].map(({ label, url }) => (
            <a
              key={label}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-sm text-primary hover:text-primary-dark transition"
            >
              <ExternalLink size={13} />
              {label}
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
