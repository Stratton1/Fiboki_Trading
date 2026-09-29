"use client";

import { useState } from "react";
import { ApiError, apiFetch, useApi } from "@/lib/api";
import type {
  Envelope,
  KillSwitchDisarmPreflightView,
  KillSwitchView,
} from "@/lib/types";
import { AsyncBoundary } from "./AsyncBoundary";
import { useExecutionMode } from "./shell/platform";
import { Button } from "./ui/Button";
import { ConfirmDialog, type ConfirmChoice } from "./ui/ConfirmDialog";

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
 *  - FLATTEN additionally requires typing the word FLATTEN;
 *  - the disarm (re-arm trading) consequences are server-computed too, from
 *    GET /api/trading/preflight/kill-switch-disarm, fetched when the dialog
 *    opens so they describe the halt actually being lifted;
 *  - while the execution mode is unknown (the platform has never answered),
 *    neither control can be opened: this workstation cannot say where an
 *    order would go, so it confirms nothing (plan §3, report E §4.4).
 */
export function KillSwitchPanel({ compact = false }: { compact?: boolean }) {
  const state = useApi<Envelope<KillSwitchView>>("/api/system/kill-switch");
  const { mode: executionMode, mutationsAllowed } = useExecutionMode();
  const [dialog, setDialog] = useState<"arm" | "disarm" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const disarmPreflight = useApi<Envelope<KillSwitchDisarmPreflightView>>(
    dialog === "disarm" ? "/api/trading/preflight/kill-switch-disarm" : null,
  );

  const disarmChoices: ConfirmChoice[] =
    disarmPreflight.status === "success"
      ? Object.entries(disarmPreflight.data.data.consequences).map(
          ([id, consequences]) => ({
            id,
            title: id.toUpperCase(),
            body: "Lift the halt and allow risk-adding orders again.",
            consequences,
          }),
        )
      : [];
  const disarmNotice =
    disarmPreflight.status === "loading"
      ? "Loading the consequences of re-arming from the platform."
      : null;
  const disarmError =
    disarmPreflight.status === "error"
      ? `Could not load what re-arming would do: ${disarmPreflight.error.message} (${disarmPreflight.error.code}). Nothing can be confirmed without it.`
      : null;

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
            <div className="row mb-2.5">
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
              <Button
                variant="danger"
                data-testid="kill-switch-arm"
                disabled={!mutationsAllowed}
                onClick={() => {
                  setError(null);
                  setDialog("arm");
                }}
              >
                {view.active ? "Change halt level" : "Arm kill switch"}
              </Button>
              {view.active ? (
                <Button
                  variant="warn"
                  data-testid="kill-switch-disarm"
                  disabled={!mutationsAllowed}
                  onClick={() => {
                    setError(null);
                    setDialog("disarm");
                  }}
                >
                  Disarm and re-arm trading
                </Button>
              ) : null}
              {!mutationsAllowed ? (
                <span className="muted" data-testid="kill-switch-blocked">
                  {executionMode === "loading"
                    ? "Reading the execution mode before enabling this control."
                    : "The execution mode is unknown, so the kill switch cannot be confirmed from here."}
                </span>
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
              errorMessage={error ?? disarmError}
              notice={disarmNotice}
              choices={disarmChoices}
              onCancel={() => setDialog(null)}
              onConfirm={submit}
            />
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
