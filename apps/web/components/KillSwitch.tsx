"use client";

import { AsyncBoundary } from "./AsyncBoundary";
import { useKillSwitchControl } from "./KillSwitchControl";
import { Button } from "./ui/Button";

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
 *  - it goes through the one shared ConfirmDialog, with asymmetric friction
 *    (components/KillSwitchControl.tsx): PAUSE needs a reason only, in every
 *    mode including LIVE; FLATTEN additionally requires typing FLATTEN, in
 *    every mode; lifting the halt (disarm) requires typing LIFT HALT;
 *  - the consequences of lifting the halt are server-computed too, from
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
 *
 * ⇧K opens the same arm dialog from anywhere (components/shell/Hotkeys.tsx);
 * it never arms anything by itself.
 */
export function KillSwitchPanel({ compact = false }: { compact?: boolean }) {
  const control = useKillSwitchControl();
  const { state, blocked, unconfirmed } = control;

  return (
    <AsyncBoundary state={state} label="kill switch" onRetry={state.reload}>
      {(envelope) => {
        const view = envelope.data;
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
                {view.blocks_new_risk ? "New and increasing risk is blocked." : null}{" "}
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
                disabled={blocked !== null}
                onClick={() => control.open("arm")}
              >
                {view.active ? "Change halt level" : "Arm kill switch"}
              </Button>
              {view.active ? (
                <Button
                  variant="warn"
                  data-testid="kill-switch-disarm"
                  disabled={blocked !== null}
                  onClick={() => control.open("disarm")}
                >
                  Lift the halt (resume trading)
                </Button>
              ) : null}
              {blocked && blocked.reason !== "role" ? (
                <span className="muted" data-testid="kill-switch-blocked">
                  {blocked.text}
                </span>
              ) : blocked ? (
                <span className="muted" data-testid="kill-switch-role-blocked">
                  {blocked.text}
                </span>
              ) : null}
            </div>
            {control.dialogs}
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
