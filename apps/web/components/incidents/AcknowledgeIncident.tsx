"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { capability } from "@/lib/auth";
import { invalidatePath } from "@/lib/query";
import { useExecutionMode, useOperator } from "../shell/platform";
import { Button, type ButtonSize } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";

/**
 * THE acknowledge control for an incident, used by every screen that offers
 * the act (the Command attention queue, the Command incidents table, the
 * incident page). One component, so the friction cannot differ by where the
 * operator happens to press it (inventory F-3):
 *
 *  - it opens the shared ConfirmDialog, which states the execution mode and,
 *    in LIVE, asks for the typed REAL MONEY phrase;
 *  - a reason of 8 to 500 characters is required (the backend's
 *    IncidentAckRequest bounds, routers/incidents.py) and is audited;
 *  - admin only (the route is behind require_admin), and nothing can be
 *    confirmed while the execution mode is unknown or the workstation is
 *    disconnected. When it cannot be pressed, WHY is printed beside it, not
 *    only in a hover title (inventory F-4);
 *  - no optimistic UI: after the POST the dialog stays busy until the caller's
 *    `echo` says the platform shows the acknowledgement, or it times out, in
 *    which case the caller is told (`onUnconfirmed`) and shows "confirming…".
 *
 * Acknowledging records that a named human has seen the incident. It does not
 * resolve it.
 */

export const INCIDENTS_PATH = "/api/system/incidents";
/** The backend's IncidentAckRequest bounds (routers/incidents.py). */
export const ACK_REASON_MIN = 8;
export const ACK_REASON_MAX = 500;

/**
 * Why an incident action cannot be taken now, as a short line and in full, or
 * null. Acknowledging and annotating share the rule: both routes are behind
 * require_admin (routers/incidents.py).
 */
export function useAckBlocked(act: "acknowledge" | "note" = "acknowledge"): { short: string; full: string } | null {
  const { mutationsAllowed } = useExecutionMode();
  const operator = useOperator();
  const allowed = capability(operator, "can_acknowledge");
  if (!mutationsAllowed) {
    return {
      short: "Blocked: execution mode unknown or disconnected.",
      full: "The execution mode is unknown or the workstation is disconnected; nothing can be confirmed until the platform answers.",
    };
  }
  if (!allowed.allowed) {
    return {
      short: "Blocked: admin role required.",
      full:
        act === "note" && operator
          ? `Signed in as ${operator.display_name} (${operator.role}). Only the admin role may add a note to an incident; the platform would refuse it.`
          : (allowed.reason ?? "This role cannot acknowledge an incident."),
    };
  }
  return null;
}

export function AcknowledgeIncident({
  incidentId,
  title,
  testId,
  label = "Acknowledge",
  size = "sm",
  echo,
  onUnconfirmed,
}: {
  incidentId: string;
  /** The incident's title, for the dialog's heading. */
  title: string;
  testId: string;
  label?: string;
  size?: ButtonSize;
  /** Resolves true once the platform shows the acknowledgement (lib/echo.ts). */
  echo: () => Promise<boolean>;
  onUnconfirmed?: () => void;
}) {
  const client = useQueryClient();
  const { mode } = useExecutionMode();
  const blocked = useAckBlocked();
  const reasonId = useId();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function acknowledge(_choice: string, reason: string) {
    setBusy(true);
    setSent(false);
    setError(null);
    try {
      await apiFetch(`${INCIDENTS_PATH}/${encodeURIComponent(incidentId)}/ack`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (${err.code})` : "The request failed.");
      setBusy(false);
      return;
    }
    setSent(true);
    void invalidatePath(client, INCIDENTS_PATH);
    const echoed = await echo();
    if (!echoed) onUnconfirmed?.();
    setBusy(false);
    setSent(false);
    setOpen(false);
  }

  return (
    <span className="ack" data-testid={`ack-control-${testId}`}>
      <Button
        size={size}
        data-testid={testId}
        disabled={blocked !== null}
        aria-describedby={blocked ? reasonId : undefined}
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        {label}
      </Button>
      {blocked ? (
        <span id={reasonId} className="ack__blocked" data-testid={`ack-blocked-${testId}`} title={blocked.full}>
          {blocked.short}
        </span>
      ) : null}
      <ConfirmDialog
        open={open}
        title={`Acknowledge: ${title}`}
        executionMode={mode}
        choices={[
          {
            id: "acknowledge",
            title: "ACKNOWLEDGE",
            body: "Record that you have seen this incident. It is not resolved by this, and it stays in the log.",
            consequences: [],
          },
        ]}
        reasonMinLength={ACK_REASON_MIN}
        reasonMaxLength={ACK_REASON_MAX}
        confirmLabel="Acknowledge"
        busy={busy}
        errorMessage={error}
        notice={sent ? "Sent. Waiting for the platform to confirm the acknowledgement." : null}
        onCancel={() => setOpen(false)}
        onConfirm={acknowledge}
      />
    </span>
  );
}
