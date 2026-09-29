"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { capability } from "@/lib/auth";
import { awaitEcho } from "@/lib/echo";
import { useApi } from "@/lib/query";
import type {
  Envelope,
  KillSwitchDisarmPreflightView,
  KillSwitchView,
} from "@/lib/types";
import { AsyncBoundary } from "./AsyncBoundary";
import { useExecutionMode, useOperator } from "./shell/platform";
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
 *    order would go, so it confirms nothing (plan §3, report E §4.4);
 *  - a signed-in operator whose role cannot arm it sees the controls disabled
 *    with the reason (the server refuses regardless);
 *  - no optimistic UI (Wave 2): after the POST returns, the dialog stays busy
 *    until the platform echoes the new state on the stream (or a REST re-read
 *    when the stream is not feeding it). After 5 s the dialog closes and the
 *    panel shows "confirming…" until the echo arrives. The panel only ever
 *    shows what the platform says the switch is.
 */

const KILL_SWITCH_PATH = "/api/system/kill-switch";

type Expectation = { label: string; holds: (view: KillSwitchView) => boolean };
export function KillSwitchPanel({ compact = false }: { compact?: boolean }) {
  const state = useApi<Envelope<KillSwitchView>>(KILL_SWITCH_PATH);
  const client = useQueryClient();
  const { mode: executionMode, mutationsAllowed } = useExecutionMode();
  const allowed = capability(useOperator(), "can_arm_kill_switch");
  const [dialog, setDialog] = useState<"arm" | "disarm" | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A change the platform accepted but has not yet echoed back.
  const [unconfirmed, setUnconfirmed] = useState<Expectation | null>(null);

  // Adjusted during render (not in an effect): once the platform shows the
  // requested state, the "confirming…" marker goes in the same frame.
  const current = state.status === "success" ? state.data.data : null;
  if (unconfirmed && current && unconfirmed.holds(current)) setUnconfirmed(null);
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
    setSent(false);
    setError(null);
    const expectation: Expectation =
      dialog === "arm"
        ? {
            label: `armed (${choiceId.toUpperCase()})`,
            holds: (view) => view.active && view.mode === choiceId,
          }
        : { label: "disarmed", holds: (view) => !view.active };
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
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `${err.message} (${err.code})`
          : "The request failed.",
      );
      setBusy(false);
      return;
    }
    setSent(true);
    const echoed = await awaitEcho<Envelope<KillSwitchView>>(client, KILL_SWITCH_PATH, (env) =>
      expectation.holds(env.data),
    );
    if (!echoed) setUnconfirmed(expectation);
    setBusy(false);
    setSent(false);
    setDialog(null);
  }

  const sentNotice = sent
    ? "Sent. Waiting for the platform to confirm the change before closing."
    : null;

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

            {unconfirmed ? (
              <p className="row" data-testid="kill-switch-confirming" role="status">
                <span className="badge badge--neutral">confirming…</span>
                <span className="muted">
                  The platform accepted the request; this panel will show it as{" "}
                  {unconfirmed.label} once the platform confirms it. Until then it shows the last
                  confirmed state.
                </span>
              </p>
            ) : null}

            <div className="row">
              <Button
                variant="danger"
                data-testid="kill-switch-arm"
                disabled={!mutationsAllowed || !allowed.allowed}
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
                  disabled={!mutationsAllowed || !allowed.allowed}
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
                    : executionMode === "unknown"
                      ? "The execution mode is unknown, so the kill switch cannot be confirmed from here."
                      : "The workstation is disconnected from the platform, so nothing can be confirmed from here."}
                </span>
              ) : !allowed.allowed ? (
                <span className="muted" data-testid="kill-switch-role-blocked">
                  {allowed.reason}
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
              notice={sentNotice}
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
              notice={sentNotice ?? disarmNotice}
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
