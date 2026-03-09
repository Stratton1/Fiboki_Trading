"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/StatusBadge";
import { Copy, ExternalLink, Server } from "lucide-react";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const RISK_DEFAULTS = [
  { label: "Risk per Trade", value: "1%" },
  { label: "Max Portfolio Risk", value: "5%" },
  { label: "Max Open Positions", value: "8" },
  { label: "Daily Hard Stop", value: "4%" },
];

const FEATURE_FLAGS = [
  { label: "Live Execution", enabled: false },
  { label: "Paper Trading", enabled: true },
  { label: "Backtesting", enabled: true },
  { label: "Research Matrix", enabled: true },
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

  function copyToClipboard(text: string, key: string) {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1500);
  }

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Settings</h2>

      {/* User Info */}
      <div className="bg-background-card rounded-lg border border-gray-300 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">
          User
        </h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-foreground-muted mb-1">Username</p>
            <p className="text-sm font-medium">{user?.username ?? "—"}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Role</p>
            <p className="text-sm font-medium">{user?.role ?? "—"}</p>
          </div>
        </div>
      </div>

      {/* Risk Defaults */}
      <div className="bg-background-card rounded-lg border border-gray-300 p-5 mb-6">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-foreground-muted">
            Risk Defaults
          </h3>
          <StatusBadge variant="neutral">Read-only</StatusBadge>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {RISK_DEFAULTS.map(({ label, value }) => (
            <div key={label}>
              <p className="text-xs text-foreground-muted mb-1">{label}</p>
              <p className="text-sm font-semibold">{value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Feature Flags */}
      <div className="bg-background-card rounded-lg border border-gray-300 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">
          Feature Flags
        </h3>
        <div className="space-y-2">
          {FEATURE_FLAGS.map(({ label, enabled }) => (
            <div
              key={label}
              className="flex items-center justify-between py-1"
            >
              <span className="text-sm">{label}</span>
              <StatusBadge variant={enabled ? "ok" : "error"}>
                {enabled ? "Enabled" : "Disabled"}
              </StatusBadge>
            </div>
          ))}
        </div>
      </div>

      {/* Operator Tools */}
      <div className="bg-background-card rounded-lg border border-gray-300 p-5">
        <div className="flex items-center gap-2 mb-3">
          <Server size={14} className="text-foreground-muted" />
          <h3 className="text-sm font-medium text-foreground-muted">
            Operator Tools
          </h3>
        </div>

        {/* Deployment endpoints */}
        <div className="mb-4">
          <p className="text-xs text-foreground-muted mb-2">
            Deployment Endpoints
          </p>
          <div className="space-y-1.5">
            {ENDPOINTS.map(({ label, url }) => (
              <div
                key={label}
                className="flex items-center justify-between py-1"
              >
                <span className="text-sm">{label}</span>
                <div className="flex items-center gap-2">
                  <code className="text-xs font-mono text-foreground-muted bg-gray-100 px-1.5 py-0.5 rounded max-w-[220px] truncate">
                    {url}
                  </code>
                  <button
                    onClick={() => copyToClipboard(url, label)}
                    className="text-foreground-muted hover:text-foreground transition"
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
                    className="text-foreground-muted hover:text-primary transition"
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
        <div className="border-t border-gray-100 pt-3">
          <p className="text-xs text-foreground-muted mb-2">Quick Actions</p>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/system"
              className="text-sm text-primary hover:text-primary-dark transition"
            >
              Open System Diagnostics
            </Link>
            <span className="text-gray-300">|</span>
            <a
              href={`${API_URL}/docs`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-primary hover:text-primary-dark transition"
            >
              API Documentation
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
