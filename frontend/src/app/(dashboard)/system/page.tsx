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
  BarChart3,
  CheckCircle,
  Copy,
  Database,
  ExternalLink,
  FileText,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  XCircle,
} from "lucide-react";
import { useManifest } from "@/lib/hooks/use-manifest";
import { InfoTip } from "@/components/InfoTip";

type DiagResult = { status: "idle" | "running" | "pass" | "fail"; detail: string };

const AUDIT_STATUS_VARIANT: Record<string, "ok" | "warn" | "error" | "neutral"> = {
  success: "ok",
  failed: "error",
  rejected: "warn",
};

function ExecutionAuditSection() {
  const [auditFilter, setAuditFilter] = useState<string>("");
  const params = auditFilter ? `execution_mode=${auditFilter}` : undefined;
  const { data: auditEntries } = useSWR(
    `/execution/audit?${params ?? "all"}`,
    () => api.executionAudit(params),
    { refreshInterval: 30000 }
  );

  return (
    <div className="card mb-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <FileText size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">Execution Audit Log</p>
        </div>
        <select
          value={auditFilter}
          onChange={(e) => setAuditFilter(e.target.value)}
          className="input text-xs"
        >
          <option value="">All modes</option>
          <option value="paper">Paper</option>
          <option value="ig_demo">IG Demo</option>
        </select>
      </div>
      {!auditEntries || auditEntries.length === 0 ? (
        <p className="text-foreground-muted text-sm">No audit entries yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Time</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Mode</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Action</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Instrument</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Status</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Bot</th>
              </tr>
            </thead>
            <tbody>
              {auditEntries.slice(0, 50).map((entry) => (
                <tr key={entry.id} className="border-b border-gray-100">
                  <td className="px-3 py-1.5 text-xs text-foreground-muted whitespace-nowrap">
                    {new Date(entry.timestamp).toLocaleString()}
                  </td>
                  <td className="px-3 py-1.5">
                    <StatusBadge variant={entry.execution_mode === "ig_demo" ? "info" : "neutral"}>
                      {entry.execution_mode}
                    </StatusBadge>
                  </td>
                  <td className="px-3 py-1.5 font-medium">{entry.action}</td>
                  <td className="px-3 py-1.5">{entry.instrument}</td>
                  <td className="px-3 py-1.5">
                    <StatusBadge variant={AUDIT_STATUS_VARIANT[entry.status] ?? "neutral"}>
                      {entry.status}
                    </StatusBadge>
                  </td>
                  <td className="px-3 py-1.5 text-xs text-foreground-muted">
                    {entry.bot_id ?? "—"}
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

function SlippageSection() {
  const { data: slippage } = useSWR(
    "/execution/slippage",
    () => api.slippage(),
    { refreshInterval: 60000 }
  );

  return (
    <div className="card mb-6">
      <div className="flex items-center gap-2 mb-4">
        <BarChart3 size={14} className="text-foreground-muted" />
        <p className="section-label !mb-0">Slippage Analytics<InfoTip text="Slippage = difference between requested price and actual fill price. Paper mode has zero slippage. Relevant when using IG demo/live execution." /></p>
      </div>
      {!slippage || slippage.total_fills === 0 ? (
        <p className="text-foreground-muted text-sm">
          No execution fill data yet. Slippage analytics will appear once IG demo fills accumulate.
          Paper mode has zero slippage by design.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-4">
            <div>
              <p className="text-xs text-foreground-muted mb-1">Total Fills</p>
              <p className="text-lg font-semibold">{slippage.total_fills}</p>
            </div>
            <div>
              <p className="text-xs text-foreground-muted mb-1">Avg Slippage</p>
              <p className="text-lg font-semibold">{slippage.avg_slippage_pips} pips</p>
            </div>
            <div>
              <p className="text-xs text-foreground-muted mb-1">Instruments</p>
              <p className="text-lg font-semibold">{slippage.instruments.length}</p>
            </div>
          </div>
          {slippage.instruments.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left px-3 py-2 text-xs text-foreground-muted">Instrument</th>
                    <th className="text-right px-3 py-2 text-xs text-foreground-muted">Fills</th>
                    <th className="text-right px-3 py-2 text-xs text-foreground-muted">Avg Slip</th>
                    <th className="text-right px-3 py-2 text-xs text-foreground-muted">Max Slip</th>
                    <th className="text-right px-3 py-2 text-xs text-foreground-muted">Avg Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {slippage.instruments.map((inst) => (
                    <tr key={inst.instrument} className="border-b border-gray-100">
                      <td className="px-3 py-1.5 font-medium">{inst.instrument}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{inst.fills}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{inst.avg_slippage_pips} pips</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{inst.max_slippage_pips} pips</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{inst.avg_latency_ms}ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

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

  const { manifest, datasets, refresh: refreshManifest } = useManifest();

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
            <StatusBadge variant={execMode?.mode === "ig_demo" ? "info" : "neutral"}>
              {execMode?.mode === "ig_demo" ? "IG Demo" : "Paper Trading"}
            </StatusBadge>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Kill Switch<InfoTip text="Emergency stop. When active, all bots are paused and no new entries are taken. Use when something looks wrong." /></p>
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

      {/* Data Manifest */}
      <div className="card mb-6">
        <div className="flex items-center justify-between mb-4">
          <p className="section-label !mb-0">Data Manifest</p>
          <button
            onClick={async () => {
              await api.refreshManifest();
              refreshManifest();
            }}
            className="btn btn-ghost text-xs"
          >
            <Database size={14} />
            Refresh Manifest
          </button>
        </div>
        {manifest ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
              <div>
                <p className="text-xs text-foreground-muted mb-1">Datasets</p>
                <p className="text-lg font-semibold">{datasets.length}</p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted mb-1">Symbols</p>
                <p className="text-lg font-semibold">
                  {new Set(datasets.map((d) => d.symbol)).size}
                </p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted mb-1">Total Bars</p>
                <p className="text-lg font-semibold">
                  {datasets.reduce((sum, d) => sum + d.bars, 0).toLocaleString()}
                </p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted mb-1">Generated</p>
                <p className="text-sm font-semibold">
                  {new Date(manifest.generated_at).toLocaleDateString()}
                </p>
              </div>
            </div>
            {datasets.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className="text-left px-3 py-2 text-xs text-foreground-muted">Symbol</th>
                      <th className="text-left px-3 py-2 text-xs text-foreground-muted">TF</th>
                      <th className="text-right px-3 py-2 text-xs text-foreground-muted">Bars</th>
                      <th className="text-left px-3 py-2 text-xs text-foreground-muted">Range</th>
                      <th className="text-left px-3 py-2 text-xs text-foreground-muted">Provider</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datasets.map((d, i) => (
                      <tr key={i} className="border-b border-gray-100">
                        <td className="px-3 py-1.5 font-medium">{d.symbol}</td>
                        <td className="px-3 py-1.5">{d.timeframe}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{d.bars.toLocaleString()}</td>
                        <td className="px-3 py-1.5 text-xs text-foreground-muted">
                          {d.from_date.slice(0, 10)} — {d.to_date.slice(0, 10)}
                        </td>
                        <td className="px-3 py-1.5 text-xs">{d.provider}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <p className="text-foreground-muted text-sm">
            No manifest found. Click &ldquo;Refresh Manifest&rdquo; to generate one, or run <code className="bg-background-muted px-1 rounded text-xs">fibokei manifest</code> on the server.
          </p>
        )}
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

      {/* Execution Audit Log */}
      <ExecutionAuditSection />

      {/* Slippage Analytics */}
      <SlippageSection />

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
