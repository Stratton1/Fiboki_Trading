"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Bell,
  BellOff,
  CheckCheck,
  TrendingUp,
  AlertTriangle,
  BarChart3,
  Zap,
  Filter,
} from "lucide-react";

const SEVERITY_VARIANT: Record<string, "ok" | "warn" | "error" | "neutral"> = {
  info: "neutral",
  warning: "warn",
  critical: "error",
};

const TYPE_ICONS: Record<string, typeof Zap> = {
  signal: Zap,
  trade: TrendingUp,
  risk: AlertTriangle,
  summary: BarChart3,
};

const TYPE_LABELS: Record<string, string> = {
  signal: "Signal",
  trade: "Trade",
  risk: "Risk",
  summary: "Summary",
};

export default function AlertsPage() {
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [readFilter, setReadFilter] = useState<string>("");

  const params = new URLSearchParams();
  if (typeFilter) params.set("alert_type", typeFilter);
  if (readFilter === "unread") params.set("is_read", "false");
  if (readFilter === "read") params.set("is_read", "true");
  params.set("limit", "100");
  const queryString = params.toString();

  const { data, mutate } = useSWR(
    `/alerts?${queryString}`,
    () => api.listAlerts(queryString),
    { refreshInterval: 15000 }
  );

  const alerts = data?.items ?? [];
  const unreadCount = data?.unread_count ?? 0;

  async function handleMarkRead(id: number) {
    await api.markAlertRead(id);
    mutate();
  }

  async function handleMarkAllRead() {
    await api.markAllAlertsRead();
    mutate();
  }

  function formatTime(iso: string) {
    if (!iso) return "";
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    return `${diffDay}d ago`;
  }

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Alert Centre"
        subtitle={`${unreadCount} unread alert${unreadCount !== 1 ? "s" : ""}`}
      />

      {/* Controls */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <div className="flex items-center gap-1.5">
          <Filter size={14} className="text-foreground-muted" />
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="input text-xs"
          >
            <option value="">All types</option>
            <option value="signal">Signal</option>
            <option value="trade">Trade</option>
            <option value="risk">Risk</option>
            <option value="summary">Summary</option>
          </select>
        </div>
        <select
          value={readFilter}
          onChange={(e) => setReadFilter(e.target.value)}
          className="input text-xs"
        >
          <option value="">All</option>
          <option value="unread">Unread</option>
          <option value="read">Read</option>
        </select>
        {unreadCount > 0 && (
          <button
            onClick={handleMarkAllRead}
            className="btn btn-secondary text-xs ml-auto flex items-center gap-1.5"
          >
            <CheckCheck size={14} />
            Mark all read
          </button>
        )}
      </div>

      {/* Alert List */}
      <div className="space-y-2">
        {alerts.length === 0 && (
          <EmptyState
            icon={<BellOff size={36} strokeWidth={1.5} />}
            title="No alerts"
            description="Alerts from signals, trades, and risk events will appear here."
          />
        )}
        {alerts.map((alert) => {
          const Icon = TYPE_ICONS[alert.alert_type] ?? Bell;
          return (
            <div
              key={alert.id}
              className={`card flex items-start gap-3 cursor-pointer transition-all hover:shadow-sm ${
                !alert.is_read ? "border-l-4 border-l-primary bg-primary/[0.02]" : "opacity-75"
              }`}
              onClick={() => !alert.is_read && handleMarkRead(alert.id)}
            >
              <div
                className={`mt-0.5 p-1.5 rounded-lg ${
                  alert.severity === "critical"
                    ? "bg-red-100 text-red-600"
                    : alert.severity === "warning"
                    ? "bg-amber-100 text-amber-600"
                    : "bg-gray-100 text-gray-500"
                }`}
              >
                <Icon size={16} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <p className={`text-sm ${!alert.is_read ? "font-semibold" : "font-medium text-foreground-muted"}`}>
                    {alert.title}
                  </p>
                  <StatusBadge variant={SEVERITY_VARIANT[alert.severity] ?? "neutral"}>
                    {alert.severity}
                  </StatusBadge>
                  <span className="text-[10px] text-foreground-muted uppercase tracking-wide">
                    {TYPE_LABELS[alert.alert_type] ?? alert.alert_type}
                  </span>
                </div>
                <p className="text-xs text-foreground-muted">{alert.message}</p>
              </div>
              <div className="text-right shrink-0">
                <p className="text-[11px] text-foreground-muted whitespace-nowrap">
                  {formatTime(alert.created_at)}
                </p>
                {!alert.is_read && (
                  <div className="w-2 h-2 rounded-full bg-primary mt-1 ml-auto" />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
