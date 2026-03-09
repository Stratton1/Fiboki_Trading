"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { AlertTriangle, ShieldCheck } from "lucide-react";

export function ExecutionModeBanner() {
  const { data: execMode } = useSWR("/execution/mode", () => api.executionMode(), {
    refreshInterval: 30000,
  });

  if (!execMode || execMode.mode === "paper") return null;

  return (
    <div
      className={`flex items-center gap-2 px-4 py-2 text-xs font-medium rounded-lg mb-4 ${
        execMode.kill_switch_active
          ? "bg-red-100 text-red-800 border border-red-200"
          : "bg-yellow-50 text-yellow-800 border border-yellow-200"
      }`}
    >
      {execMode.kill_switch_active ? (
        <>
          <AlertTriangle size={14} />
          <span>Kill switch active — all execution halted</span>
        </>
      ) : (
        <>
          <ShieldCheck size={14} />
          <span>
            IG Demo mode active
            {execMode.live_execution_enabled ? " — live execution enabled" : ""}
          </span>
        </>
      )}
    </div>
  );
}
