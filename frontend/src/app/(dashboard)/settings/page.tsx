"use client";

import { useAuth } from "@/lib/auth";

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

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Settings</h2>

      {/* User Info */}
      <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">User</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-foreground-muted mb-1">Username</p>
            <p className="text-sm font-medium">{user?.username ?? "-"}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted mb-1">Role</p>
            <p className="text-sm font-medium">{user?.role ?? "-"}</p>
          </div>
        </div>
      </div>

      {/* Risk Defaults */}
      <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">Risk Defaults</h3>
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
      <div className="bg-background-card rounded-lg border border-gray-200 p-5">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">Feature Flags</h3>
        <div className="space-y-2">
          {FEATURE_FLAGS.map(({ label, enabled }) => (
            <div key={label} className="flex items-center justify-between py-1">
              <span className="text-sm">{label}</span>
              <span
                className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                  enabled
                    ? "bg-green-100 text-green-800"
                    : "bg-red-100 text-red-800"
                }`}
              >
                {enabled ? "Enabled" : "Disabled"}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
