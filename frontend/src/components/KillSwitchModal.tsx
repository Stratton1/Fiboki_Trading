"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { ShieldAlert, ShieldCheck, X, AlertTriangle } from "lucide-react";

/**
 * Custom confirmation modal for the kill switch — replaces the native
 * `window.confirm()` dialog so operators can see exactly what activating /
 * deactivating the kill switch does before they pull the trigger.
 *
 * Lists the concrete consequences instead of a generic "Are you sure?".
 */
export function KillSwitchModal({
  open,
  isActive,
  loading,
  onCancel,
  onConfirm,
  openPositions,
  runningBots,
}: {
  open: boolean;
  /** Current kill switch state — true means switch is currently active. */
  isActive: boolean;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  openPositions?: number;
  runningBots?: number;
}) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open || typeof document === "undefined") return null;

  const action = isActive ? "Deactivate" : "Activate";
  const title = isActive ? "Deactivate kill switch" : "Activate kill switch";
  const Icon = isActive ? ShieldCheck : ShieldAlert;
  const accent = isActive ? "text-primary" : "text-danger";

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="kill-switch-modal-title"
      className="fixed inset-0 z-[10000] flex items-center justify-center"
    >
      <div
        className="absolute inset-0 bg-black/40"
        onClick={loading ? undefined : onCancel}
      />
      <div className="relative bg-background-card rounded-2xl shadow-2xl border border-gray-200 w-full max-w-md mx-4 p-6">
        <button
          type="button"
          onClick={onCancel}
          disabled={loading}
          aria-label="Close dialog"
          className="absolute top-3 right-3 text-foreground-muted hover:text-foreground p-1 rounded-md hover:bg-background-muted transition-colors disabled:opacity-50"
        >
          <X size={16} />
        </button>

        <div className="flex items-start gap-3 mb-4">
          <div className={`w-10 h-10 rounded-xl ${isActive ? "bg-primary/10" : "bg-red-50"} flex items-center justify-center shrink-0`}>
            <Icon size={20} className={accent} />
          </div>
          <div>
            <h2 id="kill-switch-modal-title" className="text-lg font-bold tracking-tight">
              {title}
            </h2>
            <p className="text-xs text-foreground-muted mt-0.5">
              {isActive
                ? "Restore normal trading operation"
                : "Halt all new entries across the fleet"}
            </p>
          </div>
        </div>

        <div className="text-sm text-foreground space-y-2 mb-5">
          {isActive ? (
            <>
              <p>Bots will resume monitoring closed candles for entry signals.</p>
              <p className="text-foreground-muted">
                Any open positions remained under their own stop / target rules while the
                switch was active — none will be auto-closed by deactivation.
              </p>
            </>
          ) : (
            <>
              <p>This will <strong>halt all new entries</strong> immediately:</p>
              <ul className="list-disc pl-5 space-y-0.5 text-sm text-foreground-muted">
                <li>No new orders submitted to any execution target</li>
                <li>No re-entries until the switch is deactivated</li>
                <li>
                  <strong className="text-foreground">{openPositions ?? 0}</strong>{" "}
                  open position{(openPositions ?? 0) === 1 ? "" : "s"} will continue
                  running per their stops / targets
                </li>
                <li>
                  <strong className="text-foreground">{runningBots ?? 0}</strong> running
                  bot{(runningBots ?? 0) === 1 ? "" : "s"} will keep evaluating signals
                  but will not place trades
                </li>
              </ul>
              <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mt-3">
                <AlertTriangle size={14} className="text-amber-700 mt-0.5 shrink-0" />
                <p className="text-xs text-amber-900">
                  Use this for emergency stops only. Deactivating later resumes trading
                  immediately.
                </p>
              </div>
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="btn btn-secondary text-sm disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className={`text-sm px-4 py-2 rounded-lg font-medium transition-colors disabled:opacity-50 ${
              isActive
                ? "bg-primary text-white hover:bg-primary/90"
                : "bg-red-600 text-white hover:bg-red-700"
            }`}
          >
            {loading ? "Working..." : `${action} kill switch`}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
