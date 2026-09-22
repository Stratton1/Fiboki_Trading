"use client";

import { useState } from "react";
import { ApiError, apiFetch, useApi } from "@/lib/api";
import type { Envelope, ExecutionModeBanner, KillSwitchView } from "@/lib/types";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDialog, type ConfirmChoice } from "./ConfirmDialog";

/**
 * The kill switch, reachable in EVERY mode.
 *
 * V1's was a bare icon button on /system with no confirmation, and it could not
 * even be ARMED in paper mode — so the one mode where an operator could safely
 * practise the control was the one mode where it did nothing.
 *
 * Here:
 *  - PAUSE and FLATTEN are two explicit choices with no default and no
 *    pre-selection, matching KillSwitch.activate() which refuses a default mode;
 *  - the consequences of each come from the API, computed for the current mode
 *    and the current number of open positions;
 *  - it goes through the one shared ConfirmDialog;
 *  - FLATTEN additionally requires typing the word FLATTEN.
 */
export function KillSwitchPanel({ compact = false }: { compact?: boolean }) {
  const state = useApi<Envelope<KillSwitchView>>("/api/system/kill-switch");
  const mode = useApi<Envelope<ExecutionModeBanner>>("/api/system/execution-mode");
  const [dialog, setDialog] = useState<"arm" | "disarm" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const executionMode = mode.status === "success" ? mode.data.data.mode : "unknown";

  async function submit(choiceId: string, reason: string) {
    setBusy(true);
    setError(null);
    try {
      if (dialog === "arm") {
        await apiFetch("/api/system/kill-switch/arm", {
          method: "POST",
          body: JSON.stringify({ mode: choiceId, reason }),
        });
      } else {
        await apiFetch("/api/system/kill-switch/disarm", {
          method: "POST",
          body: JSON.stringify({ reason }),
        });
      }
      setDialog(null);
      state.reload();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `${err.message} (${err.code})`
          : "The request failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AsyncBoundary state={state} label="kill switch" onRetry={state.reload}>
      {(envelope) => {
        const view = envelope.data;
        const armChoices: ConfirmChoice[] = [
          {
            id: "pause",
            title: "PAUSE",
            body: "Stop opening and increasing. Leave open positions alone.",
            consequences: view.consequences.pause ?? [],
          },
          {
            id: "flatten",
            title: "FLATTEN",
            body: "Stop everything AND close every open position.",
            consequences: view.consequences.flatten ?? [],
            destructive: true,
          },
        ];

        return (
          <div data-testid="kill-switch-panel">
            <div className="row" style={{ marginBottom: 10 }}>
              <span
                className={`badge badge--${view.active ? "down" : "ok"}`}
                data-testid="kill-switch-status"
                data-active={view.active}
              >
                {view.active ? `ARMED · ${view.mode?.toUpperCase()}` : "DISARMED"}
              </span>
              <span className="muted">
                {view.active
                  ? `Armed by ${view.operator ?? "?"}: ${view.reason ?? ""}`
                  : `${view.open_positions} open position(s). The switch is armable in every mode, including this one.`}
              </span>
            </div>

            {!compact && view.active ? (
              <p className="muted">
                {view.blocks_new_risk
                  ? "New and increasing risk is blocked."
                  : null}{" "}
                {view.requires_flatten
                  ? "Open positions are queued to be closed."
                  : "Open positions are untouched."}
              </p>
            ) : null}

            <div className="row">
              <button
                type="button"
                className="btn--danger"
                data-testid="kill-switch-arm"
                onClick={() => {
                  setError(null);
                  setDialog("arm");
                }}
              >
                {view.active ? "Change halt level" : "Arm kill switch"}
              </button>
              {view.active ? (
                <button
                  type="button"
                  className="btn--warn"
                  data-testid="kill-switch-disarm"
                  onClick={() => {
                    setError(null);
                    setDialog("disarm");
                  }}
                >
                  Disarm and re-arm trading
                </button>
              ) : null}
            </div>

            <ConfirmDialog
              open={dialog === "arm"}
              title="Halt trading"
              executionMode={executionMode}
              choices={armChoices}
              confirmLabel="Halt trading"
              busy={busy}
              errorMessage={error}
              onCancel={() => setDialog(null)}
              onConfirm={submit}
            />
            <ConfirmDialog
              open={dialog === "disarm"}
              title="Re-arm trading"
              executionMode={executionMode}
              confirmPhrase="RE-ARM"
              confirmLabel="Re-arm trading"
              busy={busy}
              errorMessage={error}
              choices={[
                {
                  id: "disarm",
                  title: "DISARM",
                  body: "Lift the halt and allow risk-adding orders again.",
                  consequences: [
                    "New positions and increases become permitted again.",
                    "Strategies that were blocked will act on their next signal.",
                    `Execution resumes in ${executionMode.toUpperCase()} mode.`,
                    "This is recorded against your name in the audit trail.",
                  ],
                },
              ]}
              onCancel={() => setDialog(null)}
              onConfirm={submit}
            />
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
