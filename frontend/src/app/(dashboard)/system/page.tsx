"use client";

import useSWR from "swr";
import { api } from "@/lib/api";

export default function SystemPage() {
  const { data: health, isLoading: healthLoading } = useSWR("/system/health", () => api.systemHealth(), {
    refreshInterval: 10000,
  });
  const { data: status, isLoading: statusLoading } = useSWR("/system/status", () => api.systemStatus(), {
    refreshInterval: 10000,
  });

  const isHealthy = health?.status === "ok";

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">System</h2>

      {/* Health */}
      <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">Health</h3>
        {healthLoading ? (
          <p className="text-foreground-muted text-sm">Checking...</p>
        ) : (
          <div className="flex items-center gap-2">
            <span
              className={`inline-block w-3 h-3 rounded-full ${
                isHealthy ? "bg-green-500" : "bg-red-500"
              }`}
            />
            <span className="text-sm font-medium">{isHealthy ? "Healthy" : "Unhealthy"}</span>
            {health?.version && (
              <span className="text-xs text-foreground-muted ml-2">v{health.version}</span>
            )}
          </div>
        )}
      </div>

      {/* Engine Status */}
      <div className="bg-background-card rounded-lg border border-gray-200 p-5">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">Engine Status</h3>
        {statusLoading ? (
          <p className="text-foreground-muted text-sm">Loading...</p>
        ) : status ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            {Object.entries(status).map(([key, value]) => (
              <div key={key}>
                <p className="text-xs text-foreground-muted mb-1">{key.replace(/_/g, " ")}</p>
                <p className="text-sm font-medium">
                  {typeof value === "boolean" ? (
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                        value ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
                      }`}
                    >
                      {value ? "Active" : "Inactive"}
                    </span>
                  ) : (
                    String(value ?? "-")
                  )}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-foreground-muted text-sm">Unable to fetch status.</p>
        )}
      </div>
    </div>
  );
}
