"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { CONTROL_TONE, sessionControlPath, CONTROLS_PATH, type BotControlView, type ControlAction } from "@/lib/ops";
import { invalidatePath, useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";
import { useExecutionMode, useOperator } from "../shell/platform";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";

/**
 * Per-bot PAUSE / RESUME / DRAIN (Wave 3O).
 *
 * The control is the platform's: GET /api/trading/sessions/{id}/control says
 * the bot's state, what each allowed action will do to it (server-computed
 * consequences) and which actions are allowed from here. This component opens
 * the shared ConfirmDialog, requires a reason of 8 to 500 characters (the
 * backend's ControlRequest bounds), POSTs, and re-reads. No optimistic UI: the
 * state shown after the POST is the one the API returns.
 *
 * Admin only, like the kill switch (routers/ops.py uses require_admin); when
 * it cannot be pressed it says why beside the button. No single keystroke
 * mutates: `PAUSE <bot>` on the command line opens this dialog, never acts.
 */

/** The backend's ControlRequest reason bounds (routers/ops.py, ops/controls.py). */
export const CONTROL_REASON_MIN = 8;
export const CONTROL_REASON_MAX = 500;

const TITLE: Record<ControlAction, string> = { pause: "PAUSE", resume: "RESUME", drain: "DRAIN" };
const BODY: Record<ControlAction, string> = {
  pause: "Stop new entries for this bot. Open positions keep being managed.",
  resume: "Let this bot open new positions again.",
  drain: "Stop new entries and let open positions close by their own rules; then the bot stops.",
};

export function ControlStateTag({ state, note }: { state: string | null | undefined; note?: string | null }) {
  if (!state) return <span className="muted">—</span>;
  return (
    <span
      className={`badge badge--${CONTROL_TONE[state] ?? "unknown"} ops-control-tag`}
      data-testid="bot-control-state"
      data-state={state}
      title={note ?? undefined}
    >
      {state.toUpperCase()}
    </span>
  );
}

function useControlBlocked(): string | null {
  const { mutationsAllowed } = useExecutionMode();
  const operator = useOperator();
  if (!mutationsAllowed) return "Blocked: execution mode unknown or disconnected.";
  // Mirrors require_admin on POST /api/trading/sessions/{id}/control; the
  // server refuses any other role whatever this says.
  if (operator !== null && operator.role !== "admin") return `Blocked: admin role required (you are ${operator.role}).`;
  return null;
}

export function BotControlButton({
  sessionId,
  request,
  label = "Control…",
  testId = "bot-control-open",
  ariaLabel,
}: {
  sessionId: string;
  /** When set (from `PAUSE <bot>`), the dialog opens with this action preselected. */
  request?: ControlAction | null;
  label?: string;
  testId?: string;
  ariaLabel?: string;
}) {
  const [manual, setManual] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const blocked = useControlBlocked();
  const reasonId = useId();
  // A request from the URL opens the dialog once (until dismissed), with the
  // named act chosen; a click opens it with every allowed act.
  const fromRequest = Boolean(request) && blocked === null && !dismissed;
  const open = manual || fromRequest;
  const preferred = fromRequest ? (request ?? null) : null;
  return (
    <span className="ops-control" data-testid={`bot-control-${sessionId}`}>
      <Button
        size="sm"
        data-testid={testId}
        aria-label={ariaLabel}
        disabled={blocked !== null}
        aria-describedby={blocked ? reasonId : undefined}
        onClick={() => setManual(true)}
      >
        {label}
      </Button>
      {blocked ? (
        <span id={reasonId} className="ack__blocked" data-testid="bot-control-blocked">
          {blocked}
        </span>
      ) : null}
      {open ? (
        <BotControlDialog
          sessionId={sessionId}
          preferred={preferred}
          onClose={() => {
            setManual(false);
            setDismissed(true);
          }}
        />
      ) : null}
    </span>
  );
}

function BotControlDialog({
  sessionId,
  preferred,
  onClose,
}: {
  sessionId: string;
  preferred: ControlAction | null;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const { mode } = useExecutionMode();
  const path = sessionControlPath(sessionId);
  const view = useApi<Envelope<BotControlView>>(path);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const data = view.status === "success" ? view.data.data : null;
  const allowed = (data?.allowed_actions ?? []) as ControlAction[];
  // From `PAUSE <bot>` the named act is the only choice (so it is selected);
  // otherwise every allowed act, with no default.
  const ordered = preferred && allowed.includes(preferred) ? [preferred] : allowed;
  const notice =
    view.status === "loading"
      ? "Reading this bot's control state…"
      : view.status === "error"
        ? null
        : data && preferred && !allowed.includes(preferred)
          ? `${TITLE[preferred]} is not allowed: the bot is ${data.state.toUpperCase()}. ${data.meaning}`
          : data
            ? `Now ${data.state.toUpperCase()}: ${data.meaning}`
            : null;

  async function confirm(choice: string, reason: string) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(path, { method: "POST", body: JSON.stringify({ action: choice, reason }) });
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (${err.code})` : "The request failed.");
      setBusy(false);
      return;
    }
    await Promise.all([
      invalidatePath(client, path),
      invalidatePath(client, CONTROLS_PATH),
      invalidatePath(client, "/api/trading/fleet"),
    ]);
    setBusy(false);
    onClose();
  }

  return (
    <ConfirmDialog
      open
      title={`Bot control: ${sessionId}`}
      executionMode={mode}
      choices={ordered.map((action) => ({
        id: action,
        title: TITLE[action],
        body: BODY[action],
        consequences: data?.consequences[action] ?? [],
        // Pausing and draining only REMOVE entries: no typed phrase, even in
        // LIVE, so the brake never sits behind a typing test.
        phrase: action === "resume" ? undefined : null,
      }))}
      requireReason
      reasonMinLength={CONTROL_REASON_MIN}
      reasonMaxLength={CONTROL_REASON_MAX}
      confirmLabel="Record"
      busy={busy || view.status === "loading"}
      errorMessage={error ?? (view.status === "error" ? `Could not read the control state: ${view.error.message}` : null)}
      notice={notice}
      onCancel={onClose}
      onConfirm={confirm}
    />
  );
}
