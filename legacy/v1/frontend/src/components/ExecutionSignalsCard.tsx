"use client";

import { useState } from "react";
import useSWR from "swr";
import { ChevronDown, ChevronRight, Layers } from "lucide-react";

import { api } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { InfoTip } from "@/components/InfoTip";

const PARENT_STATUS_VARIANT: Record<string, "ok" | "warn" | "error" | "neutral" | "info"> = {
  all_filled: "ok",
  partial_success: "warn",
  all_skipped: "neutral",
  all_rejected: "error",
  failed: "error",
  mixed: "warn",
  empty: "neutral",
};

const ATTEMPT_STATUS_VARIANT: Record<string, "ok" | "warn" | "error" | "neutral"> = {
  filled: "ok",
  closed: "ok",
  partially_filled: "ok",
  submitted: "ok",
  pending: "neutral",
  skipped: "neutral",
  rejected: "error",
  failed: "error",
};

const BROKER_LABELS: Record<string, string> = {
  paper: "Paper",
  ig: "IG",
  tradovate: "Tradovate",
};

/**
 * Phase-3 grouped audit view — one row per parent BotSignal with an
 * expandable list of child ExecutionAttempts. Replaces the flat
 * ``execution_audit`` table for partial-success visibility while leaving
 * the legacy view in place.
 */
export function ExecutionSignalsCard() {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const { data: signals } = useSWR(
    "/execution/signals",
    () => api.listExecutionSignals({ limit: 50 }),
    { refreshInterval: 30000 },
  );

  const toggle = (id: number) =>
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  return (
    <div className="card mb-6">
      <div className="flex items-center gap-2 mb-4">
        <Layers size={14} className="text-foreground-muted" />
        <p className="section-label !mb-0">
          Execution Signals (Phase 3)
          <InfoTip text="One parent signal per bot evaluation. Expand to see each broker/account attempt: filled, skipped, or rejected. Partial-success rolls up here." />
        </p>
      </div>

      {!signals || signals.length === 0 ? (
        <p className="text-foreground-muted text-sm">
          No signals recorded yet. The router writes one parent row + one row per
          target on every dispatch.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="px-2 py-2 text-xs text-foreground-muted w-6"></th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Time</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Bot</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Strategy</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Instrument</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Dir</th>
                <th className="text-center px-3 py-2 text-xs text-foreground-muted">Status</th>
                <th className="text-center px-3 py-2 text-xs text-foreground-muted">Attempts</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => {
                const isOpen = !!expanded[s.id];
                return (
                  <>
                    <tr key={s.id} className="border-b border-gray-100">
                      <td className="px-2 py-1.5 align-middle">
                        <button
                          type="button"
                          onClick={() => toggle(s.id)}
                          className="text-foreground-muted hover:text-foreground"
                          aria-label={isOpen ? "Collapse" : "Expand"}
                        >
                          {isOpen ? (
                            <ChevronDown size={14} />
                          ) : (
                            <ChevronRight size={14} />
                          )}
                        </button>
                      </td>
                      <td className="px-3 py-1.5 text-xs text-foreground-muted whitespace-nowrap">
                        {s.signal_timestamp
                          ? new Date(s.signal_timestamp).toLocaleString()
                          : "—"}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-xs">{s.bot_id}</td>
                      <td className="px-3 py-1.5 text-xs">{s.strategy_id}</td>
                      <td className="px-3 py-1.5">{s.instrument}</td>
                      <td className="px-3 py-1.5 text-xs">{s.direction}</td>
                      <td className="px-3 py-1.5 text-center">
                        <StatusBadge variant={PARENT_STATUS_VARIANT[s.parent_status] ?? "neutral"}>
                          {s.parent_status.replace(/_/g, " ")}
                        </StatusBadge>
                      </td>
                      <td className="px-3 py-1.5 text-center text-xs text-foreground-muted">
                        {s.attempt_count}
                      </td>
                    </tr>
                    {isOpen && s.attempts && s.attempts.length > 0 ? (
                      <tr key={`${s.id}-detail`} className="bg-background-muted/40">
                        <td colSpan={8} className="px-6 py-3">
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-foreground-muted">
                                <th className="text-left px-2 py-1">Broker</th>
                                <th className="text-left px-2 py-1">Env</th>
                                <th className="text-left px-2 py-1">Symbol</th>
                                <th className="text-right px-2 py-1">Size</th>
                                <th className="text-right px-2 py-1">Fill px</th>
                                <th className="text-center px-2 py-1">Status</th>
                                <th className="text-left px-2 py-1">Broker ID</th>
                                <th className="text-left px-2 py-1">Reason</th>
                              </tr>
                            </thead>
                            <tbody>
                              {s.attempts.map((a) => (
                                <tr key={a.id} className="border-t border-gray-100">
                                  <td className="px-2 py-1 font-medium">
                                    {BROKER_LABELS[a.broker] ?? a.broker}
                                  </td>
                                  <td className="px-2 py-1">{a.environment}</td>
                                  <td className="px-2 py-1 font-mono">
                                    {a.broker_symbol ?? "—"}
                                  </td>
                                  <td className="px-2 py-1 text-right tabular-nums">
                                    {a.filled_size ?? a.requested_size ?? "—"}
                                  </td>
                                  <td className="px-2 py-1 text-right tabular-nums">
                                    {a.filled_price ?? "—"}
                                  </td>
                                  <td className="px-2 py-1 text-center">
                                    <StatusBadge
                                      variant={ATTEMPT_STATUS_VARIANT[a.status] ?? "neutral"}
                                    >
                                      {a.status}
                                    </StatusBadge>
                                  </td>
                                  <td className="px-2 py-1 font-mono">
                                    {a.broker_deal_id ?? a.broker_order_id ?? "—"}
                                  </td>
                                  <td className="px-2 py-1 text-foreground-muted">
                                    {a.error_code
                                      ? `${a.error_code}: ${a.rejection_reason ?? ""}`
                                      : a.rejection_reason ?? "—"}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    ) : null}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
