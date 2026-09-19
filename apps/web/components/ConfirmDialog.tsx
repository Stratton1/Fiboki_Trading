"use client";

import { useEffect, useId, useRef, useState } from "react";

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
 *  - a confirm phrase can be required for the worst actions.
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
  onCancel: () => void;
  onConfirm: (choiceId: string, reason: string) => void;
}) {
  const [choiceId, setChoiceId] = useState<string | null>(
    choices.length === 1 ? (choices[0]?.id ?? null) : null,
  );
  const [reason, setReason] = useState("");
  const [phrase, setPhrase] = useState("");
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

  const selected = choices.find((c) => c.id === choiceId) ?? null;
  const reasonOk = !requireReason || reason.trim().length >= reasonMinLength;
  const phraseOk = !confirmPhrase || phrase.trim() === confirmPhrase;
  const canConfirm = selected !== null && reasonOk && phraseOk && !busy;

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
        ) : (
          <p className="muted" data-testid="confirm-no-choice">
            Select an action above to see its consequences.
          </p>
        )}

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
              if (selected) onConfirm(selected.id, reason.trim());
            }}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
