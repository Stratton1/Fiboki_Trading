"use client";

import { useCallback, useState } from "react";
import useSWR from "swr";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Activity,
  CheckCircle,
  Copy,
  ExternalLink,
  RefreshCw,
  ShieldCheck,
  Wifi,
  XCircle,
} from "lucide-react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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

  const isHealthy = health?.status === "ok";

  // ── Diagnostics ──────────────────────────────────────────────
  const [diag, setDiag] = useState<Record<string, DiagResult>>({});
  const [copied, setCopied] = useState(false);

  const runDiagnostic = useCallback(
    async (key: string, label: string, fn: () => Promise<string>) => {
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

  // ── Helpers ──────────────────────────────────────────────────

  function DiagIcon({ state }: { state: DiagResult["status"] }) {
    if (state === "running")
      return <RefreshCw size={14} className="animate-spin text-blue-500" />;
    if (state === "pass") return <CheckCircle size={14} className="text-green-600" />;
    if (state === "fail") return <XCircle size={14} className="text-red-600" />;
    return <Activity size={14} className="text-gray-400" />;
  }

  const diagEntries: { key: string; label: string }[] = [
    { key: "health", label: "Backend Health" },
    { key: "session", label: "Session State" },
    { key: "instruments", label: "Instrument Registry" },
    { key: "strategies", label: "Strategy Registry" },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold">System</h2>
        <button
          onClick={() => {
            refreshHealth();
            refreshStatus();
          }}
          className="flex items-center gap-1.5 text-sm text-foreground-muted hover:text-foreground transition"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {/* ── Overview cards ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        {/* Health */}
        <div className="bg-background-card rounded-lg border border-gray-300 p-4">
          <p className="text-xs text-foreground-muted mb-1">Backend Health</p>
          {healthLoading ? (
            <p className="text-sm text-foreground-muted">Checking...</p>
          ) : (
            <div className="flex items-center gap-2">
              <span
                className={`inline-block w-2.5 h-2.5 rounded-full ${
                  isHealthy ? "bg-green-500" : "bg-red-500"
                }`}
              />
              <span className="text-sm font-medium">
                {isHealthy ? "Healthy" : "Unhealthy"}
              </span>
            </div>
          )}
        </div>

        {/* Version */}
        <div className="bg-background-card rounded-lg border border-gray-300 p-4">
          <p className="text-xs text-foreground-muted mb-1">API Version</p>
          <p className="text-sm font-medium">{health?.version ?? "—"}</p>
        </div>

        {/* Instruments */}
        <div className="bg-background-card rounded-lg border border-gray-300 p-4">
          <p className="text-xs text-foreground-muted mb-1">Instruments</p>
          <p className="text-sm font-medium">
            {instruments ? (
              <>
                {instruments.length}{" "}
                <span className="text-xs text-foreground-muted font-normal">
                  ({instruments.filter((i) => i.has_canonical_data).length} canonical)
                </span>
              </>
            ) : (
              "—"
            )}
          </p>
        </div>

        {/* Strategies */}
        <div className="bg-background-card rounded-lg border border-gray-300 p-4">
          <p className="text-xs text-foreground-muted mb-1">Strategies</p>
          <p className="text-sm font-medium">{strategies?.length ?? "—"}</p>
        </div>
      </div>

      {/* ── Engine Status ── */}
      <div className="bg-background-card rounded-lg border border-gray-300 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">
          Engine Status
        </h3>
        {statusLoading ? (
          <p className="text-foreground-muted text-sm">Loading...</p>
        ) : status ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            {Object.entries(status).map(([key, value]) => (
              <div key={key}>
                <p className="text-xs text-foreground-muted mb-1">
                  {key.replace(/_/g, " ")}
                </p>
                <p className="text-sm font-medium">
                  {typeof value === "boolean" ? (
                    <StatusBadge variant={value ? "ok" : "error"}>
                      {value ? "Active" : "Inactive"}
                    </StatusBadge>
                  ) : (
                    String(value ?? "—")
                  )}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-foreground-muted text-sm">
            Unable to fetch status.
          </p>
        )}
      </div>

      {/* ── Environment ── */}
      <div className="bg-background-card rounded-lg border border-gray-300 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">
          Environment
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-foreground-muted mb-1">API Base URL</p>
            <div className="flex items-center gap-2">
              <code className="text-sm font-mono bg-gray-100 px-2 py-0.5 rounded">
                {API_URL}
              </code>
              <button
                onClick={copyApiUrl}
                className="text-foreground-muted hover:text-foreground transition"
                title="Copy API URL"
              >
                <Copy size={13} />
              </button>
              {copied && (
                <span className="text-xs text-green-600">Copied</span>
              )}
            </div>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Mode</p>
            <StatusBadge variant="info">Paper Trading</StatusBadge>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Session</p>
            <p className="text-sm font-medium">
              {user ? (
                <>
                  {user.username}{" "}
                  <span className="text-xs text-foreground-muted font-normal">
                    ({user.role})
                  </span>
                </>
              ) : (
                <StatusBadge variant="warn">Not authenticated</StatusBadge>
              )}
            </p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Frontend</p>
            <p className="text-sm font-medium">
              {typeof window !== "undefined" ? window.location.origin : "—"}
            </p>
          </div>
        </div>
      </div>

      {/* ── Diagnostics ── */}
      <div className="bg-background-card rounded-lg border border-gray-300 p-5 mb-6">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-foreground-muted">
            Diagnostics
          </h3>
          <button
            onClick={runAllDiagnostics}
            className="flex items-center gap-1.5 text-sm text-primary hover:text-primary-dark font-medium transition"
          >
            <ShieldCheck size={14} />
            Run All Checks
          </button>
        </div>
        <div className="space-y-2">
          {diagEntries.map(({ key, label }) => {
            const d = diag[key];
            return (
              <div
                key={key}
                className="flex items-center justify-between py-1.5 border-b border-gray-100 last:border-0"
              >
                <div className="flex items-center gap-2">
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

      {/* ── Quick Links ── */}
      <div className="bg-background-card rounded-lg border border-gray-300 p-5">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">
          Quick Links
        </h3>
        <div className="flex flex-wrap gap-3">
          <a
            href={`${API_URL}/api/v1/health`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-sm text-primary hover:text-primary-dark transition"
          >
            <ExternalLink size={13} />
            Health Endpoint
          </a>
          <a
            href={`${API_URL}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-sm text-primary hover:text-primary-dark transition"
          >
            <ExternalLink size={13} />
            API Docs (Swagger)
          </a>
          <a
            href={`${API_URL}/api/v1/instruments/`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-sm text-primary hover:text-primary-dark transition"
          >
            <ExternalLink size={13} />
            Instruments API
          </a>
        </div>
      </div>
    </div>
  );
}
