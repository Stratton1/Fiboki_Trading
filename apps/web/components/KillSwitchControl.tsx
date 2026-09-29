"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { capability } from "@/lib/auth";
import { awaitEcho } from "@/lib/echo";
import { invalidatePath, useApi, type ApiHandle } from "@/lib/query";
import type { Envelope, KillSwitchDisarmPreflightView, KillSwitchView } from "@/lib/types";
import { useExecutionMode, useOperator, MODE_PATH } from "./shell/platform";
import { ConfirmDialog, type ConfirmChoice } from "./ui/ConfirmDialog";

/**
 * The kill switch's mutation flow, shared by the panel (Overview, Risk) and
 * the shell's ⇧K dialog, so there is exactly one implementation of "arm" and
 * "disarm" on this workstation.
 *
 * Friction is asymmetric (report G W-08, W-09; plan §3):
 *  - PAUSE only reduces risk: a reason, and NO typed phrase in any mode, LIVE
 *    included. The emergency brake is not behind a typing test.
 *  - FLATTEN closes every open position at market: the operator types
 *    FLATTEN, in every mode.
 *  - Disarm re-enables risk: the typed RE-ARM phrase and server-computed
 *    consequences.
 *
 * No optimistic UI: after the POST the dialog stays busy until the platform
 * echoes the change (lib/echo.ts). On success the execution-mode read is
 * re-read at once as well, so the banner's "KILL SWITCH ARMED" can never
 * disagree with the panel while the stream is not feeding it (report G W-07).
 */

export const KILL_SWITCH_PATH = "/api/system/kill-switch";
export const FLATTEN_PHRASE = "FLATTEN";
export const REARM_PHRASE = "RE-ARM";

type Expectation = { label: string; holds: (view: KillSwitchView) => boolean };
export type KillSwitchDialog = "arm" | "disarm" | null;

export interface KillSwitchControl {
  state: ApiHandle<Envelope<KillSwitchView>>;
  dialog: KillSwitchDialog;
  open: (dialog: Exclude<KillSwitchDialog, null>) => void;
  /** Whether a dialog may be opened, and why not. */
  blocked: { reason: "loading" | "unknown" | "disconnected" | "role"; text: string } | null;
  unconfirmed: Expectation | null;
  dialogs: ReactNode;
}

/** The arm choices, with their friction. Consequences are the server's. */
export function armChoices(view: KillSwitchView | null): ConfirmChoice[] {
  return [
    {
      id: "pause",
      title: "PAUSE",
      body: "Stop opening and increasing. Leave open positions alone.",
      consequences: view?.consequences.pause ?? [],
      phrase: null,
    },
    {
      id: "flatten",
      title: "FLATTEN",
      body: "Stop everything AND close every open position.",
      consequences: view?.consequences.flatten ?? [],
      destructive: true,
      phrase: FLATTEN_PHRASE,
    },
  ];
}

export function useKillSwitchControl(
  options: { onClosed?: () => void; read?: boolean } = {},
): KillSwitchControl {
  const read = options.read ?? true;
  const state = useApi<Envelope<KillSwitchView>>(read ? KILL_SWITCH_PATH : null);
  const client = useQueryClient();
  const { mode: executionMode, mutationsAllowed } = useExecutionMode();
  const allowed = capability(useOperator(), "can_arm_kill_switch");
  const [dialog, setDialog] = useState<KillSwitchDialog>(null);
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

  const blocked: KillSwitchControl["blocked"] = !mutationsAllowed
    ? executionMode === "loading"
      ? { reason: "loading", text: "Reading the execution mode before enabling this control." }
      : executionMode === "unknown"
        ? {
            reason: "unknown",
            text: "The execution mode is unknown, so the kill switch cannot be confirmed from here.",
          }
        : {
            reason: "disconnected",
            text: "The workstation is disconnected from the platform, so nothing can be confirmed from here.",
          }
    : !allowed.allowed
      ? { reason: "role", text: allowed.reason ?? "Your role cannot arm the kill switch." }
      : null;

  const close = () => {
    setDialog(null);
    options.onClosed?.();
  };

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
      setError(err instanceof ApiError ? `${err.message} (${err.code})` : "The request failed.");
      setBusy(false);
      return;
    }
    setSent(true);
    // The banner reads the execution-mode payload, which carries the
    // kill-switch state too: re-read it now rather than on its next poll.
    void invalidatePath(client, MODE_PATH);
    const echoed = await awaitEcho<Envelope<KillSwitchView>>(client, KILL_SWITCH_PATH, (env) =>
      expectation.holds(env.data),
    );
    if (!echoed) setUnconfirmed(expectation);
    void invalidatePath(client, MODE_PATH);
    setBusy(false);
    setSent(false);
    close();
  }

  const disarmChoices: ConfirmChoice[] =
    disarmPreflight.status === "success"
      ? Object.entries(disarmPreflight.data.data.consequences).map(([id, consequences]) => ({
          id,
          title: id.toUpperCase(),
          body: "Lift the halt and allow risk-adding orders again.",
          consequences,
        }))
      : [];
  const disarmNotice =
    disarmPreflight.status === "loading"
      ? "Loading the consequences of re-arming from the platform."
      : null;
  const disarmError =
    disarmPreflight.status === "error"
      ? `Could not load what re-arming would do: ${disarmPreflight.error.message} (${disarmPreflight.error.code}). Nothing can be confirmed without it.`
      : null;
  const armNotice =
    state.status === "loading"
      ? "Loading the consequences of each halt level from the platform."
      : null;
  const armError =
    state.status === "error"
      ? `Could not load the kill switch: ${state.error.message} (${state.error.code}). Nothing can be confirmed without its consequences.`
      : null;
  const sentNotice = sent
    ? "Sent. Waiting for the platform to confirm the change before closing."
    : null;

  const view = state.status === "success" ? state.data.data : null;
  const dialogs = (
    <>
      <ConfirmDialog
        open={dialog === "arm"}
        title="Halt trading"
        executionMode={executionMode}
        choices={view ? armChoices(view) : []}
        confirmLabel="Halt trading"
        busy={busy}
        errorMessage={error ?? armError}
        notice={sentNotice ?? armNotice}
        onCancel={close}
        onConfirm={submit}
      />
      <ConfirmDialog
        open={dialog === "disarm"}
        title="Re-arm trading"
        executionMode={executionMode}
        confirmPhrase={REARM_PHRASE}
        confirmLabel="Re-arm trading"
        busy={busy}
        errorMessage={error ?? disarmError}
        notice={sentNotice ?? disarmNotice}
        choices={disarmChoices}
        onCancel={close}
        onConfirm={submit}
      />
    </>
  );

  return {
    state,
    dialog,
    open: (next) => {
      setError(null);
      setDialog(next);
    },
    blocked,
    unconfirmed,
    dialogs,
  };
}
