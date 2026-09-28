"use client";

import { useEffect, useId, useRef, useState } from "react";

/** One server-supplied qualifier the operator must tick before confirming. */
export interface Acknowledgement {
  code: string;
  text: string;
}

export interface ConfirmChoice {
  id: string;
  title: string;
  body: string;
  /** Server-computed consequences. Never written into this component. */
  consequences: string[];
  destructive?: boolean;
}

/**
 * THE confirm dialog. There is exactly one, and every destructive or
 * capital-committing action in the workstation goes through it.
 *
 * V1 used a native `confirm()` whose message was a hardcoded string
 * ("Paper trading only — no live execution") that stayed the same in every
 * execution mode, told the operator nothing about what would happen, and could
 * not enumerate consequences. Here:
 *
 *  - the consequence list comes from the API, per choice, per mode;
 *  - the current execution mode is stated in the dialog itself;
 *  - a reason is mandatory and goes to the audit trail;
 *  - a choice must be picked explicitly when several exist — no default;
 *  - a confirm phrase can be required for the worst actions;
 *  - server-supplied acknowledgements (realism caveats on a promotion) render
 *    as one required checkbox each, unticked on every open. Confirm stays
 *    disabled until every one is ticked, and the ticked codes are handed to
 *    `onConfirm` so the caller sends what the operator actually acknowledged
 *    rather than a hard-coded `true`.
 */
export function ConfirmDialog({
  open,
  title,
  executionMode,
  choices,
  requireReason = true,
  reasonMinLength = 8,
  confirmPhrase,
  confirmLabel = "Confirm",
  busy = false,
  errorMessage,
  notice,
  acknowledgements = [],
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  executionMode: string;
  choices: ConfirmChoice[];
  requireReason?: boolean;
  reasonMinLength?: number;
  confirmPhrase?: string;
  confirmLabel?: string;
  busy?: boolean;
  errorMessage?: string | null;
  /** A non-error status line, e.g. while server-computed consequences load. */
  notice?: string | null;
  acknowledgements?: Acknowledgement[];
  onCancel: () => void;
  onConfirm: (choiceId: string, reason: string, acknowledged: string[]) => void;
}) {
  const [choiceId, setChoiceId] = useState<string | null>(
    choices.length === 1 ? (choices[0]?.id ?? null) : null,
  );
  const [reason, setReason] = useState("");
  const [phrase, setPhrase] = useState("");
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const headingId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  // Reset on open/close during render, not in an effect. A reason typed into
  // one confirmation must never survive into the next one: this dialog is
  // shared by every destructive action, and a stale reason would be written to
  // the audit trail against the wrong act.
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    setChoiceId(choices.length === 1 ? (choices[0]?.id ?? null) : null);
    setReason("");
    setPhrase("");
    setTicked(new Set());
  }

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    dialogRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  // A lone choice is selected even when it arrives after the dialog opened
  // (server-computed choices load asynchronously).
  const selected =
    choices.find((c) => c.id === choiceId) ??
    (choices.length === 1 ? (choices[0] ?? null) : null);
  const reasonOk = !requireReason || reason.trim().length >= reasonMinLength;
  const phraseOk = !confirmPhrase || phrase.trim() === confirmPhrase;
  const acknowledgedCodes = acknowledgements
    .map((a) => a.code)
    .filter((code) => ticked.has(code));
  const acknowledgedOk = acknowledgedCodes.length === acknowledgements.length;
  const canConfirm =
    selected !== null && reasonOk && phraseOk && acknowledgedOk && !busy;

  const toggle = (code: string, on: boolean) =>
    setTicked((previous) => {
      const next = new Set(previous);
      if (on) next.add(code);
      else next.delete(code);
      return next;
    });

  return (
    <div className="dialog-scrim" data-testid="confirm-scrim">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        data-testid="confirm-dialog"
        tabIndex={-1}
        ref={dialogRef}
      >
        <h2 id={headingId}>{title}</h2>
        <div className="dialog__mode" data-testid="confirm-mode">
          Execution mode: <strong>{executionMode.toUpperCase()}</strong>
        </div>

        {choices.length > 1 ? (
          <>
            <label>Choose an action. There is no default.</label>
            <div className="choice-group">
              {choices.map((choice) => (
                <button
                  type="button"
                  key={choice.id}
                  className="choice"
                  data-selected={choice.id === choiceId}
                  data-testid={`confirm-choice-${choice.id}`}
                  onClick={() => setChoiceId(choice.id)}
                >
                  <span className="choice__title">{choice.title}</span>
                  <span className="choice__body">{choice.body}</span>
                </button>
              ))}
            </div>
          </>
        ) : null}

        {selected ? (
          <>
            <label>What this will do</label>
            <ul className="dialog__consequences" data-testid="confirm-consequences">
              {selected.consequences.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </>
        ) : choices.length > 0 ? (
          <p className="muted" data-testid="confirm-no-choice">
            Select an action above to see its consequences.
          </p>
        ) : null}

        {acknowledgements.length > 0 ? (
          <>
            <label>
              Acknowledge each caveat ({acknowledgedCodes.length} of{" "}
              {acknowledgements.length}). Every one is required.
            </label>
            <ul className="ack-list" data-testid="confirm-acknowledgements">
              {acknowledgements.map((ack) => (
                <li key={ack.code}>
                  <label data-testid={`confirm-ack-${ack.code}`}>
                    <input
                      type="checkbox"
                      data-testid={`confirm-ack-input-${ack.code}`}
                      checked={ticked.has(ack.code)}
                      onChange={(e) => toggle(ack.code, e.target.checked)}
                    />
                    <span>
                      <span className="mono">{ack.code}</span> {ack.text}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        ) : null}

        {notice ? (
          <p className="muted" data-testid="confirm-notice" role="status">
            {notice}
          </p>
        ) : null}

        {requireReason ? (
          <>
            <label htmlFor={`${headingId}-reason`}>
              Reason (recorded to the audit trail, minimum {reasonMinLength}{" "}
              characters)
            </label>
            <textarea
              id={`${headingId}-reason`}
              data-testid="confirm-reason"
              rows={2}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </>
        ) : null}

        {confirmPhrase ? (
          <>
            <label htmlFor={`${headingId}-phrase`}>
              Type <span className="mono">{confirmPhrase}</span> to confirm
            </label>
            <input
              id={`${headingId}-phrase`}
              type="text"
              data-testid="confirm-phrase"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
            />
          </>
        ) : null}

        {errorMessage ? (
          <p className="state state--error" data-testid="confirm-error">
            {errorMessage}
          </p>
        ) : null}

        <div className="dialog__actions">
          <button type="button" onClick={onCancel} data-testid="confirm-cancel">
            Cancel
          </button>
          <button
            type="button"
            className={selected?.destructive ? "btn--danger" : "btn--primary"}
            disabled={!canConfirm}
            data-testid="confirm-submit"
            onClick={() => {
              if (selected) onConfirm(selected.id, reason.trim(), acknowledgedCodes);
            }}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
